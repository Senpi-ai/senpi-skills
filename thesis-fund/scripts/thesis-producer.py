#!/usr/bin/env python3
# Senpi THESIS FUND Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""THESIS FUND v1.0.0 Producer — one configurable engine, many one-tap macro bets.

You bring the macro VIEW; this fund expresses it with discipline. The THESIS
env var selects a PRESET from config/thesis-presets.json. Each preset defines a
`long` basket and a `short` basket of assets that together express a single
market view. The fund holds the whole basket in ONE wallet — a single coherent
bet — but it is NOT a blind hold:

  - Each basket name is only PRESSED when the market is CONFIRMING the thesis
    direction (4h/1h trend + 24h momentum aligned). A long-basket name in a
    fresh downtrend is skipped; a short-basket name in a fresh uptrend is
    skipped. Neutral structure enters at lower conviction.
  - The DSL + drawdown gate (runtime.yaml) de-risk when the thesis stops
    working. Disciplined conviction, not a hope trade.

Example presets: risk_off (bet against the Trump economy), recovery (US
risk-on), war_escalation / war_recovery (Iran-US-Israel), hype_vs_market,
gold_over_btc / btc_over_gold. The opposing presets are just flipped baskets.

NOT a copy-trader. It scores its own preset basket and pushes signals via
SenpiClient.push_signal(); the runtime owns the LLM gate (pass-through), DSL
exits, and all risk.guard_rails. Leverage is clamped to maxLeverage then each
asset's Hyperliquid venue max.

Environment / config resolution:
  THESIS                — REQUIRED. Preset key (e.g. "risk_off"). See thesis-presets.json.
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  THESIS_WALLET         — strategy wallet (or config.wallet)
  THESIS_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thesis_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
THESIS = cfg.THESIS
SCANNER_NAME = "thesis_signals"
SIGNAL_TYPE = "THESIS_FUND"

# Max raw score ~ 7.
NORM_DIV = 8.0

_DEFAULTS = {
    "minScore": 4,
    "marginPct": 0.12,      # a diversified basket of up to ~6 names
    "maxLeverage": 5,
    "maxSlots": 6,
    "venueMinNotionalUsd": 10,
    "minNotionalPctOfEquity": 0.01,
    "tickSeconds": 300,
    "momThresholdPct": 1.0,
    "rsiOverbought": 78,
    "rsiOversold": 22,
}


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


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
    data = cfg.mcp_call("market_list_instruments")
    out = {}
    if not data:
        return out
    insts = data.get("data", data)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name") or inst.get("context", {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def fetch_candles(asset, intervals):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=intervals,
        dex=_dex_for(asset),
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return None
    d = data.get("data", data)
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def ret_24h(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if prev <= 0 or mark <= 0:
        return 0.0
    return (mark - prev) / prev * 100.0


# ═══════════════════════════════════════════════════════════════
# Thesis scoring — press a basket name only when the market confirms it
# ═══════════════════════════════════════════════════════════════

def score_thesis(asset, target_dir, meta, config):
    """Score one basket name. `target_dir` ("LONG"/"SHORT") is FIXED by the
    preset (it's the thesis's directional bias for this asset). The score
    measures how strongly the market is CONFIRMING that direction. Returns
    None when the market opposes the thesis leg (don't fight the tape)."""
    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    own = ret_24h(meta)
    mom = float(config.get("momThresholdPct", _DEFAULTS["momThresholdPct"]))

    score = 0
    reasons = []

    if target_dir == "LONG":
        # Don't long into a confirmed downtrend — wait for the thesis to work.
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 3
            reasons.append(f"4h_bullish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h_bullish_{s1:.0%}")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h_bearish")
        if own >= mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")
    else:  # SHORT
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h_bearish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h_bearish_{s1:.0%}")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")
        if own <= -mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if rsi > rsi_os:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")

    return {
        "coin": asset, "direction": target_dir, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "own24h": own,
    }


# ═══════════════════════════════════════════════════════════════
# Leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def clamp_leverage(desired, meta):
    venue = (meta or {}).get("max_leverage")
    try:
        venue = int(venue)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "heldAssets": held_assets,
        "thesis": THESIS,
        "trend4h": thesis.get("trend4h"),
        "own24h": round(thesis.get("own24h", 0), 2),
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {thesis['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {thesis['coin']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# Basket — resolve the preset's (asset, target_direction) pairs
# ═══════════════════════════════════════════════════════════════

def build_basket(preset, meta_map):
    """Return [(asset, target_dir)] from the preset's long/short lists,
    keeping only names live on the instrument board."""
    out = []
    for asset in preset.get("long", []) or []:
        if isinstance(asset, str) and (meta_map.get(asset) or meta_map.get(asset.upper())):
            out.append((asset, "LONG"))
    for asset in preset.get("short", []) or []:
        if isinstance(asset, str) and (meta_map.get(asset) or meta_map.get(asset.upper())):
            out.append((asset, "SHORT"))
    return out


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    thesis_key, preset = cfg.get_active_preset()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "thesis": thesis_key,
                    "_thesis_producer_version": VERSION})
        return
    if not preset:
        cfg.output({"status": "error", "reason": "unknown_thesis",
                    "thesis": thesis_key,
                    "note": "THESIS not found in thesis-presets.json",
                    "_thesis_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "thesis": thesis_key, "note": "no account value",
                    "_thesis_producer_version": VERSION})
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "thesis": thesis_key, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_thesis_producer_version": VERSION})
        return

    meta_map = get_universe_meta()
    basket = build_basket(preset, meta_map)

    candidates = []
    recently_skipped = []
    for asset, target_dir in basket:
        if asset.upper() in held_set:
            continue
        if cfg.was_recently_signaled(asset):
            recently_skipped.append(asset)
            continue
        meta = meta_map.get(asset) or meta_map.get(asset.upper())
        if not meta:
            continue
        thesis = score_thesis(asset, target_dir, meta, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "thesis": thesis_key,
            "preset": preset.get("name", thesis_key),
            "basket_size": len(basket), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no basket name confirmed the thesis (min score {min_score})",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_thesis_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    to_emit = candidates[:open_slots]
    margin_usd = round(account_value * margin_pct, 2)

    pushed = 0
    emitted = []
    for th in to_emit:
        leverage = clamp_leverage(max_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "thesis": thesis_key,
        "preset": preset.get("name", thesis_key),
        "basket_size": len(basket), "candidates": len(candidates),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_thesis_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or THESIS).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"thesis-{THESIS}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
