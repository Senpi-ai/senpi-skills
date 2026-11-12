"""Every shipped preset's time cuts must stay inside the rule the author guidance states.

A preset is copied into a user's runtime.yaml, so a bad one propagates further than a bad
template: every strategy authored from it inherits the defect, and `balanced` is the default.
Two invariants, both cheap and both the kind that drift silently.

Run: /usr/bin/python3 -B -m pytest senpi-strategy-author/tests/test_preset_time_cuts.py -q
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = os.path.join(HERE, "..", "references", "dsl-presets.yaml")


def _presets():
    """{name: block_text} — regex, not PyYAML: agent hosts have no PyYAML."""
    s = open(PRESETS, encoding="utf-8").read()
    body = s[s.index("presets:"):]
    out = {}
    for m in re.finditer(r'\n  ([a-z][a-z0-9_-]*):\s*\n', body):
        start = m.end()
        nxt = re.search(r'\n  [a-z][a-z0-9_-]*:\s*\n', body[start:])
        out[m.group(1)] = body[start:start + (nxt.start() if nxt else len(body))]
    return out


def _cut(block, key):
    m = re.search(rf'^(\s*){key}:\s*\n((?:\1\s+.*\n|\s*\n)*)', block, re.M)
    if not m:
        return None
    b = m.group(2)
    en = re.search(r'enabled:\s*(\w+)', b)
    if not (en and en.group(1) == "true"):
        return None
    iv = re.search(r'interval_in_minutes:\s*([0-9.]+)', b)
    mv = re.search(r'min_value:\s*([0-9.]+)', b)
    return {"interval": float(iv.group(1)) if iv else None,
            "min_value": float(mv.group(1)) if mv else None}


def _first_tier(block):
    m = re.search(r'trigger_pct:\s*([0-9.]+)', block)
    return float(m.group(1)) if m else None


def test_no_preset_can_cut_a_position_that_already_locked_profit():
    """weak_peak_cut fires on `peakROE < min_value`. Put min_value at or above the first
    tier's trigger_pct and the cut can close a position that has ALREADY armed a floor --
    a clock cutting a winner, which is the documented hard_timeout failure (72% of its fleet
    closes were in profit). Below the first tier it can only reach a position that never got
    going, which is the only safe setting."""
    bad = []
    for name, block in _presets().items():
        wp = _cut(block, "weak_peak_cut")
        t1 = _first_tier(block)
        if wp and wp["min_value"] is not None and t1 is not None and wp["min_value"] >= t1:
            bad.append(f"{name}: weak_peak min_value {wp['min_value']:g} >= first tier {t1:g}")
    assert not bad, (
        "A preset's weak_peak_cut can close a position that already locked profit:\n  "
        + "\n  ".join(bad)
        + "\n\nLower min_value below the first tier, or raise the first tier with it.")


def test_the_long_hold_presets_carry_no_weak_peak_cut():
    """let_winners_run and parabolic_runner exist for theses that need days or weeks. A
    weak-peak cut closes at market with no floor, so on those books it realises a loss on a
    position that simply had not moved yet."""
    p = _presets()
    for name in ("let_winners_run", "parabolic_runner"):
        assert name in p, f"{name} preset is gone — update this test deliberately"
        assert _cut(p[name], "weak_peak_cut") is None, (
            f"{name} gained a weak_peak_cut; it is a long-hold preset and must not have one")


def test_the_scalp_preset_keeps_its_cuts():
    """The rule is two-sided: the sniper's cut is what makes it a sniper. Removing it would be
    the same error as leaving one on a position book."""
    s = _presets()["scalp"]
    assert _cut(s, "dead_weight_cut") or _cut(s, "weak_peak_cut"), \
        "the scalp preset lost the fast cut that defines it"
    ht = _cut(s, "hard_timeout")
    assert ht and ht["interval"] <= 240, "the scalp preset's clock is no longer a scalper's"


def test_the_guidance_file_is_reachable_and_under_the_read_cap():
    """Every `read` of a file is truncated at 16,000 chars on an agent host, so guidance past
    that point is never seen. The pointer lives in dsl-configuration.md, which must stay under
    the cap for the pointer itself to be read."""
    g = os.path.join(HERE, "..", "references", "time-cuts-by-horizon.md")
    assert os.path.exists(g), "references/time-cuts-by-horizon.md is missing"
    cfg = os.path.join(HERE, "..", "references", "dsl-configuration.md")
    size = os.path.getsize(cfg)
    assert size < 16000, f"dsl-configuration.md is {size} chars — past the 16k read cap"
    assert "time-cuts-by-horizon.md" in open(cfg, encoding="utf-8").read()[:16000], \
        "the pointer to the horizon guidance sits past the 16k read cap"
