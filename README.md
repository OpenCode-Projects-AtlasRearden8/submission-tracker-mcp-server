# submission-tracker-mcp-server

A **local-first MCP server for writers** who send work out to magazines, literary
agents, and anthologies. It tracks venues, submission dates, response windows,
form rejections vs. "please send more" replies, resubmission reminders, and
acceptance-ratio rollups you can query on demand.

Everything lives in **one JSON file you own**. No network, no accounts, no
telemetry — it is plain stdlib Python speaking MCP-style JSON-RPC 2.0 over
stdio, so you can plug it into any MCP-capable assistant or drive it from a
script.

* Built by [Asdale Dynamics, LLC](https://dylanasdale.github.io) — Dylan
  Asdale's hub for plain-English AI guides and practical, local-first tools.
* Part of a family of single-purpose servers: `book-draft-mcp-server`
  (manuscripts) and `rights-reversion-tracker` (contracts). This one is purely
  about **tracking submissions out to markets**.

## What it does

- **Venues** — magazines, agents, anthologies, publishers, each with an
  optional response window in days and a notes/guidelines field.
- **Submissions** — log what you sent, where, and when. If the venue has a
  response window, the server auto-sets a *response due / follow-up* date.
- **Response log** — record the outcome: `form_rejection`,
  `personal_rejection`, `requested_more`, or `accepted`. Every change is
  appended to an event log with a date and optional note, so the
  form-rejection vs. request-more history is always visible on the record.
- **Resubmission reminders** — after a rejection, schedule a `resubmit_after`
  date (send to the next tier, or back to a turbo-reopen list).
- **Follow-up nudges** — pending submissions whose follow-up date has arrived
  show up in `due_items`, including `days_over`.
- **Rollups** — `stats` returns totals by status, acceptance ratio,
  request-more ratio, rejection ratio, and a per-venue breakdown.
- **Manual follow-up override** — `set_follow_up` for when an editor says
  "check back in a few weeks."

## Running it

Requirements: Python 3.10+ (stdlib only; pytest only if you want to run the
tests).

```sh
python -m submission_tracker_mcp
```

It reads one JSON-RPC request per line from stdin and writes one JSON-RPC
response per line to stdout, just like an MCP stdio server should.

**Where is the data?** One JSON file you own:

1. `--data /path/to/tracker.json` argument, or
2. `$SUBMISSION_TRACKER_HOME/tracker.json`, or
3. `~/.local/share/submission-tracker/tracker.json` (respects `XDG_DATA_HOME`).

Point an MCP client at it, e.g. in a `claude_desktop_config.json`-style config:

```json
{
  "mcpServers": {
    "submissions-tracker": {
      "command": "python",
      "args": ["-m", "submission_tracker_mcp"],
      "env": {"SUBMISSION_TRACKER_HOME": "/home/you/notes/submission-tracker"}
    }
  }
}
```

Or poke it by hand:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | python -m submission_tracker_mcp --data ./tracker.json
```

## Tools

| Tool | Purpose |
| --- | --- |
| `add_venue` | Add a market (kind: magazine/agent/anthology/publisher/other) with an optional response window |
| `list_venues`, `update_venue`, `delete_venue` | Manage venues (delete refused while submissions point at the venue) |
| `add_submission` | Log a piece sent; sets response-due/follow-up from the venue's window |
| `list_submissions`, `get_submission` | List/filter (by status or venue) or read one with its full event log |
| `log_response` | Record form rejection, personal rejection, request for more, or acceptance |
| `schedule_resubmission` | Reminder date for sending the piece to the next tier |
| `set_follow_up` | Manual nudge date for a pending submission |
| `withdraw` | Mark withdrawn (e.g. accepted elsewhere) |
| `delete_submission` | Remove a submission |
| `due_items` | Pending submissions past their follow-up date + resubmission reminders due |
| `stats` | Acceptance/request-more/rejection ratios overall and per venue |

Sample `tools/call`:

```sh
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_venue","arguments":{"name":"Shimmer Magazine","kind":"magazine","response_window_days":90}}}' \
  | python -m submission_tracker_mcp --data ./tracker.json
```

## A typical session

1. `add_venue` — "Shimmer Magazine" (magazine, 90-day window).
2. `add_submission` — "The Lighthouse Letters" → response due in 90 days.
3. Later: `due_items` — it shows up once the follow-up date arrives.
4. `log_response` — `form_rejection`, note "slush pile, boilerplate".
5. `schedule_resubmission` — send to the next anthology after 2026-09-01.
6. `stats` — "Shimmer: 3 sent, 2 form rejections, acceptance ratio 0.0;
   agents overall: request-more ratio 0.4."

## Data format

Human-readable JSON:

```json
{
  "version": 1,
  "venues": [
    {"id": "venue-1", "name": "Shimmer Magazine", "kind": "magazine",
     "response_window_days": 90}
  ],
  "submissions": [
    {"id": "sub-1", "title": "The Lighthouse Letters", "venue_id": "venue-1",
     "date_sent": "2026-06-15", "status": "sent",
     "response_due": "2026-09-13", "follow_up_on": "2026-09-13",
     "events": [
       {"date": "2026-06-15", "event": "sent", "note": ""}
     ]}
  ]
}
```

Writes are atomic (temp file + rename), so a crash mid-save will not eat your
log. Back the file up like any other writing note; it is yours.

## Privacy

No network calls, no accounts, no metrics. The only files touched are the
tracker JSON you choose and nothing else. Do not store sensitive personal
data in venue notes; treat this like a notebook.

## Tests

```sh
python -m pytest
```

The suite covers storage round-trips and validation, the full JSON-RPC
surface (called directly against the handler, with the filesystem mocked into
`tmp_path`), date-window math, rollups, and the stdio framing including a real
subprocess end-to-end run.

## Status and known gaps

See [STATUS.md](STATUS.md).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Asdale Dynamics, LLC.

The MCP protocol shape used here (initialize / tools/list / tools/call over
stdio JSON-RPC) follows the public Model Context Protocol conventions, but no
MCP SDK is required — stdlib only.
