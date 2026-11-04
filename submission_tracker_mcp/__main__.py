"""stdio transport: one JSON-RPC request per line, one response per line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .server import PARSE_ERROR, Server
from .storage import Store, default_data

DEFAULT_DATA_DIRNAME = "submission-tracker"


def resolve_store_path(explicit=None):
    """Data file location: explicit arg, or $SUBMISSION_TRACKER_HOME, or
    a folder you own inside ~/.local/share (never a network path)."""
    if explicit:
        return Path(explicit)
    home = None
    env = None
    try:
        env = __import__("os").environ.get("SUBMISSION_TRACKER_HOME")
    except Exception:
        pass
    if env:
        home = Path(env)
    else:
        base = __import__("os").environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
        )
        home = Path(base) / DEFAULT_DATA_DIRNAME
    return home / "tracker.json"


def serve(store_path, reader=None, writer=None):
    store = Store(store_path)
    try:
        store.load()
    except Exception:
        store.data = default_data()
    server = Server(store)

    reader = reader or sys.stdin
    writer = writer or sys.stdout
    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = Server._error(None, PARSE_ERROR,
                                     "invalid JSON on line", str(exc))
        else:
            response = server.handle(request)
            if response is None:
                continue
        writer.write(json.dumps(response, ensure_ascii=False) + "\n")
        try:
            writer.flush()
        except (BrokenPipeError, ValueError):
            return


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m submission_tracker_mcp",
        description="Local submissions-tracker MCP server (JSON-RPC over stdio).",
    )
    parser.add_argument(
        "--data", default=None,
        help="Path to tracker JSON file "
             "(default: $SUBMISSION_TRACKER_HOME/tracker.json "
             "or ~/.local/share/submission-tracker/tracker.json)",
    )
    args = parser.parse_args(argv)
    store_path = resolve_store_path(args.data)
    serve(store_path)


if __name__ == "__main__":
    main()
