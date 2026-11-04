"""JSON-file storage for the submission tracker.

One folder you own, one JSON file, no network, stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

STATUS_SENT = "sent"
STATUS_FORM_REJECTION = "form_rejection"
STATUS_PERSONAL_REJECTION = "personal_rejection"
STATUS_REQUESTED_MORE = "requested_more"
STATUS_ACCEPTED = "accepted"
STATUS_WITHDRAWN = "withdrawn"

VALID_STATUSES = {
    STATUS_SENT,
    STATUS_FORM_REJECTION,
    STATUS_PERSONAL_REJECTION,
    STATUS_REQUESTED_MORE,
    STATUS_ACCEPTED,
    STATUS_WITHDRAWN,
}

VENUE_KINDS = {"magazine", "agent", "anthology", "publisher", "other"}


def default_data():
    return {"version": SCHEMA_VERSION, "venues": [], "submissions": []}


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def add_days(day, days):
    return day + timedelta(days=days)


class StoreError(ValueError):
    """Raised for validation and lookup problems."""


class Store:
    """Loads, validates, and atomically saves the tracker JSON file."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = default_data()

    # -- file handling -----------------------------------------------------

    def load(self):
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise StoreError(
                    f"{self.path} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(loaded, dict):
                raise StoreError(f"{self.path} must contain a JSON object")
            data = default_data()
            data["version"] = loaded.get("version", SCHEMA_VERSION)
            data["venues"] = loaded.get("venues", [])
            data["submissions"] = loaded.get("submissions", [])
            for record in data["venues"]:
                self._validate_venue(record, partial=False)
            for record in data["submissions"]:
                self._validate_submission(record, partial=False)
            self.data = data
        return self.data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".tracker-",
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(self.data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.close()
            os.replace(handle.name, self.path)
        except BaseException:
            handle.close()
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    # -- validation helpers ------------------------------------------------

    @staticmethod
    def _validate_venue(venue, partial):
        if not isinstance(venue, dict):
            raise StoreError("each venue must be a JSON object")
        if partial and "id" not in venue:
            return
        if "id" not in venue or "name" not in venue:
            raise StoreError("venues need at least an id and a name")
        kind = venue.get("kind", "other")
        if kind not in VENUE_KINDS:
            raise StoreError(
                f"venue kind {kind!r} not one of {sorted(VENUE_KINDS)}"
            )
        window = venue.get("response_window_days")
        if window is not None and (not isinstance(window, int) or window < 0):
            raise StoreError("response_window_days must be a positive integer")

    @staticmethod
    def _validate_submission(sub, partial):
        if not isinstance(sub, dict):
            raise StoreError("each submission must be a JSON object")
        if partial:
            return
        for key in ("id", "title", "venue_id", "date_sent", "status"):
            if key not in sub:
                raise StoreError(f"submissions need a {key}")
        if sub["status"] not in VALID_STATUSES:
            raise StoreError(
                f"status {sub['status']!r} not one of {sorted(VALID_STATUSES)}"
            )

    # -- venues ------------------------------------------------------------

    def next_id(self, prefix):
        used = []
        for item in self.data["venues"] + self.data["submissions"]:
            item_id = str(item.get("id", ""))
            if item_id.startswith(prefix + "-"):
                try:
                    used.append(int(item_id[len(prefix) + 1:]))
                except ValueError:
                    continue
        return f"{prefix}-{max(used, default=0) + 1}"

    def add_venue(self, name, kind="other", response_window_days=None,
                  guidelines=None, notes=None):
        venue = {
            "id": self.next_id("venue"),
            "name": str(name).strip(),
            "kind": kind,
        }
        if not venue["name"]:
            raise StoreError("venue name must not be empty")
        self._validate_venue(venue, partial=False)
        if response_window_days is not None:
            if not isinstance(response_window_days, int) or response_window_days < 0:
                raise StoreError("response_window_days must be a positive integer")
        venue["response_window_days"] = response_window_days
        if guidelines:
            venue["guidelines"] = str(guidelines)
        if notes:
            venue["notes"] = str(notes)
        self.data["venues"].append(venue)
        return venue

    def get_venue(self, venue_id):
        for venue in self.data["venues"]:
            if venue["id"] == venue_id:
                return venue
        raise StoreError(f"no venue with id {venue_id!r}")

    def update_venue(self, venue_id, **fields):
        venue = self.get_venue(venue_id)
        allowed = {
            "name", "kind", "response_window_days", "guidelines", "notes"
        }
        unknown = set(fields) - allowed
        if unknown:
            raise StoreError(f"cannot update fields: {sorted(unknown)}")
        merged = dict(venue)
        merged.update(fields)
        self._validate_venue(merged, partial=False)
        venue.update(fields)
        return venue

    def delete_venue(self, venue_id):
        venue = self.get_venue(venue_id)
        linked = [s for s in self.data["submissions"]
                  if s["venue_id"] == venue_id]
        if linked:
            raise StoreError(
                f"venue {venue_id!r} still has {len(linked)} submission(s); "
                "delete or move them first"
            )
        self.data["venues"].remove(venue)
        return venue

    def list_venues(self):
        return list(self.data["venues"])

    # -- submissions -------------------------------------------------------

    def add_submission(self, title, venue_id, date_sent=None, today=None,
                       status=STATUS_SENT, notes=None):
        now = parse_date(date_sent) if date_sent else (today or date.today())
        self.get_venue(venue_id)
        if status not in VALID_STATUSES:
            raise StoreError(f"status {status!r} not one of {sorted(VALID_STATUSES)}")
        sub_id = self.next_id("sub")
        sub = {
            "id": sub_id,
            "title": str(title).strip(),
            "venue_id": venue_id,
            "date_sent": now.isoformat(),
            "status": status,
            "events": [{"date": now.isoformat(), "event": "sent",
                        "note": ""}],
        }
        if not sub["title"]:
            raise StoreError("submission title must not be empty")
        venue = self.get_venue(venue_id)
        window = venue.get("response_window_days")
        if window is not None:
            sub["response_due"] = add_days(now, window).isoformat()
            sub["follow_up_on"] = sub["response_due"]
        if notes:
            sub["notes"] = str(notes)
        self.data["submissions"].append(sub)
        return sub

    def get_submission(self, sub_id):
        for sub in self.data["submissions"]:
            if sub["id"] == sub_id:
                return sub
        raise StoreError(f"no submission with id {sub_id!r}")

    def log_response(self, sub_id, status, date_logged=None, today=None,
                     note=None):
        sub = self.get_submission(sub_id)
        if status not in VALID_STATUSES:
            raise StoreError(f"status {status!r} not one of {sorted(VALID_STATUSES)}")
        when = parse_date(date_logged) if date_logged else (today or date.today())
        sub["status"] = status
        sub.setdefault("events", []).append(
            {"date": when.isoformat(), "event": status, "note": str(note or "")}
        )
        if status in (STATUS_FORM_REJECTION, STATUS_PERSONAL_REJECTION):
            sub.pop("response_due", None)
            sub.pop("follow_up_on", None)
        return sub

    def schedule_resubmission(self, sub_id, after_date, note=None):
        sub = self.get_submission(sub_id)
        when = parse_date(after_date)
        sub["resubmit_after"] = when.isoformat()
        if note:
            sub["resubmit_note"] = str(note)
        return sub

    def set_follow_up(self, sub_id, on_date, note=None):
        sub = self.get_submission(sub_id)
        sub["follow_up_on"] = parse_date(on_date).isoformat()
        if note:
            sub["follow_up_note"] = str(note)
        return sub

    def withdraw(self, sub_id, date_logged=None, today=None, note=None):
        return self.log_response(
            sub_id, STATUS_WITHDRAWN,
            date_logged=date_logged, today=today, note=note,
        )

    def delete_submission(self, sub_id):
        sub = self.get_submission(sub_id)
        self.data["submissions"].remove(sub)
        return sub

    def list_submissions(self, status=None, venue_id=None):
        out = []
        for sub in self.data["submissions"]:
            if status and sub["status"] != status:
                continue
            if venue_id and sub["venue_id"] != venue_id:
                continue
            out.append(sub)
        return out

    # -- queries -----------------------------------------------------------

    def due_items(self, today=None):
        now = today or date.today()
        pending, reminders = [], []
        for sub in self.data["submissions"]:
            follow = sub.get("follow_up_on")
            if sub["status"] == STATUS_SENT and follow:
                due = parse_date(follow)
                if due <= now:
                    pending.append({
                        "id": sub["id"],
                        "title": sub["title"],
                        "venue_id": sub["venue_id"],
                        "date_sent": sub["date_sent"],
                        "follow_up_on": follow,
                        "days_over": (now - due).days,
                    })
            resub = sub.get("resubmit_after")
            if resub:
                due = parse_date(resub)
                if due <= now:
                    reminders.append({
                        "id": sub["id"],
                        "title": sub["title"],
                        "venue_id": sub["venue_id"],
                        "status": sub["status"],
                        "resubmit_after": resub,
                        "note": sub.get("resubmit_note", ""),
                    })
        return {"responses_due": pending, "resubmission_reminders": reminders}

    def stats(self, today=None):
        subs = self.data["submissions"]
        counts = {status: 0 for status in sorted(VALID_STATUSES)}
        for sub in subs:
            counts[sub["status"]] = counts.get(sub["status"], 0) + 1
        total = len(subs)
        closed_rejections = (
            counts[STATUS_FORM_REJECTION] + counts[STATUS_PERSONAL_REJECTION]
        )
        decided = (
            closed_rejections + counts[STATUS_REQUESTED_MORE]
            + counts[STATUS_ACCEPTED]
        )
        request_more = counts[STATUS_REQUESTED_MORE]
        def ratio(part, whole):
            return round(part / whole, 4) if whole else None
        by_venue = {}
        for venue in self.data["venues"]:
            vsubs = [s for s in subs if s["venue_id"] == venue["id"]]
            vcounts = {status: 0 for status in sorted(VALID_STATUSES)}
            for sub in vsubs:
                vcounts[sub["status"]] = vcounts.get(sub["status"], 0) + 1
            v_decided = (
                vcounts[STATUS_FORM_REJECTION]
                + vcounts[STATUS_PERSONAL_REJECTION]
                + vcounts[STATUS_REQUESTED_MORE]
                + vcounts[STATUS_ACCEPTED]
            )
            by_venue[venue["id"]] = {
                "venue": venue["name"],
                "kind": venue.get("kind", "other"),
                "total": len(vsubs),
                "counts": vcounts,
                "acceptance_ratio": ratio(vcounts[STATUS_ACCEPTED], v_decided),
            }
        return {
            "total_submissions": total,
            "counts": counts,
            "acceptance_ratio": ratio(counts[STATUS_ACCEPTED], decided),
            "request_more_ratio": ratio(request_more, decided),
            "rejection_ratio": ratio(closed_rejections, decided),
            "decided": decided,
            "pending": counts[STATUS_SENT],
            "by_venue": by_venue,
        }
