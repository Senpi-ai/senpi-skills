"""Athena's sleeves ARE Phalanx and Aegis. Each leg's scanners are byte-identical to its
source template and its runtime.yaml differs only in the identity lines (name, group, wallet,
the description's first line). A fix that lands in phalanx/ or aegis/ must land here too —
this test is what says so. The default weighting is pinned because it is the one thing this
package adds."""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
STRATEGIES = os.path.join(PKG, "..")
LEGS = {"phalanx": "phalanx", "aegis": "aegis"}          # athena leg -> source template
IDENTITY = ("name:", "group:", "  wallet:")


def _code_lines(path):
    """The lines that run: comments and blank lines stripped."""
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.strip() and not l.lstrip().startswith("#")]


def test_scanners_are_byte_identical_to_the_source_templates():
    for leg, src in LEGS.items():
        sdir = os.path.join(STRATEGIES, src, "main", "scanners")
        ddir = os.path.join(PKG, leg, "scanners")
        names = sorted(n for n in os.listdir(sdir) if n.endswith(".py"))
        assert names == sorted(n for n in os.listdir(ddir) if n.endswith(".py")), leg
        for n in names:
            with open(os.path.join(sdir, n), "rb") as a, open(os.path.join(ddir, n), "rb") as b:
                assert a.read() == b.read(), f"{leg}/scanners/{n} drifted from {src}"


# The concentration delta, declared line-for-line. Both arms were sized up on 2026-10-09 from a
# 14-day replay of this package's own round trips (34 aegis over 7 coins, 54 phalanx over 11):
# the stop moved onto each arm's measured adverse p90, the first rung onto 2.00% of price where
# it arms on 41% / 60% of trades instead of 29% / 36%, and the locks onto what the give-back
# distribution supports. Leverage went to each scanner's hard ceiling (aegis 5x, phalanx 4x) and
# every ROE number was re-derived so the price distances are deliberate rather than a side effect.
#
# Listing the exact lines means a FIFTH change to either arm fails here instead of quietly
# becoming part of "the concentration experiment". The detail — why each number is what it is —
# lives in test_arm_sizing.py, which asserts the price distances rather than the ROE values.
CONCENTRATION_DELTA = {
    "phalanx": {
        '  slots: 2',
        '  margin_pct: 40',
        '  default_leverage: 4',
        '      marginPctBase: 40',
        '      marginPctMax: 48',
        '      leverageDefault: 4',
        '      max_loss_pct: 24.0              # 6.00% of price at 4x — the measured adverse p90 (5.96%)',
        '      retrace_threshold: 24           # INERT while phase1.enabled is false',
        '        - { trigger_pct: 8, lock_hw_pct: 40 }     #  2.00% of price at 4x — arms on 60% of trades (was 36%)',
        '        - { trigger_pct: 24, lock_hw_pct: 55 }    #  6.00% of price',
        '        - { trigger_pct: 49.4, lock_hw_pct: 70 }  # 12.35% of price — the measured peak p90',
        '        - { trigger_pct: 66.4, lock_hw_pct: 88 }  # 16.60% of price — the measured peak max',
        '    drawdown_halt_pct: 28                   # THE overall risk budget: 28% of PnL from peak,',
    },
    "aegis": {
        '  slots: 2',
        '  margin_pct: 35',
        '  default_leverage: 5',
        '      marginPctBase: 100',
        '      marginPctMax: 45',
        '      leverageDefault: 5',
        '      maxSlots: 2',
        '      max_loss_pct: 16.7              # 3.33% of price at 5x — the measured adverse p90 (3.41%)',
        '      retrace_threshold: 16.7         # INERT while phase1.enabled is false',
        '        - { trigger_pct: 10, lock_hw_pct: 35 }   #  2.00% of price at 5x — arms on 41% of trades',
        '        - { trigger_pct: 20, lock_hw_pct: 45 }   #  4.00% of price — arms on 29%',
        '        - { trigger_pct: 40, lock_hw_pct: 60 }   #  8.00% of price',
        '        - { trigger_pct: 83.3, lock_hw_pct: 85 } # 16.67% of price — above the 9.84% max peak',
        '    drawdown_halt_pct: 25                   # THE overall risk budget: 25% of PnL from peak,',
    },
}


def test_runtime_differs_only_in_identity_and_the_declared_concentration_delta():
    for leg, src in LEGS.items():
        s = _code_lines(os.path.join(STRATEGIES, src, "main", "runtime.yaml"))
        d = _code_lines(os.path.join(PKG, leg, "runtime.yaml"))
        assert len(s) == len(d), f"{leg}: runtime.yaml gained or lost lines vs {src}"
        diffs = [(a, b) for a, b in zip(s, d) if a != b]
        delta = CONCENTRATION_DELTA[leg]
        allowed = [b for a, b in diffs
                   if b.startswith(IDENTITY) or b.startswith("  ATHENA ") or b in delta]
        assert len(diffs) == 4 + len(delta) and len(allowed) == len(diffs), (
            f"{leg}: unexpected differences {[d for d in diffs if d[1] not in delta]}")
        assert {b for _a, b in diffs} >= delta, (
            f"{leg}: the declared concentration delta is missing — expected {sorted(delta)}")
        assert f"name: athena-concentrated-{leg}" in d and "group: athena-concentrated" in d
        assert f'  wallet: "${{ATHENA_{leg.upper()}_WALLET}}"' in d


def test_both_arms_are_deliberately_bigger_than_their_parents():
    """Until 2026-10-09 this asserted that phalanx's TOTAL exposure was unchanged — concentration
    without size, so a difference in results was attributable to holding two names instead of
    three. Jason retired that constraint: "both arms take somewhat bigger more concentrated bets
    in this version". So the assertion is inverted — each arm must now be bigger than its parent
    on BOTH axes, and the confound is accepted on purpose.

    Stated plainly because it changes how the results read: this version moves concentration,
    position size AND leverage at once, so it can no longer be read as a controlled test of
    concentration. It is a higher-risk variant, measured against its parent in aggregate only."""
    import yaml as _y
    for leg in ("phalanx", "aegis"):
        src = _y.safe_load(open(os.path.join(STRATEGIES, LEGS[leg], "main", "runtime.yaml"),
                                encoding="utf-8"))["strategy"]
        dst = _y.safe_load(open(os.path.join(PKG, leg, "runtime.yaml"),
                                encoding="utf-8"))["strategy"]
        assert dst["slots"] <= src["slots"], f"{leg}: slots went UP, that is not concentration"
        assert dst["margin_pct"] > src["margin_pct"], f"{leg}: position size did not grow"
        assert dst["default_leverage"] > src["default_leverage"], f"{leg}: leverage did not grow"
        # margin x leverage is the per-position notional share; it must have grown
        src_notional = src["margin_pct"] * src["default_leverage"]
        dst_notional = dst["margin_pct"] * dst["default_leverage"]
        assert dst_notional > src_notional, (
            f"{leg}: per-position notional {dst_notional} is not above the parent's {src_notional}")


def test_default_weighting_is_65_35():
    with open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8") as f:
        m = yaml.safe_load(f)
    shares = {i["name"]: i["funding_share"] for i in m["instances"]}
    assert shares == {"phalanx": 0.65, "aegis": 0.35}
    assert abs(sum(shares.values()) - 1.0) < 1e-9
