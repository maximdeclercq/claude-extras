"""Account layout and routing."""

import json
import os

import pytest

from claude_extras import accounts
from claude_extras.accounts import AccountError


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Point the whole module at a throwaway config tree."""
    cfg = tmp_path / "config"
    default = tmp_path / "default"
    (cfg / "accounts").mkdir(parents=True)
    (cfg / "auth").mkdir()
    default.mkdir()
    monkeypatch.setattr(accounts, "CONFIG", cfg)
    monkeypatch.setattr(accounts, "ACCOUNTS_DIR", cfg / "accounts")
    monkeypatch.setattr(accounts, "AUTH_DIR", cfg / "auth")
    monkeypatch.setattr(accounts, "ROUTES", cfg / "routes")
    monkeypatch.setattr(accounts, "DEFAULT_DIR", default)
    return {"cfg": cfg, "default": default, "tmp": tmp_path}


def write_routes(config, rows):
    config["cfg"].joinpath("routes").write_text(
        "".join(f"{p}\t{a}\n" for p, a in rows))


def make_account(config, name, credentials=True):
    d = config["cfg"] / "accounts" / name
    d.mkdir(parents=True, exist_ok=True)
    if credentials:
        (d / ".credentials.json").write_text("{}")
    return d


def test_unrouted_directory_is_the_default_account(config):
    write_routes(config, [("/work/acme", "acme")])
    assert accounts.resolve_account("/somewhere/else") == "default"


def test_longest_prefix_wins(config):
    write_routes(config, [("/work", "outer"), ("/work/acme", "inner")])
    assert accounts.resolve_account("/work/acme/sub/dir") == "inner"


def test_prefix_match_is_on_path_components(config):
    """/work/acme must not capture /work/acme-other."""
    write_routes(config, [("/work/acme", "acme")])
    assert accounts.resolve_account("/work/acme-other") == "default"
    assert accounts.resolve_account("/work/acme") == "acme"
    assert accounts.resolve_account("/work/acme/deep") == "acme"


def test_symlinked_project_cannot_dodge_its_route(config, tmp_path):
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(real)
    write_routes(config, [(str(real), "acme")])
    assert accounts.resolve_account(str(link)) == "acme"


def test_binding_default_unsets_the_variable(config, monkeypatch):
    """Routing to default means unsetting CLAUDE_CONFIG_DIR, not pointing at it."""
    write_routes(config, [("/work/acme", "default")])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/stale/from/another/shell")
    accounts.bind_env(accounts.resolve_account("/work/acme"))
    assert "CLAUDE_CONFIG_DIR" not in os.environ
    accounts.bind_env("acme")
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(accounts.ACCOUNTS_DIR / "acme")


def test_known_accounts_hides_reserved_words(config):
    make_account(config, "acme")
    (config["cfg"] / "auth" / "default.json").write_text("{}")
    (config["cfg"] / "auth" / "all.json").write_text("{}")
    assert accounts.known_accounts() == ["acme"]


def test_select_accounts_all_lists_default_first(config):
    make_account(config, "acme")
    make_account(config, "beta")
    got = [name for name, _ in accounts.select_accounts("all")]
    assert got == ["default", "acme", "beta"]


def test_select_accounts_named_and_unknown(config):
    make_account(config, "acme")
    assert accounts.select_accounts("acme")[0][0] == "acme"
    with pytest.raises(AccountError):
        accounts.select_accounts("nope")


def test_ensure_account_dir_refuses_without_credentials(config):
    (config["cfg"] / "accounts" / "ghost").mkdir()
    with pytest.raises(AccountError, match="no credentials"):
        accounts.ensure_account_dir("ghost")


def test_ensure_account_dir_creates_nothing_when_it_refuses(config):
    with pytest.raises(AccountError):
        accounts.ensure_account_dir("ghost")
    assert not (config["cfg"] / "accounts" / "ghost").exists()


def test_ensure_account_dir_copies_settings_and_links_shared(config):
    (config["default"] / "settings.json").write_text('{"model": "opus"}')
    (config["default"] / "CLAUDE.md").write_text("shared rules")
    (config["default"] / "hooks").mkdir()
    make_account(config, "acme")

    account_dir = accounts.ensure_account_dir("acme")

    settings = account_dir / "settings.json"
    assert settings.is_file() and not settings.is_symlink()
    assert settings.read_text() == '{"model": "opus"}'
    # shared, so the account tracks edits to the original
    assert (account_dir / "CLAUDE.md").is_symlink()
    assert (account_dir / "hooks").is_symlink()


def test_ensure_account_dir_never_overwrites_diverged_settings(config):
    (config["default"] / "settings.json").write_text('{"model": "opus"}')
    account_dir = make_account(config, "acme")
    (account_dir / "settings.json").write_text('{"model": "sonnet"}')

    accounts.ensure_account_dir("acme")

    assert (account_dir / "settings.json").read_text() == '{"model": "sonnet"}'


def test_ensure_account_dir_installs_saved_credentials(config):
    (config["cfg"] / "auth" / "acme.json").write_text('{"token": "x"}')
    account_dir = accounts.ensure_account_dir("acme")
    creds = account_dir / ".credentials.json"
    assert creds.is_file()
    assert creds.stat().st_mode & 0o777 == 0o600


def test_seed_plugins_rewrites_marketplace_locations(config):
    plugins = config["default"] / "plugins"
    (plugins / "marketplaces" / "shop").mkdir(parents=True)
    (plugins / "cache").mkdir()
    (plugins / "data").mkdir()
    (plugins / "known_marketplaces.json").write_text(json.dumps({
        "shop": {"installLocation": str(plugins / "marketplaces" / "shop")},
        "elsewhere": {"installLocation": "/opt/somewhere/else"},
    }))
    account_dir = make_account(config, "acme")

    accounts.seed_plugins(account_dir)

    # the cache is derived and huge, so it is shared by symlink
    assert (account_dir / "plugins" / "cache").is_symlink()
    # a marketplace must be a real checkout under this account, or refreshes fail
    assert (account_dir / "plugins" / "marketplaces" / "shop").is_dir()
    assert not (account_dir / "plugins" / "marketplaces" / "shop").is_symlink()

    written = json.loads((account_dir / "plugins" / "known_marketplaces.json").read_text())
    assert written["shop"]["installLocation"] == str(
        account_dir / "plugins" / "marketplaces" / "shop")
    assert written["elsewhere"]["installLocation"] == "/opt/somewhere/else"


def test_routes_round_trip(config):
    accounts.write_routes([("/work/acme", "acme"), ("/work/beta", "beta")])
    assert accounts.read_routes() == [("/work/acme", "acme"), ("/work/beta", "beta")]


def test_routes_ignore_comments_and_blanks(config):
    config["cfg"].joinpath("routes").write_text(
        "# a comment\n\n/work/acme\tacme\n\n")
    assert accounts.read_routes() == [("/work/acme", "acme")]
