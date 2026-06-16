"""The cache is the only component with real invalidation logic, and two of the
bugs found in review lived here, so the failure modes are pinned explicitly."""

import json

from claude_extras import cache, parser

TS = "2026-08-03T10:00:00.000Z"


def _total(turns):
    return sum(t.input + t.output + t.cache_read + t.cache_write_5m + t.cache_write_1h
               for t in turns)


def test_cache_matches_a_direct_parse(make_config, arec):
    cfg = make_config([arec(TS, rid="a", inp=10, out=5, cr=100, cw5=7)])
    assert _total(cache.load_turns(cfg)) == _total(parser.load_turns(cfg))


def test_duplicates_across_files_counted_once(make_config, arec):
    """A resumed session copies earlier records into the new transcript."""
    shared = arec(TS, mid="m", rid="r", inp=100)
    cfg = make_config([shared], [shared, arec(TS, mid="m2", rid="r2", inp=5)])
    turns = cache.load_turns(cfg)
    assert len(turns) == 2
    assert _total(turns) == 105


def test_duplicates_within_one_file_counted_once(make_config, arec):
    cfg = make_config([arec(TS, mid="m", rid="r", inp=100),
                       arec(TS, mid="m", rid="r", inp=100)])
    assert _total(cache.load_turns(cfg)) == 100


def test_records_without_keys_are_never_deduped(make_config, arec):
    """Both id and requestId absent means the two cannot be shown to be the same
    turn, so dropping one would silently undercount."""
    rec = arec(TS, inp=7)
    del rec["requestId"], rec["message"]["id"]
    cfg = make_config([rec, dict(rec)])
    assert _total(cache.load_turns(cfg)) == 14


def test_changed_file_is_reparsed(make_config, arec):
    cfg = make_config([arec(TS, rid="a", inp=10)])
    assert _total(cache.load_turns(cfg)) == 10
    transcript = next((cfg / "projects").glob("**/*.jsonl"))
    with transcript.open("a") as fh:
        fh.write(json.dumps(arec(TS, rid="b", inp=5)) + "\n")
    assert _total(cache.load_turns(cfg)) == 15


def test_unchanged_file_is_not_reparsed(make_config, arec, monkeypatch):
    cfg = make_config([arec(TS, rid="a", inp=10)])
    cache.load_turns(cfg)

    def explode(path):
        raise AssertionError(f"re-parsed unchanged file: {path}")

    monkeypatch.setattr(cache, "_encode", explode)
    assert _total(cache.load_turns(cfg)) == 10


def test_deleted_file_drops_out(make_config, arec):
    cfg = make_config([arec(TS, rid="a", inp=10)], [arec(TS, rid="b", inp=5)])
    assert _total(cache.load_turns(cfg)) == 15
    next((cfg / "projects").glob("**/s1.jsonl")).unlink()
    turns = cache.load_turns(cfg)
    assert _total(turns) == 10
    assert len(json.loads((cfg / cache.CACHE_NAME).read_text())["files"]) == 1


def test_version_bump_discards_the_old_cache(make_config, arec):
    cfg = make_config([arec(TS, rid="a", inp=10)])
    cache.load_turns(cfg)
    path = cfg / cache.CACHE_NAME
    stale = json.loads(path.read_text())
    stale["version"] = cache.CACHE_VERSION + 1
    path.write_text(json.dumps(stale))
    assert cache.load(cfg)["files"] == {}
    assert _total(cache.load_turns(cfg)) == 10


def test_corrupt_cache_rebuilds_rather_than_raising(make_config, arec):
    cfg = make_config([arec(TS, rid="a", inp=10)])
    (cfg / cache.CACHE_NAME).write_text("{not json")
    assert _total(cache.load_turns(cfg)) == 10


def test_synthetic_records_skipped(make_config, arec):
    cfg = make_config([arec(TS, model="<synthetic>", rid="syn", inp=99),
                       arec(TS, rid="real", inp=1)])
    assert _total(cache.load_turns(cfg)) == 1
