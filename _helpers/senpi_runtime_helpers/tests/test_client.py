"""client — HTTP plumbing on a local mock MCP server. No external network."""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_HELPERS_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _HELPERS_PARENT not in sys.path:
    sys.path.insert(0, _HELPERS_PARENT)

from senpi_runtime_helpers.client import SenpiClient, SenpiClientError

# Failure-injection sentinel for the mock /signals server.
# Tests pass addresses like `0xfail0001`, `0xfail0002` to trigger per-item
# failure responses. Kept distinctive enough that no realistic test address
# collides ("fail" is not hex; real Ethereum addresses don't contain it).
_FAIL_PREFIX = "0xfail"


class _Handler(BaseHTTPRequestHandler):
    """Tiny MCP-shaped JSON-RPC server: handles initialize + tools/call."""

    def log_message(self, *args, **kwargs):
        pass  # silence

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        msg = json.loads(body) if body else {}

        # /signals path: body is a JSON array (per runtime API schema).
        # Route by URL path BEFORE assuming msg is a JSON-RPC object.
        # Mirrors the live runtime's senpi-stack envelope:
        #   { "success": true, "data": { "results": [...] } }
        # Per-item entries match real runtime shape: routing fields at the
        # result level, success payload under `data:`, failure detail under
        # `error: { code, message }`.
        #
        # Failure-injection sentinel: items whose address (lowercased) starts
        # with `_FAIL_PREFIX` get a per-item failure response. Using a long
        # sentinel (`0xfail0000...`) reduces the chance a future test happens
        # to use a colliding address.
        #
        # Shape-skew injection: header `X-Mock-Shape: legacy` makes the mock
        # return the OLD pre-MCP-envelope shape (`{ results: [...] }` at top
        # level, no `data` wrap). Used to verify the helper's strict-shape
        # parser raises rather than silently swallowing per-item failures.
        # Header `X-Mock-Body: empty` returns an empty 200 body.
        # Header `X-Mock-Body: bad-json` returns malformed JSON.
        if self.path == "/signals":
            items = msg if isinstance(msg, list) else []
            shape_mode = self.headers.get("X-Mock-Shape", "").lower()
            body_mode = self.headers.get("X-Mock-Body", "").lower()

            if body_mode == "empty":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # No body.
                return
            if body_mode == "bad-json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success": true, "data": {"resu')
                return

            results = []
            for it in items:
                addr = (it.get("address") or "") if isinstance(it, dict) else ""
                scanner = (it.get("scanner") or "") if isinstance(it, dict) else ""
                if addr.lower().startswith(_FAIL_PREFIX):
                    results.append({
                        "success": False,
                        "address": addr.lower(),
                        "scanner": scanner,
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": f"mock-injected failure for {_FAIL_PREFIX}*",
                        },
                    })
                else:
                    results.append({
                        "success": True,
                        "address": addr.lower(),
                        "scanner": scanner,
                        "data": {
                            "timestamp": 1714492800000,
                            "signalCount": 1,
                            "contextUpdated": False,
                        },
                    })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if shape_mode == "legacy":
                # Pre-envelope shape: top-level results, no `data` wrap. The
                # strict parser must reject this as "unexpected envelope shape".
                self.wfile.write(json.dumps({"results": results}).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({
                    "success": True,
                    "data": {"results": results},
                }).encode("utf-8"))
            return

        method = msg.get("method") if isinstance(msg, dict) else None
        if method == "initialize":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "test-session-123")
            self.end_headers()
            self.wfile.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg.get("id", 1),
                "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
            }).encode("utf-8"))
            return
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if method == "tools/call":
            tool = msg.get("params", {}).get("name", "")
            args = msg.get("params", {}).get("arguments", {})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            inner = {"tool": tool, "echoed_args": args, "ok": True}
            envelope = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {"content": [{"type": "text", "text": json.dumps(inner)}]},
            }
            self.wfile.write(json.dumps(envelope).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        # /state probe endpoint. Shape mirrors the live runtime:
        #   200 OK + senpi-stack envelope with `data.runtimes[]` of
        #   RuntimeSystemState. The mock supports a `?address=` filter and
        #   wallet-conditional canned responses keyed off the query value.
        if self.path.startswith("/state"):
            # Naive query parsing — the test only exercises ?address=…
            address = ""
            if "?" in self.path:
                _, _, qs = self.path.partition("?")
                for pair in qs.split("&"):
                    if pair.startswith("address="):
                        address = pair[len("address="):].lower()
            # Address conventions for the mock:
            #   0xrt1*  → registered runtime, scanners=[my_signals, alt_scanner]
            #   0xrt2*  → registered runtime, scanners=[]   (no external scanners)
            #   anything else → 0 runtimes (wallet not registered)
            runtimes = []
            # Mock body mirrors the real runtime shape. The scanners block
            # nests under `.state.scanners[]` (per ComponentSystemState<T>
            # in senpi-trading-runtime/src/health/types.ts). Using the wrong
            # path here would make the wrapper unit tests pass against a
            # fake the parser tolerates, but fail in production — exactly
            # the bug the bugbot pass caught on 2026-05-07.
            if address.startswith("0xrt1"):
                runtimes = [{
                    "runtimeName": "rt1",
                    "components": {
                        "scanners": {
                            "component": "scanners",
                            "state": {
                                "totalRegistered": 2,
                                "totalEnabled": 2,
                                "scanners": [
                                    {"scannerId": "my_signals", "enabled": True, "initialized": True},
                                    {"scannerId": "alt_scanner", "enabled": True, "initialized": True},
                                ],
                            },
                        },
                    },
                }]
            elif address.startswith("0xrt2"):
                runtimes = [{
                    "runtimeName": "rt2",
                    "components": {
                        "scanners": {
                            "component": "scanners",
                            "state": {
                                "totalRegistered": 0,
                                "totalEnabled": 0,
                                "scanners": [],
                            },
                        },
                    },
                }]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "data": {"runtimes": runtimes},
            }).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_mcp_call_unwraps_inner_json(self) -> None:
        client = SenpiClient(
            mcp_url=f"http://127.0.0.1:{self.port}",
            auth_token="test-token",
        )
        result = client.mcp_call("leaderboard_get_markets", limit=100)
        self.assertEqual(result["tool"], "leaderboard_get_markets")
        self.assertEqual(result["echoed_args"], {"limit": 100})
        self.assertTrue(result["ok"])

    def test_initialize_runs_once(self) -> None:
        client = SenpiClient(
            mcp_url=f"http://127.0.0.1:{self.port}",
            auth_token="test-token",
        )
        client.mcp_call("a")
        first_session = client._session.session_id
        client.mcp_call("b")
        self.assertEqual(client._session.session_id, first_session)
        self.assertTrue(client._session.initialized)

    def test_push_signal_single(self) -> None:
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        result = client.push_signal(
            address="0xabc",
            scanner="pangolin_signals",
            data={"score": 12, "asset": "TST"},
        )
        # Senpi-stack envelope: { success: true, data: { results: [...] } }
        self.assertTrue(result.get("success"))
        self.assertIsInstance(result.get("data"), dict)
        self.assertEqual(len(result["data"]["results"]), 1)
        entry = result["data"]["results"][0]
        self.assertTrue(entry["success"])
        # Routing fields at result level, payload wrapped in `data`.
        self.assertEqual(entry["address"], "0xabc")
        self.assertEqual(entry["scanner"], "pangolin_signals")
        self.assertIsInstance(entry["data"], dict)
        self.assertEqual(entry["data"]["signalCount"], 1)

    def test_push_signals_batch(self) -> None:
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        result = client.push_signals([
            {"address": "0xabc", "scanner": "s1", "data": {"k": 1}},
            {"address": "0xabc", "scanner": "s1", "data": {"k": 2}},
        ])
        self.assertEqual(len(result["data"]["results"]), 2)
        self.assertTrue(all(r["success"] for r in result["data"]["results"]))

    def test_push_signals_raises_on_per_item_failure(self) -> None:
        # Mock injects a failure for any address starting with "0xfail".
        # The helper must surface that as SenpiClientError carrying the
        # error.{code,message} from the new wire shape — silently swallowing
        # per-item failures would let producers send into a black hole.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        with self.assertRaises(SenpiClientError) as cm:
            client.push_signals([
                {"address": "0xfail0001", "scanner": "s1", "data": {"k": 1}},
            ])
        # Error message must include the per-item code from the wrapped shape.
        self.assertIn("INVALID_REQUEST", str(cm.exception))
        self.assertIn("mock-injected failure", str(cm.exception))

    def test_push_signals_partial_failure_raises(self) -> None:
        # One success + one failure in the same batch. The helper raises so
        # the producer cannot accidentally treat the response as "all
        # accepted". Important nuance — the runtime is NOT atomic: the
        # successful item WAS ingested even though the helper raises. A
        # producer that needs per-item outcome should either call
        # `push_signal` one item at a time (pangolin's pattern) or catch
        # SenpiClientError and inspect the message.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        with self.assertRaises(SenpiClientError) as cm:
            client.push_signals([
                {"address": "0xabc", "scanner": "s1", "data": {"k": 1}},
                {"address": "0xfail0002", "scanner": "s1", "data": {"k": 2}},
            ])
        self.assertIn("1/2", str(cm.exception))

    def test_push_signals_failure_message_includes_code_histogram(self) -> None:
        # Multi-mode failures must surface a code histogram in the exception
        # message so a producer can see distinct failure causes at a glance,
        # not just the first one. Without this, a 3-item batch with two
        # different failure codes loses one code entirely.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        with self.assertRaises(SenpiClientError) as cm:
            client.push_signals([
                {"address": "0xfail0001", "scanner": "s1", "data": {"k": 1}},
                {"address": "0xfail0002", "scanner": "s1", "data": {"k": 2}},
                {"address": "0xfail0003", "scanner": "s1", "data": {"k": 3}},
            ])
        self.assertIn("by_code=", str(cm.exception))
        # Mock injects INVALID_REQUEST for all 0xfail* addresses → all 3 land
        # in the same bucket.
        self.assertIn("'INVALID_REQUEST': 3", str(cm.exception))

    def test_push_signals_raises_on_legacy_envelope_shape(self) -> None:
        # Critical version-skew protection: if a producer with this helper
        # talks to an OLD runtime that still returns the pre-MCP envelope
        # `{ results: [...] }`, the strict parser must raise rather than
        # silently swallow per-item failures.
        #
        # Without this guard, a version-skewed deployment would log "all
        # 50 signals accepted" while the runtime actually rejected most.
        # That's the kind of silent failure that costs trading capital.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        # Inject a per-item failure into the legacy-shape response — proves
        # the parser would have noticed the failure if it had read the body.
        # The strict guard fires on shape, so the failure-detection path
        # never runs; the test asserts that's the right behavior.
        # (The mock honors `X-Mock-Shape: legacy` to return the old shape.)
        # Direct urllib path so we can attach the header without changing
        # SenpiClient's signature.
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/signals"
        body = json.dumps([
            {"address": "0xfail0001", "scanner": "s1", "data": {"k": 1}},
        ]).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Mock-Shape": "legacy",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — local mock
            self.assertEqual(resp.status, 200)
            self.assertIn(b'"results"', resp.read())
        # Now the helper itself — same payload with the shape header. The
        # helper has no API for arbitrary headers (by design), so to exercise
        # the strict-shape branch we monkey-patch _post_json's headers arg.
        from senpi_runtime_helpers import client as client_mod
        original = client_mod._post_json

        def with_legacy_header(pool, url, body, headers, timeout):
            headers = dict(headers)
            headers["X-Mock-Shape"] = "legacy"
            return original(pool, url, body, headers, timeout)

        client_mod._post_json = with_legacy_header
        try:
            with self.assertRaises(SenpiClientError) as cm:
                client.push_signals([
                    {"address": "0xfail0001", "scanner": "s1", "data": {"k": 1}},
                ])
            self.assertIn("unexpected envelope shape", str(cm.exception))
            # Operator-friendly: list the actual top-level keys so they
            # can diagnose vs. blindly trusting "version mismatch".
            self.assertIn("results", str(cm.exception))
        finally:
            client_mod._post_json = original

    def test_push_signals_raises_on_empty_body(self) -> None:
        # Distinct error message for empty body (proxy stripped) vs.
        # missing-shape — operators triaging at 3am need different messages
        # for different causes.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        from senpi_runtime_helpers import client as client_mod
        original = client_mod._post_json

        def with_empty_header(pool, url, body, headers, timeout):
            headers = dict(headers)
            headers["X-Mock-Body"] = "empty"
            return original(pool, url, body, headers, timeout)

        client_mod._post_json = with_empty_header
        try:
            with self.assertRaises(SenpiClientError) as cm:
                client.push_signals([
                    {"address": "0xabc", "scanner": "s1", "data": {"k": 1}},
                ])
            self.assertIn("response body was empty", str(cm.exception))
        finally:
            client_mod._post_json = original

    def test_push_signals_raises_on_malformed_json(self) -> None:
        # Distinct error for malformed JSON (TLS truncation, corrupt proxy)
        # vs. wrong shape vs. empty body.
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        from senpi_runtime_helpers import client as client_mod
        original = client_mod._post_json

        def with_bad_json_header(pool, url, body, headers, timeout):
            headers = dict(headers)
            headers["X-Mock-Body"] = "bad-json"
            return original(pool, url, body, headers, timeout)

        client_mod._post_json = with_bad_json_header
        try:
            with self.assertRaises(SenpiClientError) as cm:
                client.push_signals([
                    {"address": "0xabc", "scanner": "s1", "data": {"k": 1}},
                ])
            self.assertIn("not valid JSON", str(cm.exception))
        finally:
            client_mod._post_json = original

    def test_push_signals_rejects_empty(self) -> None:
        client = SenpiClient()
        with self.assertRaises(SenpiClientError):
            client.push_signals([])

    def test_push_signals_validates_required_fields(self) -> None:
        client = SenpiClient()
        with self.assertRaises(SenpiClientError):
            client.push_signals([{"data": {"k": 1}}])  # missing address + scanner

    # ──────────────── /state probes ────────────────

    def _client_for_state(self) -> SenpiClient:
        # The state probe uses runtime_host/runtime_port (not mcp_url). The
        # mock server runs on 127.0.0.1:<port>; point both at it.
        return SenpiClient(
            mcp_url=f"http://127.0.0.1:{self.port}",
            auth_token="test-token",
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )

    def test_is_runtime_registered_true_when_wallet_present(self) -> None:
        c = self._client_for_state()
        self.assertTrue(c.is_runtime_registered("0xrt1ABCDEF"))

    def test_is_runtime_registered_false_when_wallet_absent(self) -> None:
        c = self._client_for_state()
        self.assertFalse(c.is_runtime_registered("0xnotregistered"))

    def test_is_scanner_registered_true_when_both_match(self) -> None:
        c = self._client_for_state()
        self.assertTrue(c.is_scanner_registered("0xrt1ABCDEF", "my_signals"))
        self.assertTrue(c.is_scanner_registered("0xrt1ABCDEF", "alt_scanner"))

    def test_is_scanner_registered_false_when_scanner_missing(self) -> None:
        c = self._client_for_state()
        self.assertFalse(c.is_scanner_registered("0xrt1ABCDEF", "nonexistent_scanner"))

    def test_is_scanner_registered_false_when_runtime_has_no_scanners(self) -> None:
        c = self._client_for_state()
        self.assertFalse(c.is_scanner_registered("0xrt2ABCDEF", "any_scanner"))

    def test_is_scanner_registered_false_when_wallet_absent(self) -> None:
        c = self._client_for_state()
        self.assertFalse(c.is_scanner_registered("0xnotregistered", "my_signals"))

    def test_state_probe_rejects_non_hex_wallet(self) -> None:
        c = self._client_for_state()
        with self.assertRaises(SenpiClientError):
            c.is_runtime_registered("not-a-wallet")

    def test_state_probe_rejects_empty_scanner(self) -> None:
        c = self._client_for_state()
        with self.assertRaises(SenpiClientError):
            c.is_scanner_registered("0xrt1ABCDEF", "")


if __name__ == "__main__":
    unittest.main()
