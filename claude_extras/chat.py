"""The `claude chat` verb, for conversations that are not about a repository."""

import json
import os
import sys
import time

from . import chats, export, sessions, spawn
from .accounts import (
    LAUNCH_LOG,
    AccountError,
    known_accounts,
    resolve_account,
    select_accounts,
)
from .args import parse, take_count

# How many chats a terminal listing prints. A script takes the lot instead, since
# it filters what it was given and a truncated list cannot be searched past.
LIST_LIMIT = 20
FLAGS = {**export.FLAGS, "--account": str, "-a": "--account", "--limit": take_count,
         "--json": None}

HELP = """Usage: claude chat [options] [command]

Start, list and resume chats

Options:
  -a, --account <name>    One account, or "all" (default follows the cwd)
      --limit <n>         How many chats to list (default 20, or all with --json)
      --json              List as JSON, for a script
      --format md|html    Export format (default md)
  -o, --out <file>        Export to this file instead of stdout
      --include-tools     Name the tools an export ran, never their output
  -h, --help              Display help for command

Commands:
  (none)                  List chats, newest first (default)
  <n>                     Resume chat number <n> from the list
  new [description]       Start a chat; the name is generated if you omit one
  export <n>              Write chat <n> out as markdown or html
  account                 Print the account a new chat here would use
  tidy                    Rename settled chats now (SessionEnd hook)
  help                    Display help for command

A chat is a session in its own directory under <project>/chats, so its artifacts
and files have somewhere to live. Names are generated from the title Claude Code
writes a turn or two in, so starting one costs no typing.

Examples:
  claude chat                     # what have I been talking about
  claude chat 3                   # pick that one back up
  claude chat new                 # just start, name it later
  claude chat new tax return      # or name it now
  claude chat export 3 -o chat.md # keep one
"""


def chat_account(explicit=None):
    """Which account a chat belongs to."""
    if explicit:
        return explicit
    # A status bar has no directory of its own, so it falls through to the window.
    for candidate in (os.environ.get("PWD") or os.getcwd(), spawn.focused_cwd()):
        if not candidate:
            continue
        name = resolve_account(candidate)
        if name != "default":
            return name
    # Not the last account used, which would make the commonest one unreachable
    return "default"


def config_dir_for(account):
    return select_accounts(account)[0][1]


def list_rows(account, limit, tidy=True):
    """Chat sessions for an account, newest first."""
    if account == "all":
        rows = [row for name in chat_accounts() for row in list_rows(name, limit, tidy)]
        rows.sort(key=lambda r: r["activity"], reverse=True)
        return rows[:limit] if limit else rows
    config_dir = config_dir_for(account)
    if tidy:
        chats.tidy(account, config_dir)
    try:
        under = str(chats.chats_root(account))
    except AccountError:
        return []
    logical = sessions.load_logical_map(LAUNCH_LOG)
    rows = sessions.sessions_for(account, config_dir, logical, limit, under=under)
    rows.sort(key=lambda r: r["activity"], reverse=True)
    return rows[:limit]


def start(account, description=""):
    return chats.new_chat_dir(account, description)


def cmd_new(account, description):
    """Start a chat here, or in a new window when there is nowhere to start one."""
    directory = start(account, description)
    if not sys.stdout.isatty():
        spawn.spawn(directory, account=account)
        return 0
    os.chdir(directory)
    os.environ["PWD"] = str(directory)
    from .cli import launch

    launch(["--account", account])


def cmd_account(account):
    """The account a new chat would land in, resolved the way chat new resolves it."""
    if account == "all":
        raise AccountError("a new chat needs one account, so --account all names none")
    name = chat_account(account)
    # Raises for an account with no chats root, so a button can say so before the click.
    chats.chats_root(name)
    print(name)
    return 0


def cmd_tidy(account):
    """Give settled chats their real names."""
    for name in ([account] if account else chat_accounts()):
        try:
            chats.tidy(name, config_dir_for(name))
        except AccountError:
            continue
    return 0


def chat_accounts():
    """Every account a chat can go to, default included."""
    return ["default", *known_accounts()]


def cmd_export(account, rest, options, limit):
    """Write one chat out. Numbering is the listing's, so `export 3` follows `chat`."""
    if len(rest) != 1 or not rest[0].isdigit():
        raise AccountError("usage: claude chat export <n> [--format md|html] [-o FILE]")
    number = int(rest[0])
    rows = list_rows(account, max(limit, number))
    if not 1 <= number <= len(rows):
        raise AccountError(f"no chat #{number}")
    return export.emit(rows[number - 1], *export.choices(options))


def cmd_resume(account, number, limit):
    rows = list_rows(account, max(limit, number))
    if not 1 <= number <= len(rows):
        raise AccountError(f"no chat #{number}")
    return sessions.resume(rows[number - 1])


def cmd_list(account, limit, as_json=False):
    rows = list_rows(account, limit)
    if as_json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if not rows:
        if account == "all":
            print("no chats yet\nstart one:  claude chat new")
            return 0
        root = chats.chats_root(account)
        print(f"no chats yet in {root}\nstart one:  claude chat new")
        return 0
    print(sessions.render(rows, time.time(), heading=f"chats  {account}",
                          resume="claude chat <#>"))
    return 0


def main(argv):
    options, rest = parse(argv, FLAGS, "chat")
    if options["--help"]:
        print(HELP, end="")
        return 0
    account, limit, as_json = options["--account"], options["--limit"], options["--json"]

    verb = rest[0] if rest else ""
    if verb == "export":
        return cmd_export(chat_account(account), rest[1:], options, limit or LIST_LIMIT)
    if verb == "account":
        return cmd_account(account)
    if verb == "tidy":
        return cmd_tidy(account)
    resolved = chat_account(account)
    if verb == "new":
        if resolved == "all":
            raise AccountError("a new chat needs one account, so --account all cannot start one")
        return cmd_new(resolved, " ".join(rest[1:]))
    if verb.isdigit():
        if len(rest) > 1:
            raise AccountError(f"claude chat: unexpected argument '{rest[1]}'")
        return cmd_resume(resolved, int(verb), limit or LIST_LIMIT)
    if verb:
        print(f"error: claude chat: unexpected argument '{verb}'", file=sys.stderr)
        print(HELP, end="", file=sys.stderr)
        return 1
    return cmd_list(resolved, limit or (None if as_json else LIST_LIMIT), as_json)
