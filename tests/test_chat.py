"""Which account a chat lands in."""

import json

import pytest

from claude_extras import args, chat
from claude_extras.accounts import AccountError


@pytest.fixture
def routing(tmp_path, monkeypatch):
    """A world with two accounts and nothing routed."""
    monkeypatch.setattr(chat, "known_accounts", lambda: ["acme", "beta"])
    monkeypatch.setattr(chat.spawn, "focused_cwd", lambda: None)
    monkeypatch.setattr(chat, "resolve_account", lambda d: "default")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    return tmp_path


def test_explicit_account_wins(routing, monkeypatch):
    monkeypatch.setattr(chat.spawn, "focused_cwd", lambda: "/work/beta")
    monkeypatch.setattr(chat, "resolve_account", lambda d: "beta")
    assert chat.chat_account("acme") == "acme"


def test_default_is_a_real_choice(routing):
    """default is the bucket for chats with nothing to protect, not a rejection."""
    assert chat.chat_account("default") == "default"


def test_current_directory_beats_the_focused_window(routing, monkeypatch):
    """Typing the command somewhere is a clearer statement than window focus."""
    monkeypatch.setattr(chat.spawn, "focused_cwd", lambda: "/work/beta")

    def resolve(directory):
        return "acme" if str(directory) == str(routing) else "beta"

    monkeypatch.setattr(chat, "resolve_account", resolve)
    assert chat.chat_account() == "acme"


def test_focused_window_used_when_the_directory_is_unrouted(routing, monkeypatch):
    """A status bar has no meaningful directory, so it follows the window."""
    monkeypatch.setattr(chat.spawn, "focused_cwd", lambda: "/work/beta")

    def resolve(directory):
        return "beta" if str(directory) == "/work/beta" else "default"

    monkeypatch.setattr(chat, "resolve_account", resolve)
    assert chat.chat_account() == "beta"


def test_unrouted_is_default_not_the_last_account_used(routing):
    """default is the commonest bucket; nothing may shadow it."""
    assert chat.chat_account() == "default"


def test_no_named_accounts_still_works(routing, monkeypatch):
    """A fresh install with no accounts can still keep generic chats."""
    monkeypatch.setattr(chat, "known_accounts", list)
    assert chat.chat_account() == "default"


def test_account_prints_the_account_a_click_would_use(routing, monkeypatch, capsys):
    monkeypatch.setattr(chat, "resolve_account", lambda d: "acme.dev")
    monkeypatch.setattr(chat.chats, "chats_root", lambda account: "/work/acme.dev/chats")
    assert chat.cmd_account(None) == 0
    assert capsys.readouterr().out == "acme.dev\n"


def test_account_fails_when_a_click_would(routing, monkeypatch):
    """A button must not promise an account whose chats have nowhere to live."""
    monkeypatch.setattr(chat, "resolve_account", lambda d: "acme.dev")
    with pytest.raises(AccountError, match="no route"):
        chat.cmd_account(None)


def test_json_listing_takes_every_chat(monkeypatch, capsys):
    """A picker filters what it is given, so a truncated list cannot be searched past."""
    seen = []
    monkeypatch.setattr(chat, "chat_account", lambda explicit=None: "acme")
    monkeypatch.setattr(chat, "list_rows", lambda account, limit: seen.append(limit) or
                        [{"account": account, "title": "t", "activity": 1}])
    assert chat.main(["--json"]) == 0
    assert seen == [None], "no limit unless asked"
    assert json.loads(capsys.readouterr().out)[0]["account"] == "acme"


def test_new_chat_opens_a_window_when_there_is_no_terminal(routing, monkeypatch, tmp_path):
    """A desktop button has no tty; exec'ing claude there kills it instantly."""
    made = tmp_path / "2026-08-15-untitled-1200"
    made.mkdir()
    monkeypatch.setattr(chat.chats, "new_chat_dir", lambda a, d="": made)
    monkeypatch.setattr(chat.sys.stdout, "isatty", lambda: False)

    opened = []
    monkeypatch.setattr(chat.spawn, "spawn",
                        lambda d, a=(), account=None: opened.append((str(d), account)))

    assert chat.cmd_new("acme", "") == 0
    assert opened == [(str(made), "acme")]


def test_new_chat_takes_over_the_terminal_when_there_is_one(routing, monkeypatch, tmp_path):
    made = tmp_path / "2026-08-15-untitled-1200"
    made.mkdir()
    monkeypatch.setattr(chat.chats, "new_chat_dir", lambda a, d="": made)
    monkeypatch.setattr(chat.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(chat.spawn, "spawn",
                        lambda *a, **k: pytest.fail("should not open a second window"))

    launched = []
    import claude_extras.cli as cli_mod
    monkeypatch.setattr(cli_mod, "launch", lambda argv: launched.append(argv))

    chat.cmd_new("acme", "")
    assert launched == [["--account", "acme"]]


def test_account_all_spans_every_account_newest_first(monkeypatch):
    """`all` is the same scope word usage and recent take. chat used to reject it
    with advice ("claude auth login all") naming a word that can never be an
    account, so the flag cli.py advertises did nothing but confuse."""
    monkeypatch.setattr(chat, "chat_accounts", lambda: ["default", "acme"])
    monkeypatch.setattr(chat.chats, "tidy", lambda account, config_dir: None)
    monkeypatch.setattr(chat.chats, "chats_root", lambda account: f"/chats/{account}")
    monkeypatch.setattr(chat, "config_dir_for", lambda account: f"/cfg/{account}")
    monkeypatch.setattr(chat.sessions, "load_logical_map", lambda path: {})

    per_account = {
        "default": [{"activity": 10, "account": "default", "title": "older"}],
        "acme": [{"activity": 99, "account": "acme", "title": "newest"}],
    }
    monkeypatch.setattr(chat.sessions, "sessions_for",
                        lambda name, cfg, logical, want, under=None: per_account[name])

    rows = chat.list_rows("all", limit=10)
    assert [r["title"] for r in rows] == ["newest", "older"]


def test_limit_rejects_a_non_number():
    with pytest.raises(AccountError, match="positive number"):
        args.take_count("abc")


def test_limit_rejects_zero():
    with pytest.raises(AccountError, match="positive number"):
        args.take_count("0")
