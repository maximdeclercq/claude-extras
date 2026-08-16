#!/usr/bin/env python3
"""Pick a chat, or start one, from a dmenu-style launcher.

Built on `claude chat --json`, `claude auth list --json`, `claude chat new` and
`claude spawn`, so it holds no view of its own. Tries wofi, fuzzel, rofi and dmenu
in that order. `--new` offers only the accounts. CLAUDE_CHAT_ACCOUNTS, a
space-separated list of names, puts those accounts first.
"""
import json
import os
import subprocess
import sys
import time

ROWS = 10


def claude(*args):
    done = subprocess.run(["claude", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        sys.exit(done.stderr.strip() or f"claude {' '.join(args)} failed")
    return done.stdout


def accounts():
    listed = json.loads(claude("auth", "list", "--json"))["accounts"]
    known = ["default", *(a["account"] for a in listed if a["account"] != "default")]
    wanted = [n for n in os.environ.get("CLAUDE_CHAT_ACCOUNTS", "").split() if n in known]
    return wanted + [n for n in known if n not in wanted]


def pick(lines, prompt, rows):
    """The chosen line from the first launcher installed, or None."""
    count = str(rows)
    # multi-contains rather than fuzzy, which across hundreds of titles matches
    # scattered letters and buries the entry you typed the name of. The cache is
    # off because it reorders by past picks, floating stale rows above "new chat".
    for command in (["wofi", "--dmenu", "--prompt", prompt, "--lines", count,
                     "--insensitive", "--matching", "multi-contains",
                     "--cache-file", os.devnull],
                    ["fuzzel", "--dmenu", "--prompt", prompt + ": ", "--lines", count],
                    ["rofi", "-dmenu", "-p", prompt, "-l", count],
                    ["dmenu", "-p", prompt, "-l", count]):
        try:
            out = subprocess.run(command, input="\n".join(lines), capture_output=True,
                                 text=True, check=False)
        except OSError:
            continue
        return out.stdout.strip() or None
    sys.exit("no launcher found (wofi, fuzzel, rofi or dmenu)")


def age(when, now):
    secs = max(0, int(now - when))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def main(argv):
    new_only = "--new" in argv
    new_rows = [f"+  new chat  [{name}]" for name in accounts()]
    rows, labels = [], []
    if not new_only:
        rows = json.loads(claude("chat", "--json", "--account", "all"))
        now = time.time()
        # The index keeps a label unique, since two chats can share a title.
        labels = [f"{i:>3}  {age(r['activity'], now):>4}  {r['title'][:58]}  [{r['account']}]"
                  for i, r in enumerate(rows)]
    if new_only:
        choice = pick(new_rows, "New chat in", rows=len(new_rows))
    else:
        choice = pick(new_rows + labels, "Chat", rows=ROWS)
    if not choice:
        return 0
    if choice in new_rows:
        name = choice.rsplit("[", 1)[1].rstrip("]")
        # With stdout captured there is no terminal, so chat new opens a window.
        subprocess.run(["claude", "chat", "new", "--account", name], capture_output=True, check=False)
        return 0
    try:
        row = rows[int(choice.split(None, 1)[0])]
    except (ValueError, IndexError):
        return 0
    subprocess.run(["claude", "spawn", row["real"], "--resume", row["id"],
                    "--account", row["account"]], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
