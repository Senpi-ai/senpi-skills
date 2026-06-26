#!/usr/bin/env python3
"""Self-contained streamable-HTTP MCP client (stdlib only).

Vendored from senpi-strategy-discover/scripts/_mcp.py so the ops skill has ZERO
cross-skill dependency — it ships its own MCP transport. Unlike discovery (read-only),
ops uses it for the strategy LIFECYCLE: strategy_create_custom_strategy / strategy_list /
strategy_close / strategy_get / strategy_get_clearinghouse_state.

  client = MCPClient()                                   # reads SENPI_AUTH_TOKEN / SENPI_MCP_URL
  res = client.mcp_call("strategy_create_custom_strategy", initialBudget=100, positions=[],
                        skillName="spider", skillVersion="6.0.0")        # -> unwrapped JSON dict

`timeout` is the per-call HTTP request timeout (seconds the client waits for the SERVER to
respond), NOT the async on-chain completion time. Lifecycle ops (create/close) are async:
submit with a generous timeout, then POLL a read tool (strategy_list) until the terminal
state. Returns the same unwrapped JSON `mcporter`/SenpiClient returns; None on a protocol error.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import http.client
import itertools
import json
import os
from urllib.parse import urlsplit

MCP_URL = os.environ.get("SENPI_MCP_URL", "https://mcp.prod.senpi.ai/mcp")
AUTH = os.environ.get("SENPI_AUTH_TOKEN", "")
_PROTOCOL = "2025-03-26"


class MCPError(Exception):
    pass


def _post(url, body, headers, timeout):
    """POST a JSON body on a fresh connection; return (status, raw_bytes, content_type, session_id)."""
    parts = urlsplit(url)
    scheme = parts.scheme
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    data = json.dumps(body).encode("utf-8")
    hdrs = {
        "Host": parts.netloc,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Content-Length": str(len(data)),
    }
    hdrs.update(headers)
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = cls(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, raw, (resp.getheader("Content-Type") or ""), resp.getheader("Mcp-Session-Id")
    finally:
        try:
            conn.close()
        except Exception:  # noqa
            pass


def _parse(raw, content_type):
    """Streamable-HTTP returns a single JSON doc OR an SSE stream; return the first JSON-RPC payload."""
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in (content_type or "").lower():
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        obj = json.loads(payload)
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        continue
        return {}
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"result": obj}
    except json.JSONDecodeError:
        return {}


def _unwrap(rpc):
    """tools/call JSON-RPC -> the inner JSON document (content[0].text), like mcporter."""
    if not isinstance(rpc, dict) or rpc.get("error"):
        return None
    result = rpc.get("result")
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


class MCPClient:
    """Lazy `initialize` handshake (streamable-http), then `tools/call`. One per process."""

    def __init__(self, url=None, token=None):
        self.url = url or MCP_URL
        self.token = token if token is not None else AUTH
        self._sid = None
        self._initialized = False
        self._id = itertools.count(1)

    def _headers(self, sid=None):
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        sid = sid or self._sid
        if sid:
            h["Mcp-Session-Id"] = sid
        return h

    def _initialize(self, timeout):
        if self._initialized:
            return
        body = {"jsonrpc": "2.0", "id": next(self._id), "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL, "capabilities": {},
                           "clientInfo": {"name": "senpi-strategy-ops", "version": "2.0.0"}}}
        status, _raw, _ct, sid = _post(self.url, body, self._headers(), timeout)
        if status >= 400:
            raise MCPError(f"initialize HTTP {status}")
        self._sid = sid
        # streamable-http requires notifications/initialized after init
        note = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        _post(self.url, note, self._headers(sid), timeout)
        self._initialized = True

    def mcp_call(self, tool, timeout=12, **arguments):
        self._initialize(timeout)
        body = {"jsonrpc": "2.0", "id": next(self._id), "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        status, raw, ct, _sid = _post(self.url, body, self._headers(), timeout)
        if status >= 400:
            raise MCPError(f"{tool} HTTP {status}")
        return _unwrap(_parse(raw, ct))
