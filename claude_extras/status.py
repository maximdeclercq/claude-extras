"""The `claude status` verb, which is both the line under the prompt and its cache.

The 5h and 7d plan-burn percentages exist nowhere else on disk, so the side
effect of caching them is what lets `claude usage` report them off-session.
"""

import json
import os
import sys
import time
from datetime import UTC, datetime

# Burn levels worth noticing. Anything below the first is not worth colouring.
SNAPSHOT = "ratelimit.json"
WARN_PCT, CRIT_PCT = 70, 90
YELLOW, RED, RESET = "\033[33m", "\033[31m", "\033[0m"


def snapshot(payload, now):
    """The rate-limit facts worth keeping, or None when there are none."""
    limits = payload.get("rate_limits")
    if not limits:
        return None
    return {
        "captured_at": int(now),
        "five_hour": _window(limits.get("five_hour")),
        "seven_day": _window(limits.get("seven_day")),
    }


def _window(window):
    window = window or {}
    return {"used_percentage": window.get("used_percentage"),
            "resets_at": window.get("resets_at")}


def write_snapshot(config_dir, snap):
    """Cache the snapshot, but only when a percentage actually moved."""
    target = os.path.join(config_dir, SNAPSHOT)
    fresh = {k: v for k, v in snap.items() if k != "captured_at"}
    try:
        with open(target) as fh:
            stored = json.load(fh)
        if {k: v for k, v in stored.items() if k != "captured_at"} == fresh:
            return
    except (OSError, ValueError):
        pass

    tmp = os.path.join(config_dir, f".ratelimit.tmp.{os.getpid()}")
    try:
        with open(tmp, "w") as fh:
            json.dump(snap, fh)
        os.replace(tmp, target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_snapshot(config_dir):
    """The cached plan-burn snapshot, or None when there is none to read."""
    try:
        with open(os.path.join(config_dir, SNAPSHOT)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def percent(value, use_color=True):
    if value is None:
        return "-"
    try:
        whole = int(float(value))
    except (TypeError, ValueError):
        return "-"
    text = f"{whole}%"
    if not use_color:
        return text
    if whole >= CRIT_PCT:
        return f"{RED}{text}{RESET}"
    if whole >= WARN_PCT:
        return f"{YELLOW}{text}{RESET}"
    return text


def account_tag(config_dir):
    """The account name a config dir belongs to, for the tail of the line."""
    marker = os.sep + "accounts" + os.sep
    if config_dir and marker in config_dir:
        return config_dir.rsplit(marker, 1)[1].split(os.sep)[0]
    return "default"


def _clock(stamp):
    """A reset time as the wall clock the reader is looking at."""
    try:
        return datetime.fromtimestamp(int(stamp), UTC).astimezone().strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return None


def render(payload, config_dir, use_color=True):
    """The one line Claude Code prints under the prompt."""
    model = payload.get("model") or {}
    name = model.get("display_name") or model.get("id") or "?"
    effort = (payload.get("effort") or {}).get("level") or "-"
    context = (payload.get("context_window") or {}).get("used_percentage")
    limits = payload.get("rate_limits") or {}
    five, seven = limits.get("five_hour") or {}, limits.get("seven_day") or {}

    resets = _clock(five.get("resets_at"))
    tail = f" (resets {resets})" if resets else ""
    return (f"{name} {effort} | ctx {percent(context, use_color)} | "
            f"5h {percent(five.get('used_percentage'), use_color)}{tail} | "
            f"7d {percent(seven.get('used_percentage'), use_color)} | "
            f"{account_tag(config_dir)}")


HELP = """Usage: claude status

Show this account's plan burn, and render the status line for Claude Code

Options:
  -h, --help              Display help for command

Typed at a terminal it prints the last cached burn for the account you are in.
Piped a statusline payload it renders the line under the prompt and refreshes
that cache, which is what wires it into settings.json:

  { "statusLine": { "type": "command", "command": "claude status",
                    "refreshInterval": 5 } }

refreshInterval keeps the numbers fresh between messages, which is what lets
this and `claude usage` report them when no session is running.
"""


def render_cached(config_dir):
    """The burn line from the cached snapshot, for someone who typed the command."""
    snap = read_snapshot(config_dir)
    if snap is None:
        return (f"no burn recorded for {account_tag(config_dir)} yet "
                "(wire the status line: claude status --help)")
    five, seven = snap.get("five_hour") or {}, snap.get("seven_day") or {}
    resets = _clock(five.get("resets_at"))
    seen = _clock(snap.get("captured_at"))
    return (f"5h {percent(five.get('used_percentage'))}"
            + (f" (resets {resets})" if resets else "")
            + f" | 7d {percent(seven.get('used_percentage'))}"
            + f" | {account_tag(config_dir)}"
            + (f"   as of {seen}" if seen else ""))


def main(argv):
    if any(arg in ("-h", "--help", "help") for arg in argv):
        print(HELP, end="")
        return 0
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    payload = _payload()
    # A payload means Claude Code is asking for the line under its prompt. No
    # payload means a person or a script is asking what the burn is, and the
    # cache is the only answer available without a live session.
    if payload is None:
        print(render_cached(config_dir))
        return 0
    try:
        snap = snapshot(payload, time.time())
        if snap:
            write_snapshot(config_dir, snap)
        print(render(payload, config_dir, use_color=True))
    except (OSError, ValueError, KeyError, TypeError):
        # Printing a traceback here would put it under the prompt every turn.
        return 0
    return 0


def _payload():
    """Claude Code\x27s statusline JSON, or None when nobody piped one in."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
