#!/usr/bin/env python3
"""Self-contained streamable-HTTP MCP client (stdlib only).

Ported from senpi_runtime_helpers.SenpiClient so the discovery skill has ZERO cross-skill
dependency — it ships its own market-data transport. Read-only tool calls only.

  client = MCPClient()                       # reads SENPI_AUTH_TOKEN / SENPI_MCP_URL from env
  data = client.mcp_call("market_get_asset_data", asset="BTC")   # -> unwrapped {success,data,...}

Returns the same unwrapped JSON `mcporter`/SenpiClient returns. JSON-RPC errors and tool-level
failures (result.isError) raise MCPError so callers log a warning instead of silently reading None.
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
    """tools/call JSON-RPC -> the inner JSON document (content[0].text), like mcporter.

    Raises MCPError on a JSON-RPC error or a tool-level failure (result.isError) — silence
    here is how a missing/renamed tool masquerades as 'no data'."""
    if not isinstance(rpc, dict):
        return None
    if rpc.get("error"):
        err = rpc["error"]
        raise MCPError(f"JSON-RPC error {err.get('code')}: {err.get('message')}"
                       if isinstance(err, dict) else str(err))
    result = rpc.get("result")
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    text = None
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            text = first["text"]
    if result.get("isError"):
        raise MCPError(f"tool error: {str(text)[:300]}")
    if text is not None:
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return result
        # the senpi server reports tool failures app-level: HTTP 200 + {"success": false, "error": {...}}
        if isinstance(doc, dict) and doc.get("success") is False:
            err = doc.get("error") or {}
            raise MCPError(f"tool failed: {err.get('code')}: {err.get('message')}"
                           if isinstance(err, dict) else f"tool failed: {err}")
        return doc
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
                           "clientInfo": {"name": "senpi-smart-money", "version": "2.0.0"}}}
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
