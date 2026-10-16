"""The regime engine must stay the PARENT's, byte for byte.

This fork's whole claim is that concentration is a sizing and risk change, not a thesis
change — so a reader can compare its results against ibis's and have the comparison mean
something. That claim is only true while `scoring.py` is identical, and a copy that silently
diverges is indistinguishable from one that was deliberately improved.

ibis itself ships NO tests, so these assertions are the only thing pinning the shared engine
in either direction: a change to the parent's scorer now breaks this file too, which is the
point. If the parent's thesis is genuinely retuned, the fork must be re-forked deliberately
and this test updated in the same commit — not silently drift apart.

scan.py is NOT byte-identical (per-regime margin), so its difference is enumerated instead,
which keeps an unrelated edit from hiding inside the diff that is allowed.

── A RESCORING WAS BUILT, TESTED, AND REJECTED (2026-10-12). Read this before writing another.

The audit behind the 0.45 gate also found five real defects IN THE SCORE, all verified from the
code: `minRangeWidthPct` pays +2 to every surviving fade (its else-branch rejects, so the
points are a constant); `trend_structure`'s +3 is likewise flat; funding is scored backwards
(calm pays +2 and measured WORSE, +1.25% vs +4.04% ROE); "deep at the edge" is noise
(z=+0.58); and the weights are inverted — ER survives de-overlapping (z=+2.57/+2.51) on ONE
point while pullback does not (sign flips at 24-bar spacing) on TWO. A higher score measurably
performed worse: trend 6 -> +2.65% ROE against trend 7 -> +0.32%.

So a rescored engine was written — ER-banded base, confirmations demoted to +1, a flat fade
score — and measured head to head on the same 200 days, same exits, same one-slot wallet:

    OLD scorer, gate 0.45   120 entries   +1.84% ROE/entry
    NEW scorer, gate 0.45   112 entries   +0.62% ROE/entry

It was NOT shipped, and the reason is the reason to be careful here rather than the number:

  1. NO POWER. The score's job is ALLOCATION, which only shows up at the ~112 entries a single
     slot takes in 200 days. Per-entry sd is 14.9pp, so SE of a configuration's mean is
     1.41pp — the ENTIRE observed spread across every variant tried (0.15 to 1.84) fits inside
     one standard error, and a 95% CI on the difference between two of them is about +/-4pp.
  2. NO STABLE ANSWER. Split in half, "prefer the trend" vs "prefer the fade" on contested
     ticks gave +3.78pp in H1 and -10.03pp in H2 — the sign FLIPS, and H2's flip is
     significant. There is no allocation rule this data supports.

The gate fix was kept because it passes exactly the test the rescoring fails: it is a FILTER,
measurable at signal level with n in the hundreds, and the ER effect holds with the same sign
and significance in BOTH halves (+2.67pp H1, +5.02pp H2; the admitted band +1.85pp H1 /
+4.00pp H2).

The five defects remain TRUE. What is missing is evidence that fixing them helps, and this
window cannot supply it — by the arithmetic above it would take roughly 900-1800 days of
one-slot history to resolve a 1pp difference. So: do not rescore this engine off a backtest of
this size. Either wait for live entries to accumulate, or change it as a deliberate bet and
label it as one.

Run: python3 -m pytest strategies/ibis-concentrated/tests/test_engine_parity.py -q
"""
import hashlib
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "main", "scanners")
PARENT = os.path.join(HERE, "..", "..", "ibis", "main", "scanners")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_scoring_is_byte_identical_to_the_parent():
    mine, theirs = _read(os.path.join(PKG, "scoring.py")), _read(os.path.join(PARENT, "scoring.py"))
    assert hashlib.sha256(mine.encode()).hexdigest() == hashlib.sha256(theirs.encode()).hexdigest(), (
        "ibis-concentrated's scoring.py has drifted from ibis's. The fork's thesis is the "
        "parent's by construction — if the regime engine is genuinely being changed, change it "
        "in the PARENT and re-fork, so both packages move together and the comparison between "
        "them stays honest.")


def test_scan_differs_from_the_parent_only_in_the_sizing_path():
    """Normalise the three documented differences away; everything else must match."""
    mine, theirs = _read(os.path.join(PKG, "scan.py")), _read(os.path.join(PARENT, "scan.py"))

    # 1. the log prefix
    mine_n = mine.replace("[ibis-cc.scan]", "[ibis.scan]")

    # 2. the fork note in the module docstring (rstrip the blank line it left behind)
    fork_note = mine_n.index("FORKED FROM IBIS.")
    end = mine_n.index('"""', fork_note)
    mine_n = mine_n[:fork_note].rstrip() + "\n" + mine_n[end:]

    # 3. the per-regime margin base — the only behavioural change
    for block in (
        '''    # Per-regime concentration. Falls back to the single parent key so a config that never heard
    # of these still sizes sanely rather than silently reading 0.
    base_trend_pct = float(inputs.get("marginPctBaseTrend", base_margin_pct))
    base_range_pct = float(inputs.get("marginPctBaseRange", base_margin_pct))
''',
    ):
        assert block in mine_n, "the per-regime margin inputs are gone — the fork has no concentration"
        mine_n = mine_n.replace(block, "")

    old_emit = '''        is_trend = th["regime"] == "TREND"
        lev = lev_trend if is_trend else lev_range
        leverage = max(lev_min, min(lev, lev_max))
        # RISK = marginPct x max_loss_pct. PRICE DISTANCE to the stop = max_loss_pct / leverage.
        # The two are independent, so leverage places the stop where each regime's thesis dies
        # and margin prices the loss. See runtime.yaml's exit block for the derivation.
        margin_pct = round(scoring.margin_tier_pct(
            th["score"], base_trend_pct if is_trend else base_range_pct), 4)'''
    parent_emit = '''        lev = lev_trend if th["regime"] == "TREND" else lev_range
        leverage = max(lev_min, min(lev, lev_max))
        margin_pct = round(scoring.margin_tier_pct(th["score"], base_margin_pct), 4)'''
    assert old_emit in mine_n
    mine_n = mine_n.replace(old_emit, parent_emit)

    # trailing blank-line noise from the excisions above
    mine_n = re.sub(r"\n{3,}", "\n\n", mine_n)
    theirs_n = re.sub(r"\n{3,}", "\n\n", theirs)

    assert mine_n == theirs_n, (
        "scan.py differs from the parent's beyond the log prefix, the fork note and the "
        "per-regime margin base. Those three are the whole allowed diff — an unrelated change "
        "must either go into the PARENT (so both get it) or be documented here deliberately.")


def test_the_fork_does_not_quietly_reintroduce_a_scoring_import_of_its_own():
    """A local re-implementation of any scoring helper would defeat the parity test above."""
    scan = _read(os.path.join(PKG, "scan.py"))
    for fn in ("def classify_regime", "def efficiency_ratio", "def build_thesis",
               "def margin_tier_pct", "def range_bounds"):
        assert fn not in scan, (
            f"scan.py defines its own {fn!r} — the thesis math must come from the shared "
            "scoring module or the parity guarantee is void")
