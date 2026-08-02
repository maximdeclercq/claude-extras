"""Account inventory and health checks.

Every fault checked here has happened on a real machine and none of them
announced itself.
"""

import json
import os
import sys
from pathlib import Path

from . import identity

# Below this, transcripts are being deleted sooner than most people expect.
MIN_CLEANUP_DAYS = 365
# Keys an account must agree with the default account on. settings.json is copied
# once at seeding and never resynced, so these drift the moment default changes.
SHARED_KEYS = ("hooks", "statusLine")
STATUSLINE_REFRESH_SECONDS = 5
STATUSLINE_COMMAND = "claude status"
WARN, FAIL = "warn", "fail"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _settings(config_dir):
    return _read_json(Path(config_dir) / "settings.json") or {}


def _finding(level, code, message, account=None, fix=None, path=None, **extra):
    return {"level": level, "code": code, "message": message, "account": account,
            "fix": fix, "path": path, **extra}


# Hooks the wrapper's own features are built on. Each is (event, command, what
# breaks without it). Claude Code reads these from settings.json -> hooks -> event;
# anywhere else they are inert, which is a failure with no symptom but the feature
# quietly never working.
REQUIRED_HOOKS = (
    ("SessionEnd", "claude chat tidy", "chats keep their placeholder names until you list them"),
)


def _commands_in(node):
    """Every command string in a hook block, whatever shape the block is in."""
    out = []
    for entry in node if isinstance(node, list) else []:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks") if isinstance(entry.get("hooks"), list) else []:
            if isinstance(hook, dict) and hook.get("command"):
                out.append(hook["command"])
    return out


def _hook_commands(settings, event):
    """Every command wired to an event, from the one place Claude Code reads."""
    hooks = settings.get("hooks")
    return _commands_in(hooks.get(event) if isinstance(hooks, dict) else None)


def _misplaced_commands(settings, event):
    """Commands wired at the top level of settings.json, where nothing reads them."""
    return _commands_in(settings.get(event))


def _check_hooks(name, config_dir):
    settings = _settings(config_dir)
    out = []
    for event, command, consequence in REQUIRED_HOOKS:
        if command in _hook_commands(settings, event):
            continue
        if command in _misplaced_commands(settings, event):
            out.append(_finding(
                FAIL, "hook-misplaced",
                f"'{command}' sits at the top level of settings.json instead of under "
                f"\"hooks\", so it never runs and {consequence}",
                name, fix="hook", event=event, command=command))
            continue
        out.append(_finding(
            WARN, "hook-missing",
            f"no {event} hook running '{command}', so {consequence}",
            name, fix="hook", event=event, command=command))
    return out


def _check_symlinks(name, config_dir):
    """A symlink whose target is gone reads as "no overrides" rather than an error."""
    out = []
    for child in sorted(Path(config_dir).iterdir()) if Path(config_dir).is_dir() else []:
        if child.is_symlink() and not child.exists():
            out.append(_finding(
                FAIL, "dangling-symlink",
                f"{child.name} points at a missing target ({os.readlink(child)})",
                name, fix="unlink", path=str(child)))
    return out


def _check_credentials(name, ident):
    if not ident["has_credentials"]:
        return [_finding(FAIL, "no-credentials", "no .credentials.json", name)]
    if not ident["fingerprint"]:
        return [_finding(FAIL, "broken-credentials",
                         "credential has no refresh token, so it can never refresh", name)]
    if not ident["expires_at"]:
        return [_finding(WARN, "no-expiry", "credential has no expiry recorded", name)]
    return []


def _check_settings(name, config_dir):
    out = []
    settings = _settings(config_dir)
    days = settings.get("cleanupPeriodDays")
    if days is None:
        out.append(_finding(WARN, "retention-default",
                            "cleanupPeriodDays unset, so transcripts are swept on the built-in "
                            f"schedule; set it to {MIN_CLEANUP_DAYS} or more to keep what usage "
                            "and search read", name))
    elif days < MIN_CLEANUP_DAYS:
        out.append(_finding(WARN, "retention-low",
                            f"cleanupPeriodDays is {days}; set it to {MIN_CLEANUP_DAYS} or more "
                            "to keep what usage and search read", name))
    status = settings.get("statusLine") or {}
    if not status:
        out.append(_finding(FAIL, "no-statusline",
                            "no statusLine, so nothing caches the 5h/7d plan burn and "
                            "claude usage reports no percentages", name, fix="statusline"))
    elif not status.get("refreshInterval"):
        out.append(_finding(WARN, "no-refresh-interval",
                            "statusLine has no refreshInterval, so its usage snapshot goes "
                            "stale between messages", name, fix="statusline"))
    return out


def _check_shared(accounts):
    """Two dirs on one credential, and dirs sharing a quota meter."""
    out = []
    by_fingerprint, by_subscription = {}, {}
    for name, ident in accounts:
        if ident["fingerprint"]:
            by_fingerprint.setdefault(ident["fingerprint"], []).append(name)
        if ident["user_uuid"]:
            by_subscription.setdefault(identity.subscription_key(ident), []).append(name)
    for fingerprint, names in by_fingerprint.items():
        if len(names) > 1:
            out.append(_finding(
                FAIL, "shared-credential",
                f"{', '.join(names)} share one login ({fingerprint}); when one rotates the "
                "token the others are left with a stale copy"))
    for names in by_subscription.values():
        if len(names) > 1:
            out.append(_finding(
                WARN, "shared-subscription",
                f"{', '.join(names)} bill one subscription, so they share a single "
                "5h/7d quota and separate accounts buy no extra headroom"))
    return out


def _check_orphans(config, account_names):
    """Leftovers that hold credentials, reported and never removed."""
    out = []
    auth_dir = Path(config) / "auth"
    if auth_dir.is_dir():
        for snap in sorted(auth_dir.glob("*.json")):
            if snap.stem not in account_names:
                out.append(_finding(WARN, "orphan-snapshot",
                                    f"auth/{snap.name} matches no account; holds a credential, "
                                    "so remove it yourself", path=str(snap)))
    legacy = Path(config) / "profiles"
    if legacy.is_dir() and any(legacy.iterdir()):
        out.append(_finding(WARN, "legacy-profiles",
                            f"{legacy} still holds pre-accounts state, including credentials",
                            path=str(legacy)))
    return out


def _check_routes():
    """A route whose directory is gone bills default without a word."""
    from .accounts import read_routes

    return [_finding(WARN, "route-dead",
                     f"route {path} -> {account} names a directory that no longer exists",
                     fix="route", path=path)
            for path, account in read_routes() if not Path(path).is_dir()]


def run_checks(config, accounts):
    findings = []
    for name, config_dir, ident in accounts:
        findings += _check_symlinks(name, config_dir)
        findings += _check_credentials(name, ident)
        findings += _check_settings(name, config_dir)
        findings += _check_hooks(name, config_dir)
    findings += _check_shared([(n, i) for n, _, i in accounts])
    findings += _check_orphans(config, {n for n, _, _ in accounts})
    findings += _check_routes()
    # Last, so that with --fix the default account is already repaired by the time
    # its settings are copied into the others.
    dirs = {n: d for n, d, _ in accounts}
    if "default" in dirs:
        for name, config_dir in dirs.items():
            findings += _check_drift(name, config_dir, _settings(dirs["default"]))
    return findings


def _check_drift(name, config_dir, default_settings):
    """Shared keys an account no longer agrees with the default account on."""
    if name == "default":
        return []
    settings = _settings(config_dir)
    return [_finding(WARN, "settings-drift",
                     f"{key} differs from the default account's, and settings.json is "
                     "copied once and never resynced", name, fix="settings", key=key)
            for key in SHARED_KEYS if settings.get(key) != default_settings.get(key)]


def apply_fix(finding, accounts):
    """Repair what is safely repairable. Deleting anything holding credentials is
    left to the operator, so those are reported and never removed."""
    lookup = {n: d for n, d, _ in accounts}
    kind = finding.get("fix")
    if kind == "route":
        from .accounts import read_routes, write_routes

        write_routes([row for row in read_routes() if row[0] != finding["path"]])
        return f"removed route {finding['path']}"
    if kind == "unlink":
        target = Path(finding["path"])
        if target.is_symlink():
            target.unlink()
            return f"removed dangling {target}"
    if kind == "hook":
        return _install_hook(Path(lookup[finding["account"]]) / "settings.json",
                             finding["event"], finding["command"])
    if kind == "statusline":
        path = Path(lookup[finding["account"]]) / "settings.json"
        settings = _read_json(path) or {}
        status = settings.setdefault("statusLine", {})
        status.setdefault("type", "command")
        status.setdefault("command", STATUSLINE_COMMAND)
        status["refreshInterval"] = STATUSLINE_REFRESH_SECONDS
        _write_settings(path, settings)
        return f"set statusLine to '{status['command']}' every {status['refreshInterval']}s in {path}"
    if kind == "settings":
        key = finding["key"]
        path = Path(lookup[finding["account"]]) / "settings.json"
        settings = _read_json(path) or {}
        wanted = _settings(lookup["default"]).get(key)
        if wanted is None:
            settings.pop(key, None)
        else:
            settings[key] = wanted
        _write_settings(path, settings)
        return f"copied {key} from default into {path}"
    return None


def _write_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def undo_fixes(path):
    """Take out of one settings.json exactly what apply_fix puts in, naming each removal."""
    settings = _read_json(path)
    if not isinstance(settings, dict):
        return []
    removed = []
    status = settings.get("statusLine")
    if isinstance(status, dict) and status.get("command") == STATUSLINE_COMMAND:
        del settings["statusLine"]
        removed.append(f"removed statusLine from {path}")
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    for event, command, _ in REQUIRED_HOOKS:
        entries = hooks.get(event)
        if not isinstance(entries, list) or command not in _commands_in(entries):
            continue
        hooks[event] = _without_command(entries, command)
        removed.append(f"removed {event} -> {command} from {path}")
    for event in [e for e, entries in hooks.items() if entries == []]:
        del hooks[event]
    if hooks == {} and isinstance(settings.get("hooks"), dict):
        del settings["hooks"]
    if removed:
        _write_settings(path, settings)
    return removed


def _without_command(entries, command):
    """The hook entries with one command taken out, dropping any group left empty."""
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            out.append(entry)
            continue
        kept = [h for h in entry["hooks"]
                if not (isinstance(h, dict) and h.get("command") == command)]
        if kept:
            out.append({**entry, "hooks": kept})
    return out


def _install_hook(path, event, command):
    """Add one hook under settings.json\x27s "hooks", leaving every other hook alone."""
    settings = _read_json(path)
    if not isinstance(settings, dict):
        return None
    if not isinstance(settings.get("hooks"), dict):
        settings["hooks"] = {}
    hooks = settings["hooks"]
    if not isinstance(hooks.get(event), list):
        hooks[event] = []
    if command not in _commands_in(hooks[event]):
        hooks[event].append({"hooks": [{"type": "command", "command": command}]})

    if event in settings:
        stale = [entry for entry in settings[event] if isinstance(settings[event], list)
                 and isinstance(entry, dict) and command not in _commands_in([entry])]
        if stale:
            settings[event] = stale
        else:
            del settings[event]

    path.write_text(json.dumps(settings, indent=2) + "\n")
    return f"wired {event} -> {command} in {path}"


def render_list(accounts, routes):
    """Accounts, then routes with the one governing this directory marked."""
    from .accounts import matching_route
    from .report import table

    rows = []
    for name, _, ident in accounts:
        rows.append([name, ident["tier"] or "-", ident["fingerprint"] or "-",
                     ident["email"] or "-"])
    lines = ["accounts"] + table(["account", "tier", "login", "email"], rows)

    here = matching_route()
    lines += ["", "routes"]
    lines += ([f"{'*' if here and p == here[0] else ' '} {_tilde(p)} -> {a}"
               for p, a in routes] or ["  (none)"])
    lines += ["", f"* governs {_tilde(os.getcwd())}"] if here else [
        "", f"no route covers {_tilde(os.getcwd())}, so it bills default"]
    return "\n".join(lines)


def _tilde(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def render_findings(findings):
    if not findings:
        return "no problems found"
    lines = []
    for f in findings:
        where = f" [{f['account']}]" if f["account"] else ""
        lines.append(f"{f['level']:<5}{where} {f['message']}")
    counts = {level: sum(1 for f in findings if f["level"] == level) for level in (FAIL, WARN)}
    lines.append("")
    lines.append(f"{counts[FAIL]} failing, {counts[WARN]} warnings")
    return "\n".join(lines)


def report(config, pairs, mode="list", fix=False, as_json=False, undo=False):
    """Render the inventory or the health report for a set of (name, dir) pairs."""
    from .accounts import read_routes

    if undo:
        removed = [line for _, config_dir in pairs
                   for line in undo_fixes(Path(config_dir) / "settings.json")]
        print("\n".join(removed) if removed else "nothing to remove")
        return 0
    accounts = [(name, str(config_dir), identity.read(config_dir))
                for name, config_dir in pairs]
    routes = read_routes()

    if mode == "list":
        if as_json:
            json.dump({"accounts": [{"account": n, **i} for n, _, i in accounts],
                       "routes": [{"path": p, "account": a} for p, a in routes]},
                      sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(render_list(accounts, routes))
        return 0

    findings = run_checks(config, accounts)
    repaired = []
    if fix:
        for finding in findings:
            done = apply_fix(finding, accounts)
            if done:
                repaired.append({**finding, "fixed": done})
                if not as_json:
                    print(done)
        # Re-check, and let that be the answer. A fault that was repaired is not
        # a fault any more, so counting it would report failure for work that
        # succeeded and would exit non-zero on a clean machine.
        findings = run_checks(config, accounts)

    if as_json:
        # Repairs ride along separately, because a consumer cannot otherwise tell
        # a fixed fault from one that was never there.
        json.dump({"findings": findings, "repaired": repaired}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_findings(findings))
    return 1 if any(f["level"] == FAIL for f in findings) else 0

