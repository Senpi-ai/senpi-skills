"""Puffer 1.1.0: four slots at 20% and 4x, a 3%-of-price stop, the fee gate at measured live rates.

From the 2026-10-12 template audit: in a 208-day replay on four majors, as 1.0 was written, Puffer
traded about seven times per coin per month with 28% winners, and 88% of its live closes were the
exchange stop — an 8% ROE stop 1.6% of price away at 5x, inside the release candle's own range. The
fee gate priced a round trip at 0.06% (HL's base fee alone, 4.5/1.5 bps) against 8 bps taker / 6 bps
maker per side measured on live fills (0.14-0.16%; HL + the builder fee, which is per user). These tests pin the shipped numbers, the fee
arithmetic, and scan() ticks against a fake desk. The wider stop's trip rate is not measured yet, so
nothing here (or on the card) promises one.

Run: python3 -B -m pytest strategies/puffer/tests -q
"""
import math
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SCANNER = next(s for s in RUNTIME["scanners"] if s["name"] == "puffer_scanner")
INPUTS = dict(SCANNER["inputs"])
PRESET = RUNTIME["exit"]["dsl_preset"]
CARD = MANIFEST["catalog"]
NOW = time.time()


# ── a tape in the feed's shape (o/h/l/c/v strings, t/T epoch ms, hourly, oldest first) ──────

def _feed(rows, now=NOW):
    out = []
    for k, (h, l, c, v) in enumerate(rows):
        t = int((now - (len(rows) - k) * 3600) * 1000)
        out.append({"t": t, "T": t + 3600000, "s": "X", "i": "1h", "o": f"{c}", "h": f"{h}", "l": f"{l}",
                    "c": f"{c}", "v": f"{v}", "n": 1})
    return out


def _rows(scale=1.0):
    """60 wide bars (the regime the coil is measured against) then 30 calm bars (the coil). `scale`
    shrinks every deviation from 100 — the squeeze ratio is unchanged, the ATR in percent is not."""
    wide = [(100 + 6 * scale, 100 - 6 * scale, 100 + 4.0 * scale * math.sin(i / 2), 1000.0) for i in range(60)]
    calm = [(100 + 0.3 * scale, 100 - 0.3 * scale, 100 + 0.08 * scale * math.sin(i), 1000.0) for i in range(30)]
    return wide + calm


def _coiled(scale=1.0):
    return _feed(_rows(scale))


def _released(direction="LONG", scale=1.0, vol=4000.0):
    h, l, c = (102.5, 100.2, 102.2) if direction == "LONG" else (99.8, 97.5, 97.8)
    bar = (100 + (h - 100) * scale, 100 + (l - 100) * scale, 100 + (c - 100) * scale, vol)
    return _feed(_rows(scale) + [bar])


# ── the config, as shipped ───────────────────────────────────────────────────────────────

def test_the_stop_is_twelve_roe_three_percent_of_price_and_there_is_no_clock():
    assert PRESET["phase1"]["enabled"] is False                       # trailing off; max_loss_pct IS the stop
    assert PRESET["phase1"]["max_loss_pct"] == 12.0                   # ROE
    assert PRESET["phase1"]["max_loss_pct"] / INPUTS["maxLeverage"] == 3.0      # 3.0% of price at 4x (1.0: 1.6% at 5x)
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert PRESET[cut]["enabled"] is False, cut
    tiers = PRESET["phase2"]["tiers"]
    assert tiers[0] == {"trigger_pct": 20, "lock_hw_pct": 40}         # first lock 40% of high-water (preset: 25)
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)
    assert [t["lock_hw_pct"] for t in tiers] == sorted(t["lock_hw_pct"] for t in tiers)   # a rung never unlocks


def test_four_slots_at_twenty_percent_and_four_x_everywhere():
    assert RUNTIME["strategy"]["slots"] == INPUTS["maxSlots"] == CARD["max_slots"] == 4
    assert RUNTIME["strategy"]["margin_pct"] == INPUTS["maxMarginPct"] == 20
    assert set(INPUTS["marginPctTiers"].values()) == {20}
    assert RUNTIME["strategy"]["slots"] * RUNTIME["strategy"]["margin_pct"] <= 100       # every slot can fund
    assert RUNTIME["strategy"]["default_leverage"] == INPUTS["maxLeverage"] == CARD["leverage_max"] == 4
    assert set(INPUTS["leverageTiers"].values()) == {4}
    for band in ("apex", "good", "base"):
        assert scoring.sizing_for(band, INPUTS) == (4, 20.0), band   # the band ranks; it no longer sizes
    assert scoring.sizing_for("apex", INPUTS, venue_max=3) == (3, 20.0)
    assert scoring.sizing_for("apex", INPUTS, venue_max="3.0") == (3, 20.0)   # the field arrives as a string on some rows
    # the fallbacks agree with the file: a stripped input cannot run 1.0's 3-5x / 8-22% sizing
    assert scoring.sizing_for("base", {}) == (4, 20.0)


def test_the_fee_gate_is_priced_at_the_measured_rates():
    assert (INPUTS["takerFeeBps"], INPUTS["makerFeeBps"], INPUTS["feeCoverMultiple"]) == (8, 6, 2.5)
    assert abs(scoring.roundtrip_fee_pct(INPUTS) - 0.14) < 1e-9                       # maker in, taker out
    assert abs(scoring.roundtrip_fee_pct(INPUTS, maker_entry=False) - 0.16) < 1e-9    # taker both ways
    assert abs(scoring.roundtrip_fee_pct({}) - 0.14) < 1e-9       # 1.0's fallback was 0.06: the base schedule
    ok, detail = scoring.clears_fees(_released(), INPUTS)
    assert ok and abs(detail["required_pct"] - 0.35) < 1e-9 and detail["expected_capture_pct"] >= 0.35
    # a tape offering 0.6 x ATR between 0.15% and 0.35% of price was tradeable at 1.0's pricing and is
    # priced out at the measured rates — the audit's finding, as arithmetic
    thin = _feed([(100.2, 99.8, 100.0, 1000.0)] * 20)                                  # ATR 0.4% -> capture 0.24%
    assert scoring.clears_fees(thin, INPUTS)[0] is False
    assert scoring.clears_fees(thin, dict(INPUTS, takerFeeBps=4.5, makerFeeBps=1.5))[0] is True


def test_rails_cadence_card_and_version():
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > RUNTIME["strategy"]["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False and rails["bypass_max_entries_per_day_on_profit"] is False
    assert SCANNER["timeout_seconds"] < SCANNER["interval_seconds"]
    assert MANIFEST["version"] == "1.1.0"
    text = " ".join(str(CARD[f]) for f in ("tagline", "belief_plain", "thesis"))
    assert "3%" in text and "4x" in text and "20%" in text
    assert "5x" not in text and "widest" not in text                  # 1.0's leverage and an unmeasured superlative
    assert "28% winners" in text and "88%" in text                    # the replay facts, quoted as measured


# ── the engine at the shipped inputs ─────────────────────────────────────────────────────

def test_a_release_is_traded_only_when_the_tape_pays_the_measured_fees():
    th = scoring.build_thesis("BTC", _released(), INPUTS)
    assert th and th["direction"] == "LONG" and th["squeeze_ratio"] < INPUTS["maxSqueezeRatio"]
    assert abs(th["fees"]["required_pct"] - 0.35) < 1e-9
    assert scoring.build_thesis("BTC", _released("SHORT"), INPUTS)["direction"] == "SHORT"
    assert scoring.build_thesis("BTC", _coiled(), INPUTS) is None       # still inside the coil
    # the same coil and release at 40% of the range: just as coiled, but 0.6 x ATR is under 0.35%
    quiet = _released(scale=0.4)
    assert scoring.squeeze_ratio(quiet[:-1], INPUTS) < INPUTS["maxSqueezeRatio"]
    assert scoring.build_thesis("BTC", quiet, INPUTS) is None
    assert scoring.build_thesis("BTC", quiet, dict(INPUTS, takerFeeBps=4.5, makerFeeBps=1.5)) is not None  # 1.0 took it


# ── scan(): ticks against a fake desk ────────────────────────────────────────────────────

class _State:
    def __init__(self, last=None):
        self._log = [last] if last else []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


def _inst(name, max_lev, vol):
    return {"name": name, "max_leverage": max_lev, "is_delisted": False, "context": {"dayNtlVlm": str(vol)}}


class _MCP:
    def __init__(self, instruments, held=(), fail=(), candles=None):
        self.instruments, self.held, self.fail, self.calls = instruments, held, set(fail), []
        self.candles = candles or {}

    def call_tool(self, tool, args):
        self.calls.append((tool, args.get("asset")))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": [{"position": {"coin": c, "szi": "1"}} for c in self.held]}}}
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": self.instruments, "count": len(self.instruments)}}
        if tool == "market_get_asset_data":
            if args["asset"] in self.fail:
                raise RuntimeError("asset data down")
            return {"success": True, "data": {"candles": {"1h": self.candles.get(args["asset"], _released())}}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xpuffer", state, False
        self.scanner_name, self.interval_seconds = "puffer_scanner", SCANNER["interval_seconds"]


def _reads(mcp, tool="market_get_asset_data"):
    return [a for t, a in mcp.calls if t == tool]


def test_a_tick_sizes_every_emit_at_twenty_percent_and_four_x_clamped_to_the_venue():
    mcp = _MCP([_inst("BTC", 20, 9e8), _inst("HYPE", 3, 5e8), _inst("DUST", 5, 1e5)])
    state = _State()
    out = scan.scan(dict(INPUTS), _Ctx(mcp, state))
    by = {s["asset"]: s for s in out}
    assert set(by) == {"BTC", "HYPE"}, by                                 # DUST is under the $25M floor
    assert by["BTC"]["leverage"] == 4 and by["HYPE"]["leverage"] == 3     # 4x asked, the venue caps HYPE at 3x
    assert all(s["marginPct"] == 20 for s in out)
    schema = SCANNER["signal_data_schema"]
    for s in out:
        assert set(s["data"]) <= set(schema) and all(v is not None for v in s["data"].values())
        assert s["data"]["leverage"] == s["leverage"] and s["data"]["direction"] == s["direction"] == "LONG"
        assert s["data"]["squeezeRatio"] < INPUTS["maxSqueezeRatio"] and abs(s["data"]["requiredPct"] - 0.35) < 1e-9
        assert s["data"]["band"] in ("apex", "good", "base")
    assert sorted(_reads(mcp)) == ["BTC", "HYPE"]                         # one candle read per name over the floor
    rec = state.last()
    assert set(rec["recent"]) == {"BTC", "HYPE"} and rec["result"]["opened"] == 2 and rec["result"]["held"] == 0


def test_free_slots_cap_the_emits_and_a_held_name_costs_no_read():
    insts = [_inst(n, 10, v) for n, v in (("BTC", 9e8), ("ETH", 8e8), ("SOL", 7e8), ("HYPE", 5e8), ("XRP", 4e8))]
    mcp = _MCP(insts, held=("BTC", "ETH", "SOL"))
    out = scan.scan(dict(INPUTS), _Ctx(mcp, _State()))
    assert [s["asset"] for s in out] == ["HYPE"], out                     # 4 slots, 3 held: one open, the most liquid free name
    assert not {"BTC", "ETH", "SOL"} & set(_reads(mcp))
    full = _MCP(insts, held=("BTC", "ETH", "SOL", "HYPE"))
    assert scan.scan(dict(INPUTS), _Ctx(full, _State())) == []
    assert _reads(full) == [] and _reads(full, "market_list_instruments") == []   # a full book reads nothing but the clearinghouse


def test_a_name_signalled_within_the_ttl_is_not_re_emitted():
    mcp = _MCP([_inst("HYPE", 10, 5e8)])
    state = _State()
    assert [s["asset"] for s in scan.scan(dict(INPUTS), _Ctx(mcp, state))] == ["HYPE"]
    assert scan.scan(dict(INPUTS), _Ctx(mcp, _State(state.last()))) == []               # 6h debounce
    aged = dict(state.last(), recent={"HYPE": NOW - INPUTS["recentSignalTtlSeconds"] - 1})
    assert [s["asset"] for s in scan.scan(dict(INPUTS), _Ctx(mcp, _State(aged)))] == ["HYPE"]


def test_an_unreadable_clearinghouse_or_instrument_list_holds_the_tick(capsys):
    mcp = _MCP([_inst("HYPE", 10, 5e8)], fail=("strategy_get_clearinghouse_state",))
    assert scan.scan(dict(INPUTS), _Ctx(mcp, _State())) == []
    assert _reads(mcp, "market_list_instruments") == [] and _reads(mcp) == []       # the tick stops at the first unreadable
    assert "clearinghouse unreadable" in capsys.readouterr().err
    mcp = _MCP([_inst("HYPE", 10, 5e8)], fail=("market_list_instruments",))
    state = _State()
    assert scan.scan(dict(INPUTS), _Ctx(mcp, state)) == []
    assert _reads(mcp) == [] and state.last()["result"]["opened"] == 0
    assert "instruments unreadable" in capsys.readouterr().err


def test_an_unreadable_candle_read_skips_that_name_and_the_tick_survives(capsys):
    mcp = _MCP([_inst("BTC", 20, 9e8), _inst("HYPE", 3, 5e8)], fail=("BTC",))
    out = scan.scan(dict(INPUTS), _Ctx(mcp, _State()))
    assert [s["asset"] for s in out] == ["HYPE"]
    assert "market_get_asset_data(BTC) read failed" in capsys.readouterr().err


def test_a_market_still_inside_its_coil_is_left_alone(capsys):
    mcp = _MCP([_inst("BTC", 20, 9e8)], candles={"BTC": _coiled()})
    state = _State()
    assert scan.scan(dict(INPUTS), _Ctx(mcp, state)) == []
    assert state.last()["result"]["opened"] == 0 and "no opens" in capsys.readouterr().err
