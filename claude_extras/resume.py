"""The `claude resume` verb: sessions across every account, picked by number."""

import json
import os
import sys
from datetime import UTC, datetime

from . import export, sessions
from .accounts import AccountError
from .args import parse, take_count

FLAGS = {**export.FLAGS, "--account": str, "-a": "--account", "--here": None,
         "--json": None, "--limit": take_count}

HELP = """Usage: claude resume [options] [number]

List and resume recent sessions

Arguments:
  number                  Resume the session on that row

Options:
  -a, --account <name>    One account, or "all" (default: all)
      --here              Only sessions started under this repository
      --limit <n>         How many to list (default 10)
      --json              List as JSON, for a script
      --format md|html    Export format (default md)
  -o, --out <file>        Export to this file instead of stdout
      --include-tools     Name the tools an export ran, never their output
  -h, --help              Display help for command

Commands:
  export <n>              Write session <n> out as markdown or html

Subagent transcripts and headless -p runs are never listed, since a headless run's
title describes the transcript it processed, not the work done where it ran.

Examples:
  claude resume
  claude resume 3
  claude resume --here
  claude resume --account acme --limit 20
  claude resume export 3 -o session.html --format html
"""


def here():
    """The repository the shell is in, as Claude Code records it: the real path."""
    from .accounts import project_root

    return project_root(os.getcwd())


def collect(scope, want, under=None):
    """Rows for an account scope, newest first across every account in it."""
    from .accounts import LAUNCH_LOG, select_accounts

    logical = sessions.load_logical_map(LAUNCH_LOG)
    rows = []
    for name, config_dir in select_accounts(scope):
        rows += sessions.sessions_for(name, config_dir, logical, want, under=under)
    rows.sort(key=lambda r: r["activity"], reverse=True)
    # a row picked by number stays reachable without repeating --limit
    return rows[:want]


def cmd_export(argv):
    """Write one session out. Numbering is the listing's, so `export 3` follows it."""
    options, rest = parse(argv, FLAGS, "resume export")
    if options["--help"]:
        print(HELP, end="")
        return 0
    if len(rest) != 1 or not rest[0].isdigit():
        raise AccountError("usage: claude resume export <n> [--format md|html] [-o FILE]")
    number = int(rest[0])
    under = here() if options["--here"] else None
    rows = collect(options["--account"] or "all", max(sessions.DEFAULT_LIMIT, number), under)
    if not 1 <= number <= len(rows):
        raise AccountError(f"no session #{number}")
    return export.emit(rows[number - 1], *export.choices(options))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "export":
        return cmd_export(argv[1:])
    options, rest = parse(argv, FLAGS, "resume")
    if options["--help"]:
        print(HELP, end="")
        return 0
    if len(rest) > 1 or (rest and not rest[0].isdigit()):
        raise AccountError(f"claude resume: unexpected argument '{rest[-1]}'")
    number = int(rest[0]) if rest else None
    under = here() if options["--here"] else None
    limit = options["--limit"] or sessions.DEFAULT_LIMIT
    as_json = options["--json"]

    rows = collect(options["--account"] or "all", max(limit, number or 0), under)
    if number is not None:
        if not 1 <= number <= len(rows):
            raise AccountError(f"no session #{number}")
        return sessions.resume(rows[number - 1])
    if as_json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    print(sessions.render(rows, datetime.now(UTC).timestamp()))
    return 0
