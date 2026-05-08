"""SenpiClient — direct-HTTPS MCP transport + runtime-API signal POST.

Bypasses openclaw gateway and mcporter entirely. The 6-process spawn tree
(gateway → sh → python → node mcporter → npm exec → sh → node mcp-remote)
is replaced with a single Python HTTP request.

Connection reuse: a thread-local pool of `http.client.HTTPSConnection`
keeps one keep-alive connection per (scheme, host, port) per thread. The
first MCP call in a tick pays the TLS handshake; subsequent calls reuse
the connection and only pay the request round-trip. On errors the
connection is closed and re-opened on next use.

Returns the same unwrapped JSON shape that `mcporter call` returns — so
it is a near drop-in for `mcporter_call(tool, **params)`.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import http.client
import io
import itertools
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import _config as cfg
from ._logging import log_event


_MCP_PROTOCOL_VERSION = "2025-03-26"
_HELPERS_NAME = "senpi_runtime_helpers"
# Must stay in sync with __version__ in __init__.py — this string is sent as
# MCP `clientInfo.version`, and the package `__version__` is what `pip show`
# / log scrapers report. A drift between the two makes incident triage say
# different things depending on where you look.
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


class _ConnectionPool:
    """Thread-local keep-alive connection pool keyed by (scheme, host, port).

    Each worker thread keeps its own connection per host, so two threads
    in `parallel(...)` don't serialise on one connection. http.client's
    HTTPConnection is single-request-at-a-time per instance; the
    thread-local layout is the simplest way to get keep-alive without
    adding a dependency on a real pool library.
    """

    def __init__(self) -> None:
        self._local = threading.local()

    def _conns(self) -> Dict[Tuple[str, str, int], "http.client.HTTPConnection"]:
        if not hasattr(self._local, "conns"):
            self._local.conns = {}
        return self._local.conns

    def get(self, scheme: str, host: str, port: int, timeout: float) -> "http.client.HTTPConnection":
        key = (scheme, host, port)
        conns = self._conns()
        conn = conns.get(key)
        if conn is None:
            cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            conn = cls(host, port, timeout=timeout)
            conns[key] = conn
        else:
            # `conn.timeout` is only consulted by http.client during connect().
            # On a reused connection the socket was created with the original
            # timeout; mutating the attribute now is a silent no-op. To make
            # per-call timeout overrides actually take effect, push the new
            # timeout down to the live socket's deadline. `conn.sock` is None
            # before connect — that path is fine because the next request()
            # will trigger connect() and pick up the updated `conn.timeout`.
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
        return conn

    def reset(self, scheme: str, host: str, port: int) -> None:
        key = (scheme, host, port)
        conns = self._conns()
        conn = conns.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def _post_json(
    pool: "_ConnectionPool",
    url: str,
    body: Any,
    headers: Dict[str, str],
    timeout: float,
):
    """POST a JSON body and return the open `http.client.HTTPResponse`.

    Reuses a per-thread keep-alive connection from `pool`. On any
    transport error the connection is closed (so the next call gets a
    fresh one) and a `urllib.error.URLError` / `HTTPError` is raised to
    keep the existing exception-handling shape intact.
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    data = json.dumps(body).encode("utf-8")
    request_headers = {
        "Host": parts.netloc,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Content-Length": str(len(data)),
    }
    request_headers.update(headers)

    conn = pool.get(scheme, host, port, timeout)
    try:
        conn.request("POST", path, body=data, headers=request_headers)
        resp = conn.getresponse()
    except (http.client.HTTPException, ConnectionError, OSError, TimeoutError) as e:
        pool.reset(scheme, host, port)
        raise urllib.error.URLError(str(e)) from e

    if resp.status >= 400:
        # Drain so the connection can be reused for the next request.
        body_bytes = resp.read()
        # If the server signalled connection-close, drop our reference.
        if resp.getheader("Connection", "").lower() == "close":
            pool.reset(scheme, host, port)
        raise urllib.error.HTTPError(
            url, resp.status, resp.reason, dict(resp.getheaders()), io.BytesIO(body_bytes)
        )

    return resp


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
        # Thread-local keep-alive HTTP connections. First call per thread
        # pays a TLS handshake; subsequent calls reuse the connection.
        self._pool = _ConnectionPool()

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
                with _post_json(self._pool, self.mcp_url, body, self._mcp_headers(), timeout) as resp:
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
                with _post_json(self._pool, self.mcp_url, note, note_headers, timeout) as resp:
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
            with _post_json(self._pool, self.mcp_url, body, self._mcp_headers(), timeout) as resp:
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

    def _state_url(self, address: Optional[str] = None) -> str:
        base = f"http://{self.runtime_host}:{self.runtime_port}/state"
        return f"{base}?address={address}" if address else base

    def _fetch_state(
        self,
        wallet: str,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """GET /state?address=<wallet>, return the matching `RuntimeSystemState` or None.

        Used internally by `is_runtime_registered` and `is_scanner_registered`
        as the single shared probe path. Returns None when the runtime API
        responds successfully but the wallet has no registered runtime
        (deleted, or never installed). Raises `SenpiClientError` on transport
        errors, malformed responses, or non-2xx responses — callers (e.g.
        `producer_daemon`) treat raised errors as "still alive" since a flaky
        `/state` endpoint must not kill the daemon.
        """
        if not isinstance(wallet, str) or not wallet.startswith("0x"):
            raise SenpiClientError(f"wallet must be a 0x-prefixed string (got {wallet!r})")
        wallet_lower = wallet.lower()
        timeout = timeout if timeout is not None else cfg.SIGNAL_TIMEOUT_SECONDS
        url = self._state_url(address=wallet_lower)
        parts = urlsplit(url)
        scheme = parts.scheme
        host = parts.hostname or ""
        port = parts.port or 80
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

        conn = self._pool.get(scheme, host, port, timeout)
        try:
            conn.request("GET", path, headers={"Host": parts.netloc, "Accept": "application/json"})
            resp = conn.getresponse()
        except (http.client.HTTPException, ConnectionError, OSError, TimeoutError) as e:
            self._pool.reset(scheme, host, port)
            raise SenpiClientError(f"state probe transport error: {e}") from e

        try:
            raw = resp.read()
        finally:
            if resp.getheader("Connection", "").lower() == "close":
                self._pool.reset(scheme, host, port)

        if resp.status != 200:
            raise SenpiClientError(
                f"state probe HTTP {resp.status}: {raw[:200]!r}"
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SenpiClientError(f"state probe response not valid JSON: {e}") from e

        # Senpi-stack envelope:
        #   { "success": true, "data": { "runtimes": [RuntimeSystemState, ...] } }
        if not isinstance(parsed, dict) or parsed.get("success") is not True:
            err = parsed.get("error") if isinstance(parsed, dict) else None
            raise SenpiClientError(
                f"state probe envelope error: {err if err else parsed!r}"[:300]
            )
        envelope_data = parsed.get("data")
        runtimes = envelope_data.get("runtimes") if isinstance(envelope_data, dict) else None
        if not isinstance(runtimes, list):
            raise SenpiClientError(
                f"state probe response missing data.runtimes[]: keys="
                f"{sorted(envelope_data.keys()) if isinstance(envelope_data, dict) else type(envelope_data).__name__}"
            )
        # Filter to the requested wallet (case-insensitive). The runtime
        # already filtered when we passed ?address=, but be defensive in case
        # of future shape changes — callers ask about a specific wallet.
        for rt in runtimes:
            if not isinstance(rt, dict):
                continue
            # RuntimeSystemState doesn't carry `address` at the top level;
            # the wallet identity comes from the route's filter. With
            # ?address=, a non-empty list means the wallet is registered.
            return rt
        return None

    def is_runtime_registered(
        self,
        wallet: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """Probe `/state` and return True if `wallet` has a running runtime.

        Wallet-level liveness — does **any** runtime exist for this wallet?
        For a stricter "this scanner is still valid" check, use
        `is_scanner_registered(wallet, scanner)` instead.
        """
        return self._fetch_state(wallet, timeout=timeout) is not None

    def is_scanner_registered(
        self,
        wallet: str,
        scanner: str,
        timeout: Optional[float] = None,
    ) -> bool:
        """Probe `/state` and return True if `scanner` is registered for `wallet`.

        Stricter than `is_runtime_registered`: catches the case where a runtime
        exists for the wallet but the producer's specific external_scanner
        (e.g. `pangolin_signals`) was renamed, dropped, or replaced when the
        runtime was reinstalled. Walks `components.scanners.scanners[]` and
        looks for a matching `scannerId` (the runtime-side identifier — same
        value as the producer's `client.push_signal(scanner=...)` argument).

        Enabled / disabled state of the scanner is **not** considered:
        registration is the binary the daemon cares about. A scanner that's
        registered-but-temporarily-disabled is still a valid target on the
        next ingest; the daemon should not commit suicide on transient state.
        """
        if not isinstance(scanner, str) or not scanner:
            raise SenpiClientError(f"scanner must be a non-empty string (got {scanner!r})")
        rt = self._fetch_state(wallet, timeout=timeout)
        if rt is None:
            return False
        components = rt.get("components") if isinstance(rt, dict) else None
        scanners_block = components.get("scanners") if isinstance(components, dict) else None
        # ScannerSystemState shape (per `src/health/types.ts` in the
        # `senpi-trading-runtime` repo — ScannerSystemState extends
        # ComponentSystemState<{totalRegistered, totalEnabled, scanners}>):
        #
        #   { component: "scanners", health, updatedAt, state: {
        #       totalRegistered, totalEnabled, scanners: [...]
        #     }
        #   }
        #
        # The `state` key (not `data`) carries the typed payload. Be strict —
        # if the shape ever changes, fail loudly rather than silently treating
        # every scanner as "not registered" (which would cause every daemon
        # using this check to self-terminate on the next probe).
        state_block = scanners_block.get("state") if isinstance(scanners_block, dict) else None
        scanners_list = state_block.get("scanners") if isinstance(state_block, dict) else None
        if not isinstance(scanners_list, list):
            raise SenpiClientError(
                f"state probe: cannot locate components.scanners.state.scanners[]; "
                f"scanners_block keys="
                f"{sorted(scanners_block.keys()) if isinstance(scanners_block, dict) else type(scanners_block).__name__}"
            )
        for s in scanners_list:
            sid = s.get("scannerId") if isinstance(s, dict) else None
            if isinstance(sid, str) and sid == scanner:
                return True
        return False

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
            with _post_json(self._pool, self._signals_url(), body, {}, timeout) as resp:
                raw = resp.read()
            duration_ms = int((time.time() - started) * 1000)
            # --- Body parse: distinguish empty / malformed / non-object ---
            # Three failure modes that previous code collapsed into one
            # "missing data.results" message — incident triage needs them
            # separate so operators can tell "proxy stripped the body" from
            # "TLS truncation" from "actual version skew".
            if not raw:
                raise SenpiClientError(
                    "signal_post: response body was empty (HTTP 200 with zero bytes); "
                    "check for a proxy or sidecar that may be stripping the body"
                )
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise SenpiClientError(
                    f"signal_post: response not valid JSON: {e}; "
                    f"first 200 bytes={raw[:200]!r}"
                ) from e
            if not isinstance(parsed, dict):
                raise SenpiClientError(
                    f"signal_post: response root is {type(parsed).__name__}, "
                    f"expected a JSON object; first 200 bytes={raw[:200]!r}"
                )

            # Senpi-stack response envelope (matches senpi-hyperliquid-mcp):
            #   2xx success: { "success": true, "data": { "results": [...] } }
            #   2xx mixed:   per-item entries inside `data.results[]` are either
            #                { "success": true,  "address", "scanner", "data":  { timestamp, signalCount, contextUpdated } }
            #                { "success": false, "address", "scanner", "error": { code, message } }
            #   4xx/5xx:     parsed in the HTTPError except-branch below
            #                (envelope error surfaced as SenpiClientError).
            #
            # Strict on shape: if the runtime ever returns 200 without a parseable
            # `data.results` array, fail loudly rather than silently treating it
            # as "all items accepted". This protects producers from sending into
            # a black hole if a version-skewed runtime returns the legacy shape.
            envelope_data = parsed.get("data")
            results = envelope_data.get("results") if isinstance(envelope_data, dict) else None
            if not isinstance(results, list):
                # Don't tell the operator a single hypothesis as fact.
                # Several causes are equally plausible.
                raise SenpiClientError(
                    f"signal_post: unexpected envelope shape — expected "
                    f"{{ success, data: {{ results }} }}; got top-level keys="
                    f"{sorted(parsed.keys())}. Possible causes: "
                    f"(a) version skew between this helper and the runtime, "
                    f"(b) a proxy/middleware mangling the response body, "
                    f"(c) runtime_host/port pointing at a different service."
                )

            # Individual items can have success=false (e.g. INVALID_REQUEST when
            # an undeclared data field is sent). Surface those rejections.
            failed = [r for r in results if isinstance(r, dict) and r.get("success") is False]
            if failed:
                first = failed[0]
                first_err = first.get("error") if isinstance(first.get("error"), dict) else {}
                first_code = first_err.get("code")
                # Truncation is intentional on both the log event AND the
                # exception — protects log-storage cost when a misbehaving
                # runtime returns multi-KB stack traces in the message field.
                first_message = str(first_err.get("message", ""))[:200]
                # Histogram of all failure codes in the batch — preserves
                # visibility into multi-mode failures that surface only the
                # FIRST item in the exception. For pangolin (one signal at a
                # time) this is just `{first_code: 1}`; for any future batch
                # producer it is the difference between "I have no idea why
                # the batch failed" and a shaped error report.
                code_histogram: Dict[str, int] = {}
                for r in failed:
                    err = r.get("error") if isinstance(r.get("error"), dict) else {}
                    code = err.get("code") or "UNKNOWN"
                    code_histogram[code] = code_histogram.get(code, 0) + 1
                log_event(
                    "signal_post",
                    batch_size=len(items),
                    bytes=len(raw),
                    duration_ms=duration_ms,
                    status="rejected",
                    failed_count=len(failed),
                    failed_by_code=code_histogram,
                    first_code=first_code,
                    first_message=first_message,
                )
                raise SenpiClientError(
                    f"signal_post: {len(failed)}/{len(items)} item(s) rejected; "
                    f"by_code={code_histogram}; "
                    f"first: code={first_code} message={first_message}"
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
            # 4xx/5xx envelope from the runtime — payload is the senpi-stack
            # error shape `{ success: false, error: { code, message } }`. Read
            # it best-effort and surface the envelope code/message to the
            # producer; without this they only see the HTTP status code (e.g.
            # `HTTPError 400`) and lose the human-readable cause (e.g.
            # "Exceeded api.maxItemsPerSignalsRequest=10").
            envelope_code: Optional[str] = None
            envelope_message: Optional[str] = None
            try:
                body_bytes = e.read()
                body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
                if body_str:
                    env = json.loads(body_str)
                    err_obj = env.get("error") if isinstance(env, dict) else None
                    if isinstance(err_obj, dict):
                        envelope_code = err_obj.get("code")
                        msg = err_obj.get("message")
                        if isinstance(msg, str):
                            envelope_message = msg[:200]
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                # Body absent / non-JSON / unreadable — fall through to
                # status-code-only reporting. Best-effort, never the
                # primary failure mode.
                pass
            log_event(
                "signal_post",
                batch_size=len(items),
                duration_ms=duration_ms,
                status="http_error",
                http_status=getattr(e, "code", None),
                envelope_code=envelope_code,
                envelope_message=envelope_message,
            )
            if envelope_code is not None:
                raise SenpiClientError(
                    f"signal_post: HTTP {e.code} {envelope_code}: {envelope_message}"
                ) from e
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

        Field semantics (per `src/runtime-api/routes/signals.schema.ts` and
        `external-scanner-receiver.ts` in the `senpi-trading-runtime` repo):

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
