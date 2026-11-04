# STATUS.md — submission-tracker-mcp-server

Built: 2026-11-03 · By Asdale Dynamics, LLC for Dylan Asdale's writing workflow.

## What was built

A local-first MCP-style server for tracking fiction/nonfiction submissions to
magazines, agents, and anthologies:

- `submission_tracker_mcp/storage.py` — one-JSON-file store (versioned schema,
  venues + submissions + event logs), validation, atomic saves
  (temp-file + rename), date-window math, `due_items` (pending follow-ups with
  `days_over`, plus resubmission reminders), and `stats` rollups
  (acceptance / request-more / rejection ratios overall and per venue).
- `submission_tracker_mcp/server.py` — transport-agnostic MCP JSON-RPC 2.0
  handler: `initialize`, `tools/list`, `tools/call`, `ping`,
  `notifications/initialized` (silent). 14 tools covering venues,
  submissions, response logging (form rejection vs personal rejection vs
  request-more vs accepted), resubmission reminders, manual follow-ups,
  withdrawals, and rollups. Errors are typed: `-32601` unknown method/tool,
  `-32602` missing/invalid params, `-32000` tool failures, plus standard
  `-32700/-32600`.
- `submission_tracker_mcp/__main__.py` — line-framed stdio transport
  (one request per line, one response per line) with `--data PATH` and
  `$SUBMISSION_TRACKER_HOME` / `$XDG_DATA_HOME` defaults; survives malformed
  lines, blank lines, and notifications without dying.
- `tests/` — 70 pytest cases: storage round-trips/atomicity/corruption,
  full JSON-RPC surface called directly against the handler (mocked-call
  style, filesystem confined to `tmp_path`), stdio framing with fake
  reader/writer, and a real subprocess end-to-end run.
- `README.md` (credits Asdale Dynamics, LLC), `LICENSE` (MIT, exact required
  copyright line), `STATUS.md` (this file).

Lifecycle distinction honored: this tracks submissions *out to markets* —
`book-draft-mcp-server` covers manuscripts and `rights-reversion-tracker`
covers contracts.

## How to run it

```sh
python -m submission_tracker_mcp --data ./tracker.json
```

Then feed it JSON-RPC, one object per line:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  | python -m submission_tracker_mcp --data ./tracker.json
```

Data defaults to `~/.local/share/submission-tracker/tracker.json` (env override:
`SUBMISSION_TRACKER_HOME`, `XDG_DATA_HOME`). No network access at any point.

Configure any MCP client with:

```json
{"command": "python", "args": ["-m", "submission_tracker_mcp"],
 "env": {"SUBMISSION_TRACKER_HOME": "/your/folder/submission-tracker"}}
```

## Verification results

- `python -m pytest` — **70 passed** (0 failed), run with pytest 9.1.1 on
  Python 3.13.5.
- `python -m py_compile` on all package and test files — clean.
- Real-process smoke test (subprocess, 15-line session): initialize, 2 venues,
  3 submissions, form rejection + resubmission reminder, request-more,
  acceptance; `due_items` correctly surfaced the reminder due 2026-03-15;
  `stats` returned counts `{accepted:1, form_rejection:1, requested_more:1}`
  and acceptance ratio 0.3333 with per-venue breakdowns; malformed JSON got a
  `-32700` error response and an unknown method got `-32601`, both without
  killing the server; final JSON file on disk held all 2 venues and
  3 submissions with full event logs.
- Date math verified: 2026-01-05 + 90-day window → response due 2026-04-05
  behavior; overdue pending follow-ups report `days_over`.

## Known gaps

- No CLI wrapper: driving single interactions requires echo/pipe rather than a
  friendlier command (a tiny `add`/`status` CLI would help phone-free daily use).
- No `prompts/` or `resources/` MCP surfaces — tools only, by design.
- Rollups are global or per-venue; no date-range filters (e.g. "this year's
  ratios") yet.
- No import/export or CSV backup command; users should copy the JSON file.
- `response window` is advisory: the server never auto-changes statuses, only
  surfaces dates — intentional, but worth telling users.
- Venue delete is blocked while submissions exist; a `venue_id` reassignment
  tool would make cleanup easier.
