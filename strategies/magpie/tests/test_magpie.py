"""Magpie 1.2.0: the IPOP signature is the venue's leverage cap, the wallet is read before an emit,
both books read closed candles, the stop is outside the move the book requires, and no clock closes
a trade.

From the 12 Oct 2026 template audit. Both books are sized three slots at 25% margin and 3x (the
pre-listing book was 3 x 12% at 3x, the graduation book 3 x 15% at 5x). The graduation stop was 9% ROE
at 5x = 1.8% of price — smaller than the 3% hourly move the book requires before it enters — and is
now 12% ROE = 4.0% of price; the pre-listing stop moves from 7% ROE (2.3% of price) to 10% (3.3%).
Neither book read the wallet, so a held name was emitted again every tick its 12-minute dedup allowed
(the pre-listing tick is 15 minutes, so every tick); both trend/momentum reads included the bar in
progress. The IPOP signature required |funding| <= 1e-7 AND a cap <= 5: on the live xyz board
(114 names, read 2026-10-12) one name sat at cap <= 5, one at |funding| <= 1e-7 and none at both, so
neither book could ever see an IPOP; the signature is now the cap plus a volume floor, the band
opt-in. These tests pin the shipped numbers, the signature with and without the band, the
cap-based conversion, the held-name skip, the closed-candle reads, and the first-tick contract.

Run: python3 -B -m pytest strategies/magpie/tests -q
"""
import inspect
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")


def _load(instance):
    """Import one book's scanner modules with its own scanners/ dir first on sys.path. Both books
    ship modules called `scan` and `scoring`, so each load evicts the other's names first; the
    returned module objects keep their own bindings."""
    d = os.path.join(PKG, instance, "scanners")
    for mod in ("scoring", "scan"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, d)
    try:
        import scoring
        import scan
        return scan, scoring
    finally:
        sys.path.remove(d)
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)


PRE, PRE_SCORING = _load("pre_listing")
GRAD, GRAD_SCORING = _load("graduation")
MANIFEST = yaml.safe_load(open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8"))
RUNTIME = {i: yaml.safe_load(open(os.path.join(PKG, i, "runtime.yaml"), encoding="utf-8"))
           for i in ("pre_listing", "graduation")}
SIGNALS = {i: next(s for s in RUNTIME[i]["scanners"] if s["type"] == "external_scanner") for i in RUNTIME}
INPUTS = {i: dict(SIGNALS[i]["inputs"]) for i in RUNTIME}
NOW = time.time()
IPOP = "xyz:SPCX"


# ── payloads, in the live shapes (market_list_instruments / market_get_asset_data / the 4h board /
#    strategy_get_clearinghouse_state, read 2026-10-12) ─────────────────────────────────────────

def _inst(name, cap, funding, vol):
    return {"name": name, "sz_decimals": 2, "max_leverage": cap, "is_delisted": False, "only_isolated": True,
            "margin_modes": ["ISOLATED"], "margin_mode": None,
            "context": {"coin": name, "funding": f"{funding}", "dayNtlVlm": f"{vol}", "markPx": "100.0", "prevDayPx": "99.0"}}


# funding values are the live ones: a pre-IPO perp funds like any xyz equity (3.17e-7 was the cap-5
# name's reading on 2026-10-12), which is why the funding band is off
IPOP_ROW = _inst(IPOP, 5, 3.17e-7, 1_850_000)            # the signature: 5x cap, over both volume floors
QUIET_IPOP_ROW = _inst("xyz:OURA", 5, 3.17e-7, 22_417)   # the live cap-5 name: under the $25k signature floor
BANDED_ROW = _inst(IPOP, 5, 1e-8, 1_850_000)             # the same name with funding inside the v2 band
GRADUATED_ROW = _inst(IPOP, 20, 1.25e-5, 41_000_000)     # the same name after conversion: cap lifted
DEAD_ROW = _inst("xyz:GBP", 50, 0.0, 335_034)            # the live zero-funding name: cap 50, never an IPOP
STANDARD_ROW = _inst("xyz:TSLA", 20, 1.25e-5, 41_000_000)


def _bars(lows, highs, hours=1, live=None):
    """Structured candles for the pre-listing trend read: closed bars oldest first (the k-th from the
    end opened k bars ago), plus — when `live` = (low, high) is given — the bar in progress, opened
    ten minutes ago."""
    rows, n = [], len(lows)
    for i, (lo, hi) in enumerate(zip(lows, highs)):
        t = int((NOW - (n - i) * 3600 * hours) * 1000)
        rows.append({"t": t, "T": t + 3600000 * hours - 1, "s": IPOP, "i": f"{hours}h", "o": f"{lo + 0.5}",
                     "h": f"{hi}", "l": f"{lo}", "c": f"{hi - 0.5}", "v": "1200", "n": 40})
    if live is not None:
        lo, hi = live
        t = int((NOW - 600) * 1000)
        rows.append({"t": t, "T": t + 3600000 * hours - 1, "s": IPOP, "i": f"{hours}h", "o": f"{hi}",
                     "h": f"{hi}", "l": f"{lo}", "c": f"{lo}", "v": "9", "n": 2})
    return rows


BULL = _bars([100, 101, 102, 103, 104, 105], [102, 103, 104, 105, 106, 107])   # 5/5 higher lows -> BULLISH (hourly)
BULL4 = _bars([100, 101, 102, 103, 104, 105], [102, 103, 104, 105, 106, 107], hours=4)   # the same shape as closed 4h bars
FLAT4 = _bars([100, 99, 100, 99, 100, 99], [102, 103, 102, 103, 102, 103], hours=4)      # 2/5 either way -> NEUTRAL
# exactly 3/5 higher lows: BULLISH at the 0.6 threshold, so one partial bar can tip the read
EDGE_LOWS, EDGE_HIGHS = [100, 101, 99, 102, 98, 103], [102, 103, 101, 104, 100, 105]


def _hourly(closes, live_close=None):
    """Closed hourly candles oldest first plus, when `live_close` is given, the bar in progress —
    opened ten minutes ago, carrying that close."""
    rows, n = [], len(closes)
    for i, c in enumerate(closes):
        t = int((NOW - (n - i) * 3600) * 1000)
        rows.append({"t": t, "T": t + 3600000 - 1, "s": IPOP, "i": "1h", "o": f"{c}", "h": f"{c + 0.5}",
                     "l": f"{c - 0.5}", "c": f"{c}", "v": "1", "n": 10})
    if live_close is not None:
        t = int((NOW - 600) * 1000)
        rows.append({"t": t, "T": t + 3600000 - 1, "s": IPOP, "i": "1h", "o": f"{closes[-1]}",
                     "h": f"{max(closes[-1], live_close)}", "l": f"{min(closes[-1], live_close)}",
                     "c": f"{live_close}", "v": "1", "n": 3})
    return rows


RISING = [100] * 6 + [100, 102, 104, 106, 108, 110]     # +10% over the last six closed bars: strong momentum, score 5


def _row(token, dex, direction, pct, traders):
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 5 if dex == "xyz" else 40,
            "pct_of_top_traders_gain": pct, "trader_count": traders, "is_dominant_direction": direction == "long",
            "contribution_pct_change_15m": 0.4, "contribution_pct_change_1h": 0.9, "contribution_pct_change_4h": 1.6,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5, "token_price_change_pct_4h": 1.3,
            "day_notional_volume": 1.0e8}


# what the 4h board normally looks like for a new listing: rows for the majors, none for it
OTHER_ROWS = (_row("BTC", "", "long", 38.2, 41), _row("BTC", "", "short", 0.9, 3), _row("ETH", "", "long", 17.5, 26))


def _clearinghouse(main=(), xyz=()):
    """Both sections of the one wallet; `main` / `xyz` list the coins held in each, as the venue spells them."""
    def section(coins):
        return {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                                  "totalRawUsd": "1000.0"}, "withdrawable": "1000.0",
                "assetPositions": [{"type": "oneWay", "position": {"coin": c, "szi": "1.5", "entryPx": "100.0",
                                                                    "leverage": {"type": "isolated", "value": 3}}}
                                   for c in coins]}
    return {"success": True, "data": {"main": section(main), "xyz": section(xyz)}}


class _MCP:
    """A fake desk answering the four reads the books make. `fail` names tools that raise;
    `candles` is the market_get_asset_data `candles` dict; `clearinghouse` the full response."""

    def __init__(self, instruments, markets=OTHER_ROWS, candles=None, clearinghouse=None, fail=()):
        self.instruments, self.markets, self.candles = instruments, list(markets), candles or {}
        self.clearinghouse = clearinghouse if clearinghouse is not None else _clearinghouse()
        self.fail, self.calls = set(fail), []

    def call_tool(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": self.instruments, "count": len(self.instruments)}}
        if tool == "market_get_asset_data":
            return {"success": True, "data": {"asset": args["asset"], "candles": self.candles}}
        if tool == "leaderboard_get_markets":
            return {"success": True, "data": {"markets": self.markets, "source_trader_count": 100,
                                              "window": "4h", "timestamp": int(NOW)}}
        if tool == "strategy_get_clearinghouse_state":
            return self.clearinghouse
        raise AssertionError(f"unexpected tool call {tool!r}")


class _State:
    def __init__(self, last=None):
        self._log = [last] if last else []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


class _Ctx:
    def __init__(self, mcp, instance, state=None):
        self.senpi_mcp, self.wallet, self.dry_run = mcp, "0x" + "a" * 40, False
        self.state = state if state is not None else _State()
        self.scanner_name, self.interval_seconds = SIGNALS[instance]["name"], SIGNALS[instance]["interval_seconds"]


def _tools(mcp):
    return [t for t, _ in mcp.calls]


def _pre_tick(mcp, state=None, **overrides):
    ctx = _Ctx(mcp, "pre_listing", state)
    return PRE.scan(dict(INPUTS["pre_listing"], **overrides), ctx), ctx.state.last() or {}


def _grad_tick(mcp, state=None, **overrides):
    ctx = _Ctx(mcp, "graduation", state)
    return GRAD.scan(dict(INPUTS["graduation"], **overrides), ctx), ctx.state.last() or {}


def _classes(rec):
    return {n: v["class"] for n, v in rec["class_state"].items()}


# a prior tick that saw the IPOP, in the pre-1.2.0 record shape (a bare class string): a state file
# written before the upgrade must still flip on the lift
SEEDED = {"class_state": {IPOP: "IPOP"}, "conversions": {}, "recent": {}}


# ── the config, as shipped ───────────────────────────────────────────────────────────────

def test_both_stops_are_outside_the_entry_move_and_no_clock_closes_a_trade():
    for inst, stop_roe, stop_px in (("pre_listing", 10.0, 3.3), ("graduation", 12.0, 4.0)):
        pre = RUNTIME[inst]["exit"]["dsl_preset"]
        assert pre["phase1"]["enabled"] is False
        assert pre["phase1"]["max_loss_pct"] == stop_roe, inst
        assert round(stop_roe / INPUTS[inst]["maxLeverage"], 1) == stop_px, inst     # ROE / leverage = price distance
        for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
            assert pre[cut]["enabled"] is False, (inst, cut)
        tiers = pre["phase2"]["tiers"]
        assert tiers[0]["lock_hw_pct"] >= 40
        assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
        assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)
    # the graduation stop (in price) now sits outside the move the book requires before it enters
    grad = RUNTIME["graduation"]["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / INPUTS["graduation"]["maxLeverage"]
    assert grad > INPUTS["graduation"]["minMomentumPct"]            # 4.0% of price vs a 3% move; 1.8% was inside it


def test_sizing_leverage_rails_signature_inputs_and_version_agree_everywhere():
    for inst in ("pre_listing", "graduation"):
        strat, inp, rails, sig = RUNTIME[inst]["strategy"], INPUTS[inst], RUNTIME[inst]["risk"]["guard_rails"], SIGNALS[inst]
        assert inp["marginPct"] == strat["margin_pct"] == 25 and strat["slots"] == 3, inst
        assert strat["slots"] * inp["marginPct"] <= 100
        assert inp["maxLeverage"] == strat["default_leverage"] == MANIFEST["catalog"]["leverage_max"] == 3, inst
        # the IPOP SIGNATURE (what makes a name a pre-IPO perp), not the trading leverage; the band is off
        assert inp["ipopMaxLeverageCap"] == 5 and inp["ipopMinDayVolumeUsd"] == 25000
        assert inp["ipopFundingBandEnabled"] is False and inp["ipopFundingBandAbs"] == 1e-7
        assert "ipopFundingMaxAbs" not in inp
        assert rails["max_entries_per_day"] > strat["slots"]
        assert rails["drawdown_reset_on_day_rollover"] is False
        assert sig["timeout_seconds"] < sig["interval_seconds"]
    assert INPUTS["pre_listing"]["ipopMinDailyVolUsd"] >= INPUTS["pre_listing"]["ipopMinDayVolumeUsd"]   # the book's floor sits above the signature's
    assert MANIFEST["version"] == "1.2.0"
    assert MANIFEST["catalog"]["max_slots"] == sum(RUNTIME[i]["strategy"]["slots"] for i in RUNTIME)


def test_the_card_says_what_the_code_does():
    thesis = MANIFEST["catalog"]["thesis"]
    assert "Both books take direction from 4h structure" not in thesis     # the graduation book never reads a 4h candle
    assert "closed hourly candles" in thesis and "4h structure" in thesis
    card = " ".join(str(MANIFEST["catalog"][k]) for k in ("tagline", "belief_plain", "thesis"))
    assert "5x" not in card.replace("at 5x or under", "").replace("at a 5x cap", "") and "25% margin and 3x" in card
    assert "tiny funding" not in card and "funding jumps" not in card    # the band is not the signature any more
    assert "leverage cap" in MANIFEST["catalog"]["belief_plain"] and "optional funding band" in MANIFEST["catalog"]["belief_plain"]
    for inst in RUNTIME:
        assert "funding jumps" not in RUNTIME[inst]["description"]


# ── the IPOP signature: the cap, a volume floor, the band only on request ─────────────────

def test_the_signature_is_the_leverage_cap_over_a_volume_floor_and_the_band_is_opt_in():
    """The live board on 2026-10-12: the one cap-5 name traded $22k/day at funding 3.17e-7, the one
    |funding| <= 1e-7 name sat at cap 50 — under the old band-AND-cap predicate nothing was an IPOP."""
    for scoring in (PRE_SCORING, GRAD_SCORING):
        off = dict(INPUTS["pre_listing"])
        assert scoring.is_ipop(5, 1_850_000, 3.17e-7, off)              # cap 5 over the floor: an IPOP, funding irrelevant
        assert not scoring.is_ipop(5, 22_417, 3.17e-7, off)             # cap 5 under the $25k floor: a market that has not traded
        assert not scoring.is_ipop(50, 335_034, 0.0, off)               # cap 50 at zero funding: never an IPOP
        assert not scoring.is_ipop(20, 41_000_000, 1.25e-5, off)
        assert scoring.is_ipop("5", "1850000", "3.17e-7", off)          # the fields arrive as strings
        assert not scoring.is_ipop(None, 1_850_000, 3.17e-7, off) and not scoring.is_ipop(5, "lots", 0, off)
        on = dict(off, ipopFundingBandEnabled=True)
        assert not scoring.is_ipop(5, 1_850_000, 3.17e-7, on)           # the band, when on, is an extra AND
        assert scoring.is_ipop(5, 1_850_000, 1e-8, on)
        assert not scoring.is_ipop(5, 1_850_000, None, on)
    # the two books share the predicate VERBATIM
    for fn in ("ipop_capped", "is_ipop"):
        assert inspect.getsource(getattr(PRE_SCORING, fn)) == inspect.getsource(getattr(GRAD_SCORING, fn)), fn


def test_pre_listing_universe_takes_the_signature_and_the_books_own_floor():
    board = [QUIET_IPOP_ROW, IPOP_ROW, DEAD_ROW, STANDARD_ROW]
    out, rec = _pre_tick(_MCP(board, candles={"1h": BULL, "4h": BULL4}))
    assert rec["result"]["ipop_universe"] == [IPOP] and [s["asset"] for s in out] == [IPOP]
    out, rec = _pre_tick(_MCP(board, candles={"1h": BULL, "4h": BULL4}), ipopFundingBandEnabled=True)
    assert rec["result"]["ipop_universe"] == [] and out == []            # with the band on, live funding fails it
    out, rec = _pre_tick(_MCP([BANDED_ROW], candles={"1h": BULL, "4h": BULL4}), ipopFundingBandEnabled=True)
    assert rec["result"]["ipop_universe"] == [IPOP]
    out, rec = _pre_tick(_MCP([_inst(IPOP, 5, 3.17e-7, 60_000)], candles={"1h": BULL, "4h": BULL4}))
    assert rec["result"]["ipop_universe"] == []                          # over the signature floor, under the book's $100k floor


def test_graduation_conversion_is_the_cap_lifting_over_the_floor():
    cfg = dict(INPUTS["graduation"])
    prior = {"class": "IPOP", "max_leverage": 5, "funding": 3.17e-7}
    assert GRAD_SCORING.detect_conversion(prior, 20, 41_000_000, cfg)                 # the cap lifted on a live market
    assert not GRAD_SCORING.detect_conversion(prior, 20, 10_000, cfg)                 # lifted, but not trading: wait
    assert not GRAD_SCORING.detect_conversion(prior, 5, 41_000_000, cfg)              # a volume spike alone is not a conversion
    quiet = {"class": "STANDARD", "max_leverage": 5, "funding": 3.17e-7}              # under the floor the tick before (the live cap-5 name)
    assert GRAD_SCORING.detect_conversion(quiet, 20, 41_000_000, cfg)                 # the prior test is the CAP, so it still flips
    assert not GRAD_SCORING.detect_conversion({"class": "STANDARD", "max_leverage": 20, "funding": 1e-5}, 20, 41_000_000, cfg)
    assert GRAD_SCORING.detect_conversion("IPOP", 20, 41_000_000, cfg)                # a pre-1.2.0 record: its class
    assert not GRAD_SCORING.detect_conversion("STANDARD", 20, 41_000_000, cfg)
    assert not GRAD_SCORING.detect_conversion(None, 20, 41_000_000, cfg)              # unknown prior: never a flip
    on = dict(cfg, ipopFundingBandEnabled=True)
    assert not GRAD_SCORING.detect_conversion(prior, 20, 41_000_000, on)              # with the band on, the prior must be inside it
    assert GRAD_SCORING.detect_conversion({"class": "IPOP", "max_leverage": 5, "funding": 1e-8}, 20, 41_000_000, on)
    assert not GRAD_SCORING.detect_conversion({"class": "IPOP", "max_leverage": 5, "funding": 1e-8}, 5, 41_000_000, on)   # funding leaving the band is not a flip


# ── pre-listing: one tick against a fake desk ────────────────────────────────────────────

def test_pre_listing_emits_a_trend_only_ipop_at_the_shipped_size_and_leverage():
    mcp = _MCP([IPOP_ROW, STANDARD_ROW], candles={"1h": BULL, "4h": BULL4})
    out, rec = _pre_tick(mcp)
    (sig,) = out
    assert sig["asset"] == IPOP and sig["direction"] == "LONG"
    assert sig["marginPct"] == 25 and sig["leverage"] == 3              # 3x asked; the IPOP's 5x venue cap does not bind
    assert sig["data"]["score"] == 5 and sig["data"]["ipopFlag"] is True and sig["data"]["trend4h"] == "BULLISH"
    schema = SIGNALS["pre_listing"]["signal_data_schema"]
    assert set(sig["data"]) <= set(schema) and all(v is not None for v in sig["data"].values())
    assert "strategy_get_clearinghouse_state" in _tools(mcp)
    assert rec["result"]["emitted"] == [IPOP] and rec["result"]["ipop_universe"] == [IPOP]
    assert _pre_tick(_MCP([IPOP_ROW], candles={"1h": BULL, "4h": FLAT4}))[0] == []    # a neutral 4h structure: no direction


def test_pre_listing_trend_is_read_off_closed_bars_only():
    """The closed bars make a 3-of-5 higher-lows structure (BULLISH at the threshold). With the bar in
    progress counted, its partial low and high turn the same six-bar window into 3-of-5 lower highs:
    the old read went SHORT on both frames. The closed read is the same with or without that bar."""
    clean = {"1h": _bars(EDGE_LOWS, EDGE_HIGHS), "4h": _bars(EDGE_LOWS, EDGE_HIGHS, hours=4)}
    live = {"1h": _bars(EDGE_LOWS, EDGE_HIGHS, live=(1, 2)), "4h": _bars(EDGE_LOWS, EDGE_HIGHS, hours=4, live=(1, 2))}
    (want,) = _pre_tick(_MCP([IPOP_ROW], candles=clean))[0]
    (got,) = _pre_tick(_MCP([IPOP_ROW], candles=live))[0]
    assert want["direction"] == "LONG" and want["data"]["score"] == 5
    assert (got["direction"], got["data"]["score"], got["data"]["reasons"]) == (want["direction"], want["data"]["score"], want["data"]["reasons"])
    # pure: the helper drops the live bar and anything without a readable open, oldest first
    rows = PRE_SCORING.closed_candles(live["4h"][::-1] + [{"c": "1"}, {"t": "x"}], PRE_SCORING.FOUR_HOUR_SECONDS, NOW)
    assert [r["l"] for r in rows] == [f"{x}" for x in EDGE_LOWS]


def test_pre_listing_degrades_when_a_read_raises():
    out, rec = _pre_tick(_MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, fail=("market_get_asset_data",)))
    assert out == [] and rec["result"]["ipop_universe"] == [IPOP]         # candles down: the name is skipped, the tick survives
    out, rec = _pre_tick(_MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, fail=("market_list_instruments",)))
    assert out == [] and rec["result"]["ipop_universe"] == []             # instruments down: no universe, nothing invented


def test_pre_listing_never_re_emits_a_name_the_wallet_holds():
    """Before 1.2.0 nothing read the wallet: with a 12-minute dedup under a 15-minute tick a held
    IPOP was emitted again on every tick. The xyz section may spell the coin bare or qualified."""
    for held in ({"xyz": (IPOP,)}, {"xyz": ("SPCX",)}):
        mcp = _MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, clearinghouse=_clearinghouse(**held))
        assert _pre_tick(mcp)[0] == [], held
        assert "market_get_asset_data" not in _tools(mcp)                 # a held name costs no candle read
    mcp = _MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, clearinghouse=_clearinghouse(main=("SPCX",)))
    assert len(_pre_tick(mcp)[0]) == 1                                    # a main-DEX coin is not the xyz name


def test_pre_listing_opens_nothing_when_the_wallet_cannot_be_read(capsys):
    mcp = _MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, fail=("strategy_get_clearinghouse_state",))
    assert _pre_tick(mcp)[0] == []
    assert "HOLD" in capsys.readouterr().err
    mcp = _MCP([IPOP_ROW], candles={"1h": BULL, "4h": BULL4}, clearinghouse={"success": True, "data": {}})
    assert _pre_tick(mcp)[0] == []                                        # no main/xyz section is unreadable, never "holds nothing"
    mcp = _MCP([STANDARD_ROW], fail=("strategy_get_clearinghouse_state",))
    assert _pre_tick(mcp)[0] == [] and "strategy_get_clearinghouse_state" not in _tools(mcp)   # no candidate: no wallet read


# ── graduation: the flip, the window, the closed-candle read ─────────────────────────────

def test_graduation_first_tick_seeds_then_trades_the_flip_on_the_next_tick():
    """A conversion is a transition: at or under the cap on one tick, above it on a later one. The
    first tick has one observation, and the live board carries no field that dates a listing or a
    cap change (market_list_instruments rows: name, max_leverage, is_delisted, only_isolated,
    margin_modes, sz_decimals and a context of funding / volume / prices — read 2026-10-12), so a
    name already above the cap when the book first runs cannot be told from one that converted
    months ago. The chosen behaviour: tick one seeds the cache and emits nothing; the lift between
    tick one and tick two is stamped and traded; a name above the cap on both ticks is never stamped."""
    candles = {"1h": _hourly(RISING)}
    out, rec = _grad_tick(_MCP([IPOP_ROW, STANDARD_ROW], candles=candles))
    assert out == [] and rec["conversions"] == {}
    assert _classes(rec) == {IPOP: "IPOP", "xyz:TSLA": "STANDARD"}
    assert rec["class_state"][IPOP] == {"class": "IPOP", "max_leverage": 5, "funding": 3.17e-7}
    out, rec2 = _grad_tick(_MCP([GRADUATED_ROW, STANDARD_ROW], candles=candles), state=_State(rec))
    (sig,) = out
    assert sig["asset"] == IPOP and sig["direction"] == "LONG" and sig["marginPct"] == 25 and sig["leverage"] == 3
    assert sig["data"]["conversionEvent"] is True and sig["data"]["momentumPct"] == 10.0 and sig["data"]["score"] == 5
    schema = SIGNALS["graduation"]["signal_data_schema"]
    assert set(sig["data"]) <= set(schema) and all(v is not None for v in sig["data"].values())
    assert set(rec2["conversions"]) == {IPOP} and _classes(rec2)[IPOP] == "STANDARD"
    assert "xyz:TSLA" not in rec2["conversions"]                          # above the cap on both ticks: never a conversion


def test_graduation_a_quiet_pre_ipo_name_still_flips_when_its_cap_lifts():
    """The live cap-5 name traded under the $25k signature floor; the tick before its listing it is
    classed STANDARD for volume, and the lift must still count — the prior test is the cap."""
    candles = {"1h": _hourly(RISING)}
    out, rec = _grad_tick(_MCP([QUIET_IPOP_ROW], candles=candles))
    assert out == [] and _classes(rec) == {"xyz:OURA": "STANDARD"} and rec["class_state"]["xyz:OURA"]["max_leverage"] == 5
    out, rec = _grad_tick(_MCP([_inst("xyz:OURA", 20, 1.25e-5, 41_000_000)], candles=candles), state=_State(rec))
    assert [s["asset"] for s in out] == ["xyz:OURA"] and set(rec["conversions"]) == {"xyz:OURA"}
    # the same lift onto a market still under the floor is not a conversion yet
    out, rec = _grad_tick(_MCP([QUIET_IPOP_ROW], candles=candles))
    out, rec = _grad_tick(_MCP([_inst("xyz:OURA", 20, 1.25e-5, 10_000)], candles=candles), state=_State(rec))
    assert out == [] and rec["conversions"] == {}


def test_graduation_a_name_already_standard_on_the_first_tick_is_not_tradable():
    """The limit, pinned so it is a decision and not a surprise: a book started after the conversion
    never trades it (the docstring above says why it cannot be dated)."""
    candles = {"1h": _hourly(RISING)}
    out, rec = _grad_tick(_MCP([GRADUATED_ROW], candles=candles))
    assert out == [] and rec["conversions"] == {} and _classes(rec) == {IPOP: "STANDARD"}
    out, rec = _grad_tick(_MCP([GRADUATED_ROW], candles=candles), state=_State(rec))
    assert out == [] and rec["conversions"] == {}


def test_graduation_momentum_is_read_off_closed_candles_only():
    """Before 1.2.0 the bar in progress was the `latest` close, so the thesis changed with every tick
    inside the hour. Here the six closed bars rise 10% while the bar in progress prints half the
    price: the old read called it a 51% drop and went SHORT; the closed read goes LONG at +10%."""
    candles = {"1h": _hourly(RISING, live_close=50.0)}
    out, _ = _grad_tick(_MCP([GRADUATED_ROW], candles=candles), state=_State(dict(SEEDED)))
    (sig,) = out
    assert sig["direction"] == "LONG" and sig["data"]["momentumPct"] == 10.0
    # pure: the helper drops the live bar and anything without a readable open, oldest first
    rows = GRAD_SCORING.closed_candles(candles["1h"][::-1] + [{"c": "1"}, {"t": "x"}], GRAD_SCORING.HOUR_SECONDS, NOW)
    assert [r["c"] for r in rows] == [f"{c}" for c in RISING]
    assert GRAD_SCORING.closed_candles(None, GRAD_SCORING.HOUR_SECONDS, NOW) == []


def test_graduation_never_re_emits_a_held_name_and_holds_when_the_wallet_is_unreadable(capsys):
    candles = {"1h": _hourly(RISING)}
    mcp = _MCP([GRADUATED_ROW], candles=candles, clearinghouse=_clearinghouse(xyz=("SPCX",)))
    out, rec = _grad_tick(mcp, state=_State(dict(SEEDED)))
    assert out == [] and "market_get_asset_data" not in _tools(mcp)
    assert set(rec["conversions"]) == {IPOP}                              # the flip is still stamped; only the open is skipped
    mcp = _MCP([GRADUATED_ROW], candles=candles, fail=("strategy_get_clearinghouse_state",))
    out, rec = _grad_tick(mcp, state=_State(dict(SEEDED)))
    assert out == [] and "HOLD" in capsys.readouterr().err
    assert set(rec["conversions"]) == {IPOP} and _classes(rec)[IPOP] == "STANDARD"   # cache and window kept


def test_graduation_degrades_when_a_read_raises():
    out, rec = _grad_tick(_MCP([GRADUATED_ROW], candles={"1h": _hourly(RISING)}, fail=("market_get_asset_data",)),
                          state=_State(dict(SEEDED)))
    assert out == [] and set(rec["conversions"]) == {IPOP}                # candles down: skipped this tick, the window stays open
    out, rec = _grad_tick(_MCP([GRADUATED_ROW], fail=("market_list_instruments",)), state=_State(dict(SEEDED)))
    assert out == [] and rec["class_state"] == {IPOP: "IPOP"}             # instruments down: the prior cache is preserved as is
