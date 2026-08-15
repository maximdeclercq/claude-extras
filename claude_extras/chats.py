"""Where a chat directory goes and what it is called."""

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import sessions
from .accounts import AccountError, read_routes

CHATS_DIR_NAME = "chats"
SLUG_MAX = 60
UNTITLED = "untitled"

# Seeded once per chats root. Global instructions are usually written for code
# work, and most of them are wrong here, where there is no repository, no test
# suite and nothing to ship. Everything else in them still applies.
ROOT_GUIDE = """# Chats

This directory holds conversations, not a codebase. Each subdirectory is one chat,
with its own attachments, artifacts and files.

Rules written for code work do not apply here:

- There is no build, no test suite and no linter to run.
- Nothing is being shipped, so there is no commit and no completion ritual.
- Files created here are working material for one conversation, not a project.

Everything else about how to answer still holds.
"""


def ensure_root_guide(root):
    """Write the chats guide once, and never overwrite an edited one."""
    guide = Path(root) / "CLAUDE.md"
    if not guide.exists():
        guide.write_text(ROOT_GUIDE)


def slugify(text, max_len=SLUG_MAX):
    """A directory-safe ASCII slug, cut at a word boundary."""
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if len(slug) <= max_len:
        return slug
    cut = slug[:max_len]
    head, _, _tail = cut.rpartition("-")
    return head if len(head) >= max_len // 2 else cut.rstrip("-")


def chat_dir_name(date, title, uuid="", suffix=""):
    """<date>-<slug>, or <date>-untitled-<suffix> when there is no title."""
    slug = slugify(title)
    if not slug:
        slug = f"{UNTITLED}-{suffix}" if suffix else UNTITLED
    name = f"{date}-{slug}"
    return f"{name}-{uuid[:4]}" if uuid else name


def chats_root(account):
    """The chats directory for an account, derived from its route."""
    if account in ("", "default"):
        paths = [path for path, _ in read_routes()]
        home = Path.home()
        if not paths:
            return home / CHATS_DIR_NAME
        common = Path(os.path.commonpath(paths)) if len(paths) > 1 else Path(paths[0]).parent
        # Routes spread across unrelated trees share only "/", which is nowhere to
        # keep chats, so fall back to ~/chats
        if common != home and home not in common.parents:
            return home / CHATS_DIR_NAME
        return common / CHATS_DIR_NAME
    paths = [path for path, name in read_routes() if name == account]
    if not paths:
        raise AccountError(
            f"account '{account}' has no route, so there is nowhere to keep its chats "
            f"(bind one with: cd <project> && claude auth login {account})"
        )
    named = [p for p in paths if os.path.basename(p) == account]
    root = min(named or paths, key=len)
    return Path(root) / CHATS_DIR_NAME


def transcripts_for(config_dir, chat_dir):
    """Every transcript Claude Code has written for a chat directory."""
    project = Path(config_dir) / "projects" / sessions.encode_cwd(chat_dir)
    return sorted(project.glob("*.jsonl")) if project.is_dir() else []


def chat_title(transcripts):
    """The name a chat should carry, by the same rule the listing displays."""
    title = next((t for t in (read_ai_title(p) for p in transcripts) if t), None)
    if title or not transcripts:
        return title
    # Earliest last-activity is the session that opened the chat. Which one supplies
    # the prompt only matters here, where every one of them has one to offer.
    opener = min(transcripts, key=lambda p: sessions.last_activity(p) or float("inf"))
    meta = sessions.read_meta(opener)
    return meta["title"] if meta else None


def read_ai_title(path, scan_lines=300):
    """The title Claude Code generated for a session, if it has written one yet."""
    try:
        with open(path, errors="replace") as fh:
            for index, line in enumerate(fh):
                if index >= scan_lines:
                    return None
                line = line.strip()
                if not line or '"ai-title"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "ai-title" and record.get("aiTitle"):
                    return record["aiTitle"]
    except OSError:
        return None
    return None


def _own_chain():
    """This process and every ancestor, so a hook does not count its own session."""
    pids, pid = set(), os.getpid()
    while pid > 1:
        pids.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        # The command name can hold spaces, so the fixed fields start after its ")".
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return pids


def _session_names():
    """What a running Claude Code shows as its command name."""
    from .cli import find_real_bin

    found = find_real_bin()
    return {"claude", Path(found).name} if found else {"claude"}


def is_live(directory):
    """True when a Claude Code session other than our own is running in the directory.

    Only a session matters, because only a session writes a transcript under a
    path derived from its cwd; a browser or shell that inherited the directory
    follows a rename by inode and is unaffected. Our own chain is excluded because
    the SessionEnd hook runs inside the session that is ending, in its directory.
    """
    directory = str(directory)
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return True
    own, names = _own_chain(), _session_names()
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) in own:
            continue
        try:
            if (entry / "comm").read_text().strip() not in names:
                continue
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if cwd == directory or cwd.startswith(directory + os.sep):
            return True
    return False


def _rewrite_cwd(path, old, new):
    """Point every record in a transcript at the chat's new directory."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return
    stat = os.stat(path)
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if record.get("cwd") == old:
            record["cwd"] = new
        out.append(json.dumps(record))
    tmp = Path(str(path) + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.replace(tmp, path)
    os.utime(path, (stat.st_atime, stat.st_mtime))


def move_project_entry(config_dir, old_dir, new_dir):
    """Carry a directory's per-project state across a rename."""
    path = Path(config_dir) / ".claude.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    projects = data.get("projects")
    if not isinstance(projects, dict) or str(old_dir) not in projects:
        return
    projects[str(new_dir)] = projects.pop(str(old_dir))
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def rename_chat(config_dir, old_dir, new_name):
    """Move a chat directory, its transcript directory, and the cwd inside it."""
    old_dir = Path(old_dir)
    new_dir = old_dir.parent / new_name
    if new_dir == old_dir:
        return old_dir

    projects = Path(config_dir) / "projects"
    old_project = projects / sessions.encode_cwd(old_dir)
    new_project = projects / sessions.encode_cwd(new_dir)

    # Both destinations are checked before either move, because a half-done rename
    # leaves a chat whose transcript is under the old name and therefore invisible.
    if new_dir.exists():
        raise AccountError(f"cannot rename chat: {new_dir} already exists")
    if old_project.is_dir() and new_project.exists():
        raise AccountError(f"cannot rename chat: {new_project} already exists")

    old_dir.rename(new_dir)
    if old_project.is_dir():
        old_project.rename(new_project)
        for transcript in new_project.glob("*.jsonl"):
            _rewrite_cwd(transcript, str(old_dir), str(new_dir))
    move_project_entry(config_dir, old_dir, new_dir)
    return new_dir


def is_untitled(name):
    """True only for a name this module generated as a placeholder."""
    return re.match(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{UNTITLED}(-[0-9a-f]+)?$", name) is not None


def tidy(account, config_dir):
    """Rename settled untitled chats, and drop ones abandoned without a word."""
    try:
        root = chats_root(account)
    except AccountError:
        return []
    if not root.is_dir():
        return []
    renamed = []
    for chat_dir in sorted(root.iterdir()):
        if not chat_dir.is_dir() or not is_untitled(chat_dir.name):
            continue
        transcripts = transcripts_for(config_dir, chat_dir)
        # Opening a chat and closing it without saying anything leaves a directory
        # with nothing in it and no transcript. Nobody wants that kept.
        if not transcripts and not any(chat_dir.iterdir()) and not is_live(chat_dir):
            try:
                chat_dir.rmdir()
            except OSError:
                pass
            continue
        title = chat_title(transcripts)
        if not title or is_live(chat_dir):
            continue
        date = chat_dir.name[:10]
        # A title that slugifies to nothing, or to the placeholder word, would be
        # renamed again next pass and flip between two names forever
        target_name = chat_dir_name(date, title)
        if is_untitled(target_name):
            continue
        target = chat_dir.parent / target_name
        uuid = transcripts[0].stem if transcripts else ""
        if target.exists():
            target = chat_dir.parent / chat_dir_name(date, title, uuid=uuid)
        try:
            renamed.append((chat_dir, rename_chat(config_dir, chat_dir, target.name)))
        except (AccountError, OSError):
            continue
    return renamed


def new_chat_dir(account, description="", now=None):
    """Create and return the directory for a new chat."""
    now = now or datetime.now().astimezone()
    date = now.strftime("%Y-%m-%d")
    root = chats_root(account)
    root.mkdir(parents=True, exist_ok=True)
    ensure_root_guide(root)
    target = root / chat_dir_name(date, description, suffix=now.strftime("%H%M"))
    if target.exists() and not description:
        target = root / chat_dir_name(date, "", suffix=now.strftime("%H%M%S"))
    target.mkdir(parents=True, exist_ok=True)
    return target
