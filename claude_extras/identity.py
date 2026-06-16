"""Who an account dir is logged in as, and which accounts share a quota meter."""

import hashlib
import json
from pathlib import Path

TIER_LABELS = {
    "default_claude_max_20x": "max 20x",
    "default_claude_max_5x": "max 5x",
}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _account_state(config_dir):
    """Claude Code's own state file for a config dir."""
    inside = _read_json(Path(config_dir) / ".claude.json")
    if inside is not None:
        return inside
    try:
        is_default = Path(config_dir).resolve() == (Path.home() / ".claude").resolve()
    except OSError:
        is_default = False
    return (_read_json(Path.home() / ".claude.json") or {}) if is_default else {}


def _oauth(credentials_path):
    return (_read_json(credentials_path) or {}).get("claudeAiOauth") or {}


def _fingerprint(creds):
    refresh = creds.get("refreshToken") or ""
    return hashlib.sha256(refresh.encode()).hexdigest()[:8] if refresh else None


def fingerprint(credentials_path):
    """The login a credentials file holds, as the short id `auth list` shows, or None."""
    return _fingerprint(_oauth(credentials_path))


def read(config_dir):
    """Identity and subscription facts for one config dir."""
    creds = _oauth(Path(config_dir) / ".credentials.json")
    oauth = _account_state(config_dir).get("oauthAccount") or {}
    tier = creds.get("rateLimitTier")
    return {
        "user_uuid": oauth.get("accountUuid"),
        "org_uuid": oauth.get("organizationUuid"),
        "email": oauth.get("emailAddress"),
        "tier": TIER_LABELS.get(tier, tier.replace("default_claude_", "") if tier else None),
        "subscription": creds.get("subscriptionType"),
        "fingerprint": _fingerprint(creds),
        "expires_at": creds.get("expiresAt"),
        "has_credentials": bool(creds),
    }


def subscription_key(ident):
    """What decides that two accounts draw on the same quota meter."""
    return (ident.get("user_uuid"), ident.get("org_uuid"), ident.get("tier"))


def group(accounts):
    """Group [(name, identity), ...] by subscription, preserving input order."""
    subs, order = {}, []
    for name, ident in accounts:
        key = subscription_key(ident)
        if key not in subs:
            subs[key] = {"key": key, "tier": ident.get("tier"), "accounts": [], "logins": set()}
            order.append(key)
        subs[key]["accounts"].append(name)
        if ident.get("fingerprint"):
            subs[key]["logins"].add(ident["fingerprint"])
    return [{**subs[k], "logins": len(subs[k]["logins"]), "key": list(k)} for k in order]
