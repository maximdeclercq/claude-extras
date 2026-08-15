import json
import os
from pathlib import Path

import pytest

from claude_extras import chats, sessions
from claude_extras.accounts import AccountError


def test_encode_cwd_matches_claude_code():
    assert (sessions.encode_cwd("/home/user/work/globex.io/chats/2026-03-08-vof")
            == "-home-user-work-globex-io-chats-2026-03-08-vof")


def test_encode_cwd_is_lossy_in_the_documented_way():
    # '.', '/' and '-' all collapse, which is why nothing reads a cwd back out of
    # an encoded name. Pinned so a "cleverer" encoding cannot silently orphan
    # every transcript Claude Code has already written.
    assert sessions.encode_cwd("/a.b/c-d") == sessions.encode_cwd("/a-b/c/d")


@pytest.mark.parametrize("title,expected", [
    ("VOF ontbinding en vereffening", "vof-ontbinding-en-vereffening"),
    # accented Dutch and French titles are common here, so they must transliterate
    ("Belgi\xeb accenten \xe9\xe8\xea", "belgie-accenten-eee"),
    ("  Multiple   spaces  ", "multiple-spaces"),
    ("Symbols !@#$ here", "symbols-here"),
    ("", ""),
    # nothing survives, so callers have to cope with an empty slug
    ("!!!", ""),
])
def test_slugify(title, expected):
    assert chats.slugify(title) == expected


def test_slugify_cuts_at_a_word_boundary():
    slug = chats.slugify("alpha beta gamma delta epsilon zeta eta theta", max_len=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")
    assert "alpha-beta-gamma-delta".startswith(slug)


def test_chat_dir_name_titled_and_untitled():
    assert chats.chat_dir_name("2026-03-08", "Paris trip") == "2026-03-08-paris-trip"
    assert chats.chat_dir_name("2026-03-08", "", suffix="1642") == "2026-03-08-untitled-1642"
    assert chats.chat_dir_name("2026-03-08", "") == "2026-03-08-untitled"


def test_chat_dir_name_disambiguates_with_uuid():
    name = chats.chat_dir_name("2026-03-08", "Paris", uuid="a1b2c3d4-dead-beef")
    assert name == "2026-03-08-paris-a1b2"


def test_generated_placeholder_names_round_trip():
    """Whatever chat_dir_name emits for an untitled chat, tidy must recognise."""
    for suffix in ("1642", "235959", "a1b2"):
        assert chats.is_untitled(chats.chat_dir_name("2026-03-08", "", suffix=suffix))
    assert chats.is_untitled(chats.chat_dir_name("2026-03-08", ""))


def test_is_untitled_leaves_real_titles_alone():
    # a chat about untitled things is not a placeholder
    assert not chats.is_untitled("2026-03-08-untitled-chapters-of-a-book")
    assert not chats.is_untitled("2026-03-08-paris-trip")
    assert not chats.is_untitled("untitled")


def _write_transcript(path, session, cwd):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "mode", "mode": "normal", "sessionId": session},
        {"type": "ai-title", "aiTitle": "Cheap parking near Paris", "sessionId": session},
        {"type": "user", "uuid": "u1", "sessionId": session, "cwd": cwd,
         "message": {"role": "user", "content": "hi"}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_read_ai_title(tmp_path):
    transcript = tmp_path / "s.jsonl"
    _write_transcript(transcript, "s", "/somewhere")
    assert chats.read_ai_title(transcript) == "Cheap parking near Paris"


def test_read_ai_title_absent(tmp_path):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({"type": "mode", "mode": "normal"}) + "\n")
    assert chats.read_ai_title(transcript) is None


def test_rename_chat_moves_dir_project_and_cwd(tmp_path):
    """A rename has to carry all three or the chat is orphaned."""
    config = tmp_path / "config"
    old_dir = tmp_path / "chats" / "2026-03-08-untitled-1642"
    old_dir.mkdir(parents=True)
    (old_dir / "artifacts").mkdir()
    (old_dir / "artifacts" / "note.md").write_text("kept")

    project = config / "projects" / sessions.encode_cwd(old_dir)
    _write_transcript(project / "sess.jsonl", "sess", str(old_dir))

    new_dir = chats.rename_chat(config, old_dir, "2026-03-08-cheap-parking-near-paris")

    assert new_dir.is_dir() and not old_dir.exists()
    assert (new_dir / "artifacts" / "note.md").read_text() == "kept"

    moved = config / "projects" / sessions.encode_cwd(new_dir)
    assert moved.is_dir()
    assert not (config / "projects" / sessions.encode_cwd(old_dir)).exists()

    records = [json.loads(line) for line in (moved / "sess.jsonl").read_text().splitlines()]
    assert next(r for r in records if r.get("cwd"))["cwd"] == str(new_dir)
    assert any(r.get("type") == "ai-title" for r in records)


def test_rename_chat_preserves_transcript_mtime(tmp_path):
    """recent sorts on mtime, so rewriting a transcript must not look like activity."""
    config = tmp_path / "config"
    old_dir = tmp_path / "chats" / "2026-03-08-untitled-1642"
    old_dir.mkdir(parents=True)
    project = config / "projects" / sessions.encode_cwd(old_dir)
    _write_transcript(project / "sess.jsonl", "sess", str(old_dir))
    os.utime(project / "sess.jsonl", (1_700_000_000, 1_700_000_000))

    new_dir = chats.rename_chat(config, old_dir, "2026-03-08-named")
    moved = config / "projects" / sessions.encode_cwd(new_dir) / "sess.jsonl"
    assert int(moved.stat().st_mtime) == 1_700_000_000


def test_rename_chat_refuses_to_clobber(tmp_path):
    config = tmp_path / "config"
    root = tmp_path / "chats"
    old_dir = root / "2026-03-08-untitled-1642"
    old_dir.mkdir(parents=True)
    (root / "2026-03-08-taken").mkdir()
    with pytest.raises(AccountError):
        chats.rename_chat(config, old_dir, "2026-03-08-taken")
    assert old_dir.is_dir()


def test_rename_chat_leaves_nothing_half_moved(tmp_path):
    """A transcript-directory collision must abort before the chat directory moves."""
    config = tmp_path / "config"
    old_dir = tmp_path / "chats" / "2026-03-08-untitled-1642"
    old_dir.mkdir(parents=True)
    project = config / "projects" / sessions.encode_cwd(old_dir)
    _write_transcript(project / "sess.jsonl", "sess", str(old_dir))

    target = tmp_path / "chats" / "2026-03-08-taken"
    (config / "projects" / sessions.encode_cwd(target)).mkdir(parents=True)

    with pytest.raises(AccountError):
        chats.rename_chat(config, old_dir, "2026-03-08-taken")

    assert old_dir.is_dir(), "chat directory moved despite the transcript collision"
    assert project.is_dir()


def test_rename_chat_without_transcript_still_moves(tmp_path):
    config = tmp_path / "config"
    old_dir = tmp_path / "chats" / "2026-03-08-untitled-0900"
    old_dir.mkdir(parents=True)
    new_dir = chats.rename_chat(config, old_dir, "2026-03-08-named")
    assert new_dir.is_dir() and not old_dir.exists()


def test_default_chats_root_is_a_sibling_of_the_routed_ones(monkeypatch, tmp_path):
    """default has no route, so its chats sit beside the accounts that do."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    work = tmp_path / "work"
    monkeypatch.setattr(chats, "read_routes", lambda: [
        (str(work / "acme"), "acme"), (str(work / "beta"), "beta")])
    assert chats.chats_root("default") == work / "chats"
    assert chats.chats_root("acme") == work / "acme" / "chats"


def test_default_chats_root_without_any_routes(monkeypatch):
    monkeypatch.setattr(chats, "read_routes", list)
    assert chats.chats_root("default") == Path.home() / "chats"


def test_an_account_with_no_route_still_refuses(monkeypatch):
    monkeypatch.setattr(chats, "read_routes", lambda: [("/work/acme", "acme")])
    with pytest.raises(AccountError):
        chats.chats_root("nowhere")


def test_tidy_removes_abandoned_empty_chats(tmp_path, monkeypatch):
    """A chat opened and closed without a word leaves nothing worth keeping."""
    root = tmp_path / "chats"
    config = tmp_path / "config"
    abandoned = root / "2026-08-15-untitled-2039"
    abandoned.mkdir(parents=True)
    used = root / "2026-08-15-untitled-2044"
    used.mkdir()
    (used / "artifacts").mkdir()

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    chats.tidy("acme", config)

    assert not abandoned.exists(), "empty transcript-less chat should be removed"
    assert used.is_dir(), "a chat with files must never be removed"


def test_tidy_keeps_an_empty_chat_that_has_a_transcript(tmp_path, monkeypatch):
    root = tmp_path / "chats"
    config = tmp_path / "config"
    chat_dir = root / "2026-08-15-untitled-2044"
    chat_dir.mkdir(parents=True)
    _write_transcript(
        config / "projects" / sessions.encode_cwd(chat_dir) / "s.jsonl", "s", str(chat_dir))

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    chats.tidy("acme", config)

    assert (root / "2026-08-15-cheap-parking-near-paris").is_dir(), "should be renamed"


def test_rename_carries_trust_and_permissions(tmp_path):
    """Renaming must not make a chat ask to be trusted all over again."""
    config = tmp_path / "config"
    config.mkdir()
    old_dir = tmp_path / "chats" / "2026-08-15-untitled-2044"
    old_dir.mkdir(parents=True)
    (config / ".claude.json").write_text(json.dumps({
        "projects": {
            str(old_dir): {"hasTrustDialogAccepted": True, "allowedTools": ["Bash"]},
            "/somewhere/else": {"hasTrustDialogAccepted": True},
        },
        "userID": "keep-me",
    }))

    new_dir = chats.rename_chat(config, old_dir, "2026-08-15-hello-jetson-orin")

    data = json.loads((config / ".claude.json").read_text())
    assert str(old_dir) not in data["projects"]
    assert data["projects"][str(new_dir)]["hasTrustDialogAccepted"] is True
    assert data["projects"][str(new_dir)]["allowedTools"] == ["Bash"]
    assert data["projects"]["/somewhere/else"]["hasTrustDialogAccepted"] is True
    assert data["userID"] == "keep-me", "unrelated config must survive"


def test_rename_without_a_claude_json_is_fine(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    old_dir = tmp_path / "chats" / "2026-08-15-untitled-2044"
    old_dir.mkdir(parents=True)
    assert chats.rename_chat(config, old_dir, "2026-08-15-named").is_dir()


def test_default_root_never_escapes_home(monkeypatch, tmp_path):
    """Routes in unrelated trees share only '/', and a session cannot launch there."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(chats, "read_routes", lambda: [
        (str(tmp_path / "work/acme"), "acme"), ("/mnt/elsewhere/beta", "beta")])
    assert chats.chats_root("default") == tmp_path / "chats"


def test_default_root_uses_the_common_parent_when_it_is_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(chats, "read_routes", lambda: [
        (str(tmp_path / "work/acme"), "acme"), (str(tmp_path / "work/beta"), "beta")])
    assert chats.chats_root("default") == tmp_path / "work" / "chats"


def test_tidy_leaves_a_title_that_slugifies_to_nothing_alone(tmp_path, monkeypatch):
    """A title written entirely in a non-Latin script yields no name to rename to.

    Renaming on it anyway produced another placeholder, which the next pass renamed
    again: an endless flip that rewrote every transcript record each time.
    """
    root = tmp_path / "chats"
    config = tmp_path / "config"
    chat_dir = root / "2026-08-16-untitled-1430"
    chat_dir.mkdir(parents=True)
    session = "4c7b382d-6c82-44d8-b016-65dd3deaac33"
    project = config / "projects" / sessions.encode_cwd(chat_dir)
    project.mkdir(parents=True)
    (project / f"{session}.jsonl").write_text(
        json.dumps({"type": "ai-title", "aiTitle": "\u65e5\u672c\u8a9e"}) + "\n")

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    for _ in range(3):
        chats.tidy("acme", config)
        assert [p.name for p in root.iterdir()] == ["2026-08-16-untitled-1430"]


def test_is_live_reports_live_when_it_cannot_look(tmp_path, monkeypatch):
    """No /proc means no answer, and the safe answer is the one that renames nothing."""
    monkeypatch.setattr(chats.Path, "iterdir", _raise_oserror)
    assert chats.is_live(tmp_path) is True


def _raise_oserror(self):
    raise OSError("no /proc here")


def test_tidy_leaves_a_title_that_slugifies_to_the_placeholder_word(tmp_path, monkeypatch):
    """A chat actually titled "Untitled" renames to a name this loop picks up
    again, and the directory flips between two names forever."""
    root = tmp_path / "chats"
    config = tmp_path / "config"
    chat_dir = root / "2026-08-16-untitled-1430"
    chat_dir.mkdir(parents=True)
    project = config / "projects" / sessions.encode_cwd(chat_dir)
    project.mkdir(parents=True)
    (project / "9f3c1234-aaaa-bbbb-cccc-ddddeeeeffff.jsonl").write_text(
        json.dumps({"type": "ai-title", "aiTitle": "Untitled"}) + "\n")

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    seen = set()
    for _ in range(4):
        chats.tidy("acme", config)
        seen.add(tuple(sorted(p.name for p in root.iterdir())))
    assert len(seen) == 1, f"the chat never settled: {seen}"


def _chat(root, config, name, records, session):
    chat_dir = root / name
    chat_dir.mkdir(parents=True, exist_ok=True)
    project = config / "projects" / sessions.encode_cwd(chat_dir)
    project.mkdir(parents=True, exist_ok=True)
    (project / f"{session}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))
    return chat_dir


def _said(text, ts="2026-08-16T12:00:00Z"):
    return {"type": "user", "timestamp": ts, "cwd": "/c",
            "message": {"role": "user", "content": text}}


def test_tidy_names_a_chat_claude_code_never_titled(tmp_path, monkeypatch):
    """Claude Code writes an ai-title only for a long session, so short chats sat at
    `untitled` forever while the listing showed a perfectly good name for them."""
    root, config = tmp_path / "chats", tmp_path / "config"
    _chat(root, config, "2026-08-16-untitled-1430",
          [_said("Research cheap flights in May")], "4c7b382d-6c82-44d8-b016-65dd3deaac33")

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    chats.tidy("acme", config)

    assert [p.name for p in root.iterdir()] == ["2026-08-16-research-cheap-flights-in-may"]


def test_tidy_prefers_the_generated_title_over_the_opening_prompt(tmp_path, monkeypatch):
    root, config = tmp_path / "chats", tmp_path / "config"
    _chat(root, config, "2026-08-16-untitled-1430",
          [_said("some rambling opening question"),
           {"type": "ai-title", "aiTitle": "Flight research"}],
          "4c7b382d-6c82-44d8-b016-65dd3deaac33")

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    chats.tidy("acme", config)

    assert [p.name for p in root.iterdir()] == ["2026-08-16-flight-research"]


def test_tidy_keeps_two_chats_apart_when_they_open_with_the_same_words(tmp_path, monkeypatch):
    """Two chats on one day can share an opening prompt, and the second must not be
    dropped for colliding with the first."""
    root, config = tmp_path / "chats", tmp_path / "config"
    _chat(root, config, "2026-08-16-untitled-1430", [_said("same words")],
          "4c7b382d-6c82-44d8-b016-65dd3deaac33")
    _chat(root, config, "2026-08-16-untitled-1730", [_said("same words")],
          "9f1a0b6e-1111-2222-3333-444455556666")

    monkeypatch.setattr(chats, "chats_root", lambda account: root)
    monkeypatch.setattr(chats, "is_live", lambda d: False)
    chats.tidy("acme", config)

    names = sorted(p.name for p in root.iterdir())
    assert names == ["2026-08-16-same-words", "2026-08-16-same-words-9f1a"], names


def test_is_live_counts_only_another_claude_session(tmp_path, monkeypatch):
    """The SessionEnd hook runs inside the ending session, in the chat directory,
    and a browser opened from a chat inherits the directory too. Neither may hold
    the rename; a session that is not ours must."""
    import shutil
    import subprocess
    import time

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_REAL_BIN", "/opt/x/claude")
    assert chats.is_live(tmp_path) is False, "our own cwd is not an occupant"
    fake_session = tmp_path / "claude"
    shutil.copy(shutil.which("sleep"), fake_session)
    bystander = subprocess.Popen(["sleep", "30"], cwd=tmp_path)
    try:
        time.sleep(0.05)
        assert chats.is_live(tmp_path) is False, "a bystander in the directory is not"
        session = subprocess.Popen([str(fake_session), "30"], cwd=tmp_path)
        try:
            time.sleep(0.05)
            assert chats.is_live(tmp_path) is True, "another session is"
        finally:
            session.kill()
            session.wait()
    finally:
        bystander.kill()
        bystander.wait()
