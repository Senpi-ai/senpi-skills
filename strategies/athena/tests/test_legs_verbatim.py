"""Athena's sleeves are Phalanx and Aegis plus a declared set of changes, and nothing else.

Athena 2.0.0 is the fork Jason runs live on his own account (edited 22 Oct and 5 Nov 2026),
promoted to the default. Each sleeve runs its template's engine at athena-concentrated's sizing
and ladder, plus what the templates do not have: a 1h momentum entry gate on both sleeves, and
on Phalanx a cohort regime gate (no LONG while the cohort is >= 65% short across all assets), a
divergence boost only on an active shift, and phase-1 trailing.

Every difference is declared below. Anything else is drift: a fix that lands in phalanx/ or
aegis/ must land here too, and this test is what says so.

  RUNTIME_DELTA  every runtime.yaml setting that differs from the template, compared as parsed
                 YAML, so comments and layout are free and the description is prose.
  SCANNER_DELTA  a digest of the exact lines each scanner adds and removes against the template.
                 A failure prints the current delta: if a template fix is missing here, port it;
                 if a gate changed on purpose, update the digest.
"""
import difflib
import hashlib
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
STRATEGIES = os.path.join(PKG, "..")
ABSENT = "<absent>"

RUNTIME_DELTA = {
    "phalanx": {
        "name": "athena-phalanx",
        "group": "athena",
        "strategy.wallet": "${ATHENA_PHALANX_WALLET}",
        "strategy.slots": 2,
        "strategy.margin_pct": 35,
        "strategy.default_leverage": 4,
        "scanners[phalanx_main_signals].inputs.marginPctBase": 35,
        "scanners[phalanx_main_signals].inputs.marginPctMax": 42,
        "scanners[phalanx_main_signals].inputs.leverageDefault": 4,
        "scanners[phalanx_main_signals].inputs.momBlockPct": 1.0,
        "scanners[phalanx_main_signals].inputs.momBars1h": 2,
        "scanners[phalanx_main_signals].inputs.cohortShortBlockPct": 65,
        "exit.dsl_preset.phase1.enabled": True,
        "exit.dsl_preset.phase1.max_loss_pct": 24.0,
        "exit.dsl_preset.phase1.retrace_threshold": 18,
        "exit.dsl_preset.phase2.tiers": [
            {"trigger_pct": 8, "lock_hw_pct": 40}, {"trigger_pct": 24, "lock_hw_pct": 55},
            {"trigger_pct": 49.4, "lock_hw_pct": 70}, {"trigger_pct": 66.4, "lock_hw_pct": 88}],
        "risk.guard_rails.max_entries_per_day": 999,
        "risk.guard_rails.drawdown_halt_pct": 28,
    },
    "aegis": {
        "name": "athena-aegis",
        "group": "athena",
        "strategy.wallet": "${ATHENA_AEGIS_WALLET}",
        "strategy.slots": 2,
        "strategy.margin_pct": 35,
        "strategy.default_leverage": 5,
        "scanners[aegis_main_signals].inputs.marginPctBase": 100,
        "scanners[aegis_main_signals].inputs.marginPctMax": 45,
        "scanners[aegis_main_signals].inputs.leverageDefault": 5,
        "scanners[aegis_main_signals].inputs.maxSlots": 2,
        "scanners[aegis_main_signals].inputs.momBlockPct": 1.0,
        "scanners[aegis_main_signals].inputs.momBars1h": 2,
        "exit.dsl_preset.phase1.max_loss_pct": 16.7,
        "exit.dsl_preset.phase1.retrace_threshold": 16.7,
        "exit.dsl_preset.phase2.tiers": [
            {"trigger_pct": 10, "lock_hw_pct": 35}, {"trigger_pct": 20, "lock_hw_pct": 45},
            {"trigger_pct": 40, "lock_hw_pct": 60}, {"trigger_pct": 83.3, "lock_hw_pct": 85}],
        "risk.guard_rails.max_entries_per_day": 999,
        "risk.guard_rails.drawdown_halt_pct": 25,
    },
}

# (leg, file) -> sha256[:16] of the lines Athena adds (+) and removes (-) against the template.
#   phalanx scan.py     +47 -6  momentum gate, regime gate, active-shift divergence, gate counters
#   phalanx scoring.py  +23     price_momentum()
#   aegis scan.py       +22 -7  1h candles read alongside 4h, momentum gate
#   aegis scoring.py    +23     price_momentum()
SCANNER_DELTA = {
    ("phalanx", "scan.py"): "13f1cc2e82051152",
    ("phalanx", "scoring.py"): "240d46feb996fa92",
    ("aegis", "scan.py"): "700e01aafa895080",
    ("aegis", "scoring.py"): "bc60f442c9847ca4",
}


def _yaml(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _flat(node, path=""):
    """Dotted paths to leaf values; a list of named items is keyed by name, not position."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _flat(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list) and node and all(isinstance(x, dict) and "name" in x for x in node):
        for x in node:
            yield from _flat(x, f"{path}[{x['name']}]")
    else:
        yield path, node


def _scanner_delta(leg, fname):
    def lines(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as f:
            return f.read().splitlines()
    diff = difflib.unified_diff(lines(STRATEGIES, leg, "main", "scanners", fname),
                                lines(PKG, leg, "scanners", fname), n=0, lineterm="")
    return [l for l in diff if not l.startswith(("---", "+++", "@@"))]


def test_runtime_differs_from_the_template_only_by_the_declared_delta():
    for leg, declared in RUNTIME_DELTA.items():
        src = dict(_flat(_yaml(STRATEGIES, leg, "main", "runtime.yaml")))
        dst = dict(_flat(_yaml(PKG, leg, "runtime.yaml")))
        got = {k: dst.get(k, ABSENT) for k in set(src) | set(dst)
               if k != "description" and src.get(k, ABSENT) != dst.get(k, ABSENT)}
        undeclared = {k: (src.get(k, ABSENT), v) for k, v in got.items() if k not in declared}
        assert not undeclared, f"{leg}: undeclared differences from {leg}/main (template, athena): {undeclared}"
        wrong = {k: (v, got.get(k, src.get(k, ABSENT))) for k, v in declared.items() if got.get(k, ABSENT) != v}
        assert not wrong, f"{leg}: declared settings not in effect (declared, actual): {wrong}"


def test_scanners_differ_from_the_template_only_by_the_declared_delta():
    for (leg, fname), digest in SCANNER_DELTA.items():
        delta = _scanner_delta(leg, fname)
        got = hashlib.sha256("\n".join(delta).encode()).hexdigest()[:16]
        assert got == digest, (
            f"{leg}/scanners/{fname} no longer differs from {leg}/main by the declared delta "
            f"(digest {got}, declared {digest}). The current delta:\n" + "\n".join(delta))


def test_default_weighting_is_50_50():
    m = _yaml(PKG, "strategy.yaml")
    shares = {i["name"]: i["funding_share"] for i in m["instances"]}
    assert shares == {"phalanx": 0.5, "aegis": 0.5}
    assert abs(sum(shares.values()) - 1.0) < 1e-9
