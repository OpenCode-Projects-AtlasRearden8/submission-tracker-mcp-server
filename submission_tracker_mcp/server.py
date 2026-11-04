"""MCP-style JSON-RPC handler for the submission tracker.

The handler is transport-agnostic: tests call ``handle`` directly with
parsed requests. ``__main__`` wires it to stdin/stdout line framing.
"""

from __future__ import annotations

import json
from datetime import date

from . import __version__
from . import storage
from .storage import Store, StoreError

JSONRPC_VERSION = "2.0"


class JsonRpcError(Exception):
    """Carries a JSON-RPC error code, message, and data."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
TOOL_ERROR = -32000  # application range used for tool failures

PROTOCOL_VERSION = "2025-03-26"


# ---------------------------------------------------------------------------
# Tool wrappers: JSON-friendly args in, JSON-friendly results out.
# ---------------------------------------------------------------------------


def _as_date(value, field):
    if value is None:
        return None
    try:
        return storage.parse_date(value)
    except ValueError as exc:
        raise JsonRpcError(
            INVALID_PARAMS, f"invalid date for {field!r}: {value!r}",
            str(exc),
        ) from None


class TrackerApp:
    """Applies tracker operations on top of a Store."""

    def __init__(self, store, today=None):
        self.store = store
        self._today = today  # injectable clock for tests

    def today(self):
        if self._today is None:
            return date.today()
        return storage.parse_date(self._today)

    def _require(self, args, *keys):
        missing = [key for key in keys if args.get(key) in (None, "")]
        if missing:
            raise JsonRpcError(
                INVALID_PARAMS, f"missing required argument(s): {missing}"
            )

    # -- venue tools --------------------------------------------------------

    def add_venue(self, args):
        self._require(args, "name")
        window = args.get("response_window_days")
        if window is not None:
            try:
                window = int(window)
            except (TypeError, ValueError):
                raise JsonRpcError(
                    INVALID_PARAMS, "response_window_days must be an integer"
                ) from None
        venue = self.store.add_venue(
            name=args["name"],
            kind=args.get("kind", "other"),
            response_window_days=window,
            guidelines=args.get("guidelines"),
            notes=args.get("notes"),
        )
        self.store.save()
        return {"venue": venue}

    def list_venues(self, args):
        kind = args.get("kind")
        venues = self.store.list_venues()
        if kind:
            venues = [v for v in venues if v.get("kind") == kind]
        return {"venues": venues, "count": len(venues)}

    def update_venue(self, args):
        self._require(args, "venue_id")
        allowed = {"name", "kind", "response_window_days",
                   "guidelines", "notes"}
        fields = dict(args)
        fields.pop("venue_id", None)
        if "response_window_days" in fields:
            try:
                fields["response_window_days"] = (
                    None if fields["response_window_days"] is None
                    else int(fields["response_window_days"])
                )
            except (TypeError, ValueError):
                raise JsonRpcError(
                    INVALID_PARAMS, "response_window_days must be an integer"
                ) from None
        unknown = set(fields) - allowed
        if unknown:
            raise JsonRpcError(
                INVALID_PARAMS, f"cannot update fields: {sorted(unknown)}"
            )
        venue = self.store.update_venue(args["venue_id"], **fields)
        self.store.save()
        return {"venue": venue}

    def delete_venue(self, args):
        self._require(args, "venue_id")
        self.store.delete_venue(args["venue_id"])
        self.store.save()
        return {"deleted": args["venue_id"]}

    # -- submission tools ---------------------------------------------------

    def add_submission(self, args):
        self._require(args, "title", "venue_id")
        sent = _as_date(args.get("date_sent"), "date_sent")
        sub = self.store.add_submission(
            title=args["title"],
            venue_id=args["venue_id"],
            date_sent=sent,
            today=self.today(),
            notes=args.get("notes"),
        )
        self.store.save()
        return {"submission": sub}

    def list_submissions(self, args):
        subs = self.store.list_submissions(
            status=args.get("status"), venue_id=args.get("venue_id")
        )
        return {"submissions": subs, "count": len(subs)}

    def get_submission(self, args):
        self._require(args, "submission_id")
        return {"submission": self.store.get_submission(args["submission_id"])}

    def log_response(self, args):
        self._require(args, "submission_id", "status")
        logged = _as_date(args.get("date_logged"), "date_logged")
        sub = self.store.log_response(
            args["submission_id"],
            args["status"],
            date_logged=logged,
            today=self.today(),
            note=args.get("note"),
        )
        self.store.save()
        return {"submission": sub}

    def schedule_resubmission(self, args):
        self._require(args, "submission_id", "after_date")
        after = _as_date(args["after_date"], "after_date")
        sub = self.store.schedule_resubmission(
            args["submission_id"], after, note=args.get("note")
        )
        self.store.save()
        return {"submission": sub}

    def set_follow_up(self, args):
        self._require(args, "submission_id", "on_date")
        on = _as_date(args["on_date"], "on_date")
        sub = self.store.set_follow_up(
            args["submission_id"], on, note=args.get("note")
        )
        self.store.save()
        return {"submission": sub}

    def withdraw(self, args):
        self._require(args, "submission_id")
        logged = _as_date(args.get("date_logged"), "date_logged")
        sub = self.store.withdraw(
            args["submission_id"],
            date_logged=logged,
            today=self.today(),
            note=args.get("note"),
        )
        self.store.save()
        return {"submission": sub}

    def delete_submission(self, args):
        self._require(args, "submission_id")
        removed = self.store.delete_submission(args["submission_id"])
        self.store.save()
        return {"deleted": removed["id"]}

    # -- query tools ----------------------------------------------------------

    def due_items(self, args):
        return self.store.due_items(today=self.today())

    def stats(self, args):
        return self.store.stats(today=self.today())


TOOLS = [
    {
        "name": "add_venue",
        "description": "Add a market to track (magazine, agent, anthology, publisher).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Venue name"},
                "kind": {"type": "string", "enum": ["magazine", "agent", "anthology", "publisher", "other"],
                          "description": "Kind of venue (default: other)"},
                "response_window_days": {"type": "integer",
                                          "description": "Usual days before you may expect a reply"},
                "guidelines": {"type": "string", "description": "Guidelines URL or notes"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_venues",
        "description": "List tracked venues, optionally filtered by kind.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["magazine", "agent", "anthology", "publisher", "other"]},
            },
        },
    },
    {
        "name": "update_venue",
        "description": "Update a venue's name, kind, response window, or notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "venue_id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["magazine", "agent", "anthology", "publisher", "other"]},
                "response_window_days": {"type": ["integer", "null"]},
                "guidelines": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["venue_id"],
        },
    },
    {
        "name": "delete_venue",
        "description": "Delete a venue only if it has no submissions.",
        "input_schema": {
            "type": "object",
            "properties": {"venue_id": {"type": "string"}},
            "required": ["venue_id"],
        },
    },
    {
        "name": "add_submission",
        "description": ("Log a piece sent to a venue. Sets the response-due "
                        "follow-up date from the venue's response window."),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Story/query title"},
                "venue_id": {"type": "string"},
                "date_sent": {"type": "string", "format": "date",
                               "description": "ISO date (default: today)"},
                "notes": {"type": "string"},
            },
            "required": ["title", "venue_id"],
        },
    },
    {
        "name": "list_submissions",
        "description": "List submissions, optionally filtered by status or venue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                            "enum": ["sent", "form_rejection", "personal_rejection",
                                      "requested_more", "accepted", "withdrawn"]},
                "venue_id": {"type": "string"},
            },
        },
    },
    {
        "name": "get_submission",
        "description": "Get one submission with its full event log.",
        "input_schema": {
            "type": "object",
            "properties": {"submission_id": {"type": "string"}},
            "required": ["submission_id"],
        },
    },
    {
        "name": "log_response",
        "description": ("Record the venue's reply: form rejection, personal "
                        "rejection, request for more, or acceptance."),
        "input_schema": {
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
                "status": {"type": "string",
                            "enum": ["form_rejection", "personal_rejection",
                                      "requested_more", "accepted"]},
                "date_logged": {"type": "string", "format": "date"},
                "note": {"type": "string"},
            },
            "required": ["submission_id", "status"],
        },
    },
    {
        "name": "schedule_resubmission",
        "description": ("Set a resubmission reminder date for an earlier "
                        "rejection (e.g. send elsewhere or to the next tier)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
                "after_date": {"type": "string", "format": "date"},
                "note": {"type": "string"},
            },
            "required": ["submission_id", "after_date"],
        },
    },
    {
        "name": "set_follow_up",
        "description": "Manually set a follow-up (nudge) date for a pending submission.",
        "input_schema": {
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
                "on_date": {"type": "string", "format": "date"},
                "note": {"type": "string"},
            },
            "required": ["submission_id", "on_date"],
        },
    },
    {
        "name": "withdraw",
        "description": "Mark a submission withdrawn (e.g. accepted elsewhere).",
        "input_schema": {
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
                "date_logged": {"type": "string", "format": "date"},
                "note": {"type": "string"},
            },
            "required": ["submission_id"],
        },
    },
    {
        "name": "delete_submission",
        "description": "Remove a submission from the tracker.",
        "input_schema": {
            "type": "object",
            "properties": {"submission_id": {"type": "string"}},
            "required": ["submission_id"],
        },
    },
    {
        "name": "due_items",
        "description": ("Show pending submissions whose follow-up date has "
                        "arrived and resubmission reminders that are due."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stats",
        "description": ("Acceptance-ratio rollups: totals by status, "
                        "acceptance / request-more / rejection ratios, and "
                        "per-venue breakdowns."),
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_HANDLERS = {
    "add_venue": TrackerApp.add_venue,
    "list_venues": TrackerApp.list_venues,
    "update_venue": TrackerApp.update_venue,
    "delete_venue": TrackerApp.delete_venue,
    "add_submission": TrackerApp.add_submission,
    "list_submissions": TrackerApp.list_submissions,
    "get_submission": TrackerApp.get_submission,
    "log_response": TrackerApp.log_response,
    "schedule_resubmission": TrackerApp.schedule_resubmission,
    "set_follow_up": TrackerApp.set_follow_up,
    "withdraw": TrackerApp.withdraw,
    "delete_submission": TrackerApp.delete_submission,
    "due_items": TrackerApp.due_items,
    "stats": TrackerApp.stats,
}


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


class Server:
    """MCP-style JSON-RPC endpoint over any call/response transport."""

    def __init__(self, store, today=None):
        self.store = store
        self.app = TrackerApp(store, today=today)

    def handle(self, request):
        """Handle one request (a dict) and return a response dict, or None
        for notifications."""
        try:
            if not isinstance(request, dict):
                raise JsonRpcError(INVALID_REQUEST, "request must be an object")
            if request.get("jsonrpc") != JSONRPC_VERSION:
                raise JsonRpcError(
                    INVALID_REQUEST,
                    "jsonrpc field must be exactly \"2.0\"",
                )
            method = request.get("method")
            if not isinstance(method, str):
                raise JsonRpcError(INVALID_REQUEST, "method must be a string")
            request_id = request.get("id")

            if method == "initialize":
                if isinstance(request_id, int) or isinstance(request_id, str):
                    return self._ok(request_id, self._initialize_result())
                raise JsonRpcError(INVALID_REQUEST, "initialize requires an id")

            if method == "notifications/initialized":
                return None

            if method == "ping":
                return self._ok(request_id, {})

            if method == "tools/list":
                return self._ok(request_id, {"tools": TOOLS})

            if method == "tools/call":
                return self._ok(request_id, self._call_tools(request))

            raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method!r}")
        except JsonRpcError as exc:
            request_id = self._safe_id(request)
            return self._error(request_id, exc.code, exc.message, exc.data)
        except StoreError as exc:
            return self._error(self._safe_id(request), TOOL_ERROR, str(exc))
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            return self._error(
                self._safe_id(request), TOOL_ERROR,
                f"tool failed: {exc.__class__.__name__}: {exc}",
            )

    @staticmethod
    def _safe_id(request):
        if isinstance(request, dict):
            request_id = request.get("id")
            if request_id is not None:
                return request_id
        return None

    @staticmethod
    def _ok(request_id, result):
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message, data=None):
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": err}

    def _initialize_result(self):
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "submissions-tracker",
                "version": __version__,
                "title": "Submissions Tracker MCP Server",
            },
            "instructions": (
                "Local-first tracker for magazine, agent, and anthology "
                "submissions: venues, dates sent, response windows, "
                "rejection/request logs, resubmission reminders, and "
                "acceptance-ratio rollups."
            ),
        }

    def _call_tools(self, request):
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise JsonRpcError(INVALID_PARAMS, "params must be an object")
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_HANDLERS:
            raise JsonRpcError(
                METHOD_NOT_FOUND, f"unknown tool: {name!r}"
            )
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "arguments must be an object")
        result = TOOL_HANDLERS[name](self.app, arguments)
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, indent=2,
                                                     ensure_ascii=False)}
            ],
            "structuredContent": result,
            "isError": False,
        }


def error_response(request_id, code, message, data=None):
    return Server._error(request_id, code, message, data)
