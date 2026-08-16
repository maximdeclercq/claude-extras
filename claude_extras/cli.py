"""The `claude` entry point, which routes a directory to an account and execs the real binary."""

import os
import sys
import time
from pathlib import Path

from .accounts import (
    DENY,
    LAUNCH_LOG,
    AccountError,
    account_exists,
    bind_env,
    ensure_account_dir,
    private_dir,
    resolve_account,
    unknown_account,
)
from .args import take_value

LOG_LIMIT = 5000
LOG_KEEP = 2000

# Verb to the module that implements it. One table, so a verb cannot exist in the
# dispatch list and nowhere else.
MODULES = {
    "auth": "auth",
    "usage": "usage",
    "resume": "resume",
    "chat": "chat",
    "spawn": "spawn",
    "search": "search",
    "status": "status",
}

# Short forms for the verbs typed most often. One letter each, and only where the
# letter is unambiguous across the whole set.
ALIASES = {"r": "resume", "s": "search", "c": "chat", "u": "usage"}


def die(message, code=1):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def logical_cwd():
    """The path the shell thinks it is in, symlinks intact."""
    real = os.getcwd()
    pwd = os.environ.get("PWD")
    if pwd and os.path.isabs(pwd):
        try:
            if os.path.samefile(pwd, real):
                return pwd
        except OSError:
            pass
    return real


def real_bin():
    """The real claude binary, or a fatal error naming the way out."""
    found = find_real_bin()
    if found is None:
        die("real claude binary not found on PATH (set CLAUDE_REAL_BIN)")
    return found


def find_real_bin():
    """The real claude binary, skipping this wrapper wherever it sits on PATH, or None."""
    override = os.environ.get("CLAUDE_REAL_BIN")
    if override:
        return override
    self_path = Path(sys.argv[0]).resolve() if sys.argv[0] else None
    path = os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / "claude"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        if self_path and candidate.resolve() == self_path:
            continue
        return str(candidate)
    native = newest_native_version()
    return str(native) if native else None


def newest_native_version():
    """The highest version Anthropic's native installer keeps under ~/.local/share/claude.

    That installer's launcher is a symlink at ~/.local/bin/claude, the path this
    wrapper takes over, and it leaves a replaced launcher alone, so updates keep
    landing under versions/ and whatever sits at the launcher picks which one runs.
    """
    versions = Path.home() / ".local" / "share" / "claude" / "versions"
    try:
        candidates = [p for p in versions.iterdir() if p.is_file() and os.access(p, os.X_OK)]
    except OSError:
        return None
    numbered = [(tuple(int(x) for x in p.name.split(".")), p)
                for p in candidates if all(x.isdigit() for x in p.name.split("."))]
    if not numbered:
        return None
    return max(numbered)[1]


def check_dir(cwd):
    """Refuse to launch in a directory the deny list names, symlinks resolved."""
    try:
        candidates = {cwd, str(Path(cwd).resolve())}
    except OSError:
        candidates = {cwd}
    try:
        lines = DENY.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if os.path.expanduser(line) in candidates:
            die(f"blocked in {cwd}")


def bind_account(name):
    """Bind this process to an account, seeding it on first use."""
    if name != "default":
        if not account_exists(name):
            raise unknown_account(name)
        ensure_account_dir(name)
    bind_env(name)


def record_launch(account, cwd):
    """Log the logical cwd so `claude resume` can show the path actually typed."""
    try:
        real = str(Path(cwd).resolve())
    except OSError:
        real = cwd
    line = f"{int(time.time())}\t{account}\t{real}\t{cwd}"
    try:
        private_dir("state")
        lines = LAUNCH_LOG.read_text(errors="replace").splitlines() \
            if LAUNCH_LOG.is_file() else []
        if lines and lines[-1].split("\t")[-1] == cwd:
            return
        if len(lines) + 1 > LOG_LIMIT:
            LAUNCH_LOG.write_text("\n".join((lines + [line])[-LOG_KEEP:]) + "\n")
            return
        with LAUNCH_LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def take_account_flag(argv):
    """Strip --account/-a from anywhere in argv. claude has no such native flag."""
    forced, rest, index = None, [], 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--account", "-a"):
            forced, index = take_value(argv, index, arg), index + 2
        elif arg.startswith(("--account=", "-a=")):
            forced, index = arg.split("=", 1)[1], index + 1
        else:
            rest.append(arg)
            index += 1
    return forced, rest


def launch(argv):
    """Start a real session, honouring an --account override over cwd routing."""
    forced, rest = take_account_flag(argv)
    cwd = logical_cwd()
    if forced == "all":
        die("--account all selects a scope to report on, so it cannot start a session")
    # Check the directory first. Selecting an account seeds its config dir, and a
    # launch about to be refused should leave nothing behind.
    check_dir(cwd)
    account = forced or resolve_account(cwd)
    bind_account(account)
    record_launch(account, cwd)
    binary = real_bin()
    os.execv(binary, [binary, *rest])


def dispatch(name, argv):
    """Run one verb, importing only the module that implements it."""
    from importlib import import_module

    return import_module(f".{MODULES[name]}", __package__).main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        # A leading "--" gives up the wrapper's verbs for this call. Our names are
        # taken from the real CLI's namespace, so when a release adds one of its
        # own there has to be a way to reach it.
        if argv and argv[0] == "--":
            return launch(argv[1:])
        verb = ALIASES.get(argv[0], argv[0]) if argv else ""
        if verb in MODULES:
            return dispatch(verb, argv[1:]) or 0
        launch(argv)
    except AccountError as exc:
        die(str(exc))
    except BrokenPipeError:
        # A report piped into head closes the pipe early; that is not a fault.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
