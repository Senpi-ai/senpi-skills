"""Two crashes `ast.parse` cannot see, and both shipped to live wallets.

* A name read on a path that runs before it is bound. Python decides a function's locals at compile
  time, so the read is an UnboundLocalError — never a fall-through to the module-level name of the
  same spelling — and a nested `_persist()` defined above its free variable raises NameError when an
  early return calls it. Both are LATENT: the branch may first be taken days after the deploy, and
  the scanner then crashes every tick until someone looks. Both shapes have reached funded wallets:
  a branch variable read above its assignment, and a `_persist()` closure called from an early
  return that runs before the enclosing scope binds what it reads.

* A `marginPct` that can leave (0, 100]. The runtime drops the whole candidate at delivery, so the
  strategy funds, ticks clean and never opens. The catalog reads marginPct from `inputs` as a
  constant; the shape that breaks recomputes it from a live balance
  (`max(size, MIN_ORDER) / free_margin * 100`), which is correct while the wallet is liquid and
  passes 100 the moment the floor binds.

Both checks are errors, not warnings — each finding is a crash or a dropped candidate on that path,
not a style opinion. The last test is the one that keeps them honest: every module of the shipped
catalog must stay silent, or the check is too eager to ship.

Run: python3 -m pytest senpi-strategy-author/tests/test_validate_crash_before_funding.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import pathlib
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "..", "scripts", "validate_strategy.py")
CATALOG = pathlib.Path(HERE, "..", "..", "strategies").resolve()
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

RUNTIME = """\
name: probe-main
group: probe
version: 3.0.0
strategy:
  wallet: "${PROBE_WALLET}"
  slots: 1
scanners:
  - name: probe_scan
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300
actions: []
"""
MANIFEST = """\
schema_version: 1
id: probe
version: "1.0.0"
catalog:
  name: "Probe"
  tagline: "t"
instances:
  - name: main
    runtime: main/runtime.yaml
    wallet_env: PROBE_WALLET
    funding_share: 1.0
"""


def _pkg(tmp_path, scan_body):
    d = tmp_path / "probe"
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(MANIFEST)
    (d / "main" / "runtime.yaml").write_text(RUNTIME)
    (d / "main" / "scanners" / "scan.py").write_text(textwrap.dedent(scan_body))
    return d


def _run(pkg):
    r = subprocess.run([sys.executable, VALIDATOR, str(pkg)], capture_output=True, text=True)
    return r.stdout + r.stderr


# ── the closure shape: _persist() reads a free variable an early return has not bound yet ──
CLOSURE = '''
def scan(inputs, ctx):
    def _persist(extra=None):
        ctx.state.append({"held": held_assets[0] if held_assets else "", "extra": extra})

    account_value, positions = ctx.account()
    if account_value <= 0:
        _persist({"gate": "no_account"})
        return []
    held_assets = [p["coin"] for p in positions]
    return [{"asset": held_assets[0]}]
'''


def test_a_closure_called_before_its_free_variable_exists_is_an_error():
    hits = vs.unbound_name_offenders(CLOSURE)
    assert [(f, n, k) for f, n, _, _, k in hits] == [("_persist", "held_assets", "free")]


def test_the_closure_shape_fails_validation(tmp_path):
    out = _run(_pkg(tmp_path, CLOSURE))
    assert "held_assets" in out and "NameError" in out


# ── the same-scope shape: a branch reads a name the line below it binds ──
LOCAL = '''
def scan(inputs, ctx):
    rows = ctx.fetch()
    if not rows:
        return [{"stop": hard_stop_price}]
    hard_stop_price = rows[0] * 0.98
    return [{"stop": hard_stop_price}]
'''


def test_a_read_before_every_binding_in_the_same_scope_is_an_error():
    assert [(n, k) for _, n, _, _, k in vs.unbound_name_offenders(LOCAL)] == \
        [("hard_stop_price", "local")]


def test_the_same_scope_shape_fails_validation(tmp_path):
    out = _run(_pkg(tmp_path, LOCAL))
    assert "hard_stop_price" in out and "UnboundLocalError" in out


def test_a_loop_body_that_reads_what_it_binds_below_still_crashes_on_the_first_pass():
    src = "def scan(inputs, ctx):\n    for r in ctx.rows():\n        if flag:\n            pass\n        flag = r\n"
    assert [n for _, n, _, _, _ in vs.unbound_name_offenders(src)] == ["flag"]


# ── what must stay silent ──
CLEAN = '''
CAP = 5

def scan(inputs, ctx):
    total = 0
    for r in ctx.fetch():
        total += r
    seen = {k: v for k, v in ctx.items()}
    picks = [c for c in seen if c]

    def _persist(result=None, signaled=None):
        ctx.state.append({"total": total, "picks": picks, "r": result, "s": signaled})

    if total <= 0:
        _persist(result={"gate": "empty"})
        return []
    for i in range(CAP):
        pass
    return [{"n": i}]
'''


def test_parameters_module_globals_comprehensions_and_loop_vars_are_not_findings():
    assert vs.unbound_name_offenders(CLEAN) == []


def test_a_syntax_error_is_left_to_the_syntax_check():
    assert vs.unbound_name_offenders("def scan(:\n") == []
    assert vs.margin_pct_offenders("def scan(:\n") == []


# ── marginPct ──
RATIO = '''
MIN_ORDER = 14.00

def scan(inputs, ctx):
    eq = ctx.free_margin()
    margin_pct = float(inputs.get("margin_pct", 7.0))
    dyn_margin = max(eq * margin_pct / 100.0, MIN_ORDER)
    dyn_pct = dyn_margin / eq * 100.0
    return [{"asset": "ETH", "direction": "LONG", "marginPct": round(dyn_pct, 2), "leverage": 1.0}]
'''


def test_a_margin_pct_recomputed_from_a_balance_with_no_clamp_is_an_error():
    assert [k for _, k, _ in vs.margin_pct_offenders(RATIO)] == ["ratio"]


def test_the_ratio_shape_fails_validation(tmp_path):
    out = _run(_pkg(tmp_path, RATIO))
    assert "marginPct" in out and "upper clamp" in out


def test_the_same_ratio_clamped_to_100_is_fine():
    assert vs.margin_pct_offenders(RATIO.replace(
        "dyn_pct = dyn_margin / eq * 100.0",
        "dyn_pct = min(dyn_margin / eq * 100.0, 100.0)")) == []


def test_a_literal_outside_the_percent_range_is_an_error():
    over = 'def scan(i, c):\n    return [{"marginPct": 150, "leverage": 1}]\n'
    zero = 'def scan(i, c):\n    return [{"marginPct": 0, "leverage": 1}]\n'
    assert [k for _, k, _ in vs.margin_pct_offenders(over)] == ["literal"]
    assert [k for _, k, _ in vs.margin_pct_offenders(zero)] == ["literal"]


def test_a_configured_constant_and_a_fraction_literal_are_left_alone():
    # The catalog idiom, and the fraction case that `margin_fraction_offenders` already owns.
    ok = 'def scan(i, c):\n    mp = float(i.get("marginPct", 25))\n    return [{"marginPct": mp}]\n'
    frac = 'def scan(i, c):\n    return [{"marginPct": 0.20}]\n'
    assert vs.margin_pct_offenders(ok) == []
    assert vs.margin_pct_offenders(frac) == []


# ── the honesty guard ──
def test_the_shipped_catalog_is_silent_on_both_checks():
    """A check that fires on a strategy we ship is a check that cries wolf. Every module of the
    catalog goes through both; the first real finding here is a bug in the catalog or in the lint,
    and either way it has to be looked at before this suite goes green again."""
    files = sorted(CATALOG.rglob("*.py"))
    assert len(files) > 300, f"catalog looks wrong: {len(files)} modules under {CATALOG}"
    noisy = []
    for f in files:
        src = f.read_text()
        for hit in vs.unbound_name_offenders(src):
            noisy.append(f"{f.relative_to(CATALOG)}: unbound {hit}")
        if "scanners" in f.parts:
            for hit in vs.margin_pct_offenders(src):
                noisy.append(f"{f.relative_to(CATALOG)}: marginPct {hit}")
    assert noisy == [], "\n".join(noisy)
