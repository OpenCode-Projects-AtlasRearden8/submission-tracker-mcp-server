"""Tests for the MCP-style JSON-RPC server loop.

No real files or pipes are used: the sessions run against a Store in a
tmp_path and requests are handed straight to Server.handle (the "mocked
calls" style). The transport itself is exercised in test_stdio.py.
"""

import json

import pytest

from conftest import TODAY
from submission_tracker_mcp import server as server_module
from submission_tracker_mcp.server import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    TOOL_ERROR,
)


def test_initialize(server):
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    result = response["result"]
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "submissions-tracker"
    assert response["id"] == 1


def test_initialize_requires_id(server):
    response = server.handle({
        "jsonrpc": "2.0", "method": "initialize", "params": {},
    })
    assert response["error"]["code"] == INVALID_REQUEST


def test_notifications_return_no_response(server):
    response = server.handle({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })
    assert response is None


def test_ping(server):
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert response == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_unknown_method(server):
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})
    assert response["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.parametrize("body", [
    "not a dict",
    42,
    None,
    [],
])
def test_non_object_requests(server, body):
    response = server.handle(body)
    assert response["error"]["code"] == INVALID_REQUEST


def test_bad_jsonrpc_version(server):
    response = server.handle({
        "jsonrpc": "1.0", "id": 1, "method": "tools/list",
    })
    assert response["error"]["code"] == INVALID_REQUEST
    assert "2.0" in response["error"]["message"]


def test_missing_method(server):
    response = server.handle({"jsonrpc": "2.0", "id": 1})
    assert response["error"]["code"] == INVALID_REQUEST


def test_tools_list(server):
    response = server.handle({"jsonrpc": "2.0", "id": 1,
                              "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {
        "add_venue", "list_venues", "update_venue", "delete_venue",
        "add_submission", "list_submissions", "get_submission",
        "log_response", "schedule_resubmission", "set_follow_up",
        "withdraw", "delete_submission", "due_items", "stats",
    } <= names
    for tool in response["result"]["tools"]:
        assert tool["input_schema"]["type"] == "object"
        assert tool["description"]


def test_unknown_tool(server):
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "nonsense", "arguments": {}},
    })
    assert response["error"]["code"] == METHOD_NOT_FOUND
    assert "nonsense" in response["error"]["message"]


def test_tool_call_result_shape(server, rpc):
    rpc.call("add_venue", {"name": "Shimmer", "response_window_days": 90})
    response = server.handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "list_venues", "arguments": {}},
    })
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["count"] == 1
    assert parsed == result["structuredContent"]


def test_add_venue_and_list(server, rpc):
    created = rpc.call("add_venue", {
        "name": "Shimmer", "kind": "magazine", "response_window_days": 90,
        "guidelines": "shimmer.example/guidelines",
    })
    assert created["venue"]["name"] == "Shimmer"
    listed = rpc.call("list_venues", {"kind": "magazine"})
    assert listed["count"] == 1


def test_add_venue_requires_name(server, rpc):
    error = rpc.call_error("add_venue", {})
    assert error["code"] == INVALID_PARAMS
    assert "name" in error["message"]


def test_add_venue_rejects_bad_window(server, rpc):
    error = rpc.call_error("add_venue", {"name": "X",
                                          "response_window_days": "soon"})
    assert error["code"] == INVALID_PARAMS


def test_update_and_delete_venue(server, rpc):
    venue = rpc.call("add_venue", {"name": "Old Name"})["venue"]
    updated = rpc.call("update_venue", {
        "venue_id": venue["id"], "name": "New Name", "notes": "updated",
    })["venue"]
    assert updated["name"] == "New Name"
    deleted = rpc.call("delete_venue", {"venue_id": venue["id"]})
    assert deleted["deleted"] == venue["id"]
    assert rpc.call("list_venues", {})["count"] == 0


def test_update_venue_unknown_field(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    error = rpc.call_error("update_venue", {"venue_id": venue["id"],
                                             "bogus": True})
    assert error["code"] == INVALID_PARAMS


def test_add_submission_sets_dates(server, rpc):
    venue = rpc.call("add_venue", {"name": "V",
                                    "response_window_days": 30})["venue"]
    sub = rpc.call("add_submission", {
        "title": "My Story", "venue_id": venue["id"], "date_sent": TODAY,
    })["submission"]
    assert sub["date_sent"] == TODAY
    assert sub["response_due"] == "2026-07-15"
    assert sub["follow_up_on"] == "2026-07-15"


def test_add_submission_defaults_to_today(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S",
                                       "venue_id": venue["id"]})["submission"]
    assert sub["date_sent"] == TODAY


def test_add_submission_missing_venue(server, rpc):
    error = rpc.call_error("add_submission", {"title": "S",
                                               "venue_id": "venue-99"})
    assert error["code"] == TOOL_ERROR
    assert "no venue" in error["message"]


def test_add_submission_missing_title(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    error = rpc.call_error("add_submission", {"venue_id": venue["id"]})
    assert error["code"] == INVALID_PARAMS


def test_log_response_flow_and_due(server, rpc):
    venue = rpc.call("add_venue", {"name": "V",
                                    "response_window_days": 60})["venue"]
    sub = rpc.call("add_submission", {"title": "S", "venue_id": venue["id"],
                                       "date_sent": "2026-01-01"})["submission"]

    due = rpc.call("due_items")
    assert any(item["id"] == sub["id"] for item in due["responses_due"])

    updated = rpc.call("log_response", {
        "submission_id": sub["id"], "status": "form_rejection",
        "date_logged": "2026-03-01", "note": "boilerplate",
    })["submission"]
    assert updated["status"] == "form_rejection"
    assert "response_due" not in updated

    due = rpc.call("due_items", {})
    assert due["responses_due"] == []
    assert due["resubmission_reminders"] == []


def test_log_response_requires_status(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S",
                                       "venue_id": venue["id"]})["submission"]
    error = rpc.call_error("log_response",
                           {"submission_id": sub["id"]})
    assert error["code"] == INVALID_PARAMS


def test_resubmission_reminder_flow(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S", "venue_id": venue["id"],
                                       "date_sent": TODAY})["submission"]
    rpc.call("log_response", {"submission_id": sub["id"],
                               "status": "form_rejection"})
    rpc.call("schedule_resubmission", {
        "submission_id": sub["id"], "after_date": "2026-06-15",
        "note": "send to Mag B",
    })
    due = rpc.call("due_items", {})
    reminders = due["resubmission_reminders"]
    assert len(reminders) == 1
    assert reminders[0]["note"] == "send to Mag B"


def test_resubmission_requires_date(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S",
                                       "venue_id": venue["id"]})["submission"]
    error = rpc.call_error("schedule_resubmission",
                           {"submission_id": sub["id"]})
    assert error["code"] == INVALID_PARAMS


def test_set_follow_up_and_withdraw(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S",
                                       "venue_id": venue["id"]})["submission"]
    updated = rpc.call("set_follow_up", {
        "submission_id": sub["id"], "on_date": "2026-09-01",
    })["submission"]
    assert updated["follow_up_on"] == "2026-09-01"
    withdrawn = rpc.call("withdraw", {
        "submission_id": sub["id"], "note": "sold elsewhere",
    })["submission"]
    assert withdrawn["status"] == "withdrawn"


def test_delete_submission(server, rpc):
    venue = rpc.call("add_venue", {"name": "V"})["venue"]
    sub = rpc.call("add_submission", {"title": "S",
                                       "venue_id": venue["id"]})["submission"]
    out = rpc.call("delete_submission", {"submission_id": sub["id"]})
    assert out["deleted"] == sub["id"]
    error = rpc.call_error("get_submission", {"submission_id": sub["id"]})
    assert error["code"] == TOOL_ERROR


def test_stats_rollup(server, rpc):
    venue = rpc.call("add_venue", {"name": "V", "kind": "agent"})["venue"]
    accepted = rpc.call("add_submission", {
        "title": "Winner", "venue_id": venue["id"],
        "date_sent": "2026-01-01",
    })["submission"]
    rpc.call("log_response", {"submission_id": accepted["id"],
                               "status": "accepted",
                               "date_logged": "2026-02-01"})
    rejected = rpc.call("add_submission", {
        "title": "Nope", "venue_id": venue["id"], "date_sent": "2026-03-01",
    })["submission"]
    rpc.call("log_response", {"submission_id": rejected["id"],
                               "status": "form_rejection",
                               "date_logged": "2026-03-10"})
    rpc.call("add_submission", {"title": "Out now", "venue_id": venue["id"],
                                 "date_sent": TODAY})

    stats = rpc.call("stats", {})
    assert stats["total_submissions"] == 3
    assert stats["acceptance_ratio"] == 0.5
    assert stats["counts"]["accepted"] == 1
    per_venue = stats["by_venue"][venue["id"]]
    assert per_venue["venue"] == "V"
    assert per_venue["acceptance_ratio"] == 0.5
    assert stats["pending"] == 1


def test_bad_arguments_shapes(server, rpc):
    error = rpc.call_error("add_submission", {"title": "S"})
    assert error["code"] == INVALID_PARAMS
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "add_venue", "arguments": "not a dict"},
    })
    assert response["error"]["code"] == INVALID_PARAMS


def test_tool_errors_surface_as_is_error(server, rpc):
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "add_venue", "arguments": {"name": "V"}},
    })
    assert "result" in response

    original = server_module.TOOL_HANDLERS["list_venues"]

    def broken(app, args):
        raise ValueError("boom")

    server_module.TOOL_HANDLERS["list_venues"] = broken
    try:
        response = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_venues", "arguments": {}},
        })
    finally:
        server_module.TOOL_HANDLERS["list_venues"] = original
    assert response["error"]["code"] == TOOL_ERROR
    assert response["id"] == 2


def test_error_response_ids_preserved(server):
    response = server.handle({
        "jsonrpc": "2.0", "id": "abc", "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    })
    assert response["id"] == "abc"
