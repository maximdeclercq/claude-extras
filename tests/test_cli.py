"""The launcher, meaning argument handling, the deny list and the launch log."""

import os

import pytest

from claude_extras import cli
from claude_extras.accounts import AccountError


@pytest.mark.parametrize("argv,forced,rest", [
    ([], None, []),
    (["--help"], None, ["--help"]),
    (["--account", "acme"], "acme", []),
    (["-a", "acme"], "acme", []),
    (["--account=acme"], "acme", []),
    (["-a=acme"], "acme", []),
    # the flag is ours, not the real binary's, so it must be stripped from anywhere
    (["--resume", "id", "--account", "acme", "-p", "hi"], "acme", ["--resume", "id", "-p", "hi"]),
    (["-p", "prompt", "-a=acme"], "acme", ["-p", "prompt"]),
    # a later one wins, like any repeated flag
    (["--account", "one", "--account", "two"], "two", []),
])
def test_take_account_flag(argv, forced, rest):
    assert cli.take_account_flag(argv) == (forced, rest)


def test_take_account_flag_without_a_value_is_fatal():
    """A flag with nothing after it is a usage error, never an IndexError."""
    with pytest.raises(AccountError, match="--account needs a value"):
        cli.take_account_flag(["--account"])


def test_a_usage_error_reaches_the_shell_as_exit_1(capsys):
    """Whatever raises it, the user sees one line on stderr and a non-zero exit."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["usage", "--account"])
    assert exit_info.value.code == 1
    assert "--account needs a value" in capsys.readouterr().err


def test_take_account_flag_leaves_unrelated_arguments_alone():
    argv = ["--model", "opus", "--dangerously-skip-permissions"]
    assert cli.take_account_flag(argv) == (None, argv)


@pytest.fixture
def deny(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DENY", tmp_path / "deny")
    monkeypatch.setattr("claude_extras.cli.Path.home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_check_dir_allows_anywhere_not_listed(deny):
    """Client work lives on other mounts too; only a listed directory is refused."""
    cli.check_dir("/etc")


def test_check_dir_denies_the_exact_entry_only(deny, monkeypatch):
    """~/workspace is denied; the projects inside it are the whole point."""
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: deny))
    (deny / "deny").write_text(f"# comment\n\n{deny}/workspace\n")
    with pytest.raises(SystemExit):
        cli.check_dir(f"{deny}/workspace")
    cli.check_dir(f"{deny}/workspace/project")
    cli.check_dir(str(deny))


def test_check_dir_expands_tilde(deny, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: deny))
    monkeypatch.setenv("HOME", str(deny))
    (deny / "deny").write_text("~/downloads\n")
    with pytest.raises(SystemExit):
        cli.check_dir(f"{deny}/downloads")


def test_check_dir_allows_everything_without_a_deny_file(deny, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: deny))
    cli.check_dir(f"{deny}/anywhere")


def test_record_launch_dedupes_consecutive_launches(tmp_path, monkeypatch):
    log = tmp_path / "launches"
    monkeypatch.setattr(cli, "LAUNCH_LOG", log)
    cli.record_launch("acme", str(tmp_path))
    cli.record_launch("acme", str(tmp_path))
    assert len(log.read_text().splitlines()) == 1


def test_record_launch_records_a_changed_directory(tmp_path, monkeypatch):
    log = tmp_path / "launches"
    monkeypatch.setattr(cli, "LAUNCH_LOG", log)
    other = tmp_path / "other"
    other.mkdir()
    cli.record_launch("acme", str(tmp_path))
    cli.record_launch("acme", str(other))
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert lines[-1].split("\t")[-1] == str(other)


def test_record_launch_keeps_the_log_bounded(tmp_path, monkeypatch):
    log = tmp_path / "launches"
    monkeypatch.setattr(cli, "LAUNCH_LOG", log)
    monkeypatch.setattr(cli, "LOG_LIMIT", 10)
    monkeypatch.setattr(cli, "LOG_KEEP", 4)
    log.write_text("".join(f"0\tacme\t/x/{i}\t/x/{i}\n" for i in range(20)))
    cli.record_launch("acme", "/fresh")
    lines = log.read_text().splitlines()
    assert len(lines) == 4
    assert lines[-1].endswith("/fresh")


def test_record_launch_survives_an_unwritable_log(tmp_path, monkeypatch):
    """A launch must never fail because its bookkeeping could not be written."""
    monkeypatch.setattr(cli, "LAUNCH_LOG", tmp_path / "nodir" / "launches")
    cli.record_launch("acme", str(tmp_path))


def test_logical_cwd_prefers_the_symlinked_path(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(real)
    monkeypatch.chdir(real)
    monkeypatch.setenv("PWD", str(link))
    assert cli.logical_cwd() == str(link)


def test_logical_cwd_ignores_a_stale_pwd(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    monkeypatch.chdir(real)
    monkeypatch.setenv("PWD", str(tmp_path / "somewhere-else"))
    assert cli.logical_cwd() == os.getcwd()


def test_real_bin_skips_this_wrapper(tmp_path, monkeypatch):
    """The wrapper is named claude and sits on PATH, so it must not find itself."""
    ours = tmp_path / "bin"
    theirs = tmp_path / "real"
    ours.mkdir()
    theirs.mkdir()
    wrapper = ours / "claude"
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    binary = theirs / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setenv("PATH", f"{ours}{os.pathsep}{theirs}")
    monkeypatch.delenv("CLAUDE_REAL_BIN", raising=False)
    monkeypatch.setattr(cli.sys, "argv", [str(wrapper)])
    assert cli.real_bin() == str(binary)


def test_real_bin_honours_an_explicit_override(monkeypatch):
    monkeypatch.setenv("CLAUDE_REAL_BIN", "/opt/claude/bin/claude")
    assert cli.real_bin() == "/opt/claude/bin/claude"


def test_real_bin_falls_back_to_the_newest_native_version(tmp_path, monkeypatch):
    """uv tool install --force replaces the native launcher at ~/.local/bin/claude,
    after which the only real binaries left are the versions the installer keeps."""
    ours = tmp_path / "bin"
    ours.mkdir()
    wrapper = ours / "claude"
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    versions = tmp_path / ".local" / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    for name in ("2.1.99", "2.1.210", "2.1.210.partial"):
        (versions / name).write_text("#!/bin/sh\n")
        (versions / name).chmod(0o755)

    monkeypatch.setenv("PATH", str(ours))
    monkeypatch.delenv("CLAUDE_REAL_BIN", raising=False)
    monkeypatch.setattr(cli.sys, "argv", [str(wrapper)])
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
    assert cli.real_bin() == str(versions / "2.1.210"), "numeric order, whole versions only"


def test_real_bin_dies_with_nothing_to_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CLAUDE_REAL_BIN", raising=False)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
    with pytest.raises(SystemExit):
        cli.real_bin()


def test_check_dir_cannot_be_dodged_through_a_symlink(deny, monkeypatch, tmp_path):
    """Routing matches the typed path and the resolved one; the guards must too,
    or a symlink under $HOME reaches a directory either rule was meant to refuse."""
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: deny))
    real = deny / "workspace"
    real.mkdir()
    (deny / "deny").write_text(f"{real}\n")
    alias = deny / "alias"
    alias.symlink_to(real)
    with pytest.raises(SystemExit):
        cli.check_dir(str(alias))


def test_a_leading_double_dash_gives_up_our_verbs(monkeypatch):
    """Our verb names are taken from the real CLI's namespace, so there has to be
    a way to reach a native verb that later collides with one of them."""
    handed = []
    monkeypatch.setattr(cli, "launch", handed.append)
    monkeypatch.setattr(cli, "dispatch", lambda name, rest: pytest.fail("intercepted"))
    cli.main(["--", "usage", "--json"])
    assert handed == [["usage", "--json"]], "exactly one launch, with the -- stripped"


def test_our_verbs_are_still_intercepted_without_it(monkeypatch):
    monkeypatch.setattr(cli, "launch", lambda argv: pytest.fail("should not exec"))
    monkeypatch.setattr(cli, "dispatch", lambda name, rest: (name, rest))
    assert cli.main(["usage", "--json"]) == ("usage", ["--json"])


@pytest.mark.parametrize("short,full", [("r", "resume"), ("c", "chat"), ("u", "usage")])
def test_one_letter_aliases_reach_their_verb(short, full, monkeypatch):
    monkeypatch.setattr(cli, "launch", lambda argv: pytest.fail("should not exec"))
    monkeypatch.setattr(cli, "dispatch", lambda name, rest: (name, rest))
    assert cli.main([short, "--json"]) == (full, ["--json"])


def test_an_unknown_short_word_still_goes_to_the_real_binary(monkeypatch):
    """Aliases must not swallow anything the real CLI might mean."""
    handed = []
    monkeypatch.setattr(cli, "launch", handed.append)
    monkeypatch.setattr(cli, "dispatch", lambda name, rest: pytest.fail("intercepted"))
    cli.main(["x", "-p", "hi"])
    assert handed == [["x", "-p", "hi"]]
