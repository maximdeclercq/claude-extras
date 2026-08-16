"""The verbs added on top of routing, meaning search, export and status.

search must match on what was said rather than on transcript machinery, and
export must not leak tool output into a file you are about to send someone.
"""

import json
from pathlib import Path

import pytest

from claude_extras import chats, export, search, sessions
from claude_extras.accounts import AccountError


def _transcript(path, records):
    # ensure_ascii=False, since Claude Code stores non-ASCII raw and search reads
    # the raw line first
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def _said(role, text, ts="2026-08-16T12:00:00Z", **extra):
    return {"type": role, "timestamp": ts, "sessionId": "s",
            "cwd": "/work/acme", "message": {"role": role, "content": text}, **extra}


def test_only_a_terminal_session_is_listed(tmp_path):
    """`claude -p`, the SDK, hooks and agents all write transcripts that look like
    sessions. They carry entrypoint "sdk-cli" and are 85% of the corpus, so
    listing them buries the handful anyone would actually reopen."""
    config = tmp_path / "cfg"
    for name, entry in (("typed.jsonl", "cli"), ("headless.jsonl", "sdk-cli")):
        _transcript(config / "projects" / "-work-acme" / name,
                    [_said("user", "hello", entrypoint=entry)])

    listed = [Path(r["path"]).name for r in sessions.sessions_for("acme", config, {}, None)]
    assert listed == ["typed.jsonl"], listed


def test_a_hook_cannot_title_a_session(tmp_path):
    """Injected context arrives as a user message. Titling on it labelled twelve
    real sessions "A session-scoped Stop hook is now active with ..."."""
    path = tmp_path / "cfg" / "projects" / "-work-acme" / "s.jsonl"
    _transcript(path, [_said("user", "a hook said this", isMeta=True, entrypoint="cli"),
                       _said("user", "what I actually asked")])

    assert sessions.read_meta(path)["title"] == "what I actually asked"


@pytest.fixture
def routed(monkeypatch, tmp_path):
    """Two nested routes, so longest-prefix has something to get wrong."""
    outer, inner = tmp_path / "work", tmp_path / "work" / "acme"
    inner.mkdir(parents=True)
    rows = [(str(outer), "globex"), (str(inner), "acme")]
    monkeypatch.setattr(chats, "read_routes", lambda: rows, raising=False)
    monkeypatch.setattr("claude_extras.accounts.read_routes", lambda: rows)
    return outer, inner


def test_search_matches_what_was_said_not_the_json_around_it(tmp_path):
    """A hit in a tool payload or in JSON escaping is not something you can act on."""
    hit = tmp_path / "hit.jsonl"
    miss = tmp_path / "miss.jsonl"
    _transcript(hit, [_said("user", "how do I use link-dest properly")])
    # the word appears only inside a tool block, never in anything anyone said
    _transcript(miss, [{"type": "assistant", "timestamp": "2026-08-16T12:00:00Z",
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Bash",
                             "input": {"command": "rsync --link-dest x y"}}]}}])
    pattern = search.re.compile("link-dest", search.re.IGNORECASE)
    raw = search.re.compile(b"link-dest", search.re.IGNORECASE)

    assert search.scan_file(hit, pattern, raw, mine_only=False)[0] == "user"
    assert search.scan_file(miss, pattern, raw, mine_only=False) is None


def test_search_mine_only_skips_what_claude_said(tmp_path):
    path = tmp_path / "s.jsonl"
    _transcript(path, [_said("assistant", "the answer is telemetry")])
    pattern = search.re.compile("telemetry", search.re.IGNORECASE)
    raw = search.re.compile(b"telemetry", search.re.IGNORECASE)

    assert search.scan_file(path, pattern, raw, mine_only=False) is not None
    assert search.scan_file(path, pattern, raw, mine_only=True) is None


def test_search_rejects_a_bad_limit():
    """Three verbs take --limit; the one that parsed it by hand let int() raise."""
    with pytest.raises(AccountError, match="positive number"):
        search.main(["--limit", "abc", "x"])
    with pytest.raises(AccountError, match="positive number"):
        search.main(["--limit=0", "x"])


def test_resume_refuses_a_session_whose_directory_is_gone(tmp_path):
    """Every verb that resumes goes through here, so the guard lives here once."""
    row = {"real": str(tmp_path / "gone"), "id": "abc", "account": "default",
           "config_dir": str(tmp_path)}
    with pytest.raises(AccountError, match="directory is gone"):
        sessions.resume(row)


def test_search_rejects_a_bad_regex():
    with pytest.raises(AccountError, match="bad regular expression"):
        search.main(["--regex", "([unclosed"])


def test_export_never_writes_tool_output(tmp_path):
    """Tool results carry file contents and command output from the machine the
    session ran on. An export is something you send someone."""
    path = tmp_path / "s.jsonl"
    _transcript(path, [
        _said("user", "read the key file"),
        {"type": "assistant", "timestamp": "2026-08-16T12:00:01Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Reading it now."},
             {"type": "tool_use", "name": "Read", "input": {"file_path": "/etc/shadow"}}]}},
        {"type": "user", "timestamp": "2026-08-16T12:00:02Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "root:$6$SECRETHASH:19000:0:99999:7:::"}]}},
    ])
    row = {"path": str(path), "title": "T", "account": "acme", "dir": "/work", "id": "s"}

    plain = export.render(row, "md")
    assert "SECRETHASH" not in plain
    assert "/etc/shadow" not in plain
    assert "Reading it now." in plain

    # even opted in, only the tool's name travels
    named = export.render(row, "md", include_tools=True)
    assert "SECRETHASH" not in named
    assert "ran: Read" in named


def test_export_html_escapes_what_was_said(tmp_path):
    path = tmp_path / "s.jsonl"
    _transcript(path, [_said("user", "<script>alert(1)</script>")])
    row = {"path": str(path), "title": "T & co", "account": "acme", "dir": "/w", "id": "s"}

    page = export.render(row, "html")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "T &amp; co" in page


def test_export_rejects_an_unknown_format():
    """The check runs while parsing, so no verb gets as far as reading a session."""
    from claude_extras.args import parse

    with pytest.raises(AccountError, match="--format must be"):
        parse(["--format", "pdf"], export.FLAGS, "resume export")


def test_export_skips_subagent_and_harness_records(tmp_path):
    path = tmp_path / "s.jsonl"
    _transcript(path, [
        _said("user", "real question"),
        _said("assistant", "subagent chatter", isSidechain=True),
        _said("user", "<system-reminder>ignore me</system-reminder>"),
    ])
    turns = export.read_turns(str(path))
    assert [t["text"] for t in turns] == ["real question"]


def test_sessions_carry_their_transcript_path(tmp_path):
    """search and export both read the file; re-deriving its encoded name is lossy."""
    config = tmp_path / "cfg"
    path = config / "projects" / "-work-acme" / "sess.jsonl"
    _transcript(path, [_said("user", "hello", entrypoint="cli")])
    rows = sessions.sessions_for("acme", config, {}, None)
    assert rows and rows[0]["path"] == str(path)


def test_search_finds_a_non_ascii_term_in_another_case(tmp_path):
    """The bytes prefilter case-folds ASCII only, so it must stand aside for a
    non-ASCII pattern rather than reject a file that does contain the word."""
    path = tmp_path / "s.jsonl"
    _transcript(path, [_said("user", "we discussed the caf\u00e9 menu")])
    pattern = search.re.compile(search.re.escape("CAF\u00c9"), search.re.IGNORECASE)

    assert search.byte_prefilter(pattern) is None, "non-ASCII must give up the prefilter"
    assert search.scan_file(path, pattern, None, mine_only=False) is not None


def test_search_keeps_the_prefilter_for_an_ascii_pattern(tmp_path):
    pattern = search.re.compile(search.re.escape("link-dest"), search.re.IGNORECASE)
    assert search.byte_prefilter(pattern) is not None


def test_export_keeps_a_turn_that_opens_with_an_empty_tag():
    """<> is JSX fragment shorthand, so it is ordinary content, not a harness wrapper."""
    assert export._is_harness("<>\nwhat does this fragment do?") is False
    assert export._is_harness("< >x") is False
    assert export._is_harness("<system-reminder>x</system-reminder>") is True


@pytest.mark.parametrize("pattern", ["(?u)foo", r"\N{BULLET}"])
def test_search_gives_up_the_prefilter_on_a_bytes_incompatible_regex(pattern):
    """Bytes are a different regex dialect. A pattern valid as text but not as
    bytes must lose the optimisation, not crash the command."""
    assert search.byte_prefilter(search.re.compile(pattern)) is None


def test_status_renders_and_caches_only_when_a_number_moves(tmp_path):
    """refreshInterval re-runs this every few seconds for numbers that change once
    a turn, so an unchanged snapshot must not rewrite the file."""
    from claude_extras import status

    payload = {"model": {"display_name": "Opus"}, "effort": {"level": "high"},
               "context_window": {"used_percentage": 8},
               "rate_limits": {"five_hour": {"used_percentage": 92, "resets_at": 1786881600},
                               "seven_day": {"used_percentage": 41, "resets_at": 1787000000}}}
    line = status.render(payload, str(tmp_path), use_color=False)
    assert "Opus high" in line and "5h 92%" in line and "7d 41%" in line

    target = tmp_path / "ratelimit.json"
    status.write_snapshot(str(tmp_path), status.snapshot(payload, 1000))
    first = target.stat().st_mtime_ns
    status.write_snapshot(str(tmp_path), status.snapshot(payload, 2000))
    assert target.stat().st_mtime_ns == first, "unchanged percentages must not rewrite"

    payload["rate_limits"]["five_hour"]["used_percentage"] = 93
    status.write_snapshot(str(tmp_path), status.snapshot(payload, 3000))
    assert json.loads(target.read_text())["five_hour"]["used_percentage"] == 93


def test_status_never_writes_an_empty_snapshot_over_a_real_one():
    """API-key sessions carry no rate_limits; blanking the cache would blank usage."""
    from claude_extras import status

    assert status.snapshot({"model": {"id": "x"}}, 1) is None



def test_status_tags_the_account_from_the_config_dir():
    from claude_extras import status

    assert status.account_tag("/home/u/.config/claude-extras/accounts/acme") == "acme"
    assert status.account_tag("/home/u/.claude") == "default"
    assert status.account_tag("") == "default"


@pytest.mark.parametrize("payload", [
    {},                                                       # nothing at all
    {"model": {"display_name": "Opus"}},                      # API key, no rate_limits
    {"context_window": {"used_percentage": None}},            # before the first reply
    {"rate_limits": {"five_hour": {"used_percentage": 99, "resets_at": None}}},
    {"rate_limits": {"five_hour": {"used_percentage": "92.4", "resets_at": "soon"}}},
    {"model": {"id": "claude-opus-5"}, "rate_limits": {"seven_day": {}}},
])
def test_status_renders_every_shape_the_payload_takes(payload, tmp_path):
    """rate_limits is absent for API keys and until the first API response, and
    each window is independently optional. A statusline that raises puts its
    traceback under the prompt on every turn."""
    from claude_extras import status

    line = status.render(payload, str(tmp_path), use_color=False)
    assert line.count("|") == 4, line


def test_status_typed_at_a_terminal_reports_instead_of_hanging(monkeypatch, capsys, tmp_path):
    """Claude Code pipes the payload in. Typed by hand there is no payload coming,
    and reading stdin would block on an empty tty forever."""
    import io

    from claude_extras import status

    (tmp_path / "ratelimit.json").write_text(json.dumps({
        "captured_at": 1786899600,
        "five_hour": {"used_percentage": 26, "resets_at": 1786899600},
        "seven_day": {"used_percentage": 16}}))
    stdin = io.StringIO()
    stdin.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    assert status.main([]) == 0
    out = capsys.readouterr().out
    assert "5h 26%" in out and "7d 16%" in out and "as of" in out


def test_status_says_so_when_nothing_is_cached_yet(tmp_path):
    """A fresh install has no snapshot until a session has run with it wired."""
    from claude_extras import status

    line = status.render_cached(str(tmp_path))
    assert "no burn recorded" in line and "claude status --help" in line


def test_status_falls_back_to_the_cache_when_no_payload_arrives(monkeypatch, capsys, tmp_path):
    """A script calling this has no payload to give, and silence is not an answer."""
    import io

    from claude_extras import status

    (tmp_path / "ratelimit.json").write_text(json.dumps({
        "captured_at": 1786899600,
        "five_hour": {"used_percentage": 26, "resets_at": 1786899600},
        "seven_day": {"used_percentage": 16}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    for piped in ("", "not json at all", "[1,2,3]"):
        stdin = io.StringIO(piped)
        stdin.isatty = lambda: False
        monkeypatch.setattr("sys.stdin", stdin)
        assert status.main([]) == 0
        assert "5h 26%" in capsys.readouterr().out


def test_a_synthetic_message_cannot_title_a_session(tmp_path):
    """The harness writes "[Request interrupted by user]" as a user message. It
    titled 370 of 1450 real sessions before promptSource was preferred."""
    path = tmp_path / "cfg" / "projects" / "-work-acme" / "s.jsonl"
    _transcript(path, [
        _said("user", "[Request interrupted by user]", entrypoint="cli"),
        _said("user", "the question I typed", promptSource="typed"),
    ])
    assert sessions.read_meta(path)["title"] == "the question I typed"


def test_an_old_transcript_still_gets_a_title(tmp_path):
    """promptSource postdates most transcripts on disk, so its absence must fall
    back to the first real user message rather than blanking the row."""
    path = tmp_path / "cfg" / "projects" / "-work-acme" / "s.jsonl"
    _transcript(path, [_said("user", "asked before promptSource existed", entrypoint="cli")])
    assert sessions.read_meta(path)["title"] == "asked before promptSource existed"


def test_a_reconstructed_transcript_is_not_offered(tmp_path):
    """Rebuilt from history.jsonl after the sweep, these hold prompts and no
    assistant turns, so there is no session behind them to reopen."""
    path = tmp_path / "cfg" / "projects" / "-work-acme" / "s.jsonl"
    _transcript(path, [_said("user", "[reconstructed transcript] rebuilt from history",
                             entrypoint="cli", promptSource="typed", isReconstructed=True)])
    assert sessions.read_meta(path)["is_resumable"] is False
