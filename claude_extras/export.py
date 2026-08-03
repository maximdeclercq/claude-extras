"""Render a transcript as something you can read, keep or send.

Tool results are never written out. They carry file contents and command output
from the machine the session ran on.
"""

import html
import json
from datetime import datetime

from .accounts import AccountError

FORMATS = ("md", "html")


def chosen_format(value):
    if value not in FORMATS:
        raise AccountError(f"--format must be one of {', '.join(FORMATS)}, got '{value}'")
    return value


# The export options every verb with an export subcommand takes.
FLAGS = {"--format": chosen_format, "-o": "--out", "--out": str, "--include-tools": None}


def choices(options):
    """(fmt, out_path, include_tools) as emit takes them, from parsed options."""
    return options["--format"] or "md", options["--out"], options["--include-tools"]

# Wrappers Claude Code injects into the user side of a transcript, matched by tag
# name so a turn that opens with a pasted tag survives as content
HARNESS_TAGS = frozenset({
    "system-reminder", "command-name", "command-message", "command-args",
    "local-command-stdout", "local-command-stderr", "local-command-caveat",
    "user-prompt-submit-hook", "task-notification",
})


def _is_harness(text):
    stripped = text.lstrip()
    if not stripped.startswith("<") or ">" not in stripped:
        return False
    # "<>" is JSX fragment shorthand, so an empty tag is ordinary content.
    tag = stripped[1:].split(">", 1)[0].split()
    return bool(tag) and tag[0].rstrip("/") in HARNESS_TAGS


def _text_blocks(content):
    """(text, tool_names) for one message's content."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    parts, tools = [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(block.get("text") or "")
        elif kind == "tool_use":
            tools.append(block.get("name") or "tool")
    return "\n".join(parts).strip(), tools


def read_turns(path, include_tools=False):
    """[{role, text, tools, ts}] for the readable half of a transcript."""
    turns = []
    try:
        with open(path, errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return turns
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Sidechains are subagent conversations, meta records are Claude Code talking
        # to itself. Neither is part of what was said.
        if record.get("isSidechain") or record.get("isMeta"):
            continue
        message = record.get("message") or {}
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text, tools = _text_blocks(message.get("content"))
        # A user record with no prose is a tool result being fed back, not a turn.
        if not text and not (include_tools and tools):
            continue
        if _is_harness(text):
            continue
        turns.append({"role": role, "text": text,
                      "tools": tools if include_tools else [],
                      "ts": record.get("timestamp")})
    return turns


def _stamp(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except (AttributeError, TypeError, ValueError):
        return ""


def render_markdown(turns, meta):
    lines = [f"# {meta['title']}", ""]
    lines += [f"- {label}: {value}" for label, value in _header_rows(meta)]
    lines.append("")
    for turn in turns:
        who = "You" if turn["role"] == "user" else "Claude"
        when = _stamp(turn["ts"])
        lines.append(f"## {who}" + (f"  <sub>{when}</sub>" if when else ""))
        lines.append("")
        if turn["text"]:
            lines += [turn["text"], ""]
        if turn["tools"]:
            lines += [f"> ran: {', '.join(turn['tools'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _header_rows(meta):
    rows = [("account", meta["account"]), ("directory", meta["dir"]),
            ("session", meta["id"])]
    if meta.get("exported_at"):
        rows.append(("exported", meta["exported_at"]))
    return rows


# Self-contained, because an export you cannot read without a network is not
# an archive. Colours track the reader's own light or dark setting.
_CSS = """
:root { color-scheme: light dark; --edge: #8883; --muted: #8888; }
body { max-width: 46rem; margin: 2rem auto; padding: 0 1rem;
       font: 16px/1.6 system-ui, sans-serif; }
header { border-bottom: 1px solid var(--edge); margin-bottom: 2rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0 1rem; }
dt { color: var(--muted); }
dd { margin: 0; }
section { border-top: 1px solid var(--edge); padding-top: 1rem; margin-top: 1.5rem; }
h2 { font-size: 1rem; margin: 0 0 .5rem; }
h2 .when { color: var(--muted); font-weight: normal; margin-left: .5rem; }
.user h2 { color: #d97757; }
pre { white-space: pre-wrap; word-wrap: break-word; margin: 0; font: inherit; }
.tools { color: var(--muted); font-size: .9em; margin-top: .5rem; }
"""


def render_html(turns, meta):
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>{html.escape(meta['title'])}</title>",
           f"<style>{_CSS}</style></head><body>",
           f"<header><h1>{html.escape(meta['title'])}</h1><dl>"]
    for label, value in _header_rows(meta):
        out.append(f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>")
    out.append("</dl></header>")
    for turn in turns:
        who = "You" if turn["role"] == "user" else "Claude"
        when = _stamp(turn["ts"])
        out.append(f'<section class="{turn["role"]}"><h2>{who}'
                   + (f'<span class="when">{when}</span>' if when else "") + "</h2>")
        if turn["text"]:
            out.append(f"<pre>{html.escape(turn['text'])}</pre>")
        if turn["tools"]:
            out.append(f'<p class="tools">ran: {html.escape(", ".join(turn["tools"]))}</p>')
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def render(row, fmt="md", include_tools=False):
    """One session as text, ready to write."""
    turns = read_turns(row["path"], include_tools)
    meta = {
        "title": row.get("title") or row["id"],
        "account": row["account"],
        "dir": row.get("dir") or row.get("real") or "",
        "id": row["id"],
        "exported_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    }
    return (render_html if fmt == "html" else render_markdown)(turns, meta)


def emit(row, fmt, out_path, include_tools):
    """Write one session's export to a file or to stdout."""
    import sys
    from pathlib import Path

    text = render(row, fmt, include_tools)
    if not out_path:
        sys.stdout.write(text)
        return 0
    Path(out_path).write_text(text)
    print(f"wrote {out_path}")
    return 0
