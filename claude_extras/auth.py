"""The `claude auth` verb, which adds accounts and routing to the native command."""

import os
import shutil
import sys

from . import identity
from .accounts import (
    ACCOUNTS_DIR,
    CONFIG,
    DEFAULT_DIR,
    RESERVED,
    AccountError,
    bind_env,
    private_dir,
    project_root,
    read_routes,
    resolve_account,
    seed_account_dir,
    select_accounts,
    write_routes,
)

HELP = """Usage: claude auth [options] [command]

Manage accounts and which project uses which

Options:
      --fix               With doctor: repair what is safely repairable
      --undo              With doctor: take those repairs back out, before uninstalling
      --json              Output as JSON
  -h, --help              Display help for command

Commands:
  login [name]            Bind this project to an account, logging in if needed
  logout                  Log out of the routed account (native)
  status                  Show the routed account's identity (native)
  save <name>             Snapshot the current credentials as a reusable account
  list                    Accounts, logins and project routes (default)
  doctor                  Check accounts for faults that fail silently
  help                    Display help for command

Examples:
  # Put this project on a separate account:
  claude auth login acme

  # Give it back to the default account:
  claude auth login default

  # Find out what is quietly broken:
  claude auth doctor
"""


def bind_route(path, account):
    rows = [row for row in read_routes() if row[0] != path]
    rows.append((path, account))
    write_routes(rows)


def unbind_route(path):
    write_routes([row for row in read_routes() if row[0] != path])


def run_doctor(mode, flags):
    from . import doctor

    return doctor.report(CONFIG, select_accounts("all"), mode, fix="--fix" in flags,
                         as_json="--json" in flags, undo="--undo" in flags)


def native(verb, args):
    from .cli import real_bin

    binary = real_bin()
    # The route line printed just before is lost with the exec when stdout is a pipe.
    sys.stdout.flush()
    os.execv(binary, [binary, "auth", verb, *args])


def cmd_login(name):
    """Route this project to an account, logging in when it has no credentials yet."""
    if not name:
        native("login", [])
    root = project_root()
    if name == "default":
        unbind_route(root)
        print(f"{root} -> default")
        return 0
    if name in RESERVED:
        raise AccountError(f"'{name}' selects a scope, so it cannot name an account")
    seed_account_dir(name)
    bind_route(root, name)
    if (ACCOUNTS_DIR / name / ".credentials.json").is_file():
        print(f"{root} -> {name} (already logged in)")
        return 0
    print(f"{root} -> {name}")
    os.environ["CLAUDE_CONFIG_DIR"] = str(ACCOUNTS_DIR / name)
    native("login", [])


def cmd_save(name):
    if not name:
        print("usage: claude auth save <account>", file=sys.stderr)
        return 1
    # Same guard as cmd_login, or `auth save all` writes a snapshot nothing can select
    if name in RESERVED:
        raise AccountError(f"'{name}' selects a scope, so it cannot name an account")
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(DEFAULT_DIR)
    source = os.path.join(config_dir, ".credentials.json")
    if not os.path.isfile(source):
        raise AccountError(f"no credentials at {source} (log in first)")
    target = private_dir("auth") / f"{name}.json"
    # The source is whichever login the current directory routes to, so a slip in
    # the name would otherwise replace another account's snapshot with it silently.
    held = identity.fingerprint(target)
    login = identity.fingerprint(source)
    if held and held != login:
        raise AccountError(f"auth/{name}.json holds a different login ({held}); remove it first")
    shutil.copy2(source, target)
    target.chmod(0o600)
    print(f"saved account: {name} (login {login})")
    return 0


def main(argv):
    # Native subcommands act on the account the current directory routes to.
    bind_env(resolve_account())

    if any(arg in ("-h", "--help", "help") for arg in argv):
        print(HELP, end="")
        return 0

    verb = argv[0] if argv else "list"
    rest = argv[1:] if argv else []
    flags = [a for a in rest if a in ("--fix", "--json", "--undo")]

    if verb == "login":
        return cmd_login(rest[0] if rest else "")
    if verb == "save":
        return cmd_save(rest[0] if rest else "")
    if verb == "list":
        return run_doctor("list", flags)
    if verb == "doctor":
        # Exits non-zero when something is failing, so it composes in scripts.
        return run_doctor("doctor", flags)
    native(verb, rest)
