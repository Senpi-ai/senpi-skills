"""Athena-X is Athena at 5x. Raising leverage alone makes every stop TIGHTER, not looser.

A DSL stop is denominated in ROE, and ROE converts to price by dividing by leverage. So carrying a
3x-tuned ladder onto a 5x book silently shrinks every exit by 3/5: Athena's `max_loss_pct: 15` is a
5.0% price move at 3x and a 3.0% move at 5x, and the first profit-lock rung moves from 6.7% of price
to 4.0% — inside ordinary noise, so a winner gets ratcheted out on a wiggle.

phalanx/runtime.yaml states the rule outright: "Leverage, stop width and this ladder are one system:
change together." The live hand-edit on 2026-09-19 raised leverage and left the ladder, which is
what produced the stop-outs that prompted this package.

So the invariant is not "same numbers" — it is **same behaviour in PRICE terms**. Every ROE
threshold here is Athena's scaled by 5/3, and these tests compute that from the configs rather than
trusting a comment.

Run: python3 -m pytest strategies/athena-x/tests -q
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
STRATEGIES = os.path.join(PKG, "..")
LEGS = ("phalanx", "aegis")
TOL = 0.06   # the yaml carries rounded ROE values (16.7, 33, 167) — parity to within 6%

# Keys Athena-X may MOVE, all of them consequences of the leverage change.
SIZING_KEYS = ("  slots:", "  margin_pct:", "  default_leverage:", "      marginPctBase:",
               "      marginPctMax:", "      leverageDefault:", "      maxSlots:",
               "    default_signal_validity_seconds:", "      max_loss_pct:",
               "      retrace_threshold:", "        - { trigger_pct:", "    drawdown_halt_pct:")
# Keys it may DROP: both ration ENTRIES, which is what a conviction hold must not do.
REMOVED_KEYS = ("    max_entries_per_day:", "    bypass_max_entries_per_day_on_profit:",
                "    daily_loss_limit_pct:")
IDENTITY = ("name:", "group:", "  wallet:", "  ATHENA")


def _rt(pkg, leg):
    return yaml.safe_load(open(os.path.join(STRATEGIES, pkg, leg, "runtime.yaml"), encoding="utf-8"))


def _code_lines(path):
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.strip() and not l.lstrip().startswith("#")]


def _close(a, b):
    return abs(a - b) <= TOL * max(abs(a), abs(b), 1e-9)


def test_scanners_are_byte_identical_to_the_source_templates():
    for leg in LEGS:
        sdir = os.path.join(STRATEGIES, leg, "main", "scanners")
        ddir = os.path.join(PKG, leg, "scanners")
        for fn in sorted(os.listdir(sdir)):
            if fn.endswith(".py"):
                with open(os.path.join(sdir, fn), "rb") as a, open(os.path.join(ddir, fn), "rb") as b:
                    assert a.read() == b.read(), f"{leg}/{fn} drifted"


def test_the_hard_stop_is_the_same_width_in_price():
    """The one that actually closes a position. ROE / leverage is the price move it tolerates."""
    for leg in LEGS:
        a, x = _rt("athena", leg), _rt("athena-x", leg)
        pa = a["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / a["strategy"]["default_leverage"]
        px = x["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / x["strategy"]["default_leverage"]
        assert _close(pa, px), f"{leg}: stop is {px:.2f}% of price vs Athena's {pa:.2f}%"


def test_every_profit_lock_rung_triggers_at_the_same_price_move():
    for leg in LEGS:
        a, x = _rt("athena", leg), _rt("athena-x", leg)
        ta = a["exit"]["dsl_preset"]["phase2"]["tiers"]
        tx = x["exit"]["dsl_preset"]["phase2"]["tiers"]
        assert len(ta) == len(tx), f"{leg}: rung count changed"
        for ra, rx in zip(ta, tx):
            pa = ra["trigger_pct"] / a["strategy"]["default_leverage"]
            px = rx["trigger_pct"] / x["strategy"]["default_leverage"]
            assert _close(pa, px), f"{leg}: rung fires at {px:.2f}% of price vs {pa:.2f}%"
            assert ra["lock_hw_pct"] == rx["lock_hw_pct"], (
                f"{leg}: lock % must not change — it is what sets the retrace room")


def test_the_drawdown_circuit_breaker_is_not_tighter_in_price():
    for leg in LEGS:
        a, x = _rt("athena", leg), _rt("athena-x", leg)
        pa = a["risk"]["guard_rails"]["drawdown_halt_pct"] / a["strategy"]["default_leverage"]
        px = x["risk"]["guard_rails"]["drawdown_halt_pct"] / x["strategy"]["default_leverage"]
        assert px >= pa * (1 - TOL), f"{leg}: breaker fires {pa/px:.1f}x sooner in price terms"


def test_the_entry_rationing_gates_are_gone():
    """Every gate in risk.guard_rails halts ENTRIES, never exits. A conviction hold that cannot add
    while it is underwater is the exact behaviour that forced a manual close-and-redeploy."""
    for leg in LEGS:
        rails = _rt("athena-x", leg)["risk"]["guard_rails"]
        assert "max_entries_per_day" not in rails, leg
        assert "daily_loss_limit_pct" not in rails, leg
        assert rails["drawdown_halt_pct"] > 0, f"{leg}: the circuit breaker must remain"
        assert rails["drawdown_reset_on_day_rollover"] is False, leg


def test_it_is_actually_bigger():
    for leg in LEGS:
        a, x = _rt("athena", leg), _rt("athena-x", leg)
        for k in ("default_leverage", "margin_pct", "slots"):
            assert x["strategy"][k] > a["strategy"][k], f"{leg}: {k}"


def test_runtime_differs_only_in_identity_sizing_and_declared_removals():
    for leg in LEGS:
        s = [l for l in _code_lines(os.path.join(STRATEGIES, leg, "main", "runtime.yaml"))
             if not l.startswith(REMOVED_KEYS)]
        d = _code_lines(os.path.join(PKG, leg, "runtime.yaml"))
        assert len(s) == len(d), f"{leg}: line count changed beyond the declared removals"
        for a, b in zip(s, d):
            if a == b:
                continue
            assert b.startswith(IDENTITY) or any(b.startswith(k) for k in SIZING_KEYS), \
                f"{leg}: undeclared change {b!r} (was {a!r})"


def test_the_two_packages_never_share_a_wallet():
    seen = set()
    for pkg in ("athena", "athena-x"):
        for leg in LEGS:
            w = _rt(pkg, leg)["strategy"]["wallet"]
            assert w not in seen, f"{pkg}/{leg} reuses {w}"
            seen.add(w)
