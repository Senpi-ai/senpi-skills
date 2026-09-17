"""Turnover fixes from the 30-day live read (Aug 18 – Sep 17 2026). Each pin carries what the old value
cost, so an edit that brings it back has to argue with the number.

- Spider swing traded 10x with a 12% margin stop, 1.2% from entry on a leg meant to hold for days: 57% of
  its 10x exits were stop-outs, and trips under 4h lost before fees and paid 60% of the fees. At 5x the
  same 12% stop sits 2.4% away, and the ladder triggers halve so the tiers arm at the same price moves.
- Spider scalp re-signalled an asset every 3 minutes: 38% of its re-opens were the same asset within 15
  minutes of exiting. One signal per asset per hour.
- Raptor hit its 6-entry cap on 18 of 21 sampled days, and days with 12+ fills paid 94% of its fees.
  3 entries a day.
- Cuckoo's $2,000 per-position floor discarded every vote (the strategies it copies hold $6–$883
  positions), so it saw no candidates. $100, with a $50 minimum budget so its 15% x 4x size still clears
  the venue minimum.

Run: python3 -m pytest strategies/tests/test_template_fee_config.py -q
"""
import json
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _yaml(*parts):
    return yaml.safe_load(ROOT.joinpath(*parts).read_text(encoding="utf-8"))


def _inputs(rt, scanner):
    return next(s for s in rt["scanners"] if s["name"] == scanner)["inputs"]


def _catalog(sid):
    return next(s for s in json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))["skills"] if s["id"] == sid)


def test_spider_swing_trades_5x_with_its_stop_and_ladder_at_the_same_price_moves():
    rt = _yaml("spider", "swing", "runtime.yaml")
    assert rt["strategy"]["default_leverage"] == 5
    assert _inputs(rt, "spider_swing_signals")["maxLeverage"] == 5
    dsl = rt["exit"]["dsl_preset"]
    assert dsl["phase1"]["max_loss_pct"] == 12.0                     # 12% of margin at 5x = 2.4% from entry
    assert [t["trigger_pct"] for t in dsl["phase2"]["tiers"]] == [15, 30, 50]
    assert _catalog("spider")["leverage_max"] == 5


def test_spider_scalp_signals_an_asset_at_most_once_an_hour():
    assert _inputs(_yaml("spider", "scalp", "runtime.yaml"), "spider_scalp_signals")["recentSignalTtlSeconds"] == 3600


def test_raptor_opens_at_most_three_a_day():
    rails = _yaml("raptor", "main", "runtime.yaml")["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] == 3
    assert rails["bypass_max_entries_per_day_on_profit"] is False


def test_cuckoo_counts_the_positions_its_strategies_actually_hold():
    assert _inputs(_yaml("cuckoo", "main", "runtime.yaml"), "cuckoo_main_signals")["minNotionalUsd"] == 100
    scan = (ROOT / "cuckoo" / "main" / "scanners" / "scan.py").read_text(encoding="utf-8")
    assert re.search(r"^_DEFAULT_MIN_NOTIONAL_USD = 100\b", scan, re.M)
    assert _catalog("cuckoo")["min_budget"] >= 50
