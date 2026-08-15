"""Open a session in a new terminal window."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .accounts import TERMINAL, AccountError, account_exists, unknown_account
from .args import parse

FLAGS = {"--account": str, "-a": "--account", "--resume": str}

WINDOW_TITLE = "Claude Code"
# A class of its own, so a compositor can route and exempt the window on an
# identity the session does not rewrite on every turn the way it does the title.
WINDOW_CLASS = "claude-code"

# How each terminal takes a command to run. The working directory is not in here:
# it is set on the child process instead, which every terminal inherits.
TERMINALS = (
    ("foot", ["--app-id", WINDOW_CLASS, "--title", WINDOW_TITLE]),
    ("alacritty", ["--class", WINDOW_CLASS, "--title", WINDOW_TITLE, "-e"]),
    ("kitty", ["--class", WINDOW_CLASS, "--title", WINDOW_TITLE]),
    ("wezterm", ["start", "--class", WINDOW_CLASS, "--"]),
    # xterm's -class also picks which X resources apply, so it keeps its own.
    ("xterm", ["-title", WINDOW_TITLE, "-e"]),
)


def terminal_command():
    """(binary, args_before_command) for the terminal to spawn."""
    configured = os.environ.get("CLAUDE_TERMINAL")
    if not configured:
        try:
            configured = TERMINAL.read_text().strip()
        except OSError:
            configured = ""
    if configured:
        binary, *extra = configured.split()
        return binary, extra + dict(TERMINALS).get(Path(binary).name, [])
    for name, args in TERMINALS:
        if shutil.which(name):
            return name, args
    return None, []


def focused_cwd():
    """The working directory of the focused window's shell, if it has one."""
    try:
        import json

        out = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, check=True
        )
        pid = json.loads(out.stdout).get("pid")
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    if not pid or pid < 1:
        return None
    leaf = pid
    while True:
        try:
            children = Path(f"/proc/{leaf}/task/{leaf}/children").read_text().split()
        except OSError:
            break
        if not children:
            break
        leaf = int(children[-1])
    try:
        return os.readlink(f"/proc/{leaf}/cwd")
    except OSError:
        return None


# Claude Code stamps these on anything it launches, and a session that inherits
# them treats itself as a child, which turns transcript saving off.
PARENT_SESSION_VARS = (
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MAX_RETRIES",
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CONFIG_DIR",
)


def child_environment():
    """The environment for a spawned session, minus the caller's session identity."""
    env = dict(os.environ)
    for name in PARENT_SESSION_VARS:
        env.pop(name, None)
    return env


def spawn(directory, args=(), account=None):
    """Open a terminal in a directory running claude, detached from this process."""
    binary, prefix = terminal_command()
    if not binary:
        raise SystemExit(
            "error: no terminal found (install foot, or set CLAUDE_TERMINAL, "
            f"or write one to {TERMINAL})"
        )
    # An absolute path, because the new terminal starts elsewhere and a relative
    # argv[0] would not resolve from there.
    self_bin = shutil.which(sys.argv[0]) or shutil.which("claude") or "claude"
    command = [binary, *prefix, str(Path(self_bin).resolve())]
    if account:
        command += ["--account", account]
    command += list(args)
    subprocess.Popen(
        command,
        cwd=str(directory),
        env=child_environment(),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


HELP = """Usage: claude spawn [options] [directory]

Open a session in a new terminal window

Options:
  -a, --account <name>    Use a specific account instead of the directory's route
      --resume <id>       Resume a session by id instead of starting a new one
  -h, --help              Display help for command

With no directory, the focused window's working directory is used, falling back to
your home directory. This is what the desktop keybind and the chat button call.

Examples:
  claude spawn ~/workspace/acme
  claude spawn ~/workspace/acme --resume 4c7b382d-6c82-44d8-b016-65dd3deaac33
"""


def main(argv):
    options, rest = parse(argv, FLAGS, "spawn")
    if options["--help"]:
        print(HELP, end="")
        return 0
    if len(rest) > 1:
        raise AccountError(f"claude spawn: unexpected argument '{rest[1]}'")
    account, resume = options["--account"], options["--resume"]

    directory = (rest[0] if rest else None) or focused_cwd() or str(Path.home())
    if not Path(directory).is_dir():
        raise AccountError(f"not a directory: {directory}")
    # Here, where the error is visible. Failing inside the new terminal closes it,
    # so the window opens and vanishes and nothing says why.
    if account and account != "default" and not account_exists(account):
        raise unknown_account(account)
    spawn(directory, ["--resume", resume] if resume else [], account)
    return 0
