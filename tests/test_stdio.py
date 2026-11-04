"""Tests for the stdio transport, with mocked stdin/stdout."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from conftest import TODAY
from submission_tracker_mcp.__main__ import resolve_store_path, serve
from submission_tracker_mcp.server import PARSE_ERROR


class FakeReader:
    def __init__(self, lines):
        self._lines = iter(lines)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._lines)


class FakeWriter:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass


def run_session(store, lines, today=None):
    reader = FakeReader(lines)
    writer = FakeWriter()
    serve(store, reader=reader, writer=writer)
    return writer.lines


def test_serve_round_trip(tmp_path):
    store_path = tmp_path / "tracker.json"
    lines = run_session(store_path, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        "",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "add_venue",
                        "arguments": {"name": "Shimmer",
                                       "response_window_days": 30}},
        }),
        json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "add_submission",
                        "arguments": {"title": "Story A", "venue_id": "venue-1",
                                       "date_sent": TODAY}},
        }),
    ])

    initialize = json.loads(lines[0])
    assert initialize["id"] == 1
    assert initialize["result"]["serverInfo"]["name"] == "submissions-tracker"

    tools = json.loads(lines[1])
    assert tools["id"] == 2
    assert any(t["name"] == "due_items"
               for t in tools["result"]["tools"])

    venue = json.loads(lines[2])
    assert venue["id"] == 3
    assert venue["result"]["structuredContent"]["venue"]["id"] == "venue-1"

    sub = json.loads(lines[3])
    assert sub["id"] == 4
    assert sub["result"]["structuredContent"]["submission"]["response_due"] \
        == "2026-07-15"

    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["submissions"][0]["title"] == "Story A"


def test_serve_skips_blank_lines_and_notifications(tmp_path):
    lines = run_session(tmp_path / "tracker.json", [
        json.dumps({"jsonrpc": "2.0",
                     "method": "notifications/initialized"}),
        "   ",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ])
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_serve_invalid_json_returns_parse_error(tmp_path):
    lines = run_session(tmp_path / "tracker.json", ["{oops"])
    response = json.loads(lines[0])
    assert response["error"]["code"] == PARSE_ERROR


def test_serve_survives_unknown_data(tmp_path):
    lines = run_session(tmp_path / "tracker.json", [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "no/such/method"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ])
    assert json.loads(lines[0])["error"]["code"] == -32601
    assert json.loads(lines[1])["id"] == 2


def test_resolve_store_path_default_uses_home(monkeypatch):
    monkeypatch.delenv("SUBMISSION_TRACKER_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    path = resolve_store_path()
    home = Path.home()
    assert path == home / ".local" / "share" / "submission-tracker" / \
        "tracker.json"


def test_resolve_store_path_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBMISSION_TRACKER_HOME", str(tmp_path))
    assert resolve_store_path() == tmp_path / "tracker.json"


def test_resolve_store_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("SUBMISSION_TRACKER_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert resolve_store_path() == \
        tmp_path / "submission-tracker" / "tracker.json"


def test_resolve_store_path_explicit(tmp_path):
    assert resolve_store_path(tmp_path / "x.json") == tmp_path / "x.json"


def test_real_subprocess_end_to_end(tmp_path):
    store_path = tmp_path / "tracker.json"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "add_venue",
                    "arguments": {"name": "Urchin Books",
                                   "kind": "publisher"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "add_submission",
                    "arguments": {"title": "Novel query",
                                   "venue_id": "venue-1",
                                   "date_sent": TODAY}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "stats", "arguments": {}}},
    ]
    stdin_text = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "submission_tracker_mcp",
         "--data", str(store_path)],
        input=stdin_text, capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
             "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    out_lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert [line["id"] for line in out_lines] == [1, 2, 3, 4]
    stats = out_lines[3]
    assert stats["result"]["structuredContent"]["total_submissions"] == 1
    assert stats["result"]["structuredContent"]["pending"] == 1
    assert json.loads(store_path.read_text(encoding="utf-8"))["submissions"] \
        and True
