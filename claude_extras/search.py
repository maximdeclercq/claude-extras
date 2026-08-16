"""The `claude search` verb, which finds a session by what was said in it."""

import json
import re
import sys
import time

from . import sessions
from .accounts import LAUNCH_LOG, AccountError, select_accounts
from .args import parse, take_count

SNIPPET_WIDTH = 60
DEFAULT_LIMIT = 20

HELP = """Usage: claude search [options] <pattern> [number]

Find sessions by what was said in them

Arguments:
  pattern                 Text to look for, case-insensitive
  number                  Resume the session on that row

Options:
  -a, --account <name>    One account, or "all" (default: all)
      --regex             Treat the pattern as a regular expression
      --mine              Only search what you said, not what Claude answered
      --limit <n>         How many rows to list (default 20)
      --json              Output as JSON
  -h, --help              Display help for command

Examples:
  claude search "link-dest"
  claude search --mine "why does" --limit 40
  claude search --regex 'CVE-[0-9]+' --account acme
  claude search "link-dest" 2     # resume match #2
"""


def _text_of(record):
    """The human-readable text of one transcript record, or "" if it has none."""
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def _snippet(text, match):
    """One line around the hit, so a row shows why it matched."""
    start = max(0, match.start() - SNIPPET_WIDTH // 3)
    piece = text[start:start + SNIPPET_WIDTH]
    piece = " ".join(piece.split())
    lead = "..." if start > 0 else ""
    tail = "..." if start + SNIPPET_WIDTH < len(text) else ""
    return f"{lead}{piece}{tail}"


def byte_prefilter(pattern):
    """The pattern compiled for bytes, or None when bytes cannot be trusted for it.

    Bytes are a different regex dialect, and the prefilter case-folds ASCII only.
    """
    try:
        return re.compile(pattern.pattern.encode("ascii"), re.IGNORECASE)
    except (UnicodeEncodeError, re.error):
        return None


def scan_file(path, pattern, raw, mine_only):
    """(role, snippet) for the first match in a transcript, or None."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    if raw is not None and not raw.search(blob):
        return None
    text_blob = blob.decode("utf-8", "replace")
    if raw is None and not pattern.search(text_blob):
        return None
    for line in text_blob.splitlines():
        if not pattern.search(line):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = (record.get("message") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        if mine_only and role != "user":
            continue
        if record.get("isMeta") or record.get("isSidechain"):
            continue
        text = _text_of(record)
        # The line matched as raw JSON, which includes tool payloads and escaping;
        # only a hit in the readable text is one you can act on.
        found = pattern.search(text)
        if found:
            return role, _snippet(text, found)
    return None


def search(scope, pattern, mine_only=False, limit=DEFAULT_LIMIT, under=None):
    """Matching sessions, newest first, in `claude resume`'s row shape."""
    logical = sessions.load_logical_map(LAUNCH_LOG)
    raw = byte_prefilter(pattern)
    rows = []
    for name, config_dir in select_accounts(scope):
        for mtime, path in sessions.transcripts(config_dir, under):
            # The same bound `resume` uses, and sound for a content match too: a
            # row's activity is a timestamp from inside the file, so mtime is an
            # upper bound on it. Once `limit` rows are held, a file that cannot
            # beat the weakest of them cannot beat it after being read either,
            # and neither can any older file behind it.
            if len(rows) >= limit and mtime <= min(r["activity"] for r in rows):
                break
            hit = scan_file(path, pattern, raw, mine_only)
            if not hit:
                continue
            row = sessions.session_row(name, config_dir, path, mtime, logical)
            if row is None:
                continue
            rows.append({**row, "role": hit[0], "snippet": hit[1]})
    rows.sort(key=lambda r: r["activity"], reverse=True)
    return rows[:limit]


def render(rows, now):
    from .report import table

    if not rows:
        return "no matches"
    head = f"matches  {sessions.scope_label(rows)}"
    headers = ["#", "age", "account", "title", "match"]
    body = [[f"{index:>3}", sessions.age(row["activity"], now), row["account"],
             row["title"][:34], row["snippet"]] for index, row in enumerate(rows, 1)]
    return "\n".join([head, *table(headers, body, elide=("account",)), "",
                      "resume:  claude search <pattern> <#>"])


FLAGS = {"--account": str, "-a": "--account", "--limit": take_count, "--here": None,
         "--regex": None, "--mine": None, "--json": None}


def main(argv):
    options, rest = parse(argv, FLAGS, "search")
    if options["--help"]:
        print(HELP, end="")
        return 0
    if not rest:
        print(HELP, end="", file=sys.stderr)
        raise AccountError("claude search needs a pattern")
    if len(rest) > 2 or (len(rest) == 2 and not rest[1].isdigit()):
        raise AccountError(f"claude search: unexpected argument '{rest[-1]}'")
    pattern_text = rest[0]
    number = int(rest[1]) if len(rest) == 2 else None
    try:
        escaped = pattern_text if options["--regex"] else re.escape(pattern_text)
        pattern = re.compile(escaped, re.IGNORECASE)
    except re.error as exc:
        raise AccountError(f"bad regular expression: {exc}") from None

    from .resume import here

    under = here() if options["--here"] else None
    limit = options["--limit"] or DEFAULT_LIMIT
    rows = search(options["--account"] or "all", pattern, options["--mine"],
                  max(limit, number or 0), under)
    if number is not None:
        if not 1 <= number <= len(rows):
            raise AccountError(f"no match #{number}")
        return sessions.resume(rows[number - 1])
    if options["--json"]:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    print(render(rows, time.time()))
    return 0
