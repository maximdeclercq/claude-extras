"""The session layer that `resume`, `search` and `chat` share."""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from .accounts import AccountError

TITLE_SCAN_LINES = 300  # titles are generated early; bound reads on huge transcripts
TAIL_BYTES = 65536  # enough to hold the last few records of any transcript
HOME = str(Path.home())
DEFAULT_BRANCHES = {"HEAD", "main", "master", "-"}
DIR_WIDTH = 28
TITLE_WIDTH = 46
DEFAULT_LIMIT = 10

# Stamped on a session started for a terminal; every other launch mode is sdk-cli.
INTERACTIVE_ENTRYPOINT = "cli"
TYPED_PROMPT = "typed"


def _epoch(stamp):
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def last_activity(path):
    """When something was last said in a session, or None if nothing was."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            if fh.tell() > TAIL_BYTES:
                fh.seek(-TAIL_BYTES, 2)
                fh.readline()  # drop the partial line the seek landed inside
            else:
                fh.seek(0)
            chunk = fh.read()
    except OSError:
        return None
    for line in reversed(chunk.splitlines()):
        if b'"timestamp"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") in ("user", "assistant") and not rec.get("isMeta"):
            when = _epoch(rec.get("timestamp"))
            if when:
                return when
    return None


def read_meta(path):
    cwd = branch = title = typed = first_prompt = entrypoint = None
    reconstructed = False
    try:
        with open(path, errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= TITLE_SCAN_LINES and cwd:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entrypoint is None:
                    entrypoint = rec.get("entrypoint")
                if rec.get("isReconstructed"):
                    reconstructed = True
                if cwd is None:
                    cwd = rec.get("cwd")
                if branch is None:
                    branch = rec.get("gitBranch")
                if not title and rec.get("aiTitle"):
                    title = rec["aiTitle"]
                text = _user_text(rec.get("message") or {})
                if text is None:
                    continue
                # Hook-injected and Claude Code's own text arrive as user messages too
                if typed is None and rec.get("promptSource") == TYPED_PROMPT:
                    typed = text
                if first_prompt is None and not rec.get("isMeta"):
                    first_prompt = text
    except OSError:
        return None
    if not title:
        # first_prompt carries transcripts written before promptSource existed.
        title = (typed or first_prompt or "").strip().replace("\n", " ")[:50] or "(untitled)"
    # A reconstruction holds recovered prompts and no replies, so nothing to reopen.
    return {"cwd": cwd, "branch": branch, "title": title,
            "is_resumable": entrypoint == INTERACTIVE_ENTRYPOINT and not reconstructed}


def _user_text(msg):
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                text = blk.get("text")
                break
    # skip the wrappers Claude Code injects (caveats, command tags, reminders)
    if not text or text.lstrip().startswith("<"):
        return None
    return text


def load_logical_map(log_path):
    """resolved-path -> logical-path, latest wins, from the wrapper's launch log."""
    out = {}
    if log_path and Path(log_path).is_file():
        for line in Path(log_path).read_text(errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and parts[2] and parts[3]:
                out[parts[2]] = parts[3]
    return out


def session_row(name, config_dir, path, mtime, logical):
    """One listing row, or None when the transcript is not a listable session."""
    meta = read_meta(path)
    if not meta or not meta["cwd"] or not meta["is_resumable"]:
        return None
    return {
        "account": name,
        "config_dir": str(config_dir),
        "id": path.stem,
        # The transcript itself, so a caller that wants to read it does not have
        # to re-derive the encoded directory name it lives under.
        "path": str(path),
        # A session with nothing said in it has only its file to date it.
        "activity": last_activity(path) or mtime,
        "real": meta["cwd"],
        "dir": logical.get(meta["cwd"], meta["cwd"]),
        "branch": meta["branch"] or "-",
        "title": meta["title"],
    }


def encode_cwd(path):
    """A working directory as Claude Code names its transcript directory."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(path))


def transcripts(config_dir, under=None):
    """Session transcripts of one config dir as (mtime, path), newest file first.

    A session sits exactly one level below projects/; anything deeper belongs to a
    subagent. Everything under a directory shares a name prefix, so filtering on
    that first means only plausible transcripts get opened.
    """
    pattern = f"{encode_cwd(under)}*/*.jsonl" if under else "*/*.jsonl"
    files = []
    for path in (Path(config_dir) / "projects").glob(pattern):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    files.sort(reverse=True)
    return files


def sessions_for(name, config_dir, logical, want, under=None):
    """Recent interactive sessions for one account, newest first."""
    out = []
    for mtime, path in transcripts(config_dir, under):
        # mtime bounds when anything was last said, so once `want` rows are held a
        # file that cannot beat the weakest of them ends the scan, and so does
        # everything older behind it
        if want is not None and len(out) >= want and mtime <= min(r["activity"] for r in out):
            break
        row = session_row(name, config_dir, path, mtime, logical)
        if row is None:
            continue
        if under and not (row["real"] == under or row["real"].startswith(under + "/")):
            continue
        out.append(row)
        if want is not None and len(out) > want:
            out.sort(key=lambda r: r["activity"], reverse=True)
            del out[want:]
    return out


def age(when, now):
    secs = max(0, int(now - when))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _tilde(path):
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


def _dir_label(path):
    """Last two components, which stay unique in practice where a full path does not fit."""
    parts = _tilde(path).split("/")
    label = "/".join(parts[-2:]) if len(parts) > 2 else _tilde(path)
    return label if len(label) <= DIR_WIDTH else "..." + label[-(DIR_WIDTH - 3):]


def scope_label(rows):
    """What a listing covers, for its heading."""
    accounts = {r["account"] for r in rows}
    return "all accounts" if len(accounts) > 1 else accounts.pop()


def render(rows, now, heading=None, resume="claude resume <#>"):
    """Rendered through the shared table helper so column behaviour matches the
    usage views; the leading index is left-aligned there, hence the manual pad."""
    from .report import table

    if not rows:
        return "no sessions found"
    head = heading or f"recent sessions  {scope_label(rows)}"
    headers = ["#", "age", "account", "directory", "title", "branch"]
    body = []
    for i, r in enumerate(rows, 1):
        # a branch nobody switched to says nothing; printing it buries the ones that do
        branch = "" if r["branch"] in DEFAULT_BRANCHES else r["branch"]
        body.append([f"{i:>3}", age(r["activity"], now), r["account"],
                     _dir_label(r["dir"]), r["title"][:TITLE_WIDTH], branch])
    return "\n".join([head, *table(headers, body, elide=("account",)), "", f"resume:  {resume}"])


def resume(row):
    """Pick a session back up, in this terminal or in a new one."""
    from . import spawn

    if not Path(row["real"]).is_dir():
        raise AccountError(f"session directory is gone: {row['real']}")
    if not sys.stdout.isatty():
        spawn.spawn(row["real"], ["--resume", row["id"]], account=row["account"])
        return 0
    from .cli import real_bin

    os.environ["CLAUDE_CONFIG_DIR"] = row["config_dir"]
    os.chdir(row["real"])
    os.environ["PWD"] = row["real"]
    binary = real_bin()
    os.execv(binary, [binary, "--resume", row["id"]])
