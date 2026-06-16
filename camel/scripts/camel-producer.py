#!/usr/bin/env python3
# Senpi CAMEL Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""CAMEL v1.0.0 Producer — funding carry, two books, one script.

Camel harvests FUNDING CARRY: it takes the side that COLLECTS funding, on
names where the crowded trade is exhausting (not accelerating) so price
doesn't fight the carry. ONE producer script serves both books; the
CAMEL_LEG env var selects which:

  CAMEL_LEG=harvest  Funding-harvest SHORT book.
    Ranks the liquid crypto cross-section by funding DESC and SHORTS the
    names with the most-POSITIVE funding (longs pay shorts -> short
    collects). Gated so it never shorts a name in a fresh 4h uptrend
    (squeeze risk outweighs carry) — prefers crowded longs that are rolling
    over (4h bearish/neutral, RSI overbought).

  CAMEL_LEG=payout   Funding-payout LONG book.
    Mirror image: ranks by funding ASC and LONGS the most-NEGATIVE-funding
    names (shorts pay longs -> long collects = paid to hold). Never longs a
    name in a fresh 4h downtrend — prefers crowded shorts that are
    capitulating (4h bullish/neutral, RSI oversold).

The edge is CARRY (a structural, recurring funding inefficiency), with
trend/RSI as exhaustion confirmation + risk control. Taking some shorts and
some longs also skews the fund slightly net-neutral.

Funding source: the instrument board's per-asset `funding` field (hourly
decimal). Annualized ≈ funding × 24 × 365. This is the always-available,
one-call source; the ClickHouse-backed funding_history endpoint is NOT
required (it can 503 / need elevated scope). NOT a copy-trader. Each book
scores its own universe and pushes signals via SenpiClient.push_signal();
runtime owns the LLM gate (pass-through), DSL exits, and all risk.guard_rails.

Environment / config resolution:
  CAMEL_LEG             — REQUIRED. "harvest" or "payout".
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  CAMEL_HARVEST_WALLET  — harvest-book strategy wallet (or config.wallet)
  CAMEL_PAYOUT_WALLET   — payout-book strategy wallet (or config.wallet)
  CAMEL_DECISION_MODEL  — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camel_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "harvest" | "payout"
SCANNER_NAME = f"camel_{LEG}_signals"
SIGNAL_TYPE = "CAMEL_CARRY_HARVEST" if LEG == "harvest" else "CAMEL_CARRY_PAYOUT"
DIRECTION = "SHORT" if LEG == "harvest" else "LONG"

# Score normalization divisor for the 0..1 ingest-ranking score. Max raw ~ 7.
NORM_DIV = 8.0

# Funding is HOURLY decimal in the instrument context; annualized = x 8760.
HOURS_PER_YEAR = 8760.0

_DEFAULTS = {
    "harvest": {
        "minScore": 4,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 20000000,
        "universeMaxNames": 60,
        "rankPoolSize": 12,
        # Funding entry floor + tiers (HOURLY decimal). 0.00003/hr ~= 26%/yr.
        "fundingFloorHourly": 0.00003,
        "fundingTier2Hourly": 0.00006,    # ~53%/yr
        "fundingTier3Hourly": 0.0001,     # ~88%/yr
        "rsiOverbought": 70,              # exhaustion confirmation for shorts
    },
    "payout": {
        "minScore": 4,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 20000000,
        "universeMaxNames": 60,
        "rankPoolSize": 12,
        "fundingFloorHourly": 0.00003,
        "fundingTier2Hourly": 0.00006,
        "fundingTier3Hourly": 0.0001,
        "rsiOversold": 30,                # capitulation confirmation for longs
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
    """Higher lows = BULLISH, lower highs = BEARISH."""
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
    """Return (meta_map, canonical_names) from one instrument-board call.
    Context carries funding / markPx / prevDayPx / dayNtlVlm."""
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


def funding_hourly(meta):
    """Per-asset hourly funding rate (decimal) from the instrument context.
    Positive = longs pay shorts (short collects); negative = short pays long."""
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        return float(ctx.get("funding", 0) or 0)
    except (TypeError, ValueError):
        return None


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


def day_vol(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        return float(ctx.get("dayNtlVlm", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════
# Carry scoring — collect funding on exhausting crowds
# ═══════════════════════════════════════════════════════════════

def score_carry(asset, meta, fund, config):
    """Score one candidate for the current book.

    `fund` is the asset's hourly funding (decimal). HARVEST wants large
    POSITIVE funding (short collects) on a crowd that's rolling over;
    PAYOUT wants large NEGATIVE funding (long collects) on a crowd that's
    capitulating. Returns None to disqualify.
    """
    floor = float(config.get("fundingFloorHourly", _DEFAULTS["fundingFloorHourly"]))
    t2 = float(config.get("fundingTier2Hourly", _DEFAULTS["fundingTier2Hourly"]))
    t3 = float(config.get("fundingTier3Hourly", _DEFAULTS["fundingTier3Hourly"]))
    ann = fund * HOURS_PER_YEAR * 100.0  # annualized %

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
    rsi = calc_rsi(closes1)
    own = ret_24h(meta)

    score = 0
    reasons = []

    if LEG == "harvest":
        # Need meaningful POSITIVE funding to short-collect.
        if fund < floor:
            return None
        if fund >= t3:
            score += 3
        elif fund >= t2:
            score += 2
        else:
            score += 1
        reasons.append(f"funding_short_{ann:+.0f}%/yr")
        # Never short a fresh uptrend — squeeze risk dwarfs the carry.
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 2
            reasons.append(f"4h_bearish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        if rsi >= rsi_ob:
            score += 1
            reasons.append(f"rsi_ob_{rsi:.0f}")
        if own <= 0:
            score += 1
            reasons.append(f"rolling_over_{own:+.1f}%")
        elif own >= 5:
            score -= 1
            reasons.append(f"still_ripping_{own:+.1f}%")
    else:  # payout
        if fund > -floor:
            return None
        if fund <= -t3:
            score += 3
        elif fund <= -t2:
            score += 2
        else:
            score += 1
        reasons.append(f"funding_long_{ann:+.0f}%/yr")
        # Never long a fresh downtrend.
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 2
            reasons.append(f"4h_bullish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if rsi <= rsi_os:
            score += 1
            reasons.append(f"rsi_os_{rsi:.0f}")
        if own >= 0:
            score += 1
            reasons.append(f"bouncing_{own:+.1f}%")
        elif own <= -5:
            score -= 1
            reasons.append(f"still_crashing_{own:+.1f}%")

    return {
        "coin": asset, "direction": DIRECTION, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "fundingAnnPct": ann, "own24h": own,
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
        "fundingAnnPct": round(thesis.get("fundingAnnPct", 0), 1),
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
# Universe — liquid main-DEX crypto (both books share it)
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map, canonical):
    """Liquid main-DEX crypto perps (dayNtlVlm >= volFloorUsd), capped to
    universeMaxNames by 24h volume. XYZ excluded (XYZ funding is sparse)."""
    vol_floor = float(config.get("volFloorUsd", _DEFAULTS["volFloorUsd"]))
    max_names = int(config.get("universeMaxNames", _DEFAULTS["universeMaxNames"]))
    seen, pool = set(), []
    for name in canonical:
        if not isinstance(name, str) or name.lower().startswith("xyz:"):
            continue
        key = name.upper()
        if key in seen:
            continue
        meta = meta_map.get(name) or meta_map.get(key)
        if not meta:
            continue
        vol = day_vol(meta)
        if vol < vol_floor:
            continue
        seen.add(key)
        pool.append((name, vol))
    pool.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in pool[:max_names]]


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_camel_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_camel_producer_version": VERSION})
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
                    "_camel_producer_version": VERSION})
        return

    meta_map, canonical = get_universe_meta()
    universe = build_universe(config, meta_map, canonical)

    # ── Rank by funding (no candle fetch): harvest=most positive, payout=most negative ──
    funded = []  # (name, fund, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        f = funding_hourly(meta)
        if f is None:
            continue
        funded.append((name, f, meta))
    if len(funded) < 3:
        cfg.output({"status": "ok", "leg": LEG, "scanned": len(universe),
                    "candidates": 0, "signals_pushed": 0,
                    "note": "WAITING — no funding data to rank",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_camel_producer_version": VERSION})
        return

    funded.sort(key=lambda x: x[1], reverse=(LEG == "harvest"))
    pool = funded[:rank_pool]

    candidates = []
    recently_skipped = []
    for name, f, meta in pool:
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        thesis = score_carry(name, meta, f, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG,
            "scanned": len(universe), "ranked_pool": len(pool), "candidates": 0,
            "signals_pushed": 0, "min_score": min_score,
            "top_funding_annpct": round(pool[0][1] * HOURS_PER_YEAR * 100, 1) if pool else 0,
            "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no name cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_camel_producer_version": VERSION,
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
                "margin_usd": margin_usd,
                "funding_annpct": round(th["fundingAnnPct"], 1),
                "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "scanned": len(universe), "ranked_pool": len(pool),
        "candidates": len(candidates), "open_slots": open_slots,
        "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_camel_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"camel-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
