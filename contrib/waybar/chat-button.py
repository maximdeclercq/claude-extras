#!/usr/bin/env python3
"""Waybar module output for the chat button: the account a click would land in.

`claude chat account` answers the question; this dresses the answer for the bar.
CLAUDE_CHAT_ICON replaces the account name as the label, since with an icon the
colour and the tooltip already say which account it is. The class is the account
name with dots turned into dashes, because a CSS selector cannot hold a dot.
"""
import json
import os
import subprocess
import sys


def main():
    icon = os.environ.get("CLAUDE_CHAT_ICON", "")
    done = subprocess.run(["claude", "chat", "account"], capture_output=True, text=True, check=False)
    name = done.stdout.strip()
    if done.returncode == 0 and name:
        payload = {"text": icon or name, "class": name.replace(".", "-"),
                   "tooltip": f"New chat in {name}\nRight-click for past chats"}
    else:
        payload = {"text": icon or "chat", "class": "unconfigured",
                   "tooltip": done.stderr.strip() or "no account for a chat here"}
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
