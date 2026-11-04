"""Shared pytest fixtures: a fresh tmp-path-backed store and JSON-RPC client."""

import json

import pytest

from submission_tracker_mcp.server import Server
from submission_tracker_mcp.storage import Store

TODAY = "2026-06-15"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "tracker.json")
    s.load()
    return s


@pytest.fixture
def server(store):
    return Server(store, today=TODAY)


@pytest.fixture
def rpc(server):
    """Send a JSON-RPC request dict; return the parsed result or the error."""

    class Rpc:
        def request(self, method, params=None, request_id=1, jsonrpc="2.0"):
            body = {"jsonrpc": jsonrpc, "id": request_id, "method": method}
            body["params"] = params
            return server.handle(body)

        def call(self, tool, arguments=None, request_id=None):
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
            response = server.handle(body)
            assert "result" in response, response
            return response["result"]["structuredContent"]

        def call_error(self, tool, arguments=None):
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
            response = server.handle(body)
            assert "error" in response, response
            return response["error"]

        def raw(self, body):
            return json.loads(json.dumps(body))

    return Rpc()
