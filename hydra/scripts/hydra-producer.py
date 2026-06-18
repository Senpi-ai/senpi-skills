#!/usr/bin/env python3
# Senpi HYDRA Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""HYDRA v1.0.0 Producer — single-coin portfolio fund, three heads, one script.

A complete book on ONE major (HYDRA_COIN): a directional thesis bet + a
complementary dip-buyer + a stress-gated short hedge, each on its own wallet.
HYDRA_LEG selects which head this wallet runs:

  HYDRA_LEG=core   The thesis bet. Trend-momentum, conviction-tiered: LONG when
                   4h structure is bullish, SHORT when it's bearish. Sits out a
                   neutral/no-trend tape. The directional spine.
  HYDRA_LEG=dip    The complement. LONG-only; buys a PULLBACK *within a confirmed
                   4h uptrend* (1h dipped / RSI pulled back) — presses the thesis
                   on the dips the breakout-core misses. Stands down whenever the
                   4h isn't bullish, so it never knife-catches against the hedge.
  HYDRA_LEG=hedge  The hedge. SHORT-only; fires only when a confirmed 4h DOWNTREND
                   and a fast-drawdown signal agree. Idle (tiny bleed) in uptrends;
                   cushions the long heads during the flip.

The heads are gated to different regimes, so across the fund's three wallets they
never hold opposing positions at once: uptrend -> core long + dip adding, hedge
idle; downtrend -> core short + hedge short, dip idle. The fund is NET-LONG the
coin, pressed on dips and cushioned on breaks. One position per head (per wallet).

Deploy a fund for one coin = three wallets (core/dip/hedge), same HYDRA_COIN. Run
ETH + SOL + HYPE = nine wallets, one codebase.

Each head pushes signals via SenpiClient.push_signal(); the runtime owns the LLM
gate (pass-through), DSL exits, and all risk.guard_rails. NOT a copy-trader.

Environment / config resolution:
  HYDRA_LEG           — REQUIRED. "core" | "dip" | "hedge".
  HYDRA_COIN          — the asset (ETH/SOL/HYPE/…); env wins, else config.coin, else ETH.
  HYDRA_WALLET        — this head's strategy wallet (or config.wallet)
  HYDRA_DECISION_MODEL— bare LLM model name; resolved into runtime.yaml
  SENPI_AUTH_TOKEN    — REQUIRED.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hydra_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "core" | "dip" | "hedge"
COIN = cfg.resolve_coin()
SCANNER_NAME = f"hydra_{LEG}_signals"
SIGNAL_TYPE = {"core": "HYDRA_CORE", "dip": "HYDRA_DIP", "hedge": "HYDRA_HEDGE"}[LEG]

NORM_DIV = 9.0

_DEFAULTS = {
    "core": {
        "minScore": 5, "marginPct": 0.20, "maxLeverage": 5, "stdLeverage": 3,
        "apexScore": 7, "rsiOverbought": 80, "rsiOversold": 20,
        "venueMinNotionalUsd": 10, "minNotionalPctOfEquity": 0.01, "tickSeconds": 300,
    },
    "dip": {
        "minScore": 4, "marginPct": 0.18, "maxLeverage": 4, "stdLeverage": 3,
        "dipRsiMax": 48,                 # 1h RSI at/below this = a real pullback
        "venueMinNotionalUsd": 10, "minNotionalPctOfEquity": 0.01, "tickSeconds": 300,
    },
    "hedge": {
        "minScore": 4, "marginPct": 0.15, "maxLeverage": 3, "stdLeverage": 3,
        "stressDropPct": 8.0,            # fast drawdown over the lookback that arms the hedge
        "stressLookback": 6,             # 4h candles to measure the drawdown over (~24h)
        "rsiOversold": 18,               # capitulation guard — don't short an exhausted bottom
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
    """Peak-to-current drawdown over the last `lookback` closes, in %."""
    window = closes[-lookback:] if len(closes) >= lookback else closes
    if not window:
        return 0.0
    peak = max(window)
    cur = window[-1]
    return (peak - cur) / peak * 100.0 if peak > 0 else 0.0


def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def fetch_asset(coin):
    data = cfg.mcp_call(
        "market_get_asset_data", asset=coin, candle_intervals=["1h", "4h"],
        dex=_dex_for(coin), include_funding=True, include_order_book=False,
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


# ═══════════════════════════════════════════════════════════════
# Per-leg scoring — one head's view on the coin
# ═══════════════════════════════════════════════════════════════

def score(md, config):
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    closes4 = [_close(c) for c in c4]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    fund = _funding(md["ctx"])

    sc = 0
    reasons = []
    direction = None
    leverage = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))

    if LEG == "core":
        # ── thesis: ride the 4h trend either way; sit out a neutral tape ──
        if trend4 == "NEUTRAL":
            return None
        direction = "LONG" if trend4 == "BULLISH" else "SHORT"
        sc += 3
        reasons.append(f"4h_{trend4.lower()}_{s4:.0%}")
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
        # funding tailwind: paying to be on the crowded side is a small penalty
        if direction == "LONG" and fund < 0:
            sc += 1; reasons.append("funding_pays_long")
        if direction == "SHORT" and fund > 0:
            sc += 1; reasons.append("funding_pays_short")
        # conviction-tiered leverage
        leverage = max_lev if sc >= int(config.get("apexScore", _DEFAULTS["apexScore"])) else int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))

    elif LEG == "dip":
        # ── complement: buy a pullback INSIDE a confirmed 4h uptrend only ──
        if trend4 != "BULLISH":
            return None                       # never buy dips outside an uptrend
        dip_rsi = float(config.get("dipRsiMax", _DEFAULTS["dipRsiMax"]))
        pulled_back = (trend1 != "BULLISH") or (rsi <= dip_rsi)
        if not pulled_back:
            return None                       # no pullback to buy — that's core's job, not dip's
        direction = "LONG"
        sc += 2 + (1 if s4 >= 0.6 else 0)     # strength of the host uptrend
        reasons.append(f"4h_uptrend_{s4:.0%}")
        if rsi <= dip_rsi:
            sc += 2
            reasons.append(f"dip_rsi_{rsi:.0f}")
        if trend1 == "BEARISH":
            sc += 1
            reasons.append("1h_pullback")
        leverage = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))

    else:  # hedge
        # ── hedge: SHORT only on a confirmed downtrend + a fast drawdown ──
        if trend4 != "BEARISH":
            return None                       # idle outside a confirmed downtrend
        dd = drawdown_pct(closes4, int(config.get("stressLookback", _DEFAULTS["stressLookback"])))
        stress = float(config.get("stressDropPct", _DEFAULTS["stressDropPct"]))
        armed = (dd >= stress) or (trend1 == "BEARISH")
        if not armed:
            return None                       # downtrend but no stress yet — stay idle
        os_ = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if rsi < os_:
            return None                       # capitulation guard — don't short an exhausted bottom
        direction = "SHORT"
        sc += 3
        reasons.append(f"4h_bearish_{s4:.0%}")
        if dd >= stress:
            sc += 2
            reasons.append(f"drawdown_{dd:.1f}%")
        if trend1 == "BEARISH":
            sc += 1
            reasons.append("1h_breaking_down")
        leverage = int(config.get("stdLeverage", _DEFAULTS["stdLeverage"]))

    leverage = max(1, min(leverage, max_lev))
    return {"coin": COIN, "direction": direction, "score": sc, "leverage": leverage,
            "reasons": reasons, "price": price, "rsi": rsi, "trend4h": trend4, "trend1h": trend1}


# ═══════════════════════════════════════════════════════════════
# Emit
# ═══════════════════════════════════════════════════════════════

def push_signal(th, margin_usd, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if th["coin"].upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": th["score"], "leverage": th["leverage"], "marginUsd": margin_usd,
        "direction": th["direction"], "reasons": th["reasons"], "heldAssets": held_assets,
        "trend4h": th.get("trend4h"), "rsi": round(th.get("rsi", 0), 1),
    }
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
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

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

    # One position per head (per wallet). If this head already holds the coin, hold.
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
                       float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue min

    md = fetch_asset(COIN)
    if not md:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "candidates": 0, "signals_pushed": 0,
                    "note": "WAITING — no market data", "elapsed_sec": round(time.time() - run_start, 2),
                    "_hydra_producer_version": VERSION})
        return

    th = score(md, config)
    note = {"core": "no confirmed trend", "dip": "no pullback in an uptrend",
            "hedge": "no confirmed downtrend + stress"}[LEG]
    if not th or th["score"] < min_score:
        cfg.output({"status": "ok", "leg": LEG, "coin": COIN, "candidates": 0, "signals_pushed": 0,
                    "min_score": min_score, "note": f"WAITING — {note}",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_hydra_producer_version": VERSION})
        return

    margin_usd = round(account_value * margin_pct, 2)
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    notional = margin_usd * th["leverage"]
    pushed = 0
    emitted = []
    if margin_usd > 0 and notional >= min_notional and margin_usd * 1.1 <= free_margin:
        if push_signal(th, margin_usd, held_assets):
            pushed = 1
            cfg.record_signal(COIN)
            emitted.append({"coin": th["coin"], "direction": th["direction"], "score": th["score"],
                            "leverage": th["leverage"], "margin_usd": margin_usd, "reasons": th["reasons"][:6]})

    cfg.output({
        "status": "ok", "leg": LEG, "coin": COIN, "candidates": 1, "signals_pushed": pushed,
        "emitted": emitted, "direction": th["direction"], "score": th["score"],
        "account_value": round(account_value, 2), "elapsed_sec": round(time.time() - run_start, 2),
        "_hydra_producer_version": VERSION,
    })


if __name__ == "__main__":
    # Long-lived daemon. producer_daemon owns the per-tick scanner_lock with stale-PID
    # recovery. Lock id encodes coin + leg + wallet so a coin's three heads (and other
    # coins' deployments) never collide. Signature-adaptive launch: pass wallet=/scanner=
    # only if the installed helpers signature accepts them (old hosts omit; new hosts use).
    import inspect
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
