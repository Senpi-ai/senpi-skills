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
        if self.path == "/signals":
            count = len(msg) if isinstance(msg, list) else 0
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"accepted": count}).encode("utf-8"))
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
        self.assertEqual(result.get("accepted"), 1)

    def test_push_signals_batch(self) -> None:
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        result = client.push_signals([
            {"address": "0xabc", "scanner": "s1", "data": {"k": 1}},
            {"address": "0xabc", "scanner": "s1", "data": {"k": 2}},
        ])
        self.assertEqual(result.get("accepted"), 2)

    def test_push_signals_rejects_empty(self) -> None:
        client = SenpiClient()
        with self.assertRaises(SenpiClientError):
            client.push_signals([])

    def test_push_signals_validates_required_fields(self) -> None:
        client = SenpiClient()
        with self.assertRaises(SenpiClientError):
            client.push_signals([{"data": {"k": 1}}])  # missing address + scanner


if __name__ == "__main__":
    unittest.main()
