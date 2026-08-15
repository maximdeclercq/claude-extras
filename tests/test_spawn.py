"""Terminal selection and spawning."""

import pytest

from claude_extras import spawn


@pytest.fixture
def no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(spawn, "TERMINAL", tmp_path / "terminal")
    monkeypatch.delenv("CLAUDE_TERMINAL", raising=False)
    return tmp_path


def test_terminal_from_environment_wins(no_config, monkeypatch):
    monkeypatch.setenv("CLAUDE_TERMINAL", "kitty")
    binary, args = spawn.terminal_command()
    assert binary == "kitty"
    assert "--title" in args


def test_terminal_keeps_configured_arguments(no_config, monkeypatch):
    monkeypatch.setenv("CLAUDE_TERMINAL", "alacritty -o font.size=12")
    binary, args = spawn.terminal_command()
    assert binary == "alacritty"
    assert args[:2] == ["-o", "font.size=12"]
    # -e introduces the command, so it has to stay last
    assert args[-1] == "-e"


def test_terminal_from_config_file(no_config, monkeypatch):
    (no_config / "terminal").write_text("foot\n")
    assert spawn.terminal_command()[0] == "foot"


def test_unknown_terminal_still_works(no_config, monkeypatch):
    """A terminal we have no table entry for gets the command appended bare."""
    monkeypatch.setenv("CLAUDE_TERMINAL", "st")
    assert spawn.terminal_command() == ("st", [])


def test_terminal_autodetects_when_unconfigured(no_config, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda name: name == "kitty")
    assert spawn.terminal_command()[0] == "kitty"


def test_no_terminal_available(no_config, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda name: False)
    assert spawn.terminal_command() == (None, [])


def test_spawn_without_a_terminal_is_a_clear_error(no_config, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda name: False)
    with pytest.raises(SystemExit, match="no terminal found"):
        spawn.spawn("/tmp")


def test_spawned_session_does_not_inherit_the_callers_identity(monkeypatch):
    """Carrying CLAUDE_CODE_CHILD_SESSION across turns transcript saving off."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/some/parent/account")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = spawn.child_environment()

    for name in spawn.PARENT_SESSION_VARS:
        assert name not in env, f"{name} leaked into the spawned session"
    assert env["PATH"] == "/usr/bin", "unrelated environment must survive"
