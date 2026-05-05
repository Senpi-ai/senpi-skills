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
        method = msg.get("method")
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
        # /signals path
        if self.path == "/signals":
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"accepted": len(msg.get("signals", []))}).encode("utf-8"))
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

    def test_signals_post_batch(self) -> None:
        client = SenpiClient(
            runtime_host="127.0.0.1",
            runtime_port=self.port,
        )
        # Override the path so server's /signals branch fires.
        # The real client posts to /signals; our test server checks self.path.
        result = client.signals([{"k": 1}, {"k": 2}])
        self.assertEqual(result.get("accepted"), 2)

    def test_signals_rejects_empty(self) -> None:
        client = SenpiClient()
        with self.assertRaises(SenpiClientError):
            client.signals([])


if __name__ == "__main__":
    unittest.main()
