"""Build usage data models and render them as text."""

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from . import parser, pricing

FIVE_H = timedelta(hours=5)
SEVEN_D = timedelta(days=7)
STALE_AFTER = 5 * 3600  # seconds; a 5h snapshot older than its own window is suspect


def _ts(dt):
    return None if dt is None else int(dt.timestamp())


def _rate(rc, key):
    """(used_percentage, resets_at datetime|None) from a cached snapshot."""
    win = (rc or {}).get(key) or {}
    resets = win.get("resets_at")
    resets_dt = datetime.fromtimestamp(resets, tz=UTC) if resets else None
    return win.get("used_percentage"), resets_dt


def _cost_of(turns):
    known, unpriced = 0.0, 0
    for model, bucket in parser.by_model(turns).items():
        value = pricing.cost(bucket, model)
        if value is None:
            unpriced += bucket.turns
        else:
            known += value
    return known, unpriced


def session_time(turns):
    """(session count, wall-clock seconds) for a set of turns."""
    spans = defaultdict(lambda: [None, None])
    for turn in turns:
        if not turn.session:
            continue
        span = spans[turn.session]
        stamp = turn.ts.timestamp()
        span[0] = stamp if span[0] is None else min(span[0], stamp)
        span[1] = stamp if span[1] is None else max(span[1], stamp)

    total, open_lo, open_hi = 0.0, None, None
    for lo, hi in sorted(spans.values()):
        if open_hi is None or lo > open_hi:
            if open_hi is not None:
                total += open_hi - open_lo
            open_lo, open_hi = lo, hi
        else:
            open_hi = max(open_hi, hi)
    if open_hi is not None:
        total += open_hi - open_lo
    return len(spans), int(total)


def _summ(turns):
    bucket = parser.bucket(turns)
    known, unpriced = _cost_of(turns)
    sessions, seconds = session_time(turns)
    return {
        "turns": bucket.turns,
        "sidechain_turns": bucket.sidechain_turns,
        "sessions": sessions,
        "seconds": seconds,
        "tokens": {
            "input": bucket.input,
            "output": bucket.output,
            "cache_read": bucket.cache_read,
            "cache_write": bucket.cache_write,
            "total": bucket.total,
        },
        "est_cost_usd": round(known, 4),
        "unpriced_turns": unpriced,
    }


def _burn(turns, reset, now):
    """Token rate over the 5h window, and where that rate lands by its end."""
    bucket = parser.bucket(turns)
    tpm = bucket.total / (FIVE_H.total_seconds() / 60)
    out = {"tokens_per_min": round(tpm)}
    known, _ = _cost_of(turns)
    if reset is not None and reset > now:
        remaining = (reset - now).total_seconds() / 60
        projected = bucket.total + tpm * remaining
        out["remaining_min"] = round(remaining)
        out["projected_total_tokens"] = round(projected)
        if bucket.total > 0:
            out["projected_cost_usd"] = round(known * projected / bucket.total, 2)
    return out


def build_subscriptions(accounts, now):
    """One plan block per subscription."""
    from . import identity

    out = []
    for sub in identity.group([(a["name"], a["identity"]) for a in accounts]):
        members = [a for a in accounts if a["name"] in sub["accounts"]]
        best = max(
            (a for a in members if a["rc"]),
            key=lambda a: a["rc"].get("captured_at") or 0,
            default=None,
        )
        rc = best["rc"] if best else None
        captured = (rc or {}).get("captured_at")
        f_pct, f_reset = _rate(rc, "five_hour")
        s_pct, s_reset = _rate(rc, "seven_day")
        out.append({
            "tier": sub["tier"],
            "accounts": sub["accounts"],
            "logins": sub["logins"],
            "captured_at": captured,
            "stale": bool(captured and int(now.timestamp()) - captured > STALE_AFTER),
            "five_hour": {"used_percentage": f_pct, "resets_at": _ts(f_reset)},
            "seven_day": {"used_percentage": s_pct, "resets_at": _ts(s_reset)},
        })
    return out


def build(accounts, now, since=None):
    """Full model, with one plan block per subscription and attribution per account."""
    start = since or now - SEVEN_D
    rows, combined = [], []
    for account in accounts:
        recent = [t for t in account["turns"] if t.ts >= start]
        combined += recent
        rows.append({"account": account["name"], **_summ(recent)})
    # Summing per-account time would double-count sessions that overlap across
    # accounts, so the total is measured over the union of everything.
    return {
        "generated_at": int(now.timestamp()),
        "since": int(start.timestamp()),
        "window_days": round((now - start).total_seconds() / 86400, 2),
        "subscriptions": build_subscriptions(accounts, now),
        "accounts": rows,
        "totals": _summ(combined),
    }


def build_account(account, now, peers=()):
    """Detail model for a single account."""
    turns = account["turns"]
    rc = account["rc"]
    f_pct, f_reset = _rate(rc, "five_hour")
    s_pct, s_reset = _rate(rc, "seven_day")

    five = [t for t in turns if t.ts >= now - FIVE_H]
    seven = [t for t in turns if t.ts >= now - SEVEN_D]
    today_date = now.astimezone().date()
    today = [t for t in turns if t.ts.astimezone().date() == today_date]

    model_rows = []
    for model, bucket in sorted(parser.by_model(seven).items(), key=lambda kv: -kv[1].total):
        value = pricing.cost(bucket, model)
        model_rows.append({
            "model": model,
            "total_tokens": bucket.total,
            "est_cost_usd": None if value is None else round(value, 4),
        })

    subs = build_subscriptions([account, *peers], now)
    own = [s for s in subs if account["name"] in s["accounts"]] or subs
    return {
        "account": account["name"],
        "identity": account["identity"],
        "generated_at": int(now.timestamp()),
        "subscriptions": own,
        "windows": {
            "today": _summ(today),
            "5h": {**_summ(five), "used_percentage": f_pct, "resets_at": _ts(f_reset)},
            "7d": {**_summ(seven), "used_percentage": s_pct, "resets_at": _ts(s_reset)},
            "total": _summ(turns),
        },
        "by_model_7d": model_rows,
        "burn": _burn(five, f_reset, now),
    }


def _period_key(turn, period):
    local = turn.ts.astimezone()
    if period == "weekly":
        iso = local.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "monthly":
        return local.strftime("%Y-%m")
    return local.date().isoformat()


def group_period(turns, period):
    groups = defaultdict(list)
    for turn in turns:
        groups[_period_key(turn, period)].append(turn)
    return [{"key": key, **_summ(group)} for key, group in sorted(groups.items())]


def group_projects(turns):
    groups = defaultdict(list)
    for turn in turns:
        groups[turn.cwd or "(unknown)"].append(turn)
    rows = [{"project": key, **_summ(group)} for key, group in groups.items()]
    return sorted(rows, key=lambda r: -r["tokens"]["total"])


def human(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def money(x):
    if x is None:
        return "unpriced"
    if 0 < x < 1:
        return "<$1"
    return f"${round(x)}"


def duration(seconds):
    if not seconds:
        return "-"
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours}h" if hours else f"{minutes}m"


def _color(text, pct):
    code = 31 if int(pct) >= 90 else 33 if int(pct) >= 70 else 32
    return f"\033[{code}m{text}\033[0m"


def color_pct(pct, use_color, width=4):
    text = ("-" if pct is None else f"{int(pct)}%").rjust(width)
    if not use_color or pct is None:
        return text
    return _color(text, pct)


def bar(pct, use_color, width=22):
    if pct is None:
        return "[" + "-" * width + "]"
    filled = max(0, min(width, round(pct / 100 * width)))
    run = "#" * filled
    if use_color:
        run = _color(run, pct)
    return "[" + run + "-" * (width - filled) + "]"


# Counts, sizes, money, durations and percentages, plus the placeholders that
# stand in for them. A column of these reads better flush right; anything else
# is prose and reads better flush left.
_NUMERIC = re.compile(r"^[-+]?\$?[\d.,]+[%kKMBhmsd]?$")
_PLACEHOLDERS = {"", "-", "unpriced", "<$1"}


def table(headers, rows, indent="  ", elide=()):
    """Render aligned columns. A column named in elide is dropped when every cell
    in it is the same, since a value that never varies says nothing."""
    cells = [[str(c) for c in row] for row in rows]
    keep = [i for i, name in enumerate(headers)
            if not (name in elide and cells and len({row[i] for row in cells}) == 1)]
    headers = [headers[i] for i in keep]
    cells = [[row[i] for i in keep] for row in cells]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _visible_len(cell))

    right = [all(c[i] in _PLACEHOLDERS or _NUMERIC.match(c[i]) for c in cells)
             for i in range(len(headers))]

    def line(values):
        out = []
        for i, value in enumerate(values):
            pad = widths[i] - _visible_len(value)
            out.append(" " * pad + value if right[i] else value + " " * pad)
        return indent + "  ".join(out).rstrip()

    return [line(headers)] + [line(row) for row in cells]


_ANSI = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text):
    return len(_ANSI.sub("", text))


def _short_model(name):
    name = name.removeprefix("claude-")
    return re.sub(r"-\d{8}$", "", name)


def _hhmm(ts):
    return "-" if not ts else _local(ts).strftime("%H:%M")


def _dow(ts):
    return "-" if not ts else _local(ts).strftime("%a %H:%M")


def _local(ts):
    return datetime.fromtimestamp(ts, tz=UTC).astimezone()


def render_subscriptions(subs, use_color):
    """Plan blocks. One per subscription, never one per account."""
    lines = []
    for sub in subs:
        asof = _hhmm(sub["captured_at"]) if sub["captured_at"] else "never"
        stale = " (stale)" if sub["stale"] else ""
        # Stable order regardless of which account was asked about, so the same
        # subscription reads the same way in every view.
        who = ", ".join(sorted(sub["accounts"], key=lambda n: (n != "default", n)))
        tier = sub["tier"] or "unknown tier"
        shared = f"  {sub['logins']} logins" if sub["logins"] > 1 else ""
        lines.append(f"plan  {tier}  {who}{shared}   as of {asof}{stale}")
        for label, key, fmt in (("5h", "five_hour", _hhmm), ("7d", "seven_day", _dow)):
            win = sub[key]
            resets = fmt(win["resets_at"])
            tail = f"  resets {resets}" if win["resets_at"] else ""
            lines.append(f"  {label:<4}{bar(win['used_percentage'], use_color)} "
                         f"{color_pct(win['used_percentage'], use_color)}{tail}")
        lines.append("")
    return lines


def render_summary(model, use_color):
    """Default view: plan per subscription, then attribution per account."""
    lines = render_subscriptions(model["subscriptions"], use_color)
    # The table covers seven days unless --since moved it, and a column of totals
    # that silently spans something else is the one thing worth saying out loud.
    if abs(model["window_days"] - 7) > 0.01:
        lines += [f"since {_local(model['since']).date()}", ""]
    rows = [[row["account"], row["sessions"], duration(row["seconds"]),
             human(row["tokens"]["total"]), money(row["est_cost_usd"])]
            for row in model["accounts"]]
    totals = model["totals"]
    rows.append(["total", totals["sessions"], duration(totals["seconds"]),
                 human(totals["tokens"]["total"]), money(totals["est_cost_usd"])])
    lines += table(["account", "sessions", "time", "tokens", "est cost"], rows)
    return "\n".join(lines)


def render_account(model, use_color):
    """Single account: its subscription, then its own windows, split and models."""
    lines = render_subscriptions(model["subscriptions"], use_color)
    w = model["windows"]

    rows = []
    for label in ("today", "5h", "7d", "total"):
        win = w[label]
        note = f"+{win['unpriced_turns']} unpriced" if win["unpriced_turns"] else ""
        rows.append([label, duration(win["seconds"]), human(win["tokens"]["total"]),
                     money(win["est_cost_usd"]), note])
    lines += table(["window", "time", "tokens", "est cost", ""], rows)

    tok = w["5h"]["tokens"]
    lines += [""] + table(
        ["5h split", "tokens"],
        [["input", human(tok["input"])], ["output", human(tok["output"])],
         ["cache read", human(tok["cache_read"])], ["cache write", human(tok["cache_write"])]],
    )

    burn = model["burn"]
    if burn and burn.get("tokens_per_min"):
        text = f"burn  {human(burn['tokens_per_min'])}/min"
        if "projected_total_tokens" in burn:
            text += (f"  ->  ~{human(burn['projected_total_tokens'])} by "
                     f"{_hhmm(w['5h']['resets_at'])} ({burn['remaining_min']}m left)")
        lines += ["", text]

    if model["by_model_7d"]:
        rows = [[_short_model(r["model"]), human(r["total_tokens"]), money(r["est_cost_usd"])]
                for r in model["by_model_7d"][:6]]
        lines += [""] + table(["model", "7d tokens", "est cost"], rows)
    return "\n".join(lines)


def render_period(view, rows, account=None):
    head = f"{view} usage" + (f"  {account}" if account else "")
    if not rows:
        return head + "\n  (no usage found)"
    body = [[r["key"], r["sessions"], duration(r["seconds"]), r["turns"],
             human(r["tokens"]["total"]), money(r["est_cost_usd"])] for r in rows]
    return "\n".join([head] + table(
        ["period", "sessions", "time", "turns", "tokens", "est cost"], body))


def render_projects(rows, account=None, limit=20):
    head = "projects by tokens" + (f"  {account}" if account else "")
    if not rows:
        return head + "\n  (no usage found)"
    body = [[r["project"], r["sessions"], duration(r["seconds"]),
             human(r["tokens"]["total"]), money(r["est_cost_usd"])] for r in rows[:limit]]
    lines = [head] + table(["project", "sessions", "time", "tokens", "est cost"], body)
    if len(rows) > limit:
        lines.append(f"  ... and {len(rows) - limit} more")
    return "\n".join(lines)
