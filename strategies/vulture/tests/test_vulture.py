"""Vulture 1.3.0: the four things the live audit changed, pinned.

Sixteen users ran Vulture over 60 days to a net loss, with the exchange stop as the exit on 74% of
closes. The thesis is unchanged; what changed is the stop (10% ROE at 7x was 1.43% of price, inside
the universe's measured 1.38% p75 adverse bounce), the leverage (5x, clamped to the venue's own cap
per name — 12 of 27 names are capped at 3x or 5x), the clock cuts (off), the cadence (5 minutes),
and a daily-volume floor. One malformed board row no longer ends the tick.

Run: python3 -B -m pytest strategies/vulture/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "vulture_main_signals")
INPUTS = dict(SIGNALS["inputs"], quietStartUtc=0, quietEndUtc=0)   # quiet hours off so the clock cannot fail a test


def _row(token, pct=20.0, traders=40, p4h=5.0, p1h=0.5, c15m=1.5, c1h=0.5, c4h=2.5, direction="LONG"):
    """One leaderboard_get_markets row in the documented shape; the defaults clear every gate and score >= 10."""
    return {"token": token, "dex": "", "direction": direction, "pct_of_top_traders_gain": pct,
            "trader_count": traders, "token_price_change_pct_4h": p4h, "token_price_change_pct_1h": p1h,
            "token_price_change_pct_15m": 0.4, "contribution_pct_change_15m": c15m,
            "contribution_pct_change_1h": c1h, "contribution_pct_change_4h": c4h}


def _inst(name, max_lev, vol):
    return {"name": name, "max_leverage": max_lev, "is_delisted": False, "context": {"dayNtlVlm": str(vol)}}


# ── the config, as shipped ───────────────────────────────────────────────────────────────

def test_no_clock_cut_and_the_stop_is_outside_the_measured_bounce():
    pre = RUNTIME["exit"]["dsl_preset"]
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    assert pre["phase1"]["enabled"] is False
    stop_px = pre["phase1"]["max_loss_pct"] / INPUTS["maxLeverage"]
    assert stop_px >= 3.0                                  # 15% ROE at 5x = 3.0% of price; p75 bounce 1.38%
    tiers = pre["phase2"]["tiers"]
    assert tiers[0]["lock_hw_pct"] == 40 and tiers[0]["trigger_pct"] == 15
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_leverage_is_five_everywhere_and_the_catalog_agrees():
    assert all(t["leverage"] == 5 for t in INPUTS["leverageTiers"])
    assert all(t["leverage"] == 5 for t in scan._DEFAULT_TIERS)
    assert INPUTS["maxLeverage"] == MANIFEST["catalog"]["leverage_max"] == RUNTIME["strategy"]["default_leverage"] == 5


def test_cadence_margin_and_gates():
    assert SIGNALS["interval_seconds"] == 300 and SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 40
    assert RUNTIME["strategy"]["slots"] * INPUTS["marginPct"] <= 100
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False and rails["drawdown_halt_pct"] == 25
    assert INPUTS["minDayNtlVlmUsd"] == 1_000_000


# ── the venue cap and the volume floor ───────────────────────────────────────────────────

def test_the_venue_cap_wins_over_the_tier_and_the_package_cap():
    assert scoring.clamp_leverage(5, 5, 3) == 3
    assert scoring.clamp_leverage(5, 5, 20) == 5
    assert scoring.clamp_leverage(7, 5, None) == 5                  # unreadable venue max: the package cap
    assert scoring.clamp_leverage(5, 5, "3.0") == 3                 # the field arrives as a string on some rows
    assert scoring.clamp_leverage(0.4, 5, 3) == 1                   # never below 1


def test_a_board_row_with_a_bad_trader_count_scores_instead_of_raising():
    assert scoring.score_market(_row("HEMI", traders="40.0"), None, {}, INPUTS["whitelist"], INPUTS["leverageTiers"], INPUTS) is not None
    assert scoring.score_market(_row("HEMI", traders=None), None, {}, INPUTS["whitelist"], INPUTS["leverageTiers"], INPUTS) is None   # 0 traders: the gate, not a crash


# ── scan(): one tick against a fake desk ─────────────────────────────────────────────────

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


class _MCP:
    def __init__(self, markets, instruments, held=(), fail=()):
        self.markets, self.instruments, self.held, self.fail, self.calls = markets, instruments, held, set(fail), []

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "leaderboard_get_markets":
            return {"data": {"markets": self.markets}}
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": self.instruments, "count": len(self.instruments)}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": [{"position": {"coin": c, "szi": "1"}} for c in self.held]}}}
        if tool == "market_get_funding_regime":
            return {"data": {"regime": None}}
        if tool == "market_get_funding_history":
            return {"data": {"persistence_hours": 8}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xvulture", state, False
        self.scanner_name, self.interval_seconds = "vulture_main_signals", 300


def test_a_tick_clamps_to_the_venue_cap_skips_thin_names_and_survives_a_bad_row():
    markets = [_row("HEMI"), _row("LDO"), {"token": "WLD", "dex": "", "direction": "LONG", "pct_of_top_traders_gain": "lots"},
               _row("BTC"), _row("TAO", direction="SHORT", p4h=-6.0, p1h=-0.5)]
    instruments = [_inst("HEMI", 3, 5_000_000), _inst("LDO", 5, 150_000), _inst("WLD", 5, 9_000_000), _inst("TAO", 5, 30_000_000)]
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(markets, instruments), _State()))
    by = {s["asset"]: s for s in out}
    assert set(by) == {"HEMI", "TAO"}                                 # LDO under the floor; WLD malformed; BTC banned
    assert by["HEMI"]["leverage"] == 3 and by["HEMI"]["data"]["venueMaxLeverage"] == 3   # 5x asked, 3x is the cap
    assert by["TAO"]["leverage"] == 5 and by["TAO"]["direction"] == "SHORT"
    assert by["HEMI"]["marginPct"] == 40 and by["HEMI"]["data"]["dayVolumeUsd"] == 5_000_000
    schema = SIGNALS["signal_data_schema"]
    for s in out:
        assert set(s["data"]) <= set(schema) and all(v is not None for v in s["data"].values())


def test_an_instruments_outage_degrades_to_the_package_cap_not_silence():
    out = scan.scan(dict(INPUTS), _Ctx(_MCP([_row("HEMI")], [], fail=("market_list_instruments",)), _State()))
    assert [s["asset"] for s in out] == ["HEMI"] and out[0]["leverage"] == 5


def test_a_held_name_is_not_re_emitted_and_a_board_outage_holds():
    assert scan.scan(dict(INPUTS), _Ctx(_MCP([_row("HEMI")], [_inst("HEMI", 5, 5_000_000)], held=("HEMI",)), _State())) == []
    assert scan.scan(dict(INPUTS), _Ctx(_MCP([_row("HEMI")], [_inst("HEMI", 5, 5_000_000)], fail=("leaderboard_get_markets",)), _State())) == []
