"""The Striker scorer is vendored FOUR times, and purple-penguin is a one-input fork.

Both facts are load-bearing and neither was pinned, which is why this file exists.

  1. **Four byte-identical copies of `scoring.py`.** penguin, pelican and orca each ship their
     own copy, and `senpi-signals/scripts/striker_scoring.py` is a fifth surface on the skill
     side. They were byte-identical on 2026-09-30 and nothing said so. A fix applied to one and
     not the others is a silent divergence in the entry model of three live templates plus a
     skill — the exact shape of the two-copy bugs this tree keeps rediscovering.

  2. **purple-penguin differs from penguin in ONE input.** It is an experiment whose whole
     validity rests on being penguin with `minScore: 8` and nothing else. The moment a second
     thing drifts, a difference in its results stops being attributable to the score floor and
     the experiment is worthless. This compares PARSED yaml, so comments and prose are free to
     differ; only effective config is pinned.

Run: python3 -m pytest strategies/tests/test_penguin_family_drift.py -q
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

SCORER_COPIES = [
    "strategies/penguin/main/scanners/scoring.py",
    "strategies/pelican/main/scanners/scoring.py",
    "strategies/orca/main/scanners/scoring.py",
    "strategies/purple-penguin/main/scanners/scoring.py",
    "senpi-signals/scripts/striker_scoring.py",
]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _yaml(rel):
    return yaml.safe_load(_read(rel))


def test_every_vendored_striker_scorer_is_byte_identical():
    """A change to the entry model must land on all five surfaces or none."""
    base = SCORER_COPIES[0]
    ref = _read(base)
    for rel in SCORER_COPIES[1:]:
        assert _read(rel) == ref, (
            f"{rel} has drifted from {base}. These are vendored copies of one scorer — port the "
            f"change to every path in SCORER_COPIES in the same commit, or the entry model of "
            f"three live templates and the senpi-signals skill silently disagree.")


def test_the_score_floor_is_reachable_from_runtime_yaml():
    """`minScore` must be the authoritative floor, not shadowed by the module constant.

    It WAS shadowed: score_market applied a hardcoded STRIKER_MIN_SCORE and returned None below
    it, so a package setting minScore lower had no effect whatsoever. purple-penguin exists only
    because that is now fixed, so this pins the fix rather than the value."""
    scorer = _read(SCORER_COPIES[0])
    assert "floor=True" in scorer, (
        "score_market lost its `floor` kwarg — without it a caller cannot see sub-floor "
        "candidates and the CAND telemetry goes blind below the floor")
    assert "passedFloor" in scorer, "meta lost passedFloor, the precomputed floor verdict"
    for pkg in ("penguin", "pelican"):
        scan = _read(f"strategies/{pkg}/main/scanners/scan.py")
        assert "floor=False" in scan, f"{pkg}/scan.py no longer asks for sub-floor candidates"
        assert "score < min_score" in scan, (
            f"{pkg}/scan.py must enforce the floor itself once it passes floor=False — "
            f"otherwise sub-floor candidates would TRADE")


def test_purple_penguin_differs_from_penguin_in_exactly_one_input():
    """The experiment is only interpretable if minScore is the sole effective difference."""
    peng = _yaml("strategies/penguin/main/runtime.yaml")
    purp = _yaml("strategies/purple-penguin/main/runtime.yaml")

    # identity fields are expected to differ; everything else must not
    for k in ("name", "group", "version", "description"):
        peng.pop(k, None)
        purp.pop(k, None)
    peng["strategy"].pop("wallet", None)
    purp["strategy"].pop("wallet", None)

    ps = next(s for s in peng["scanners"] if s.get("type") == "external_scanner")
    qs = next(s for s in purp["scanners"] if s.get("type") == "external_scanner")
    assert ps["inputs"]["minScore"] == 9, "penguin's shipped floor moved; update this test"
    assert qs["inputs"]["minScore"] == 8, (
        f"purple-penguin's whole reason to exist is minScore 8; found {qs['inputs']['minScore']}")
    ps["inputs"].pop("minScore")
    qs["inputs"].pop("minScore")

    assert purp == peng, (
        "purple-penguin diverges from penguin beyond minScore. Every other difference destroys "
        "the experiment: a result could no longer be attributed to the score floor. Diff the two "
        "runtime.yaml files and revert anything that is not minScore.")


def test_purple_penguin_stays_out_of_the_catalog():
    """It is an unmeasured 8-floor on a 90%-margin 10x book. Users must not be offered it."""
    card = _yaml("strategies/purple-penguin/strategy.yaml")
    assert card["catalog"].get("status") == "blocked", (
        "purple-penguin lost catalog.status: blocked — gen_catalog would publish it to the "
        "catalog AND to senpi-strategy-discover, offering an untested score floor to users. "
        "Deploy it by explicit path instead; deploy.py honours a path regardless of status.")


# ── the leverage/ROE coherence invariant (cheetah, 2026-09-30) ──

def test_a_variable_leverage_book_cannot_carry_a_fixed_roe_ladder():
    """Every DSL threshold is ROE, so an exit's distance IN PRICE is (ROE / leverage).

    A package that scales conviction through LEVERAGE therefore rescales its entire exit ladder
    per trade — and backwards: cheetah's old 3/5/7/8 tiers gave a score-10 signal a 3.33%-of-price
    stop with its first profit floor 6.67% away, while a score-14 signal got 1.25% and 2.50%. The
    weakest signal was handed the widest stop and the most distant floor, and against a measured
    median peak of 0.84% of price the ladder never armed at any tier.

    The fix was to scale conviction through MARGIN and hold leverage flat. This pins that: if a
    package's tiers ever spread leverage again, one ROE ladder can no longer be correct for all
    of its trades."""
    for pkg in ("cheetah", "wild-cheetah"):
        rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
        sc = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")
        tiers = sc["inputs"].get("leverageTiers") or []
        levs = {int(t[1]) for t in tiers}
        assert len(levs) == 1, (
            f"{pkg} spreads leverage across {sorted(levs)} while its DSL ladder is fixed in ROE, "
            f"so every exit means a different price move per tier. Scale conviction through the "
            f"third tier element (marginPct) and keep leverage flat.")
        assert all(len(t) > 2 for t in tiers), (
            f"{pkg} tiers lost their marginPct element — conviction is no longer sized at all")
        lev = levs.pop()
        rungs = rt["exit"]["dsl_preset"]["phase2"]["tiers"]
        first = rungs[0]["trigger_pct"] / lev
        assert first <= 1.0, (
            f"{pkg}'s first rung arms at {first:.2f}% of price. The measured median position peaks "
            f"at 0.84%, so a rung above ~1% never arms and the position has no profit floor.")


# ── weak_peak_cut must stay a DEATH cut, never a profit cut ──

def test_weak_peak_cut_can_only_fire_before_the_ladder_arms():
    """`weak_peak_cut` closes when peakROE never reached `min_value` and current ROE has fallen
    off that peak. Entering phase 2 implies peakROE >= the first tier's `trigger_pct`, so while
    `min_value < trigger_pct` the guard is unsatisfiable once a rung arms — the cut can only ever
    fire in phase 1 (senpi-strategy-author/references/dsl-configuration.md).

    That is the whole reason it is safe to leave enabled: it bounds a position that NEVER worked,
    and cannot touch one that did. Raise `min_value` above the first trigger, or drop the first
    trigger below `min_value`, and it silently becomes able to close a position that already armed
    a profit floor — a death cut turning into a profit cut without anyone changing its settings."""
    for pkg in ("condor", "wild-condor", "penguin", "pelican", "puffin", "purple-penguin"):
        rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
        preset = rt["exit"]["dsl_preset"]
        wpc = preset.get("weak_peak_cut") or {}
        if not wpc.get("enabled"):
            continue
        first_trigger = preset["phase2"]["tiers"][0]["trigger_pct"]
        assert wpc["min_value"] < first_trigger, (
            f"{pkg}: weak_peak_cut min_value {wpc['min_value']} is not below the first tier "
            f"trigger {first_trigger}, so it can fire AFTER the ladder has armed — closing a "
            f"position that reached a profit floor. Keep min_value strictly under the first rung.")


# ── the "wild" variants: conviction and size move TOGETHER ──

WILD_PAIRS = [("wild-condor", "condor"), ("wild-cheetah", "cheetah")]


def _sizing(pkg):
    rt = _yaml(f"strategies/{pkg}/main/runtime.yaml")
    inp = next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"]
    tiers = inp.get("leverageTiers") or []
    margins = [t[2] for t in tiers if len(t) > 2] or [inp.get("marginPct")]
    return float(inp["minScore"]), max(float(m) for m in margins)


def test_a_wild_variant_raises_the_bar_and_the_size_together():
    """The point of a `wild-` package is NOT to trade bigger. It is to trade bigger ONLY on what
    already cleared a higher bar.

    Raising margin alone is leverage with extra steps — the same signal distribution, more money
    behind each one, and a worse expectancy per dollar the moment the marginal signal is weak.
    Raising the floor alone leaves a rarer, better signal paid exactly what a common one was. The
    two only make sense as one change, so this fails if either half is ever walked back on its own.

    (Deliberately NOT asserted of purple-penguin: that one LOWERS the floor to measure an
    unmeasured band, which is a different experiment with its own justification.)"""
    for wild, parent in WILD_PAIRS:
        w_score, w_margin = _sizing(wild)
        p_score, p_margin = _sizing(parent)
        assert w_score > p_score, (
            f"{wild} does not raise the score floor above {parent} ({w_score} vs {p_score}) — "
            f"it is then just {parent} with more money on the same signals.")
        assert w_margin > p_margin, (
            f"{wild} does not raise margin above {parent} ({w_margin}% vs {p_margin}%) — "
            f"a higher bar with the same size leaves the rarer signal underpaid.")


def test_every_wild_variant_stays_out_of_the_catalog():
    """Unmeasured floors on whole-book positions. None of these may reach a user via discover."""
    for wild, _ in WILD_PAIRS:
        card = _yaml(f"strategies/{wild}/strategy.yaml")
        assert card["catalog"].get("status") == "blocked", (
            f"{wild} lost catalog.status: blocked — gen_catalog would publish it to the catalog "
            f"AND to senpi-strategy-discover. Deploy experiments by explicit path instead.")
