"""SenpiClient — direct-HTTPS MCP transport + runtime-API signal POST.

Bypasses openclaw gateway and mcporter entirely. The 6-process spawn tree
(gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote)
is replaced with a single Python HTTP request.

Returns the same unwrapped JSON shape that `mcporter call` returns — so it
is a near drop-in for `mcporter_call(tool, **params)`.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import itertools
import json
import threading
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
        self._id_counter = itertools.count(1)
        self.initialized: bool = False

    def alloc_id(self) -> int:
        # itertools.count is implemented in C and atomic w.r.t. the GIL,
        # so request IDs stay unique under multi-threaded use.
        return next(self._id_counter)


def _post_json(
    url: str,
    body: Any,
    headers: Dict[str, str],
    timeout: float,
):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)


def _read_response_body(resp) -> Dict[str, Any]:
    """Streamable-HTTP allows a single JSON response or an SSE stream of events.

    For tool calls we expect one response; we accept either content-type and
    return the first JSON-RPC payload we can parse. Raises `SenpiClientError`
    on empty / malformed bodies — the caller treating those as "tool returned
    nothing" silently was the previous behavior, which masked real failures.
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
        raise SenpiClientError("MCP SSE response had no parseable data event")
    if not text:
        raise SenpiClientError("MCP response body was empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise SenpiClientError(f"MCP response not valid JSON: {e}") from e
    return parsed if isinstance(parsed, dict) else {"result": parsed}


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
        # Per-process per-client cache (used by cache.py); making the cache
        # instance-scoped removes the cross-client key-namespace leak that
        # the previous module-level _store had.
        self._cache: Dict[str, Any] = {}
        # Serializes initialize / notifications/initialized handshake so
        # parallel callers don't issue duplicate init POSTs.
        self._init_lock = threading.Lock()

    # ──────────────── MCP ────────────────

    def _mcp_headers(self) -> Dict[str, str]:
        h = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
        if self._session.session_id:
            h["Mcp-Session-Id"] = self._session.session_id
        return h

    def _initialize_if_needed(self, timeout: float) -> None:
        # Double-checked locking: cheap path takes no lock when already
        # initialized; slow path holds `_init_lock` so only one thread runs
        # the initialize + notifications/initialized handshake even when
        # called from `parallel(...)` workers.
        if self._session.initialized:
            return
        with self._init_lock:
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
            sid: Optional[str] = None
            try:
                with _post_json(self.mcp_url, body, self._mcp_headers(), timeout) as resp:
                    sid = resp.headers.get("Mcp-Session-Id")  # case-insensitive in CPython
                    _ = _read_response_body(resp)
                # Streamable-HTTP requires `notifications/initialized` after init.
                # Use the candidate sid in headers for THIS handshake so the
                # server can correlate; only commit it to the session if the
                # whole two-step succeeds.
                note_headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
                if sid:
                    note_headers["Mcp-Session-Id"] = sid
                note = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
                with _post_json(self.mcp_url, note, note_headers, timeout) as resp:
                    _ = resp.read()
            except urllib.error.HTTPError as e:
                # Init failed cleanly — leave session_id unset so the next
                # attempt starts from scratch. No partial commit.
                self._session.session_id = None
                self._session.initialized = False
                log_event(
                    "mcp_init_http_error",
                    status=getattr(e, "code", None),
                    duration_ms=int((time.time() - started) * 1000),
                )
                raise
            except urllib.error.URLError as e:
                self._session.session_id = None
                self._session.initialized = False
                log_event(
                    "mcp_init_network_error",
                    reason=str(e.reason),
                    duration_ms=int((time.time() - started) * 1000),
                )
                raise
            # Both POSTs succeeded — commit the session_id atomically.
            self._session.session_id = sid
            self._session.initialized = True
            log_event("mcp_initialized", session_id_present=bool(sid))

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
        except SenpiClientError as e:
            # Server-reported tool error or malformed protocol payload —
            # distinct from transport-layer exceptions in 3e.
            duration_ms = int((time.time() - started) * 1000)
            log_event(
                "mcp_call",
                tool=tool,
                duration_ms=duration_ms,
                status="server_error",
                error=str(e)[:200],
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
            address = it.get("address")
            scanner = it.get("scanner")
            if not isinstance(address, str) or not address.startswith("0x") or len(address) < 4:
                raise SenpiClientError(
                    f"item[{i}].address must be a 0x-prefixed string (got {type(address).__name__})"
                )
            if not isinstance(scanner, str) or not scanner:
                raise SenpiClientError(
                    f"item[{i}].scanner must be a non-empty string (got {type(scanner).__name__})"
                )
            if "score" in it:
                s = it["score"]
                if not isinstance(s, (int, float)) or not (0.0 <= float(s) <= 1.0):
                    raise SenpiClientError(
                        f"item[{i}].score must be a number in [0, 1] (got {s!r})"
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
            # The runtime always returns HTTP 200 with a per-item results array;
            # individual items can have success=false (e.g. INVALID_REQUEST when
            # an undeclared data field is sent). Surface those rejections so
            # producers don't silently send into a black hole.
            results = parsed.get("results") if isinstance(parsed, dict) else None
            failed = []
            if isinstance(results, list):
                failed = [r for r in results if isinstance(r, dict) and r.get("success") is False]
            if failed:
                log_event(
                    "signal_post",
                    batch_size=len(items),
                    bytes=len(raw),
                    duration_ms=duration_ms,
                    status="rejected",
                    failed_count=len(failed),
                    first_code=failed[0].get("code"),
                    first_message=str(failed[0].get("message", ""))[:200],
                )
                raise SenpiClientError(
                    f"signal_post: {len(failed)}/{len(items)} item(s) rejected; "
                    f"first: code={failed[0].get('code')} message={failed[0].get('message')}"
                )
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

        Field semantics (per
        senpi-trading-runtime/src/runtime-api/routes/signals.schema.ts and
        external-scanner-receiver.ts):

        Routing fields (top-level on SignalItem):
        - `address` (required): wallet address — runtime routes by lowercased copy.
        - `scanner` (required): scanner id declared in the strategy's runtime.yaml.
        - `asset` (required for signal-emitting single ingests): ticker — uppercase
          Hyperliquid-canonical ("MAVIA", "TST"). No runtime-side normalizer.
        - `direction` (optional): "LONG" | "SHORT" | None. Strict — the receiver's
          `normalizeDirection` rejects anything else with INVALID_REQUEST.
        - `score` (optional): **0..1 confidence**, NOT a strategy composite.
          Used downstream by `decision-engine.ts` as
          `Math.round(highestScore * 10)` to derive a 1..10 confidence integer.
          A value > 1 is rejected by the schema — keep producer composites
          inside `data.score` instead.
        - `signal_type` (optional): per-signal override. When omitted, the
          runtime falls back to the scanner definition's `defaultSignalType`.

        Payload field (validated against scanner config.fields):
        - `data` (optional): scanner-declared field-bag. Becomes `signal.meta`
          downstream. Field names not declared in the scanner's
          `config.fields` are rejected with INVALID_REQUEST.

        - `timeout`: per-call wall-clock cap; defaults to
          SENPI_HELPERS_SIGNAL_TIMEOUT (5s).

        Replaces `subprocess.run(["openclaw","senpi","external-scanner","ingest",
        "--address",address,"--scanner",scanner,"--payload",json.dumps(data)])`.
        """
        if score is not None and not (0.0 <= score <= 1.0):
            raise SenpiClientError(
                f"push_signal: top-level score must be in [0, 1] (got {score!r}); "
                f"keep strategy-specific composite scores inside data."
            )
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
