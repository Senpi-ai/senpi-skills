"""Squirrel's three rungs run ONE engine, byte for byte — and so do chipmunk's.

`rung1/scanners/scan.py` and `rung1/scanners/scoring.py` are the canonical copy of the dip-ladder
engine. rung2 and rung3 carry copies, and so do strategies/chipmunk's three rungs (its own
test_engine_parity.py pins those). Everything that makes one rung differ from another — the depth under
the 60-day high, chipmunk's 40-week trend gate — is an INPUT in that rung's runtime.yaml, so a fix to
the engine lands in one place and is re-vendored deliberately; a drifted copy cannot quietly make one
step of the ladder trade differently from the others. Pinned the way gannet-duo pins gannet's.

Run: python3 -B -m pytest strategies/squirrel/tests -q
"""
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.abspath(os.path.join(HERE, "..", ".."))
FILES = ("scan.py", "scoring.py")
CANON = os.path.join(STRATEGIES, "squirrel", "rung1", "scanners")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_the_engine_copies_do_not_drift():
    for rung in ("rung2", "rung3"):
        for name in FILES:
            ours = os.path.join(STRATEGIES, "squirrel", rung, "scanners", name)
            assert os.path.exists(ours), f"squirrel/{rung} is missing {name} — every rung vendors the engine"
            assert _sha(ours) == _sha(os.path.join(CANON, name)), (
                f"{name} DRIFTED between squirrel/rung1 and squirrel/{rung} — re-vendor it byte-identically "
                f"(cp strategies/squirrel/rung1/scanners/{name} strategies/squirrel/{rung}/scanners/), and the "
                f"same into strategies/chipmunk/rung1-3. The rungs differ only through runtime.yaml inputs.")


def test_the_engine_reads_every_difference_from_its_inputs():
    """The engine hard-codes no rung: depth, lookback, sizing and the trend gate come from inputs, so
    the same bytes serve all six rungs."""
    src = open(os.path.join(CANON, "scan.py"), encoding="utf-8").read() + \
        open(os.path.join(CANON, "scoring.py"), encoding="utf-8").read()
    for key in ("depthPct", "highLookbackDays", "regimeWeeks", "marginPct", "maxSlots", "allowedAssets"):
        assert f'"{key}"' in src, key


if __name__ == "__main__":
    test_the_engine_copies_do_not_drift()
    test_the_engine_reads_every_difference_from_its_inputs()
    print("ENGINE PARITY OK")
