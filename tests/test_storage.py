"""Tests for the JSON-file storage layer."""

import json

import pytest

from conftest import TODAY
from submission_tracker_mcp.storage import (
    STATUS_ACCEPTED,
    STATUS_FORM_REJECTION,
    STATUS_REQUESTED_MORE,
    STATUS_SENT,
    Store,
    StoreError,
)
from datetime import date


def make_venue(store, **kw):
    return store.add_venue("Shimmer Magazine", kind="magazine",
                           response_window_days=90, **kw)


def test_load_missing_file_returns_empty_data(store):
    assert store.data["venues"] == []
    assert store.data["submissions"] == []
    assert store.data["version"] == 1


def test_save_then_load_round_trip(store):
    venue = make_venue(store)
    sub = store.add_submission("The Lighthouse Letters", venue["id"],
                               date_sent=TODAY)
    store.save()

    fresh = Store(store.path)
    fresh.load()
    assert [v["name"] for v in fresh.data["venues"]] == ["Shimmer Magazine"]
    assert fresh.data["submissions"][0]["title"] == "The Lighthouse Letters"
    assert fresh.data["submissions"][0]["date_sent"] == TODAY


def test_save_is_atomic_no_temp_files_left(store):
    make_venue(store)
    store.save()
    leftovers = [p.name for p in store.path.parent.iterdir()
                 if p.name != store.path.name]
    assert leftovers == []


def test_load_rejects_corrupt_file(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text("{not json", encoding="utf-8")
    store = Store(path)
    with pytest.raises(StoreError, match="not valid JSON"):
        store.load()


def test_load_rejects_non_object(tmp_path):
    path = tmp_path / "tracker.json"
    path.write_text("[]", encoding="utf-8")
    store = Store(path)
    with pytest.raises(StoreError, match="JSON object"):
        store.load()


def test_add_venue_rejects_empty_name(store):
    with pytest.raises(StoreError, match="empty"):
        store.add_venue("   ")


def test_add_venue_rejects_bad_kind(store):
    with pytest.raises(StoreError, match="kind"):
        store.add_venue("X", kind="zine")


def test_add_venue_rejects_bad_window(store):
    with pytest.raises(StoreError, match="response_window_days"):
        store.add_venue("X", response_window_days=-5)


def test_add_venue_makes_unique_ids(store):
    first = make_venue(store)
    second = store.add_venue("Other Magazine")
    assert first["id"] != second["id"]


def test_update_venue(store):
    venue = make_venue(store)
    updated = store.update_venue(venue["id"], response_window_days=30,
                                 notes="fast turnaround")
    assert updated["response_window_days"] == 30
    assert updated["notes"] == "fast turnaround"


def test_update_venue_unknown_field(store):
    venue = make_venue(store)
    with pytest.raises(StoreError, match="cannot update"):
        store.update_venue(venue["id"], bogus=1)


def test_delete_venue_blocked_while_linked(store):
    venue = make_venue(store)
    store.add_submission("Story", venue["id"], date_sent=TODAY)
    with pytest.raises(StoreError, match="still has"):
        store.delete_venue(venue["id"])
    store.data["submissions"].clear()
    store.delete_venue(venue["id"])
    assert store.list_venues() == []


def test_get_venue_missing(store):
    with pytest.raises(StoreError, match="no venue"):
        store.get_venue("venue-999")


def test_add_submission_computes_response_dates(store):
    venue = make_venue(store)
    sub = store.add_submission("The Lighthouse Letters", venue["id"],
                               date_sent=TODAY)
    assert sub["response_due"] == "2026-09-13"
    assert sub["follow_up_on"] == sub["response_due"]
    assert sub["status"] == STATUS_SENT
    assert sub["events"] == [{"date": TODAY, "event": "sent", "note": ""}]


def test_add_submission_today_default(store):
    venue = make_venue(store)
    sub = store.add_submission("Story", venue["id"], today=date(2026, 6, 15))
    assert sub["date_sent"] == "2026-06-15"


def test_add_submission_requires_venue(store):
    with pytest.raises(StoreError, match="no venue"):
        store.add_submission("Story", "venue-999", date_sent=TODAY)


def test_add_submission_rejects_bad_status(store):
    venue = make_venue(store)
    with pytest.raises(StoreError, match="status"):
        store.add_submission("Story", venue["id"], date_sent=TODAY,
                             status="lost")


def test_log_response_form_rejection(store):
    venue = make_venue(store)
    sub = store.add_submission("Story", venue["id"], date_sent=TODAY)
    out = store.log_response(sub["id"], STATUS_FORM_REJECTION,
                             date_logged="2026-07-01", note="slush pile")
    assert out["status"] == STATUS_FORM_REJECTION
    assert "response_due" not in out
    assert "follow_up_on" not in out
    event = out["events"][-1]
    assert event["event"] == STATUS_FORM_REJECTION
    assert event["date"] == "2026-07-01"
    assert event["note"] == "slush pile"


def test_log_response_requested_more(store):
    venue = store.add_venue("Agent X", kind="agent", response_window_days=60)
    sub = store.add_submission("Query", venue["id"], date_sent=TODAY)
    out = store.log_response(sub["id"], STATUS_REQUESTED_MORE,
                             date_logged="2026-07-10")
    assert out["status"] == STATUS_REQUESTED_MORE
    assert len(out["events"]) == 2


def test_log_response_rejects_bad_status(store):
    venue = make_venue(store)
    sub = store.add_submission("Story", venue["id"], date_sent=TODAY)
    with pytest.raises(StoreError, match="status"):
        store.log_response(sub["id"], "kick_me")


def test_schedule_resubmission_and_due_items(store):
    venue = make_venue(store)
    sub = store.add_submission("Story", venue["id"], date_sent=TODAY)
    store.log_response(sub["id"], STATUS_FORM_REJECTION,
                       date_logged="2026-07-01")
    store.schedule_resubmission(sub["id"], "2026-08-01", note="next tier")

    before = store.due_items(today=date(2026, 7, 31))
    assert before["resubmission_reminders"] == []

    after = store.due_items(today=date(2026, 8, 1))
    assert len(after["resubmission_reminders"]) == 1
    reminder = after["resubmission_reminders"][0]
    assert reminder["id"] == sub["id"]
    assert reminder["note"] == "next tier"


def test_due_items_flags_pending_follow_ups(store):
    venue = make_venue(store)
    on_time = store.add_submission("On Time", venue["id"], date_sent=TODAY)
    overdue = store.add_submission("Overdue", venue["id"],
                                   date_sent="2026-01-01")

    due = store.due_items(today=date(2026, 6, 15))
    flagged = {item["id"] for item in due["responses_due"]}
    assert on_time["id"] not in flagged
    assert overdue["id"] in flagged
    for item in due["responses_due"]:
        if item["id"] == overdue["id"]:
            assert item["days_over"] > 0


def test_set_follow_up_manual_override(store):
    venue = make_venue(store)
    sub = store.add_submission("Story", venue["id"], date_sent=TODAY)
    store.set_follow_up(sub["id"], "2026-07-20", note="editor asked")
    assert sub["follow_up_on"] == "2026-07-20"
    assert sub["follow_up_note"] == "editor asked"


def test_stats_rollups(store):
    venue_mag = store.add_venue("Mag A", kind="magazine",
                               response_window_days=30)
    venue_agent = store.add_venue("Agent B", kind="agent")

    accepted = store.add_submission("Winner", venue_mag["id"],
                                    date_sent="2026-01-01")
    store.log_response(accepted["id"], STATUS_ACCEPTED,
                       date_logged="2026-02-01")

    form_rej = store.add_submission("Form rejects", venue_mag["id"],
                                    date_sent="2026-03-01")
    store.log_response(form_rej["id"], STATUS_FORM_REJECTION,
                       date_logged="2026-03-15")

    request = store.add_submission("Full request", venue_agent["id"],
                                   date_sent="2026-04-01")
    store.log_response(request["id"], STATUS_REQUESTED_MORE,
                       date_logged="2026-04-20")

    pending = store.add_submission("Out now", venue_agent["id"],
                                   date_sent="2026-06-01")

    stats = store.stats(today=date(2026, 6, 15))
    assert stats["total_submissions"] == 4
    assert stats["counts"][STATUS_ACCEPTED] == 1
    assert stats["counts"][STATUS_FORM_REJECTION] == 1
    assert stats["counts"][STATUS_REQUESTED_MORE] == 1
    assert stats["counts"][STATUS_SENT] == 1
    assert stats["decided"] == 3
    assert stats["pending"] == 1
    assert stats["acceptance_ratio"] == round(1 / 3, 4)
    assert stats["request_more_ratio"] == round(1 / 3, 4)
    assert stats["rejection_ratio"] == round(1 / 3, 4)

    per_mag = stats["by_venue"][venue_mag["id"]]
    assert per_mag["venue"] == "Mag A"
    assert per_mag["total"] == 2
    assert per_mag["acceptance_ratio"] == 0.5
    per_agent = stats["by_venue"][venue_agent["id"]]
    assert per_agent["total"] == 2
    assert per_agent["acceptance_ratio"] == 0.0


def test_stats_empty_store(store):
    stats = store.stats()
    assert stats["total_submissions"] == 0
    assert stats["acceptance_ratio"] is None


def test_list_submissions_filters(store):
    venue = make_venue(store)
    a = store.add_submission("A", venue["id"], date_sent=TODAY)
    b = store.add_submission("B", venue["id"], date_sent=TODAY)
    store.log_response(b["id"], STATUS_FORM_REJECTION, "2026-07-01")
    pendings = store.list_submissions(status="sent")
    assert [s["id"] for s in pendings] == [a["id"]]
    rejections = store.list_submissions(status=STATUS_FORM_REJECTION)
    assert [s["id"] for s in rejections] == [b["id"]]


def test_next_id_skips_human_ids(store):
    venue = make_venue(store)
    venue["id"] = "venue-3"
    assert store.next_id("venue") == "venue-4"


def test_saved_file_is_pretty_json(store):
    make_venue(store)
    store.save()
    text = store.path.read_text(encoding="utf-8")
    assert json.loads(text)["venues"][0]["name"] == "Shimmer Magazine"
