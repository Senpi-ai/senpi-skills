#!/usr/bin/env python3
"""A close is a scanner plus a CLOSE_POSITION action, never a direction. Two silent failures the lint
now refuses: an external scanner no action lists (its signals are dropped with no log line) and a
`direction` literal that names a close (the open action refuses it as `invalid_direction`). A user
built exactly this — force-flat signals with `direction: CLOSE` that nothing consumed — then ripped the
feature out by hand.

    python3 -m pytest senpi-strategy-author/tests/test_validate_close_wiring.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_RUNTIME = """\
name: wired-main
group: wired
description: >
  A fixture recipe whose only variable is how its scanners are wired to actions, so the wiring
  rule is the one thing under test and nothing else colours the result.
strategy:
  wallet: "${WIRED_WALLET}"
  slots: 2
  margin_pct: 20
exit:
  dsl_preset: let_winners_run
scanners:
  - name: entry_signals
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 900
    inputs: {}
    signal_data_schema:
      score: {type: number}
  - name: close_signals
    type: external_scanner
    path: ./scanners
    entrypoint: close.py
    interval_seconds: 900
    inputs: {}
    signal_data_schema:
      score: {type: number}
actions:
  - name: entry
    action_type: OPEN_POSITION
    decision_mode: rule
    scanners: [entry_signals]
    params: {order_type: FEE_OPTIMIZED_LIMIT, fee_optimized_limit_options: {ensure_execution_as_taker: true}}
    context: [{type: signal, scanner: entry_signals}]
__CLOSE_ACTION__"""

_CLOSE_ACTION = """\
  - name: close
    action_type: CLOSE_POSITION
    decision_mode: rule
    scanners: [close_signals]
    params: {order_type: MARKET}
    context: [{type: signal, scanner: close_signals}]
"""


def _package(tmp_path, close_action=_CLOSE_ACTION, close_src=None):
    d = tmp_path / "wired"
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(
        'id: wired\nversion: "1.0.0"\ninstances:\n  - name: main\n    runtime: main/runtime.yaml\n'
        "    wallet_env: WIRED_WALLET\n    funding_share: 1.0\n")
    (d / "main" / "runtime.yaml").write_text(_RUNTIME.replace("__CLOSE_ACTION__", close_action))
    (d / "main" / "scanners" / "scoring.py").write_text("def score(x):\n    return 1.0\n")
    (d / "main" / "scanners" / "scan.py").write_text(
        "import scoring\n\ndef scan(inputs, ctx):\n"
        "    return [{'asset': 'BTC', 'direction': 'LONG', 'data': {'score': scoring.score(1)}}]\n")
    (d / "main" / "scanners" / "close.py").write_text(close_src or (
        "def scan(inputs, ctx):\n"
        "    return [{'asset': 'BTC', 'direction': 'LONG', 'data': {'score': 1.0}}]\n"))
    return d


def _hits(errs, needle):
    return [e for e in errs if needle in e]


def test_barracuda_shaped_wiring_passes(tmp_path):
    errs = vs.validate(_package(tmp_path))
    assert not _hits(errs, "feeds no action") and not _hits(errs, "emits `direction:"), errs


def test_scanner_no_action_lists_is_refused(tmp_path):
    errs = vs.validate(_package(tmp_path, close_action=""))
    hit = _hits(errs, "feeds no action")
    assert len(hit) == 1 and "'close_signals'" in hit[0] and "CLOSE_POSITION" in hit[0], errs


def test_direction_close_literal_is_refused(tmp_path):
    src = ("def scan(inputs, ctx):\n"
           "    return [{'asset': 'BTC', 'direction': 'CLOSE', 'data': {'score': 1.0}}]\n")
    errs = vs.validate(_package(tmp_path, close_src=src))
    hit = _hits(errs, "emits `direction:")
    assert len(hit) == 1 and "'CLOSE'" in hit[0] and "invalid_direction" in hit[0], errs


def test_helpers_are_pure():
    assert vs.unconsumed_scanners({"scanners": [{"name": "a", "type": "external_scanner"}], "actions": []}) == ["a"]
    assert vs.unconsumed_scanners({"scanners": [{"name": "a", "type": "external_scanner"}],
                                   "actions": [{"context": [{"type": "signal", "scanner": "a"}]}]}) == []
    assert vs.direction_literal_offenders('"direction": "FLAT"; "direction": "SHORT"; \'direction\': \'exit\'') == ["FLAT", "exit"]
    assert vs.direction_literal_offenders('{"direction": "NEUTRAL"}  # an analysis dict, not a signal') == []
