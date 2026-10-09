#!/usr/bin/env python3
"""`--limit` is a cap on OUTPUT SIZE, never a pre-filter on relevance.

`match()` returns survivors ordered neutrally — by asset match, then by NAME — and `apply_theme`
scores and re-ranks them afterwards. Truncating inside `match` therefore discarded candidates
before anything had scored them, and the theme then ranked whatever happened to survive
alphabetically. A capped themed query could return a zero-scoring package while dropping the best
matches in the catalog, purely because their ids started with a later letter.

That is a silent failure: the caller gets a well-formed, ranked, plausible-looking list with the
right answer missing. The guard is stated generally rather than against one theme — whatever a
capped run returns must be the first N of the uncapped ranking.

Offline against the bundled catalog: no MCP, no auth. It drives the CLI by subprocess because the
bug lived in `main()`'s call order, not in `match` or `apply_theme` individually — a unit test
calling those two directly would have passed throughout.

Every CLI result is MEMOIZED, and the matrix is deliberately small. The first version of this file
spawned ~35 subprocesses and doubled the shared suite's wall time from 32s to 70s, which pushed an
unrelated time-windowed test in senpi-signals past its bound. A guard that reddens someone else's
test is a guard that gets deleted.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DISCOVER = os.path.join(REPO, "senpi-strategy-discover", "scripts", "discover.py")

# two themes from different corners of the catalog, so this is not a property of one archetype
THEME_A = "funding carry get paid to hold"
THEME_B = "follow smart money rotation"
CAPS = (1, 5)

_CACHE = {}


def _run(*args):
    if args not in _CACHE:
        proc = subprocess.run([sys.executable, DISCOVER, "--no-market"] + list(args),
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"discover.py failed: {proc.stderr[-800:]}"
        _CACHE[args] = json.loads(proc.stdout)
    return _CACHE[args]


def _ids(res):
    return [c["id"] for c in res.get("candidates", [])]


def test_a_capped_themed_run_is_a_prefix_of_the_uncapped_ranking():
    for theme in (THEME_A, THEME_B):
        full = _ids(_run("--theme", theme))
        assert len(full) > 10, f"{theme!r}: only {len(full)} candidates, the cap test is vacuous"
        for cap in CAPS:
            got = _ids(_run("--theme", theme, "--limit", str(cap)))
            assert got == full[:cap], (
                f"theme {theme!r} --limit {cap} returned {got}, expected the top {cap} of the "
                f"ranking {full[:cap]} — the cap is selecting on something other than score")


def test_a_capped_run_never_drops_a_scoring_candidate_for_a_zero():
    """The exact shape of the bug: a zero-scoring package present while a scoring one is absent."""
    for theme in (THEME_A, THEME_B):
        full = _run("--theme", theme)
        scoring = [c["id"] for c in full["candidates"] if c.get("theme_score", 0) > 0]
        if len(scoring) < 3:
            continue
        res = _run("--theme", theme, "--limit", str(CAPS[-1]))
        returned = _ids(res)
        zeros = [c["id"] for c in res["candidates"] if c.get("theme_score", 0) == 0]
        dropped = [i for i in scoring[:CAPS[-1]] if i not in returned]
        assert not (zeros and dropped), (
            f"theme {theme!r}: returned zero-scoring {zeros} while dropping scoring {dropped}")


def test_a_capped_run_is_ranked_and_reports_its_own_count():
    res = _run("--theme", THEME_A, "--limit", str(CAPS[-1]))
    scores = [c.get("theme_score", 0) for c in res["candidates"]]
    assert scores == sorted(scores, reverse=True), f"not ranked: {scores}"
    assert len(res["candidates"]) == CAPS[-1]
    assert res["meta"]["returned_n"] == CAPS[-1], (
        f"meta.returned_n says {res['meta']['returned_n']} but {CAPS[-1]} came back")


def test_an_uncapped_run_is_unaffected():
    res = _run("--theme", THEME_A)
    assert res["meta"]["returned_n"] == len(res["candidates"])


def test_the_cap_still_works_without_a_theme():
    """No theme means no ranking to preserve — `match`'s own neutral order and cap still apply."""
    res = _run("--limit", str(CAPS[-1]))
    assert len(res["candidates"]) == CAPS[-1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"LIMIT-AFTER-RANKING OK — {len(fns)} checks, {len(_CACHE)} CLI invocations")
