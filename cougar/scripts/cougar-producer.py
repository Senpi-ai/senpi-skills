#!/usr/bin/env python3
# Senpi COUGAR Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""COUGAR v1.0.0 Producer — U.S. equity long/short, two books, one script.

Cougar trades the tokenized U.S. equity universe on Hyperliquid XYZ (trade.xyz:
NVDA, TSLA, AAPL, AMZN, … + index products) cross-sectionally: it ranks the
equities by relative strength (24h excess return vs the equity-universe mean)
and longs the LEADERS / shorts the LAGGARDS, trend-confirmed. Run on two
equally-funded wallets the pair is ~market-neutral and harvests EQUITY
DISPERSION — the spread between the best and worst stocks. ONE producer script
serves both books; the COUGAR_LEG env var selects which:

  COUGAR_LEG=long   Long the relative-strength leaders (positive excess +
                    bullish absolute trend; blow-off guard).
  COUGAR_LEG=short  Short the relative-strength laggards (negative excess +
                    bearish absolute trend; capitulation guard).

The edge is STOCK SELECTION, not market direction. NOT a copy-trader. Each book
ranks + scores its own equity cross-section and pushes signals via
SenpiClient.push_signal(); runtime owns the LLM gate (pass-through), DSL exits,
and all risk.guard_rails.

Environment / config resolution:
  COUGAR_LEG          — REQUIRED. "long" or "short".
  SENPI_AUTH_TOKEN    — REQUIRED. Bearer token for MCP + signal POST.
  COUGAR_LONG_WALLET  — long-book strategy wallet (or config.wallet)
  COUGAR_SHORT_WALLET — short-book strategy wallet (or config.wallet)
  COUGAR_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL       — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cougar_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "long" | "short"
SCANNER_NAME = f"cougar_{LEG}_signals"
SIGNAL_TYPE = "COUGAR_EQUITY_LONG" if LEG == "long" else "COUGAR_EQUITY_SHORT"
DIRECTION = "LONG" if LEG == "long" else "SHORT"

NORM_DIV = 9.0

# Tokenized U.S. equity universe on Hyperliquid XYZ (trade.xyz). config.equities
# overrides. Names not live on the board are skipped at universe-build time, so
# new trade.xyz listings auto-join once added here (or in config).
_EQUITY_UNIVERSE = [
    "xyz:NVDA", "xyz:TSLA", "xyz:AAPL", "xyz:META", "xyz:MSFT", "xyz:GOOGL",
    "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC", "xyz:TSM", "xyz:ORCL",
    "xyz:NFLX", "xyz:AVGO", "xyz:CRM", "xyz:COIN", "xyz:MSTR", "xyz:PLTR",
    "xyz:SMCI", "xyz:UBER", "xyz:SHOP", "xyz:SPCX",
]

_DEFAULTS = {
    "long": {
        "equities": _EQUITY_UNIVERSE,
        "minScore": 5,
        "marginPct": 0.20,
        "maxLeverage": 5,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 500000,         # equity liquidity floor (lower than crypto)
        "rankPoolSize": 8,
        "rsThresholdPct": 3.0,
        "rsiOverbought": 80,
    },
    "short": {
        "equities": _EQUITY_UNIVERSE,
        "minScore": 5,
        "marginPct": 0.20,
        "maxLeverage": 5,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 500000,
        "rankPoolSize": 8,
        "rsThresholdPct": 3.0,
        "rsiOversold": 20,
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


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
    data = cfg.mcp_call("market_list_instruments")
    out, canonical = {}, []
    if not data:
        return out, canonical
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
        canonical.append(name)
    return out, canonical


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
        return None
    if prev <= 0 or mark <= 0:
        return None
    return (mark - prev) / prev * 100.0


def day_vol(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        return float(ctx.get("dayNtlVlm", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════
# Dispersion scoring — long leaders / short laggards, trend-confirmed
# ═══════════════════════════════════════════════════════════════

def score_dispersion(asset, meta, excess, config):
    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    own = ret_24h(meta)
    if own is None:
        own = 0.0

    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    rs_thresh = float(config.get("rsThresholdPct", _DEFAULTS["rsThresholdPct"]))

    score = 0
    reasons = []

    if LEG == "long":
        if excess < 0:
            return None
        if excess >= 2 * rs_thresh:
            score += 3
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 2
            reasons.append(f"rs_lead_{excess:+.1f}%")
        else:
            score += 1
            reasons.append(f"rs_lead_{excess:+.1f}%")
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 2
            reasons.append(f"4h_bullish_{s4:.0%}")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h_bullish_{s1:.0%}")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h_bearish")
        if own >= 0:
            score += 1
            reasons.append(f"abs_up_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # short
        if excess > 0:
            return None
        if excess <= -2 * rs_thresh:
            score += 3
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 2
            reasons.append(f"rs_lag_{excess:+.1f}%")
        else:
            score += 1
            reasons.append(f"rs_lag_{excess:+.1f}%")
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 2
            reasons.append(f"4h_bearish_{s4:.0%}")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h_bearish_{s1:.0%}")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")
        if own <= 0:
            score += 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_up_{own:+.1f}%")
        rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if rsi < rsi_os:
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset, "direction": DIRECTION, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "trend1h": trend1, "excess": excess, "own24h": own,
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
        "trend4h": thesis.get("trend4h"),
        "excess": round(thesis.get("excess", 0), 2),
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
# Universe — curated tokenized-US-equity whitelist, liquid + live only
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map):
    """The curated US-equity whitelist (config.equities), intersected with the
    live instrument board and a liquidity floor. Names not live / too thin are
    skipped, so new trade.xyz listings auto-join once added to the whitelist."""
    wl = config.get("equities", _DEFAULTS["equities"])
    vol_floor = float(config.get("volFloorUsd", _DEFAULTS["volFloorUsd"]))
    out = []
    for name in wl:
        if not isinstance(name, str):
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        if day_vol(meta) < vol_floor:
            continue
        out.append(name)
    return out


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_cougar_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_cougar_producer_version": VERSION})
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value
    rank_pool = int(config.get("rankPoolSize", _DEFAULTS["rankPoolSize"]))

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_cougar_producer_version": VERSION})
        return

    meta_map, _canonical = get_universe_meta()
    universe = build_universe(config, meta_map)

    # ── Cross-sectional relative-strength rank over the equity universe ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = ret_24h(meta)
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 4:
        cfg.output({"status": "ok", "leg": LEG, "scanned": len(universe),
                    "candidates": 0, "signals_pushed": 0,
                    "note": "WAITING — equity cross-section too thin to rank",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_cougar_producer_version": VERSION})
        return

    mean_rs = sum(r[1] for r in rs) / len(rs)
    rs.sort(key=lambda x: x[1], reverse=(LEG == "long"))
    pool = rs[:rank_pool]

    candidates = []
    recently_skipped = []
    for name, own, meta in pool:
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        excess = own - mean_rs
        thesis = score_dispersion(name, meta, excess, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG,
            "scanned": len(universe), "ranked_pool": len(pool), "candidates": 0,
            "signals_pushed": 0, "min_score": min_score,
            "mean_rs_24h": round(mean_rs, 2), "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no equity cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_cougar_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    margin_usd = round(account_value * margin_pct, 2)
    # Cap emissions to what the wallet can actually FUND — never emit an entry we
    # can't afford. Without this, an open slot with no free margin re-emits an
    # un-fillable order every tick (insufficient-funds create_position spam).
    # free margin = equity minus on-chain committed margin (marginUsed).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0  # 1.1 = fee/slippage headroom
    to_emit = candidates[:min(open_slots, affordable)]

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
                "margin_usd": margin_usd, "excess": round(th["excess"], 2),
                "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "scanned": len(universe), "ranked_pool": len(pool),
        "candidates": len(candidates), "open_slots": open_slots,
        "signals_pushed": pushed, "emitted": emitted,
        "mean_rs_24h": round(mean_rs, 2),
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_cougar_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"cougar-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
