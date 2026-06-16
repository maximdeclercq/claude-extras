"""Incremental transcript cache, keyed on size and mtime.

Keyed on mtime rather than a date watermark because resuming an old session
rewrites a file that sits below any watermark.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .parser import Turn, iter_file_turns

CACHE_VERSION = 1
CACHE_NAME = "usage-cache.json"

# Row layout, kept positional to hold the cache to a workable size.
_TS, _MODEL, _CWD, _SESSION, _IN, _OUT, _CR, _CW5, _CW1, _SIDE, _KEY = range(11)


def _path(config_dir):
    return Path(config_dir) / CACHE_NAME


def load(config_dir):
    """Return the stored cache, or an empty one if absent, unreadable, or stale."""
    try:
        data = json.loads(_path(config_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "files": {}}
    if data.get("version") != CACHE_VERSION or not isinstance(data.get("files"), dict):
        return {"version": CACHE_VERSION, "files": {}}
    return data


def save(config_dir, cache):
    """Write the cache atomically. A cache is an optimisation, so failure to
    persist is never fatal: the next run simply rebuilds."""
    path = _path(config_dir)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        with tmp.open("w") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _encode(path):
    """Parse one transcript into a cache entry (string tables plus rows)."""
    models, cwds, sessions = {}, {}, {}
    rows = []

    def idx(table, value):
        if value not in table:
            table[value] = len(table)
        return table[value]

    for key, turn in iter_file_turns(path):
        rows.append([
            int(turn.ts.timestamp()),
            idx(models, turn.model),
            idx(cwds, turn.cwd),
            idx(sessions, turn.session),
            turn.input, turn.output, turn.cache_read,
            turn.cache_write_5m, turn.cache_write_1h,
            1 if turn.is_sidechain else 0,
            key,
        ])
    return {
        "models": list(models),
        "cwds": list(cwds),
        "sessions": list(sessions),
        "rows": rows,
    }


def refresh(config_dir):
    """Bring the cache in line with what is on disk and return it."""
    config_dir = Path(config_dir)
    cache = load(config_dir)
    files = cache["files"]
    root = config_dir / "projects"

    present, dirty = set(), False
    for path in sorted(root.glob("**/*.jsonl")):
        rel = str(path.relative_to(config_dir))
        present.add(rel)
        try:
            st = path.stat()
        except OSError:
            continue
        entry = files.get(rel)
        if entry and entry.get("m") == st.st_mtime and entry.get("s") == st.st_size:
            continue
        files[rel] = {"m": st.st_mtime, "s": st.st_size, **_encode(path)}
        dirty = True

    for rel in set(files) - present:
        del files[rel]
        dirty = True

    if dirty:
        save(config_dir, cache)
    return cache


def load_turns(config_dir, since=None):
    """Deduplicated Turns for a config dir, sorted by timestamp."""
    cache = refresh(config_dir)
    cutoff = since.timestamp() if since is not None else None
    seen = set()
    turns = []

    for rel in sorted(cache["files"]):
        entry = cache["files"][rel]
        models, cwds, sessions = entry["models"], entry["cwds"], entry["sessions"]
        for row in entry["rows"]:
            key = row[_KEY]
            if key:
                if key in seen:
                    continue
                seen.add(key)
            if cutoff is not None and row[_TS] < cutoff:
                continue
            turns.append(Turn(
                ts=datetime.fromtimestamp(row[_TS], tz=UTC),
                model=models[row[_MODEL]],
                is_sidechain=bool(row[_SIDE]),
                input=row[_IN],
                output=row[_OUT],
                cache_read=row[_CR],
                cache_write_5m=row[_CW5],
                cache_write_1h=row[_CW1],
                cwd=cwds[row[_CWD]],
                session=sessions[row[_SESSION]],
            ))

    turns.sort(key=lambda t: t.ts)
    return turns
