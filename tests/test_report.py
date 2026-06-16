from datetime import UTC, datetime, timedelta

import pytest

from claude_extras import identity, parser, pricing, report
from claude_extras.parser import Bucket, Turn

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _turn(minutes_ago, session="s", model="claude-opus-5"):
    return Turn(ts=NOW - timedelta(minutes=minutes_ago), model=model, is_sidechain=False,
                input=1, output=0, cache_read=0, cache_write_5m=0, cache_write_1h=0,
                session=session)


# Matching on the tier word alone overstated every recent Opus turn threefold.
def test_current_opus_is_not_priced_as_legacy_opus():
    assert pricing.price_for("claude-opus-5") == (5.0, 25.0)
    assert pricing.price_for("claude-opus-4-8") == (5.0, 25.0)


def test_each_family_has_its_own_rate():
    assert pricing.price_for("claude-fable-5") == (10.0, 50.0)
    assert pricing.price_for("claude-sonnet-5") == (3.0, 15.0)
    assert pricing.price_for("claude-haiku-4-5-20251001") == (1.0, 5.0)


def test_unknown_model_is_unpriced_not_guessed():
    assert pricing.price_for("claude-opus-9") is None
    assert pricing.cost(Bucket(input=1_000_000), "claude-opus-9") is None


@pytest.mark.parametrize("field,mult", [
    ("cache_read", pricing.CACHE_READ_MULT),
    ("cache_write_5m", pricing.CACHE_WRITE_5M_MULT),
    ("cache_write_1h", pricing.CACHE_WRITE_1H_MULT),
])
def test_cache_multipliers_apply_to_the_input_rate(field, mult):
    assert pricing.cost(Bucket(**{field: 1_000_000}), "claude-opus-5") == pytest.approx(5.0 * mult)


# Summing overlapping session spans reported more hours than a week contains.
def test_overlapping_sessions_are_not_double_counted():
    sessions, seconds = report.session_time(
        [_turn(60, "a"), _turn(0, "a"), _turn(50, "b"), _turn(10, "b")])
    assert sessions == 2
    assert seconds == 3600


def test_disjoint_sessions_add_up():
    _, seconds = report.session_time(
        [_turn(120, "a"), _turn(90, "a"), _turn(50, "b"), _turn(20, "b")])
    assert seconds == 3600


def test_gaps_between_sessions_are_excluded():
    _, seconds = report.session_time(
        [_turn(600, "a"), _turn(570, "a"), _turn(30, "b"), _turn(0, "b")])
    assert seconds == 3600


def test_accounts_on_one_subscription_form_one_pool():
    accts = [(n, {"user_uuid": "A", "org_uuid": "O", "tier": "max 20x",
                  "fingerprint": f}) for n, f in (("default", "a"), ("work", "b"))]
    pools = identity.group(accts)
    assert len(pools) == 1
    assert pools[0]["logins"] == 2


def test_separate_subscriptions_form_separate_pools():
    accts = [
        ("default", {"user_uuid": "A", "org_uuid": "O", "tier": "max 20x",
                     "fingerprint": "a"}),
        ("team", {"user_uuid": "B", "org_uuid": "P", "tier": "max 5x",
                  "fingerprint": "b"}),
    ]
    assert len(identity.group(accts)) == 2


# A named account that has not run yet must not be filed under the default's plan.
def test_identity_is_not_inherited_when_state_file_is_absent(tmp_path):
    acct = tmp_path / "fresh"
    acct.mkdir()
    (acct / ".credentials.json").write_text(
        '{"claudeAiOauth":{"refreshToken":"z","rateLimitTier":"default_claude_max_5x"}}')
    ident = identity.read(acct)
    assert ident["user_uuid"] is None
    assert ident["tier"] == "max 5x"


def test_pool_reports_the_freshest_snapshot_among_its_members():
    def acct(name, captured, pct):
        return {"name": name, "turns": [],
                "rc": {"captured_at": captured,
                       "five_hour": {"used_percentage": pct, "resets_at": None},
                       "seven_day": {"used_percentage": pct, "resets_at": None},
                       "context": {"used_percentage": 1}},
                "identity": {"user_uuid": "A", "org_uuid": "O",
                             "tier": "max 20x", "fingerprint": name}}
    pools = report.build_subscriptions([acct("old", 1000, 10), acct("new", 2000, 55)], NOW)
    assert len(pools) == 1
    assert pools[0]["five_hour"]["used_percentage"] == 55


def test_numeric_columns_align_right_and_prose_aligns_left():
    lines = report.table(["name", "n"], [["a", "1"], ["longer", "22"]])
    assert lines[1].endswith(" 1")
    assert "a     " in lines[1]


def test_totals_use_the_union_not_the_sum_of_accounts():
    shared = [_turn(60, "s"), _turn(0, "s")]
    model = report.build([
        {"name": "one", "turns": shared, "rc": None, "identity": {}},
        {"name": "two", "turns": list(shared), "rc": None, "identity": {}},
    ], NOW)
    assert model["totals"]["seconds"] < sum(r["seconds"] for r in model["accounts"])


def test_windows_exclude_turns_outside_them(make_config, arec):
    cfg = make_config([
        arec("2026-08-03T11:30:00.000Z", rid="in", inp=10),
        arec("2026-07-01T10:00:00.000Z", rid="old", inp=999),
    ])
    account = {"name": "a", "turns": parser.load_turns(cfg), "rc": None, "identity": {}}
    model = report.build_account(account, NOW)
    assert model["windows"]["5h"]["tokens"]["input"] == 10
    assert model["windows"]["total"]["tokens"]["input"] == 1009


def test_since_widens_the_summary_window():
    """--since is documented as the cutoff, so the table must actually use it.

    build() re-clipped to a fixed seven days, so asking for a quarter silently
    printed a week.
    """
    def sized(days_ago, tokens):
        return Turn(ts=NOW - timedelta(days=days_ago), model="claude-opus-5",
                    is_sidechain=False, input=tokens, output=0, cache_read=0,
                    cache_write_5m=0, cache_write_1h=0, session=f"s{days_ago}")

    account = {"name": "a", "turns": [sized(30, 1000), sized(1, 7)],
               "rc": None, "identity": {}}
    peer = {"name": "b", "turns": [], "rc": None, "identity": {}}

    assert report.build([account, peer], NOW)["totals"]["tokens"]["total"] == 7
    since = NOW - timedelta(days=90)
    assert report.build([account, peer], NOW, since)["totals"]["tokens"]["total"] == 1007
