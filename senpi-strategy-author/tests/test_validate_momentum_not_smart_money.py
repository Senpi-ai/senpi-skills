#!/usr/bin/env python3
"""A 4h-board read is momentum, not smart money — the validator says so. `leaderboard_get_markets` ranks
markets by who was winning over the last four hours, so a direction taken from `pct_of_top_traders_gain`
/ `longPct` is a momentum read of the board, and a package that never reads the proven cohort
(`discovery_get_trader_state`) may not describe itself as smart money. And a PnL sign is not a side: the
position's side is the sign of `szi`.

    python3 -m pytest senpi-strategy-author/tests/test_validate_momentum_not_smart_money.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_STRATEGIES = Path(HERE).resolve().parents[1] / "strategies"

_RUNTIME = """\
name: board-main
group: board
description: >
  A fixture recipe whose only variables are what its scanner reads and how the package describes
  itself, so the board-versus-cohort naming rule is the one thing under test.
strategy:
  wallet: "${BOARD_WALLET}"
  slots: 1
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
actions:
  - name: entry
    action_type: OPEN_POSITION
    decision_mode: rule
    scanners: [entry_signals]
    params: {order_type: FEE_OPTIMIZED_LIMIT, fee_optimized_limit_options: {ensure_execution_as_taker: true}}
    context: [{type: signal, scanner: entry_signals}]
"""

# The board read: the side comes from the 4h leaderboard row, weighted by its gain share.
_BOARD_SCAN = (
    "import scoring\n\n"
    "def scan(inputs, ctx):\n"
    "    m = ctx.senpi_mcp.call_tool('leaderboard_get_markets', {})[0]\n"
    "    direction = str(m.get('direction', '')).upper()\n"
    "    pct = float(m.get('pct_of_top_traders_gain', 0) or 0)\n"
    "    return [{'asset': m['token'], 'direction': direction, 'data': {'score': scoring.score(pct)}}]\n")

# The cohort read: the side comes from what a proven trader holds (the sign of `szi`); the board's gain
# share is only a universe filter here, which is what the trader_state call exempts.
_COHORT_SCAN = (
    "import scoring\n\n"
    "def scan(inputs, ctx):\n"
    "    board = ctx.senpi_mcp.call_tool('leaderboard_get_markets', {})\n"
    "    hot = {m['token'] for m in board if float(m.get('pct_of_top_traders_gain', 0) or 0) > 5}\n"
    "    st = ctx.senpi_mcp.call_tool('discovery_get_trader_state', {'traderAddress': inputs['leader']})\n"
    "    out = []\n"
    "    for p in st['positions']:\n"
    "        direction = 'LONG' if float(p['szi']) > 0 else 'SHORT'\n"
    "        out.append({'asset': p['coin'], 'direction': direction, 'data': {'score': scoring.score(p['coin'] in hot)}})\n"
    "    return out\n")

_SCORING = "def score(x):\n    return float(x)\n"


def _package(tmp_path, tagline, scan_src=_BOARD_SCAN, scoring_src=_SCORING):
    d = tmp_path / "board"
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(
        'id: board\nversion: "1.0.0"\n'
        f'catalog:\n  name: "Board"\n  tagline: "{tagline}"\n  tags: [momentum]\n'
        "instances:\n  - name: main\n    runtime: main/runtime.yaml\n"
        "    wallet_env: BOARD_WALLET\n    funding_share: 1.0\n")
    (d / "main" / "runtime.yaml").write_text(_RUNTIME)
    (d / "main" / "scanners" / "scan.py").write_text(scan_src)
    (d / "main" / "scanners" / "scoring.py").write_text(scoring_src)
    return d


def _hits(errs, needle):
    return [e for e in errs if needle in e]


def test_board_read_described_as_smart_money_is_refused(tmp_path):
    errs = vs.validate(_package(tmp_path, "Follows the smart money on the 4h board"))
    hit = _hits(errs, "momentum read of the 4h board described as smart money")
    assert len(hit) == 1 and hit[0].startswith("main/scanners/scan.py:") and "'smart money'" in hit[0] \
        and "strategy.yaml catalog.tagline" in hit[0] and "discovery_get_trader_state" in hit[0], errs


def test_pnl_sign_direction_is_refused(tmp_path):
    scoring = (_SCORING + "\ndef side(pos):\n"
               "    return pos.get('direction', 'LONG' if float(pos.get('unrealizedPnl', 0)) >= 0 else 'SHORT')\n")
    errs = vs.validate(_package(tmp_path, "4h leader momentum", scoring_src=scoring))
    hit = _hits(errs, "direction from a PnL sign")
    assert len(hit) == 1 and hit[0].startswith("scoring.py:") and "`unrealizedPnl`" in hit[0] \
        and "szi sign" in hit[0], errs


def test_board_read_named_momentum_passes(tmp_path):
    errs = vs.validate(_package(tmp_path, "4h leader momentum: rides the side the board is winning on"))
    assert not _hits(errs, "4h board") and not _hits(errs, "PnL sign"), errs


def test_cohort_read_described_as_smart_money_passes(tmp_path):
    errs = vs.validate(_package(tmp_path, "Smart-money positioning, read from the proven cohort", scan_src=_COHORT_SCAN))
    assert not _hits(errs, "4h board") and not _hits(errs, "PnL sign"), errs


# A cohort read that lives in a helper outside scanners/ earns the phrase as much as one inside it.
_COHORT_HELPER = (
    "def leader_side(ctx, leader):\n"
    "    st = ctx.senpi_mcp.call_tool('discovery_get_trader_state', {'traderAddress': leader})\n"
    "    return {p['coin']: ('LONG' if float(p['szi']) > 0 else 'SHORT') for p in st['positions']}\n")


def test_cohort_read_in_a_helper_outside_scanners_passes(tmp_path):
    d = _package(tmp_path, "Smart-money positioning, read from the proven cohort")
    (d / "main" / "cohort.py").write_text(_COHORT_HELPER)
    errs = vs.validate(d)
    assert not _hits(errs, "4h board"), errs


def test_a_test_mock_of_the_cohort_read_does_not_buy_the_exemption(tmp_path):
    d = _package(tmp_path, "Follows the smart money on the 4h board")
    (d / "tests").mkdir()
    (d / "tests" / "test_board.py").write_text(
        "def test_mock(monkeypatch):\n    calls = ['discovery_get_trader_state']\n    assert calls\n")
    errs = vs.validate(d)
    assert len(_hits(errs, "momentum read of the 4h board described as smart money")) == 1, errs


def test_helpers_are_pure():
    assert vs.pnl_sign_directions('"LONG" if delta_pnl >= 0 else "SHORT"') == ["delta_pnl"]
    assert vs.pnl_sign_directions("'SHORT' if pos['closed_pnl'] < 0 else 'LONG'") == ["closed_pnl"]
    assert vs.pnl_sign_directions('"LONG" if float(p.get("unrealizedPnl", 0)) > 0.0 else "SHORT"') == ["unrealizedPnl"]
    assert vs.pnl_sign_directions('"LONG" if float(p["szi"]) > 0 else "SHORT"') == []      # the position's side
    assert vs.pnl_sign_directions('"LONG" if pnl_share >= 0.5 else "SHORT"') == []       # a threshold, not a sign
    assert vs.pnl_sign_directions('"LONG" if pnl_rank > 3 else "SHORT"') == []           # a rank, not a sign


def test_catalog_has_no_board_read_sold_as_smart_money():
    bad = {}
    for man in sorted(_STRATEGIES.glob("*/strategy.yaml")):
        hits = [e for e in vs.validate(man.parent) if "momentum read of the 4h board" in e or "PnL sign" in e]
        if hits:
            bad[man.parent.name] = hits
    assert not bad, "\n".join(f"{k}: {v[0]}" for k, v in sorted(bad.items()))
