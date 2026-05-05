"""SenpiClient — direct-HTTPS MCP transport + runtime-API signal POST.

Bypasses openclaw gateway and mcporter entirely. The 6-process spawn tree
(gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote)
is replaced with a single Python HTTP request.

Returns the same unwrapped JSON shape that `mcporter call` returns — so it
is a near drop-in for `mcporter_call(tool, **params)`.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from . import _config as cfg
from ._logging import log_event


_MCP_PROTOCOL_VERSION = "2025-03-26"
_HELPERS_NAME = "senpi_runtime_helpers"
_HELPERS_VERSION = "0.1.0"


class SenpiClientError(Exception):
    """Raised when MCP or signal call fails for non-network reasons (auth, schema)."""


class _MCPSession:
    """Holds the streamable-http session id obtained via `initialize`. One per process is fine."""

    def __init__(self) -> None:
        self.session_id: Optional[str] = None
        self.next_id: int = 0
        self.initialized: bool = False

    def alloc_id(self) -> int:
        self.next_id += 1
        return self.next_id


def _post_json(
    url: str,
    body: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
) -> "urllib.request.addinfourl":
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)


def _read_response_body(resp: "urllib.request.addinfourl") -> Dict[str, Any]:
    """Streamable-HTTP allows a single JSON response or an SSE stream of events.

    For tool calls we expect one response; we accept either content-type and
    return the first JSON-RPC payload we can parse.
    """
    content_type = (resp.headers.get("Content-Type") or "").lower()
    raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue
        return {}
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        return {}


def _unwrap_tool_result(rpc_response: Dict[str, Any]) -> Any:
    """Convert MCP tool/call JSON-RPC response to the shape mcporter returns.

    Returns the inner JSON document if `content[0].text` parses as JSON,
    else the raw envelope. Mirrors what producers see from `mcporter_call`.
    """
    if "error" in rpc_response and rpc_response["error"]:
        raise SenpiClientError(f"MCP error: {rpc_response['error']}")
    result = rpc_response.get("result")
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            try:
                return json.loads(first["text"])
            except (json.JSONDecodeError, TypeError):
                return result
    return result


class SenpiClient:
    """Process-wide client. Lazy `initialize`, reuses session across calls in one tick."""

    def __init__(
        self,
        mcp_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        runtime_host: Optional[str] = None,
        runtime_port: Optional[int] = None,
    ) -> None:
        self.mcp_url = mcp_url or cfg.MCP_URL
        self.auth_token = auth_token or cfg.SENPI_AUTH_TOKEN
        self.runtime_host = runtime_host or cfg.RUNTIME_API_HOST
        self.runtime_port = runtime_port or cfg.RUNTIME_API_PORT
        self._session = _MCPSession()

    # ──────────────── MCP ────────────────

    def _mcp_headers(self) -> Dict[str, str]:
        h = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
        if self._session.session_id:
            h["Mcp-Session-Id"] = self._session.session_id
        return h

    def _initialize_if_needed(self, timeout: float) -> None:
        if self._session.initialized:
            return
        body = {
            "jsonrpc": "2.0",
            "id": self._session.alloc_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _HELPERS_NAME, "version": _HELPERS_VERSION},
            },
        }
        started = time.time()
        try:
            with _post_json(self.mcp_url, body, self._mcp_headers(), timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self._session.session_id = sid
                _ = _read_response_body(resp)
            # Streamable-HTTP requires a `notifications/initialized` after init.
            note = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            with _post_json(self.mcp_url, note, self._mcp_headers(), timeout) as resp:
                _ = resp.read()
        except urllib.error.HTTPError as e:
            log_event(
                "mcp_init_http_error",
                status=getattr(e, "code", None),
                duration_ms=int((time.time() - started) * 1000),
            )
            raise
        except urllib.error.URLError as e:
            log_event(
                "mcp_init_network_error",
                reason=str(e.reason),
                duration_ms=int((time.time() - started) * 1000),
            )
            raise
        self._session.initialized = True
        log_event("mcp_initialized", session_id_present=bool(self._session.session_id))

    def mcp_call(
        self,
        tool: str,
        timeout: Optional[float] = None,
        **arguments: Any,
    ) -> Any:
        """Call an MCP tool. Returns the unwrapped JSON document (mcporter-compatible).

        Raises `SenpiClientError` for protocol errors. Raises `urllib.error.URLError` /
        `socket.timeout` on network errors / wall-clock timeout exceeded.
        """
        timeout = timeout if timeout is not None else cfg.MCP_TIMEOUT_SECONDS
        self._initialize_if_needed(timeout)
        body = {
            "jsonrpc": "2.0",
            "id": self._session.alloc_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        started = time.time()
        try:
            with _post_json(self.mcp_url, body, self._mcp_headers(), timeout) as resp:
                rpc = _read_response_body(resp)
            duration_ms = int((time.time() - started) * 1000)
            log_event("mcp_call", tool=tool, duration_ms=duration_ms, status="ok")
            return _unwrap_tool_result(rpc)
        except urllib.error.HTTPError as e:
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "mcp_call",
                tool=tool,
                duration_ms=duration_ms,
                status="http_error",
                code=getattr(e, "code", None),
            )
            raise
        except urllib.error.URLError as e:
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "mcp_call",
                tool=tool,
                duration_ms=duration_ms,
                status="network_error",
                reason=str(e.reason),
            )
            raise
        except Exception as e:
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "mcp_call",
                tool=tool,
                duration_ms=duration_ms,
                status="exception",
                error=str(e),
            )
            raise

    # ──────────────── Signals ────────────────

    def _signals_url(self) -> str:
        return f"http://{self.runtime_host}:{self.runtime_port}/signals"

    def push_signals(
        self,
        items: List[Dict[str, Any]],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """POST a batch of signal items to the runtime API at `/signals`.

        Body wire-shape is the bare JSON array (per
        `runtime-api/routes/signals.schema.ts`). Each item must include at
        minimum `address` and `scanner`. Use `data` for a single ingest
        payload (signal- or context-producing scanner).

        Replaces `subprocess.run(["openclaw","senpi","external-scanner","ingest",...])`.
        """
        if not isinstance(items, list) or not items:
            raise SenpiClientError("push_signals() requires a non-empty list")
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                raise SenpiClientError(f"item[{i}] must be a dict")
            if "address" not in it or "scanner" not in it:
                raise SenpiClientError(
                    f"item[{i}] missing required fields: address, scanner"
                )
        timeout = timeout if timeout is not None else cfg.SIGNAL_TIMEOUT_SECONDS
        body = items  # bare array — runtime schema is Array<SignalItem>
        started = time.time()
        try:
            with _post_json(self._signals_url(), body, {}, timeout) as resp:
                raw = resp.read()
            duration_ms = int((time.time() - started) * 1000)
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                parsed = {}
            log_event(
                "signal_post",
                batch_size=len(items),
                bytes=len(raw),
                duration_ms=duration_ms,
                status="ok",
            )
            return parsed
        except urllib.error.HTTPError as e:
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "signal_post",
                batch_size=len(items),
                duration_ms=duration_ms,
                status="http_error",
                code=getattr(e, "code", None),
            )
            raise
        except urllib.error.URLError as e:
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "signal_post",
                batch_size=len(items),
                duration_ms=duration_ms,
                status="network_error",
                reason=str(e.reason),
            )
            raise

    def push_signal(
        self,
        address: str,
        scanner: str,
        data: Optional[Dict[str, Any]] = None,
        asset: Optional[str] = None,
        direction: Optional[str] = None,
        score: Optional[float] = None,
        signal_type: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Push one signal — convenience wrapping a one-element batch.

        Maps 1:1 to `openclaw senpi external-scanner ingest`:
        - `address` ↔ `--address`
        - `scanner` ↔ `--scanner`
        - `data`    ↔ `--payload`
        """
        item: Dict[str, Any] = {"address": address, "scanner": scanner}
        if data is not None:
            item["data"] = data
        if asset is not None:
            item["asset"] = asset
        if direction is not None:
            item["direction"] = direction
        if score is not None:
            item["score"] = score
        if signal_type is not None:
            item["signal_type"] = signal_type
        return self.push_signals([item], timeout=timeout)
