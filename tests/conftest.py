import json
import os
import tempfile
from pathlib import Path

import pytest

# Set before claude_extras is imported, since its path constants are computed at
# import and a fixture runs too late. A test that forgets to patch one then writes
# into this scratch tree and never into the real config with its credentials.
os.environ["CLAUDE_EXTRAS_CONFIG"] = tempfile.mkdtemp(prefix="claude-extras-tests-")

from claude_extras import accounts

# Fails the day accounts.py stops reading that variable, which nothing else would catch
assert accounts.CONFIG != Path("~/.config/claude-extras").expanduser(), \
    "tests are pointed at the real config directory"


@pytest.fixture
def arec():
    """Build one assistant transcript record."""
    def _make(ts, model="claude-opus-5", mid="msg_1", rid="req_1", inp=0, out=0,
              cr=0, cw5=0, cw1=0, sidechain=False, cwd="/home/u/proj", session="s1"):
        return {
            "type": "assistant", "timestamp": ts, "requestId": rid,
            "isSidechain": sidechain, "cwd": cwd, "sessionId": session,
            "message": {
                "id": mid, "role": "assistant", "model": model,
                "usage": {
                    "input_tokens": inp, "output_tokens": out,
                    "cache_read_input_tokens": cr,
                    "cache_creation": {"ephemeral_5m_input_tokens": cw5,
                                       "ephemeral_1h_input_tokens": cw1},
                },
            },
        }
    return _make


@pytest.fixture
def make_config(tmp_path):
    """Write transcripts into a throwaway config dir, one file per list."""
    counter = {"n": 0}

    def _make(*files, ratelimit=None, tier=None, name=None):
        counter["n"] += 1
        root = tmp_path / (name or f"cfg{counter['n']}")
        proj = root / "projects" / "p"
        proj.mkdir(parents=True)
        for i, records in enumerate(files):
            with (proj / f"s{i}.jsonl").open("w") as fh:
                for rec in records:
                    fh.write(json.dumps(rec) + "\n")
        if ratelimit is not None:
            (root / "ratelimit.json").write_text(json.dumps(ratelimit))
        if tier is not None:
            (root / ".credentials.json").write_text(
                json.dumps({"claudeAiOauth": {"rateLimitTier": tier,
                                              "refreshToken": "r", "expiresAt": 1}}))
        return root
    return _make
