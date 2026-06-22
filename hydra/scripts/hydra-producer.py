#!/usr/bin/env python3
# Senpi HYDRA Producer v2.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""HYDRA v2.0.0 Producer — single-coin conviction fund with a CROSS-ASSET hedge.

A long-term conviction book on ONE major (HYDRA_COIN), hedged by a diversified
short of OTHER assets — so the hedge cushions market risk WITHOUT betting against
the thesis. HYDRA_LEG selects which head this wallet runs:

  HYDRA_LEG=core   The thesis. Trend-confirmed LONG/SHORT on HYDRA_COIN, ridden
                   on a LOOSE (catastrophic-only) DSL — a long-term hold you don't
                   get shaken out of on normal volatility. (DSL lives in runtime.)
  HYDRA_LEG=dip    The complement. LONG-only; buys a PULLBACK within a confirmed
                   uptrend on HYDRA_COIN. Tactical add; moderate DSL.
  HYDRA_LEG=hedge  The hedge — NOW CROSS-ASSET (v2.0). Shorts a diversified BLEND
                   of OTHER assets (hedgeUniverse minus the thesis coin) — but only
                   the ones in a CONFIRMED downtrend, vol-weighted, and sized UP
                   when the thesis coin itself is breaking down. Never shorts the
                   thesis coin. Auto-scales: light when little is breaking, heavy in
                   a broad selloff. The crisis cushion.

v2.0 change vs v1.0: the hedge was a same-asset stress short on HYDRA_COIN; it is
now a diversified cross-asset short blend, and the core thesis runs on a loose
long-term-hold DSL. See SKILL.md.

Deploy a fund for one coin = three wallets (core/dip/hedge), same HYDRA_COIN.

Each head pushes signals via SenpiClient.push_signal(); the runtime owns the LLM
gate (pass-through), DSL exits, and all risk.guard_rails. NOT a copy-trader.

Environment / config resolution:
  HYDRA_LEG           — REQUIRED. "core" | "dip" | "hedge".
  HYDRA_COIN          — the thesis asset (ETH/SOL/HYPE/…); env wins, else config.coin, else ETH.
  HYDRA_WALLET        — this head's strategy wallet (or config.wallet)
  HYDRA_DECISION_MODEL— bare LLM model name; resolved into runtime.yaml
  SENPI_AUTH_TOKEN    — REQUIRED.
"""

import hashlib
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hydra_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "2.1.0"
LEG = cfg.LEG  # "core" | "dip" | "hedge"
COIN = cfg.resolve_coin()
SCANNER_NAME = f"hydra_{LEG}_signals"
SIGNAL_TYPE = {"core": "HYDRA_CORE", "dip": "HYDRA_DIP", "hedge": "HYDRA_HEDGE"}[LEG]

NORM_DIV = 9.0

_DEFAULTS = {
    "core": {
        "minScore": 5, "marginPct": 0.20, "maxLeverage": 5, "stdLeverage": 3,
        "apexScore": 7, "rsiOverbought": 80, "rsiOversold": 20,
        "requireDailyAlign": True,       # v2.1: only enter when the 1d trend confirms the 4h — filters CHOP (don't buy the range-top)
        "venueMinNotionalUsd": 10, "minNotionalPctOfEquity": 0.01, "tickSeconds": 300,
    },
    "dip": {
        "minScore": 4, "marginPct": 0.18, "maxLeverage": 4, "stdLeverage": 3,
        "dipRsiMax": 42,                 # v2.1: deeper, real RSI pullback (was 48) — not noise
        "requireDailyAlign": True,       # v2.1: only dip-buy inside a 1d+4h confirmed uptrend (no knife-catching chop)
        "venueMinNotionalUsd": 10, "minNotionalPctOfEquity": 0.01, "tickSeconds": 300,
    },
    "hedge": {
        # v2.0 cross-asset hedge: short a diversified blend of OTHER assets.
        "minScore": 4, "maxLeverage": 3, "stdLeverage": 3, "maxSlots": 5,
        "stressDropPct": 8.0,            # per-asset fast drawdown that arms a short
        "stressLookback": 6,             # 4h candles for the per-asset drawdown
        "rsiOversold": 18,               # capitulation guard per asset
        # vol-parity sizing for the hedge basket (inverse-ATR, normalized + clamped)
        "hedgeRiskPct": 0.06,            # margin a reference-vol short gets, % of equity
        "referenceVolPct": 3.0,
        "minMarginPct": 0.02,
        "maxMarginPct": 0.12,
        "hedgeMaxTotalPct": 0.45,        # cap on TOTAL hedge margin deployed, % of equity
        # thesis-stress multiplier: size the hedge UP when the THESIS coin is breaking
        "thesisStressFullPct": 10.0,     # thesis-coin drawdown at which the multiplier maxes
        "stressMultMax": 2.0,            # max sizing multiplier
        # v2.1: HYBRID hedge. False (default, ETH/SOL) = pure cross-asset, never short
        # the thesis coin. True (HYPE) = ALSO short the thesis coin when IT is the one
        # breaking down — cushions an idiosyncratic move the cross-asset blend misses.
        "hedgeIncludesThesis": False,
        # the diversified blend to short. The thesis coin is auto-excluded UNLESS
        # hedgeIncludesThesis is True (then a confirmed thesis-coin breakdown is shortable).
        "hedgeUniverse": ["BTC", "ETH", "SOL", "HYPE", "BNB", "XRP", "DOGE", "AVAX",
                          "xyz:SP500", "xyz:NASDAQ"],
        "venueMinNotionalUsd": 10, "minNotionalPctOfEquity": 0.01, "tickSeconds": 300,
    },
}[LEG]


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers
# ═══════════════════════════════════════════════════════════════

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def drawdown_pct(closes, lookback):
    window = closes[-lookback:] if len(closes) >= lookback else closes
    if not window:
        return 0.0
    peak = max(window)
    cur = window[-1]
    return (peak - cur) / peak * 100.0 if peak > 0 else 0.0


def atr_pct(candles, period=14):
    if len(candles) < 3:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, lo, pc = _high(candles[i]), _low(candles[i]), _close(candles[i - 1])
        if h <= 0 or lo <= 0 or pc <= 0:
            continue
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    if not trs:
        return None
    atr = sum(trs[-period:]) / len(trs[-period:])
    price = _close(candles[-1])
    return atr / price * 100.0 if price > 0 else None


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def fetch_asset(coin, intervals=("1h", "4h", "1d"), include_funding=True):  # v2.1: 1d for the daily-align chop filter (core/dip)
    data = cfg.mcp_call(
        "market_get_asset_data", asset=coin, candle_intervals=list(intervals),
        dex=_dex_for(coin), include_funding=include_funding, include_order_book=False,
    )
    if not data or not data.get("success", True):
        return None
    d = data.get("data", data)
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def _funding(ctx):
    try:
        return float(ctx.get("funding", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def clamp_lev(desired, max_lev):
    return max(1, min(int(desired), int(max_lev)))


# ═══════════════════════════════════════════════════════════════
# CORE / DIP — single-coin thesis scoring (v2.1: + 1d-alignment chop filter)
# ═══════════════════════════════════════════════════════════════

def score_single(md, config):
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    cd = md["candles"].get("1d", [])
    trendd, sd = trend_structure(cd) if len(cd) >= 6 else ("NEUTRAL", 0)
    require_daily = bool(config.get("requireDailyAlign", True))
    rsi = calc_rsi(closes1)
    fund = _funding(md["ctx"])

    sc, reasons, direction = 0, [], None
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))
    leverage = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))

    if LEG == "core":
        if trend4 == "NEUTRAL":
            return None
        direction = "LONG" if trend4 == "BULLISH" else "SHORT"
        # v2.1 CHOP FILTER: the 1d must confirm the 4h direction. In a range the 1d
        # is NEUTRAL -> no entry, so the core stops buying the top of a chop. (Fails
        # open only if 1d data is unavailable.)
        if require_daily and len(cd) >= 6 and trendd != ("BULLISH" if direction == "LONG" else "BEARISH"):
            return None
        sc += 3
        reasons.append(f"4h_{trend4.lower()}_{s4:.0%}_1d_{trendd.lower()}")
        if (direction == "LONG" and trend1 == "BULLISH") or (direction == "SHORT" and trend1 == "BEARISH"):
            sc += 2
            reasons.append(f"1h_confirms_{trend1.lower()}")
        elif (direction == "LONG" and trend1 == "BEARISH") or (direction == "SHORT" and trend1 == "BULLISH"):
            sc -= 1
            reasons.append("1h_against")
        ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        os_ = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if direction == "LONG" and rsi > ob:
            sc -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
        if direction == "SHORT" and rsi < os_:
            sc -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")
        if direction == "LONG" and fund < 0:
            sc += 1; reasons.append("funding_pays_long")
        if direction == "SHORT" and fund > 0:
            sc += 1; reasons.append("funding_pays_short")
        leverage = max_lev if sc >= int(config.get("apexScore", _DEFAULTS["apexScore"])) else int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))

    elif LEG == "dip":
        if trend4 != "BULLISH":
            return None
        # v2.1: only dip-buy inside a 1d+4h CONFIRMED uptrend (no knife-catching chop).
        if require_daily and len(cd) >= 6 and trendd != "BULLISH":
            return None
        dip_rsi = float(config.get("dipRsiMax", _DEFAULTS["dipRsiMax"]))
        pulled_back = (rsi <= dip_rsi)   # v2.1: require a REAL RSI pullback, not just a non-bullish 1h wiggle
        if not pulled_back:
            return None
        direction = "LONG"
        sc += 2 + (1 if s4 >= 0.6 else 0)
        reasons.append(f"4h_uptrend_{s4:.0%}")
        if rsi <= dip_rsi:
            sc += 2
            reasons.append(f"dip_rsi_{rsi:.0f}")
        if trend1 == "BEARISH":
            sc += 1
            reasons.append("1h_pullback")

    leverage = clamp_lev(leverage, max_lev)
    return {"coin": COIN, "direction": direction, "score": sc, "leverage": leverage,
            "reasons": reasons, "price": price, "rsi": rsi, "trend4h": trend4, "trend1h": trend1}


# ═══════════════════════════════════════════════════════════════
# HEDGE (v2.0) — cross-asset blend short
# ═══════════════════════════════════════════════════════════════

def thesis_stress_mult(config):
    """Size the hedge UP when the THESIS coin itself is breaking down. Returns a
    multiplier in [1.0, stressMultMax] from the thesis coin's drawdown."""
    md = fetch_asset(COIN, intervals=["4h"], include_funding=False)
    if not md:
        return 1.0, 0.0
    c4 = md["candles"].get("4h", [])
    if len(c4) < 3:
        return 1.0, 0.0
    closes4 = [_close(c) for c in c4]
    dd = drawdown_pct(closes4, int(config.get("stressLookback", _DEFAULTS["stressLookback"])))
    full = float(config.get("thesisStressFullPct", _DEFAULTS["thesisStressFullPct"]))
    mmax = float(config.get("stressMultMax", _DEFAULTS["stressMultMax"]))
    frac = min(1.0, dd / full) if full > 0 else 0.0
    return round(1.0 + frac * (mmax - 1.0), 3), round(dd, 2)


def score_hedge_one(asset, md, config):
    """Score ONE hedge-universe asset for a SHORT. Returns thesis (with vol_pct) or
    None. 4h downtrend is the hard gate; a fast drawdown / 1h breakdown arms it;
    capitulation-guarded."""
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c4) < 6:
        return None
    closes4 = [_close(c) for c in c4]
    closes1 = [_close(c) for c in c1] if c1 else closes4
    price = closes4[-1]
    if price <= 0:
        return None
    trend4, s4 = trend_structure(c4)
    if trend4 != "BEARISH":
        return None                                   # only short what's actually breaking down
    trend1, _ = trend_structure(c1) if len(c1) >= 6 else ("NEUTRAL", 0)
    rsi = calc_rsi(closes1)
    dd = drawdown_pct(closes4, int(config.get("stressLookback", _DEFAULTS["stressLookback"])))
    stress = float(config.get("stressDropPct", _DEFAULTS["stressDropPct"]))
    armed = (dd >= stress) or (trend1 == "BEARISH")
    if not armed:
        return None
    if rsi < float(config.get("rsiOversold", _DEFAULTS["rsiOversold"])):
        return None                                   # capitulation guard
    vol = atr_pct(c4) or float(config.get("referenceVolPct", _DEFAULTS["referenceVolPct"]))

    sc, reasons = 3, [f"4h_downtrend_{s4:.0%}"]
    if dd >= stress:
        sc += 2; reasons.append(f"drawdown_{dd:.1f}%")
    if trend1 == "BEARISH":
        sc += 1; reasons.append("1h_breaking_down")
    return {"coin": asset, "direction": "SHORT", "score": sc, "reasons": reasons,
            "price": price, "rsi": round(rsi, 1), "trend4h": trend4, "vol_pct": round(vol, 3)}


def vol_parity_margin(account_value, vol_pct, config, mult=1.0):
    base = float(config.get("hedgeRiskPct", _DEFAULTS["hedgeRiskPct"]))
    ref = float(config.get("referenceVolPct", _DEFAULTS["referenceVolPct"]))
    lo = float(config.get("minMarginPct", _DEFAULTS["minMarginPct"]))
    hi = float(config.get("maxMarginPct", _DEFAULTS["maxMarginPct"]))
    if vol_pct <= 0:
        vol_pct = ref
    pct = base * (ref / vol_pct) * mult
    pct = max(lo, min(hi, pct))
    return round(account_value * pct, 2)


# ═══════════════════════════════════════════════════════════════
# Emit
# ═══════════════════════════════════════════════════════════════

def push_signal(th, margin_usd, leverage, held_assets, extra=None):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if th["coin"].upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": th["score"], "leverage": leverage, "marginUsd": margin_usd,
        "direction": th["direction"], "reasons": th["reasons"], "heldAssets": held_assets,
        "trend4h": th.get("trend4h"), "rsi": round(th.get("rsi", 0), 1),
    }
    if extra:
        data_block.update(extra)
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS, scanner=SCANNER_NAME, asset=th["coin"],
            direction=th["direction"], score=min(th["score"] / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE, data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {th['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {th['coin']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def single_main(config, account_value, positions, held_assets, run_start):
    """core / dip — one position on the thesis coin."""
    if COIN.upper() in {h.upper() for h in held_assets}:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "note": "position open — holding",
                    "held_assets": held_assets, "_hydra_producer_version": VERSION})
        return
    if cfg.was_recently_signaled(COIN):
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "note": "recently signaled — debounce",
                    "_hydra_producer_version": VERSION})
        return
    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)),
                       float(config.get("venueMinNotionalUsd", 10)))
    md = fetch_asset(COIN)
    if not md:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "signals_pushed": 0,
                    "note": "WAITING — no market data", "_hydra_producer_version": VERSION})
        return
    th = score_single(md, config)
    note = {"core": "no confirmed trend", "dip": "no pullback in an uptrend"}[LEG]
    if not th or th["score"] < min_score:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "signals_pushed": 0,
                    "min_score": min_score, "note": f"WAITING — {note}",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_hydra_producer_version": VERSION})
        return
    margin_usd = round(account_value * margin_pct, 2)
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    notional = margin_usd * th["leverage"]
    pushed, emitted = 0, []
    if margin_usd > 0 and notional >= min_notional and margin_usd * 1.1 <= free_margin:
        if push_signal(th, margin_usd, th["leverage"], held_assets):
            pushed = 1
            cfg.record_signal(COIN)
            emitted.append({"coin": th["coin"], "direction": th["direction"], "score": th["score"],
                            "leverage": th["leverage"], "margin_usd": margin_usd, "reasons": th["reasons"][:6]})
    cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "signals_pushed": pushed, "emitted": emitted,
                "direction": th["direction"], "score": th["score"], "account_value": round(account_value, 2),
                "elapsed_sec": round(time.time() - run_start, 2), "_hydra_producer_version": VERSION})


def hedge_main(config, account_value, positions, held_assets, run_start):
    """v2.1 cross-asset hedge — short a diversified blend of OTHER assets in a
    confirmed downtrend, vol-weighted, sized up when the thesis is breaking. If
    hedgeIncludesThesis is True (HYPE), the thesis coin is ALSO shortable when IT
    is the one breaking down (hybrid — cushions an idiosyncratic move)."""
    held_set = {h.upper() for h in held_assets}
    min_score = config.get("minScore", _DEFAULTS["minScore"])
    max_slots = int(config.get("maxSlots", _DEFAULTS["maxSlots"]))
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))
    std_lev = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))
    allow_thesis = bool(config.get("hedgeIncludesThesis", _DEFAULTS.get("hedgeIncludesThesis", False)))
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)),
                       float(config.get("venueMinNotionalUsd", 10)))
    total_cap = account_value * float(config.get("hedgeMaxTotalPct", _DEFAULTS["hedgeMaxTotalPct"]))

    universe = [a for a in config.get("hedgeUniverse", _DEFAULTS["hedgeUniverse"])
                if (allow_thesis or a.upper() != COIN.upper()) and a.upper() not in held_set]
    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "note": "hedge slots full",
                    "held_assets": held_assets, "_hydra_producer_version": VERSION})
        return

    mult, thesis_dd = thesis_stress_mult(config)

    candidates = []
    scanned = 0
    for asset in universe:
        if cfg.was_recently_signaled(asset):
            continue
        md = fetch_asset(asset, intervals=["1h", "4h"], include_funding=False)
        if not md:
            continue
        scanned += 1
        th = score_hedge_one(asset, md, config)
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "scanned": scanned,
                    "signals_pushed": 0, "thesis_drawdown_pct": thesis_dd, "stress_mult": mult,
                    "note": "WAITING — nothing in the hedge blend is breaking down",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_hydra_producer_version": VERSION})
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    deployed = sum(p.get("margin", 0) for p in positions)

    pushed, emitted = 0, []
    slots_left = open_slots
    for th in candidates:
        if slots_left <= 0:
            break
        margin_usd = vol_parity_margin(account_value, th["vol_pct"], config, mult=mult)
        leverage = clamp_lev(std_lev, max_lev)
        notional = margin_usd * leverage
        if notional < min_notional:
            continue
        if deployed + margin_usd > total_cap:          # cap TOTAL hedge margin
            continue
        if margin_usd * 1.1 > free_margin:
            continue
        # hedgeFor drives the runtime's "never short the thesis coin" guard. When the
        # hybrid is on (allow_thesis), leave it empty so a deliberate thesis-coin short
        # isn't blocked; otherwise set it to COIN to keep the guard active.
        if push_signal(th, margin_usd, leverage, held_assets,
                       extra={"hedgeFor": ("" if allow_thesis else COIN),
                              "stressMult": mult, "volPct": th["vol_pct"]}):
            pushed += 1
            slots_left -= 1
            free_margin -= margin_usd
            deployed += margin_usd
            cfg.record_signal(th["coin"])
            emitted.append({"coin": th["coin"], "direction": "SHORT", "score": th["score"],
                            "leverage": leverage, "margin_usd": margin_usd, "vol_pct": th["vol_pct"],
                            "reasons": th["reasons"][:6]})

    cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "scanned": scanned,
                "candidates": len(candidates), "signals_pushed": pushed, "emitted": emitted,
                "thesis_drawdown_pct": thesis_dd, "stress_mult": mult,
                "hedge_deployed_usd": round(deployed, 2), "hedge_cap_usd": round(total_cap, 2),
                "account_value": round(account_value, 2),
                "elapsed_sec": round(time.time() - run_start, 2), "_hydra_producer_version": VERSION})


def main():
    run_start = time.time()
    config = cfg.load_config()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG, "coin": COIN,
                    "_hydra_producer_version": VERSION})
        return
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "note": "no account value",
                    "_hydra_producer_version": VERSION})
        return
    if LEG == "hedge":
        hedge_main(config, account_value, positions, held_assets, run_start)
    else:
        single_main(config, account_value, positions, held_assets, run_start)


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (f"{COIN}:{LEG}:" + (STRATEGY_ADDRESS or "")).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    _kwargs = {"fn": main, "interval_seconds": _tick,
               "name": f"hydra-{COIN}-{LEG}-producer-{_lock_id}",
               "tick_timeout": min(180, max(30, _tick - 10))}
    try:
        _params = inspect.signature(producer_daemon).parameters
    except (TypeError, ValueError):
        _params = {}
    if "wallet" in _params:
        _kwargs["wallet"] = STRATEGY_ADDRESS
    if "scanner" in _params:
        _kwargs["scanner"] = SCANNER_NAME
    producer_daemon(**_kwargs)
