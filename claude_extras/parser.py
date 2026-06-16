"""Parse Claude Code JSONL transcripts into deduplicated token usage."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Turn:
    ts: datetime  # tz-aware (UTC)
    model: str
    is_sidechain: bool
    input: int
    output: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    cwd: str = ""
    session: str = ""


@dataclass
class Bucket:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    turns: int = 0
    sidechain_turns: int = 0

    @property
    def cache_write(self):
        return self.cache_write_5m + self.cache_write_1h

    @property
    def total(self):
        return self.input + self.output + self.cache_read + self.cache_write

    def add(self, turn):
        self.input += turn.input
        self.output += turn.output
        self.cache_read += turn.cache_read
        self.cache_write_5m += turn.cache_write_5m
        self.cache_write_1h += turn.cache_write_1h
        self.turns += 1
        if turn.is_sidechain:
            self.sidechain_turns += 1


def _parse_ts(value):
    # fromisoformat has handled a trailing Z since 3.11, which is the floor here.
    return datetime.fromisoformat(value)


def _turn_from_record(rec):
    msg = rec.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    ts_raw = rec.get("timestamp")
    if not ts_raw:
        return None
    creation = usage.get("cache_creation") or {}
    write_5m = creation.get("ephemeral_5m_input_tokens")
    write_1h = creation.get("ephemeral_1h_input_tokens", 0)
    if write_5m is None and not write_1h:
        # older records only carry the flat total; treat it as 5m (the default ttl)
        write_5m = usage.get("cache_creation_input_tokens", 0)
    return Turn(
        ts=_parse_ts(ts_raw),
        model=msg.get("model") or "unknown",
        is_sidechain=bool(rec.get("isSidechain")),
        input=usage.get("input_tokens", 0) or 0,
        output=usage.get("output_tokens", 0) or 0,
        cache_read=usage.get("cache_read_input_tokens", 0) or 0,
        cache_write_5m=write_5m or 0,
        cache_write_1h=write_1h or 0,
        cwd=rec.get("cwd") or "",
        session=rec.get("sessionId") or "",
    )


def iter_file_turns(path):
    """Yield (dedup_key, Turn) for one transcript, deduped within that file."""
    seen = set()
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        if (msg.get("model") or "") == "<synthetic>":
            continue
        mid, rid = msg.get("id"), rec.get("requestId")
        key = "" if (mid is None and rid is None) else f"{mid}|{rid}"
        if key:
            if key in seen:
                continue
            seen.add(key)
        turn = _turn_from_record(rec)
        if turn is not None:
            yield key, turn


def iter_turns(config_dir):
    """Yield deduplicated assistant Turns from <config_dir>/projects/**/*.jsonl."""
    seen = set()
    root = Path(config_dir) / "projects"
    for path in sorted(root.glob("**/*.jsonl")):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            if (msg.get("model") or "") == "<synthetic>":
                continue
            key = (msg.get("id"), rec.get("requestId"))
            if key != (None, None):
                if key in seen:
                    continue
                seen.add(key)
            turn = _turn_from_record(rec)
            if turn is not None:
                yield turn


def load_turns(config_dir, since=None):
    """List of Turns, optionally filtered to ts >= `since` (tz-aware datetime)."""
    turns = list(iter_turns(config_dir))
    if since is not None:
        turns = [t for t in turns if t.ts >= since]
    turns.sort(key=lambda t: t.ts)
    return turns


def bucket(turns):
    b = Bucket()
    for turn in turns:
        b.add(turn)
    return b


def by_model(turns):
    out = {}
    for turn in turns:
        out.setdefault(turn.model, Bucket()).add(turn)
    return out
