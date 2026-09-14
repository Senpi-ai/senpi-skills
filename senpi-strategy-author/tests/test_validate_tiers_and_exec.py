"""leverageTiers are read by COLUMN (not flattened); the executor-facing `[exec]` checks on OPEN_POSITION."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import validate_strategy as vs  # noqa: E402


def test_row_shaped_tiers_read_the_leverage_column_not_the_score_or_margin():
    condor = {"strategy": {"default_leverage": 10},
              "scanners": [{"type": "external_scanner", "inputs": {"marginPct": 50,
                            "leverageTiers": [[15, 10, 80], [13, 10, 70], [11, 10, 50]]}}]}
    assert vs.max_leverage(condor) == 10                      # not 80 (a margin) and not 15 (a score)
    assert vs.slot_plan(condor) == (1, 80)                    # margin: the tiers' 80 beats the input's 50
    grizzly = {"strategy": {"default_leverage": 7},
               "scanners": [{"type": "external_scanner", "inputs": {"leverageTiers": [[14, 10], [12, 10]]}}]}
    assert vs.max_leverage(grizzly) == 10                     # not 14


def test_map_shaped_tiers_are_all_leverages():
    starling = {"scanners": [{"type": "external_scanner", "inputs": {"leverageTiers": {"apex": 5, "good": 4, "base": 3}}}]}
    assert vs.max_leverage(starling) == 5
    assert vs.tier_margins({"apex": 5}) == []


def _package(tmp_path, order_type="FEE_OPTIMIZED_LIMIT", taker_fallback=True, options=True):
    pkg = Path(tmp_path) / "mk"
    (pkg / "scanners").mkdir(parents=True)
    (pkg / "strategy.yaml").write_text('schema_version: 1\nid: mk\nversion: "1.0.0"\ncatalog:\n  name: "Mk"\n')
    params = "      order_type: %s\n" % order_type
    if options:
        params += ("      fee_optimized_limit_options:\n        ensure_execution_as_taker: %s\n"
                   "        execution_timeout_seconds: 60\n" % ("true" if taker_fallback else "false"))
    (pkg / "runtime.yaml").write_text(
        "name: mk-main\ngroup: mk\nversion: 1.0.0\ndescription: mk\n"
        "strategy:\n  wallet: \"${MK_WALLET}\"\n  slots: 1\n  margin_pct: 10\n  default_leverage: 2\n"
        "scanners:\n  - name: s\n    type: external_scanner\n    path: ./scanners\n    entrypoint: scan.py\n    interval_seconds: 600\n    inputs: {}\n"
        "actions:\n  - name: open\n    action_type: OPEN_POSITION\n    decision_mode: rule\n    scanners: [s]\n"
        "    params:\n" + params +
        "exit:\n  engine: dsl\n  dsl_preset:\n    phase1:\n      enabled: false\n      max_loss_pct: 20\n"
        "risk:\n  guard_rails:\n    drawdown_halt_pct: 20\n")
    (pkg / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    return []\n")
    (pkg / "scanners" / "scoring.py").write_text("")
    return pkg


def _exec_errors(pkg):
    return [e for e in vs.validate(pkg) if "[exec]" in e]


def _exec_warns(pkg):
    return [w for w in vs.warnings(pkg) if "[exec]" in w]


def test_a_maker_only_entry_is_an_error(tmp_path):
    errs = _exec_errors(_package(tmp_path, taker_fallback=False))
    assert len(errs) == 1 and "ensure_execution_as_taker: false" in errs[0] and "'open'" in errs[0]


def test_a_maker_window_with_taker_fallback_is_not(tmp_path):
    pkg = _package(tmp_path, taker_fallback=True)
    assert not _exec_errors(pkg) and not _exec_warns(pkg)


def test_an_order_type_the_runtime_does_not_know_is_an_error_naming_the_fallback(tmp_path):
    errs = _exec_errors(_package(tmp_path, order_type="fee_optimized", options=False))
    assert len(errs) == 1 and "falls back to MARKET" in errs[0] and "'fee_optimized'" in errs[0]


def test_a_limit_open_is_an_error_the_runtime_cannot_price(tmp_path):
    errs = _exec_errors(_package(tmp_path, order_type="LIMIT", options=False))
    assert len(errs) == 1 and "limitPrice" in errs[0]


def test_fee_options_under_market_warn_and_do_not_error(tmp_path):
    pkg = _package(tmp_path, order_type="MARKET", taker_fallback=False)   # maker-only flag is inert here
    assert not _exec_errors(pkg)
    warns = _exec_warns(pkg)
    assert len(warns) == 1 and "never" in warns[0] and "MARKET" in warns[0]


def test_a_market_open_without_options_is_clean(tmp_path):
    pkg = _package(tmp_path, order_type="MARKET", options=False)
    assert not _exec_errors(pkg) and not _exec_warns(pkg)
