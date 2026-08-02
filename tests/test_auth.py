"""Account bootstrap, the one flow every new user runs first."""

import json

import pytest

from claude_extras import accounts, auth
from claude_extras.accounts import AccountError


@pytest.fixture
def config(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    default = tmp_path / "default"
    (cfg / "accounts").mkdir(parents=True)
    (cfg / "auth").mkdir()
    default.mkdir()
    for module in (accounts, auth):
        monkeypatch.setattr(module, "CONFIG", cfg, raising=False)
        monkeypatch.setattr(module, "ACCOUNTS_DIR", cfg / "accounts", raising=False)
        monkeypatch.setattr(module, "AUTH_DIR", cfg / "auth", raising=False)
        monkeypatch.setattr(module, "DEFAULT_DIR", default, raising=False)
    monkeypatch.setattr(accounts, "ROUTES", cfg / "routes")
    monkeypatch.setattr(auth, "project_root", lambda: "/work/acme")
    return {"cfg": cfg, "default": default}


def test_login_bootstraps_an_account_that_does_not_exist_yet(config, monkeypatch):
    """The documented onboarding command, on a machine with nothing set up."""
    handed = {}
    monkeypatch.setattr(auth, "native", lambda verb, args: handed.update(verb=verb))

    auth.cmd_login("acme")

    assert accounts.read_routes() == [("/work/acme", "acme")]
    assert (config["cfg"] / "accounts" / "acme").is_dir()
    # the native login has to run in the new account's dir, or it writes the
    # credentials into whatever account was already active
    assert handed["verb"] == "login"


def test_login_creates_the_account_dir_private(config, monkeypatch):
    """The account names alone say which businesses you hold separate logins for."""
    monkeypatch.setattr(auth, "native", lambda verb, args: None)
    auth.cmd_login("acme")
    assert (config["cfg"] / "accounts" / "acme").stat().st_mode & 0o777 == 0o700


def test_login_skips_the_native_login_when_credentials_already_exist(config, monkeypatch):
    called = []
    monkeypatch.setattr(auth, "native", lambda verb, args: called.append(verb))
    account = config["cfg"] / "accounts" / "acme"
    account.mkdir()
    (account / ".credentials.json").write_text("{}")

    assert auth.cmd_login("acme") == 0
    assert called == []


def test_login_to_default_unbinds_without_creating_anything(config):
    accounts.write_routes([("/work/acme", "acme")])
    assert auth.cmd_login("default") == 0
    assert accounts.read_routes() == []
    assert not (config["cfg"] / "accounts" / "default").exists()


def test_login_refuses_a_scope_word_as_an_account_name(config, monkeypatch):
    """`all` selects a scope everywhere else; an account named it is unreachable.

    native is stubbed even though it must never be reached: cmd_login's fall-through
    execs into an interactive login, which would replace the test process and hang
    the suite rather than fail it.
    """
    monkeypatch.setattr(auth, "native", lambda verb, args: pytest.fail("reached native login"))
    with pytest.raises(AccountError):
        auth.cmd_login("all")
    assert accounts.read_routes() == []


def test_save_refuses_a_scope_word_too(config, tmp_path, monkeypatch):
    """`auth save all` used to write auth/all.json, which known_accounts then
    filters out again: a credential on disk that nothing can ever select."""
    live = tmp_path / "live"
    live.mkdir()
    (live / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshToken": "r"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(live))

    with pytest.raises(AccountError):
        auth.cmd_save("all")
    assert not (config["cfg"] / "auth" / "all.json").exists()


def test_routing_still_refuses_an_account_with_no_credentials(config):
    """Bootstrapping is login's privilege only; a launch must not invent an account."""
    (config["cfg"] / "accounts" / "ghost").mkdir()
    with pytest.raises(AccountError, match="claude auth login ghost"):
        accounts.ensure_account_dir("ghost")


def test_save_writes_the_credential_private(config, tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    (live / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshToken": "r"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(live))

    assert auth.cmd_save("acme") == 0
    snapshot = config["cfg"] / "auth" / "acme.json"
    assert snapshot.stat().st_mode & 0o777 == 0o600


def test_save_refuses_to_replace_a_different_login(config, tmp_path, monkeypatch):
    """The source is whichever login the current directory routes to, so a slip in
    the name must not replace another account's snapshot."""
    live = tmp_path / "live"
    live.mkdir()
    (live / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshToken": "r"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(live))
    assert auth.cmd_save("acme") == 0
    assert auth.cmd_save("acme") == 0, "the same login again is a refresh, not a clobber"

    (live / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshToken": "x"}}))
    with pytest.raises(AccountError, match="different login"):
        auth.cmd_save("acme")
    assert auth.cmd_save("acme-two") == 0, "a name nobody holds is always free"


def test_save_without_a_name_is_a_usage_error(config):
    assert auth.cmd_save("") == 1


def test_help_is_recognised_after_a_verb(config, capsys):
    """`claude auth login --help` used to die with "account '--help' has no credentials"."""
    assert auth.main(["login", "--help"]) == 0
    assert "Usage: claude auth" in capsys.readouterr().out
