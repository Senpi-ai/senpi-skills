#!/usr/bin/env python3
"""Offline tests for the strategy-ops safety scripts — no network / no token needed.

The `unloadable` smoke cases fail at IMPORT time (before ctx/MCP), and the protect verdicts are pure,
so this whole file runs hermetically.

    python3 -m pytest senpi-strategy-ops/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _smoke                    # noqa: E402
import protect                   # noqa: E402
import validate_universe as vu   # noqa: E402


def _pkg_with_scan(tmp, scan_src, entry="scan.py"):
    """Write a minimal runtime.yaml + scanners/<entry> under tmp; return (runtime_path, es)."""
    scdir = os.path.join(tmp, "scanners")
    os.makedirs(scdir, exist_ok=True)
    with open(os.path.join(scdir, entry), "w") as f:
        f.write(scan_src)
    rp = os.path.join(tmp, "runtime.yaml")
    with open(rp, "w") as f:
        f.write("name: t\n")
    es = {"name": "t_signals", "path": "./scanners", "entrypoint": entry,
          "signal_data_schema": {"score": {"type": "number"}},
          "default_signal_validity_seconds": 600, "interval_seconds": 300, "timeout_seconds": 20}
    return rp, es


# ── Finding 2: an unimportable/non-runnable scan is BLOCK (unloadable) — never funded ──
# Each of these can NEVER register a scanner, so funding it just parks capital that can't trade
# (the divergence-play failure). They fail BEFORE the child needs a token, so they run offline.

def test_syntax_error_scan_is_unloadable_and_blocks():
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, "def scan(inputs, ctx)\n    return []\n")   # missing ':' → SyntaxError
        v = _smoke.smoke(rp, es)
    assert v["status"] == "unloadable"
    assert v["status"] in _smoke.BLOCK
    assert v["status"] not in _smoke.WARN


def test_import_error_scan_is_unloadable():
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, "import a_module_that_does_not_exist_xyz\ndef scan(i, c):\n    return []\n")
        v = _smoke.smoke(rp, es)
    assert v["status"] == "unloadable" and v["status"] in _smoke.BLOCK


def test_missing_entrypoint_is_unloadable():
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, "def scan(i, c):\n    return []\n")
        es["entrypoint"] = "nope.py"
        v = _smoke.smoke(rp, es)
    assert v["status"] == "unloadable" and v["status"] in _smoke.BLOCK


def test_scan_not_callable_is_unloadable():
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, "scan = 42\n")   # 'scan' exists but isn't callable
        v = _smoke.smoke(rp, es)
    assert v["status"] == "unloadable" and v["status"] in _smoke.BLOCK


# ── Finding 1: every smoke() verdict carries sizing_warnings; diagnose's extraction never KeyErrors ──

def test_every_smoke_return_has_sizing_warnings_key():
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, "def scan(inputs, ctx)\n")   # syntax error → unloadable (no sizing_warnings pre-fix)
        v = _smoke.smoke(rp, es)
    # exactly the comprehension diagnose.py runs on the smoke result — must not KeyError on ANY status
    extracted = {k: v.get(k) for k in ("status", "detail", "n_signals", "violations",
                                       "sizing_warnings", "returned_repr", "traceback")}
    assert extracted["status"] == "unloadable"
    assert extracted["sizing_warnings"] == []          # present + empty, not missing


# ── sizing_warnings logic (the marginPct percent/fraction confusion) ──

def test_sizing_warning_flags_fraction_vs_runtime_percent():
    # runtime says 18(%) but the signal emitted 0.18 (the v2 fraction) → ~100× tell
    w = _smoke.sizing_warnings([{"marginPct": 0.18}], strategy_margin_pct=18)
    assert w and "PERCENT" in w[0]


def test_sizing_warning_silent_when_consistent():
    assert _smoke.sizing_warnings([{"marginPct": 18}], strategy_margin_pct=18) == []


# ── Secondary B: a take-profit is NOT a protective stop ──

def test_take_profit_is_not_a_stop():
    assert protect._is_stop_order({"coin": "BTC", "reduceOnly": True, "orderType": "Take Profit Market"}) is False
    assert protect._is_stop_order({"coin": "BTC", "reduceOnly": True, "tpsl": "tp"}) is False


def test_stop_loss_is_a_stop():
    for sl in ({"coin": "BTC", "reduceOnly": True, "orderType": "Stop Market"},
               {"coin": "ETH", "isTrigger": True, "orderType": "Stop Limit"},
               {"coin": "SOL", "reduceOnly": True}):        # bare reduce-only still counts (conservative)
        assert protect._is_stop_order(sl) is True


def test_take_profit_only_position_is_not_reported_protected():
    """DSL-tracked, but the ONLY resting order is a take-profit (no real stop). Must read STOP-NOT-ON-VENUE,
    never PROTECTED — a TP is not downside protection."""
    stops = set()   # a TP would previously have leaked into stop_assets; _is_stop_order now drops it
    verdicts = dict(protect.reconcile({"BTC"}, {"BTC"}, stops))
    assert verdicts["BTC"] == "STOP-NOT-ON-VENUE"


# ── reconcile baseline: the chain is the source of truth ──

def test_reconcile_protected_naked_and_stale():
    v = dict(protect.reconcile({"BTC", "ETH"}, {"BTC"}, {"BTC"}))
    assert v["BTC"] == "PROTECTED"          # tracked + stop resting on venue
    assert v["ETH"] == "NAKED"              # open on-chain, engine not tracking it
    v2 = dict(protect.reconcile({"BTC"}, {"BTC", "SOL"}, {"BTC"}))
    assert v2["SOL"] == "CLOSED-OR-STALE"   # engine tracks a position the chain says is gone


# ── xyz:-prefix guardrail: an invalid coin string is caught BEFORE funding ──
# The M404726 incident: bare `NVDA` (not `xyz:NVDA`) → every position rejects → strategy FAILED after
# funding → $ parked. These lock in the two catches that prevent it.

def test_strict_unknown_symbols_flags_bare_equity_with_suggestion():
    live = {"xyz:NVDA", "BTC"}
    bad = {b["symbol"]: b["suggestion"] for b in vu.unknown_symbols(["NVDA", "BTC", "xyz:NVDA", "FAKE"], live)}
    assert bad == {"NVDA": "xyz:NVDA", "FAKE": None}   # BTC + xyz:NVDA are live → not flagged


def test_lenient_unknown_tickers_still_accepts_bare_equity():
    # contrast: the INPUT checker stays lenient (assumes scan prefixes), but the STRICT one flags the
    # literal string — that split is the whole point.
    assert vu.unknown_tickers(["NVDA"], {"xyz:NVDA"}) == []
    assert vu.unknown_symbols(["NVDA"], {"xyz:NVDA"})[0]["suggestion"] == "xyz:NVDA"


def test_unknown_asset_violations_helper():
    live = {"xyz:NVDA", "BTC", "xyz:AVGO"}
    v = _smoke._unknown_asset_violations([{"asset": "NVDA"}, {"asset": "BTC"}, {"asset": "xyz:AVGO"}], live)
    assert len(v) == 1 and "NVDA" in v[0] and "xyz:NVDA" in v[0]
    assert _smoke._unknown_asset_violations([{"asset": "NVDA"}], None) == []   # no live set → can't check


def test_smoke_blocks_scan_emitting_unprefixed_xyz_asset():
    scan_src = ("def scan(inputs, ctx):\n"
                "    return [{'asset': 'NVDA', 'direction': 'LONG', 'marginPct': 18, 'leverage': 4,\n"
                "             'data': {'score': 8}}]\n")
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, scan_src)
        v = _smoke.smoke(rp, es, live_assets={"xyz:NVDA", "BTC"})
    assert v["status"] == "bad-shape" and v["status"] in _smoke.BLOCK
    assert any("REJECT" in x and "xyz:NVDA" in x for x in v["violations"])


def test_smoke_passes_scan_emitting_prefixed_xyz_asset():
    scan_src = ("def scan(inputs, ctx):\n"
                "    return [{'asset': 'xyz:NVDA', 'direction': 'LONG', 'marginPct': 18, 'leverage': 4,\n"
                "             'data': {'score': 8}}]\n")
    with tempfile.TemporaryDirectory() as d:
        rp, es = _pkg_with_scan(d, scan_src)
        v = _smoke.smoke(rp, es, live_assets={"xyz:NVDA", "BTC"})
    assert v["status"] == "clean" and v["status"] not in _smoke.BLOCK


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
