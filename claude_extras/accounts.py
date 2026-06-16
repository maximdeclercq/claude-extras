"""Account layout: which config dir a directory uses, and how one is built."""

import json
import os
from pathlib import Path

# shutil loads bz2, lzma and zstd, about 4ms per launch, so only seeding imports it

HOME = Path.home()
# Named after this tool, since ~/.config/claude is where a hand-set CLAUDE_CONFIG_DIR ends up
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
CONFIG = Path(os.environ.get("CLAUDE_EXTRAS_CONFIG") or XDG_CONFIG / "claude-extras")
ACCOUNTS_DIR = CONFIG / "accounts"
AUTH_DIR = CONFIG / "auth"
ROUTES = CONFIG / "routes"
DENY = CONFIG / "deny"
STATE = CONFIG / "state"
TERMINAL = CONFIG / "terminal"
LAUNCH_LOG = STATE / "launches"
DEFAULT_DIR = HOME / ".claude"

# Names that select a scope, so they can never also name an account.
RESERVED = frozenset({"default", "all"})

# Config that may legitimately differ per account. Everything else is shared.
# settings.local.json is absent because Claude Code resolves that name against a
# project (.claude/settings.local.json), never against the config dir.
COPIED = ("settings.json",)
# Memory holds preferences and standing rules, which apply whoever is paying, so
# it is shared. plugins is absent, see seed_plugins.
LINKED = ("CLAUDE.md", "CLAUDE.local.md", "hooks", "skills", "memory", "commands", "workflows")


class AccountError(Exception):
    """A named account cannot be used as asked."""


def read_routes():
    """[(project_path, account_name)], in file order."""
    out = []
    try:
        text = ROUTES.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        path, _, account = line.partition("\t")
        if path and account:
            out.append((path, account))
    return out


def private_dir(*parts):
    """The config subdirectory named by parts, created 700 along with its parents."""
    path = CONFIG
    # chmod separately, because mkdir(mode=) applies to the deepest directory only
    # and does nothing at all for one that already exists.
    for part in (None, *parts):
        path = path if part is None else path / part
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    return path


def write_routes(rows):
    private_dir()
    tmp = ROUTES.with_suffix(".tmp")
    tmp.write_text("".join(f"{p}\t{a}\n" for p, a in rows))
    tmp.replace(ROUTES)


def matching_route(directory=None):
    """The (path, account) route governing a directory, longest prefix wins."""
    directory = str(directory or Path.cwd())
    try:
        real = str(Path(directory).resolve())
    except OSError:
        real = directory
    best = None
    for path, account in read_routes():
        under = any(d == path or d.startswith(path + os.sep) for d in (directory, real))
        if under and (best is None or len(path) > len(best[0])):
            best = (path, account)
    return best


def resolve_account(directory=None):
    """The account a directory routes to, "default" when no route covers it."""
    route = matching_route(directory)
    return route[1] if route and route[1] not in ("", "default") else "default"


def bind_env(name):
    """Point CLAUDE_CONFIG_DIR at an account for this process and its children.

    The default account is no variable at all, not one pointing at ~/.claude:
    Claude Code keeps the default's state file beside that directory, not inside
    it, and an inherited value would keep billing whatever routed the shell.
    """
    if name == "default":
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = str(ACCOUNTS_DIR / name)


def known_accounts():
    """Every selectable account name, sorted."""
    names = set()
    if ACCOUNTS_DIR.is_dir():
        names.update(d.name for d in ACCOUNTS_DIR.iterdir() if d.is_dir())
    if AUTH_DIR.is_dir():
        names.update(f.stem for f in AUTH_DIR.glob("*.json"))
    return sorted(names - RESERVED)


def account_exists(name):
    return (ACCOUNTS_DIR / name).is_dir() or (AUTH_DIR / f"{name}.json").is_file()


def unknown_account(name):
    """The error for a name that selects nothing, listing what would have worked."""
    have = ", ".join(["default", *known_accounts(), "all"])
    return AccountError(f"unknown account '{name}' (have: {have})")


def select_accounts(scope):
    """[(name, config_dir)] for a scope, which is an account name or "all"."""
    if scope == "all":
        out = [("default", DEFAULT_DIR)]
        if ACCOUNTS_DIR.is_dir():
            out += [(d.name, d) for d in sorted(ACCOUNTS_DIR.iterdir()) if d.is_dir()]
        return out
    if scope == "default":
        return [("default", DEFAULT_DIR)]
    if not account_exists(scope):
        raise unknown_account(scope)
    return [(scope, ACCOUNTS_DIR / scope)]


def _link(source, target):
    try:
        os.symlink(source, target)
    except FileExistsError:
        pass


def seed_plugins(account_dir):
    """Give an account its own plugin tree."""
    import shutil

    src = DEFAULT_DIR / "plugins"
    dst = account_dir / "plugins"
    if not src.is_dir():
        return
    if dst.exists() and not dst.is_symlink():
        return
    if dst.is_symlink():
        dst.unlink()
    (dst / "marketplaces").mkdir(parents=True, exist_ok=True)
    for name in ("cache", "data"):
        link = dst / name
        if link.is_symlink():
            link.unlink()
        _link(src / name, link)
    for name in ("installed_plugins.json", "plugin-catalog-cache.json", "blocklist.json"):
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    for market in src.glob("marketplaces/*"):
        target = dst / "marketplaces" / market.name
        if market.is_dir() and not target.exists():
            try:
                shutil.copytree(market, target)
            except FileExistsError:
                pass

    known = src / "known_marketplaces.json"
    if not known.is_file():
        return
    data = json.loads(known.read_text())
    for entry in data.values():
        location = entry.get("installLocation", "")
        if location.startswith(str(src / "marketplaces") + os.sep):
            entry["installLocation"] = str(dst / "marketplaces" / Path(location).name)
    (dst / "known_marketplaces.json").write_text(json.dumps(data, indent=2) + "\n")


def seed_account_dir(name):
    """Build an account's config dir and return it, credentials or not."""
    import shutil

    account_dir = private_dir("accounts", name)

    for item in COPIED:
        source = DEFAULT_DIR / item
        if source.exists() and not (account_dir / item).exists():
            shutil.copy2(source, account_dir / item)
    for item in LINKED:
        source = DEFAULT_DIR / item
        target = account_dir / item
        if source.exists() and not target.exists() and not target.is_symlink():
            _link(source, target)
    seed_plugins(account_dir)

    saved = AUTH_DIR / f"{name}.json"
    credentials = account_dir / ".credentials.json"
    if not credentials.is_file() and saved.is_file():
        shutil.copy2(saved, credentials)
        credentials.chmod(0o600)
    return account_dir


def ensure_account_dir(name):
    """Seed an account's config dir on first use, refusing if it has no credentials."""
    account_dir = ACCOUNTS_DIR / name
    saved = AUTH_DIR / f"{name}.json"
    if not (account_dir / ".credentials.json").is_file() and not saved.is_file():
        raise AccountError(
            f"account '{name}' has no credentials; log in with: "
            f"claude auth login {name}"
        )
    return seed_account_dir(name)


def project_root(directory=None):
    """The git root of a directory, or the directory itself when it is not a repo."""
    import subprocess

    directory = str(directory or Path.cwd())
    try:
        out = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or directory
    except (OSError, subprocess.CalledProcessError):
        return directory
