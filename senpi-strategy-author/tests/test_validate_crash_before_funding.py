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

An error fails validation, so only what is certain is one: a read that precedes every binding (outside
a loop that also binds it), a closure that reads the name on every call, and a literal out of range.
A division by a live balance MAY pass 100 — that is a warning. The SILENT tables are correct code that
an earlier, lexical version of these checks failed; the last test keeps the shipped catalog silent.

Run: python3 -m pytest senpi-strategy-author/tests/test_validate_crash_before_funding.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

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


def _unbound(src):
    return [(f, n, k) for f, n, _, _, k in vs.unbound_name_offenders(textwrap.dedent(src))]


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
    assert _unbound(CLOSURE) == [("_persist", "held_assets", "free")]


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
    assert _unbound(LOCAL) == [("scan", "hard_stop_price", "local")]


def test_the_same_scope_shape_fails_validation(tmp_path):
    out = _run(_pkg(tmp_path, LOCAL))
    assert "hard_stop_price" in out and "UnboundLocalError" in out


# Crashes the lexical first draft let through (review of #847).
CRASHES = {
    "x += 1 on a module-level name": ('''
        COUNT = 0
        def scan(inputs, ctx):
            COUNT += 1
            return []
        ''', ("scan", "COUNT", "local")),
    "x += 1 is the only binding, even in a loop": ('''
        def scan(inputs, ctx):
            for r in ctx.rows():
                seen += 1
            return []
        ''', ("scan", "seen", "local")),
    "x = x + … on one line": ('''
        def scan(inputs, ctx):
            held = held + [1]
            return held
        ''', ("scan", "held", "local")),
    "a comprehension variable of the same name elsewhere": ('''
        def scan(inputs, ctx):
            def _persist():
                ctx.state.append(p)
            if not ctx.ok():
                _persist()
                return []
            p = ctx.p()
            names = [p for p in ctx.pos()]
            return names
        ''', ("_persist", "p", "free")),
    "a nested def called above its def": ('''
        def scan(inputs, ctx):
            if not inputs:
                return helper()
            def helper():
                return []
            return helper()
        ''', ("scan", "helper", "local")),
    "a default argument that reads a later name": ('''
        def scan(inputs, ctx):
            def _persist(h=held_assets):
                return h
            held_assets = ctx.held()
            return _persist()
        ''', ("scan", "held_assets", "local")),
}


@pytest.mark.parametrize("src, hit", CRASHES.values(), ids=CRASHES.keys())
def test_each_crash_shape_is_an_error(src, hit):
    assert _unbound(src) == [hit]


# Correct code that must not fail validation.
SILENT = {
    "the catalog shape: parameters, globals, comprehensions, loop vars": '''
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
        ''',
    "a loop-carried read behind a guard": '''
        def scan(inputs, ctx):
            for k, r in enumerate(ctx.rows()):
                if k > 0 and r > last:
                    pass
                last = r
            return []
        ''',
    "a closure that reads the name only under a parameter check": '''
        def scan(inputs, ctx):
            def _persist(result=None):
                if result is not None:
                    ctx.state.append({"h": held})
            if not ctx.ok():
                _persist()
                return []
            held = ctx.held()
            _persist(result=1)
            return []
        ''',
    "a walrus in a conditional expression": '''
        def scan(inputs, ctx):
            note = (f"{fl['total']}" if (fl := ctx.get("funding")) else "")
            return [note]
        ''',
    "a walrus in a comprehension condition": '''
        def scan(inputs, ctx):
            rows = sorted((thr, t) for t in ctx.tiers() if (thr := t.get("threshold")) is not None)
            return [thr for thr, _ in rows]
        ''',
    "a match capture": '''
        def scan(inputs, ctx):
            match ctx.payload():
                case {"state": str(state)}:
                    if state not in ("a", "b"):
                        state = "c"
                    return [state]
            return []
        ''',
    "a name a nested def binds through nonlocal": '''
        def scan(inputs, ctx):
            def gen():
                nonlocal frame
                frame = ctx.frame()
                yield
            next(gen())
            ok = bool(frame)
            del frame
            return [ok]
        ''',
    "a coroutine or generator closure, whose body runs later": '''
        def scan(inputs, ctx):
            async def coro():
                return task
            def gen():
                yield task
            task = ctx.run(coro(), gen())
            return [task]
        ''',
}


@pytest.mark.parametrize("src", SILENT.values(), ids=SILENT.keys())
def test_correct_code_is_not_a_finding(src):
    assert _unbound(src) == []


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


def _margin(src):
    return [k for _, k, _ in vs.margin_pct_offenders(textwrap.dedent(src))]


def test_a_margin_pct_divided_by_a_live_balance_with_no_clamp_is_a_ratio_finding():
    assert _margin(RATIO) == ["ratio"]


def test_the_ratio_shape_is_a_warning_not_an_error(tmp_path):
    errors, _, warns = _run(_pkg(tmp_path, RATIO)).partition("advisory warning")
    assert "marginPct" not in errors
    assert "marginPct" in warns and "no upper clamp" in warns


RATIOS = {
    "inline": 'return [{"marginPct": round(d / eq * 100, 2)}]',
    "subscript store": 's = {"asset": "ETH"}\n    s["marginPct"] = d / eq * 100\n    return [s]',
    "dict() keyword": 'return [dict(asset="ETH", marginPct=d / eq * 100)]',
    "annotated assignment": 'pct: float = d / eq * 100\n    return [{"marginPct": pct}]',
    "min() only inside the division": 'pct = max(min(d, cap), 14) / eq * 100\n    return [{"marginPct": pct}]',
}


@pytest.mark.parametrize("body", RATIOS.values(), ids=RATIOS.keys())
def test_every_way_of_setting_a_live_ratio_is_found(body):
    src = f"def scan(inputs, ctx):\n    d, cap, eq = ctx.a(), ctx.b(), ctx.free_margin()\n    {body}\n"
    assert _margin(src) == ["ratio"]


BOUNDED = {
    "clamped where it is computed": RATIO.replace("dyn_pct = dyn_margin / eq * 100.0",
                                                  "dyn_pct = min(dyn_margin / eq * 100.0, 100.0)"),
    "clamped on a later line": RATIO.replace("    return", "    dyn_pct = min(dyn_pct, 100.0)\n    return"),
    "skipped when out of range": RATIO.replace("    return", "    if dyn_pct > 100:\n        return []\n    return"),
    "a configured percent split across slots": '''
        def scan(inputs, ctx):
            pct = float(inputs.get("margin_pct", 50)) / int(inputs.get("slots", 2))
            return [{"marginPct": pct}]
        ''',
    "the catalog idiom, a configured constant": '''
        def scan(i, ctx):
            mp = float(i.get("marginPct", 25))
            return [{"marginPct": mp}]
        ''',
    "a fraction literal, which margin_fraction_offenders owns": 'def scan(i, c):\n    return [{"marginPct": 0.20}]\n',
}


@pytest.mark.parametrize("src", BOUNDED.values(), ids=BOUNDED.keys())
def test_a_bounded_margin_pct_is_not_a_finding(src):
    assert _margin(src) == []


@pytest.mark.parametrize("lit", ["150", "0", "-5"])
def test_a_literal_outside_the_percent_range_fails_validation(tmp_path, lit):
    src = f'def scan(i, c):\n    return [{{"marginPct": {lit}, "leverage": 1}}]\n'
    assert _margin(src) == ["literal"]
    out = _run(_pkg(tmp_path, src))
    assert f"marginPct: {lit}" in out and "✗" in out


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
