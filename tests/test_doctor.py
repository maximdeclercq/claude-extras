"""Doctor reports faults that fail silently, so its own report must not."""

import json

import pytest

from claude_extras import doctor


@pytest.fixture
def account(tmp_path):
    """One account with valid credentials and no settings.json (a fixable fault)."""
    config = tmp_path / "config"
    config.mkdir()
    account_dir = config / "acct"
    account_dir.mkdir()
    (account_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r", "expiresAt": 1}}))
    return config, account_dir


def _run(config, account_dir, fix=False, as_json=False):
    return doctor.report(config, [("acct", account_dir)], "doctor",
                         fix=fix, as_json=as_json)


def test_a_fixable_fault_fails_before_the_fix(account, capsys):
    config, account_dir = account
    assert _run(config, account_dir) == 1
    assert "cleanupPeriodDays" in capsys.readouterr().out


def test_fix_repairs_and_then_reports_success(account, capsys):
    """A repaired fault is not a fault; reporting it as one exits non-zero on a
    clean machine and sends the caller looking for a problem that is gone."""
    config, account_dir = account
    assert _run(config, account_dir, fix=True) == 0
    out = capsys.readouterr().out
    # The repaired fault must not appear in the report that follows the repair.
    # Asserting on the summary line instead would pin whichever of the two shapes
    # render_findings happens to pick when the list empties.
    assert not any(line.startswith("fail") for line in out.splitlines())
    assert "cleanupPeriodDays unset" in out, "retention is advice, never written for you"
    written = json.loads((account_dir / "settings.json").read_text())
    assert written["statusLine"]["command"] == doctor.STATUSLINE_COMMAND


def test_fix_is_idempotent(account):
    config, account_dir = account
    _run(config, account_dir, fix=True)
    assert _run(config, account_dir, fix=True) == 0


def test_json_keeps_repairs_separate_from_remaining_findings(account, capsys):
    """A consumer cannot otherwise tell a fixed fault from one never present."""
    config, account_dir = account
    _run(config, account_dir, fix=True, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert "no-statusline" in [f["code"] for f in payload["repaired"]]
    assert "no-statusline" not in [f["code"] for f in payload["findings"]]


def test_undo_takes_out_only_what_fix_put_in(account):
    config, account_dir = account
    path = account_dir / "settings.json"
    theirs = {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}
    path.write_text(json.dumps({"hooks": theirs, "cleanupPeriodDays": 30}))
    _run(config, account_dir, fix=True)

    removed = doctor.undo_fixes(path)
    assert len(removed) == 1 + len(doctor.REQUIRED_HOOKS)
    left = json.loads(path.read_text())
    assert "statusLine" not in left
    assert left["hooks"] == theirs
    assert left["cleanupPeriodDays"] == 30, "never ours to remove"
    assert doctor.undo_fixes(path) == []


def test_undo_keeps_a_statusline_that_is_not_ours(account):
    _, account_dir = account
    path = account_dir / "settings.json"
    path.write_text(json.dumps({"statusLine": {"type": "command", "command": "starship"}}))
    assert doctor.undo_fixes(path) == []
    assert json.loads(path.read_text())["statusLine"]["command"] == "starship"


def test_drift_from_default_is_reported_and_fix_copies_it_over(tmp_path, capsys):
    """settings.json is copied once at seeding; a hook added to default later is
    exactly the kind of thing that then silently never runs on the other accounts."""
    config, default, acct = tmp_path / "config", tmp_path / "default", tmp_path / "acct"
    for directory in (config, default, acct):
        directory.mkdir()
    for directory, token in ((default, "r"), (acct, "s")):
        (directory / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"refreshToken": token, "expiresAt": 1}}))
    theirs = {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}
    (default / "settings.json").write_text(json.dumps({"hooks": theirs}))
    (acct / "settings.json").write_text(json.dumps({"model": "opus"}))
    pairs = [("default", default), ("acct", acct)]

    doctor.report(config, pairs, "doctor", as_json=True)
    codes = [(f["account"], f["code"]) for f in json.loads(capsys.readouterr().out)["findings"]]
    assert ("acct", "settings-drift") in codes
    assert ("default", "settings-drift") not in codes

    doctor.report(config, pairs, "doctor", fix=True, as_json=True)
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert not any(f["code"] == "settings-drift" for f in findings)
    written = json.loads((acct / "settings.json").read_text())
    assert written["hooks"] == json.loads((default / "settings.json").read_text())["hooks"]
    assert written["model"] == "opus", "per-account keys are left alone"


def test_fix_never_touches_what_holds_a_credential(tmp_path, capsys):
    """Orphan snapshots are the last copy of a credential, so they are reported only."""
    config = tmp_path / "config"
    (config / "auth").mkdir(parents=True)
    orphan = config / "auth" / "gone.json"
    orphan.write_text("{}")
    account_dir = config / "acct"
    account_dir.mkdir()
    (account_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r", "expiresAt": 1}}))
    (account_dir / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 3650}))

    _run(config, account_dir, fix=True)
    assert orphan.is_file()


def test_a_dangling_symlink_is_repaired(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    account_dir = config / "acct"
    account_dir.mkdir()
    (account_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r", "expiresAt": 1}}))
    (account_dir / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 3650}))
    (account_dir / "hooks").symlink_to(tmp_path / "nowhere")

    assert _run(config, account_dir) == 1
    _run(config, account_dir, fix=True)
    assert not (account_dir / "hooks").is_symlink()


def test_two_accounts_on_one_credential_is_a_failure(tmp_path):
    """One rotates the token and the other is left with a stale copy."""
    config = tmp_path / "config"
    config.mkdir()
    pairs = []
    for name in ("one", "two"):
        account_dir = config / name
        account_dir.mkdir()
        (account_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"refreshToken": "same", "expiresAt": 1}}))
        (account_dir / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 3650}))
        pairs.append((name, account_dir))

    assert doctor.report(config, pairs, "doctor") == 1


@pytest.mark.parametrize("settings", [
    {"hooks": ["not", "a", "dict"]},
    {"SessionStart": "oops"},
    {"hooks": {"SessionStart": "nope"}},
    # a hooks block both misplaced AND mis-shaped, which is the exact fault the
    # misplaced-hook check exists to report
    {"SessionEnd": {"hooks": [{"type": "command", "command": "claude chat tidy"}]}},
])
def test_hook_checks_survive_a_hand_mangled_settings_file(account, settings):
    """settings.json is hand-edited, so a checker that crashes on a malformed one
    is useless exactly when it is needed."""
    config, account_dir = account
    (account_dir / "settings.json").write_text(json.dumps({**settings, "cleanupPeriodDays": 3650}))
    assert _run(config, account_dir) in (0, 1)


def test_fix_wires_a_misplaced_hook_where_claude_code_reads_it(account):
    """The documented wiring put these at the top level, where nothing runs them."""
    config, account_dir = account
    (account_dir / "settings.json").write_text(json.dumps({
        "cleanupPeriodDays": 3650,
        "SessionEnd": [{"hooks": [{"type": "command", "command": "claude chat tidy"}]}],
        "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]},
    }))
    _run(config, account_dir, fix=True)
    written = json.loads((account_dir / "settings.json").read_text())

    commands = [h["command"] for e in written["hooks"]["SessionEnd"] for h in e["hooks"]]
    assert commands == ["mine.sh", "claude chat tidy"], "keeps your own hook, adds ours"
    assert "SessionEnd" not in written, "the inert top-level copy is removed"


def test_list_marks_the_route_that_governs_this_directory(account, monkeypatch, capsys):
    """Routing is a longest-prefix match; without the marker you do it by eye."""
    config, account_dir = account
    rows = [("/work", "globex"), ("/work/acme", "acme")]
    monkeypatch.setattr("claude_extras.accounts.read_routes", lambda: rows)
    monkeypatch.setattr("claude_extras.accounts.Path.cwd", staticmethod(lambda: "/work/acme/sub"))

    doctor.report(config, [("acct", account_dir)], "list")
    out = capsys.readouterr().out
    assert "* /work/acme -> acme" in out, "the longest match is marked"
    assert "  /work -> globex" in out, "the shorter one is not"


def test_fix_wires_a_statusline_where_there_is_none(account):
    """This is the install step. Without a statusLine nothing ever caches the
    5h/7d burn, so `claude usage` reports no percentages and never says why."""
    config, account_dir = account
    _run(config, account_dir, fix=True)

    status = json.loads((account_dir / "settings.json").read_text())["statusLine"]
    assert status == {"type": "command", "command": "claude status", "refreshInterval": 5}


def test_fix_keeps_a_statusline_command_someone_else_chose(account):
    """A bar of their own is theirs to keep. Only the missing interval is ours."""
    config, account_dir = account
    (account_dir / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "my-own-bar"}}))
    _run(config, account_dir, fix=True)

    status = json.loads((account_dir / "settings.json").read_text())["statusLine"]
    assert status["command"] == "my-own-bar" and status["refreshInterval"] == 5
