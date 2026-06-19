#!/usr/bin/env python3
# Senpi CARIBOU Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""CARIBOU v1.0.0 Producer — cross-asset trend fund (managed futures / CTA).

ONE producer script, two INDEPENDENT sleeves on SEPARATE wallets (CARIBOU_LEG):

  CARIBOU_LEG=long   LONG sleeve — opens LONGS on assets in a confirmed UPTREND.
  CARIBOU_LEG=short  SHORT sleeve — opens SHORTS on assets in a confirmed DOWNTREND.

The edge is TIME-SERIES TREND (each asset judged vs its OWN history), harvested
across a maximally diversified universe spanning every asset class on
Hyperliquid — crypto, xyz stocks, indices, metals, energy. Diversification is
the edge: when crypto chops, gold may be trending; when stocks sell off, oil may
be. Positions are sized to EQUAL RISK (volatility parity — margin scales
INVERSELY with the asset's volatility, normalized to a reference vol) and capped
per asset CLASS, so the book can never collapse into a single-class bet.

Long uptrends + short downtrends across uncorrelated classes => net beta ~0 and
crisis-positive (short the fallers when everything bleeds). That is the managed-
futures hedge.

NOT a copy-trader. Each sleeve scores its own universe and pushes signals via
SenpiClient.push_signal(). The runtime owns the LLM gate (pass-through), the
asymmetric trend DSL (cut losers fast, let winners run, time-cuts OFF), and all
risk.guard_rails. Leverage is clamped to the sleeve cap then each asset's HL
venue max.

Efficiency: per tick, each class is ranked by 24h momentum from the instrument
board (no fetch); only the top `rankPerClass` movers in the sleeve's direction
get candles pulled for trend confirmation + vol estimation.

Environment / config resolution:
  CARIBOU_LEG            — REQUIRED. "long" or "short".
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  CARIBOU_LONG_WALLET   — long-sleeve strategy wallet (or config.wallet)
  CARIBOU_SHORT_WALLET  — short-sleeve strategy wallet (or config.wallet)
  CARIBOU_DECISION_MODEL— bare LLM model name; resolved into runtime.yaml
"""

import hashlib
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caribou_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "long" | "short"
SCANNER_NAME = f"caribou_{LEG}_signals"
SIGNAL_TYPE = "CARIBOU_TREND_LONG" if LEG == "long" else "CARIBOU_TREND_SHORT"
DIRECTION = "LONG" if LEG == "long" else "SHORT"

# Raw score normalization for the 0..1 ingest-ranking score. Max raw ~3+2+2 = 7.
NORM_DIV = 8.0

# Per-leg defaults (config.json overrides every one of these). The two sleeves
# are symmetric — same universe, same vol-parity sizing, same class caps — they
# differ only in the direction they take and the sign of the trend gate.
_DEFAULTS = {
    "minScore": 5,
    "apexScore": 7,                 # trend conviction -> maxLeverage
    "baseLeverage": 3,
    "maxLeverage": 5,
    "maxSlots": 8,                  # diversified book
    "baseRiskPct": 0.08,           # margin a REFERENCE-vol asset gets, as % of equity
    "referenceVolPct": 3.0,        # "typical" daily ATR% — the vol-parity anchor
    "minMarginPct": 0.03,          # floor per position (% of equity)
    "maxMarginPct": 0.15,          # cap per position (% of equity)
    "classMarginCapPct": 0.40,     # max total margin per asset CLASS (% of equity)
    "venueMinNotionalUsd": 10,
    "minNotionalPctOfEquity": 0.01,
    "tickSeconds": 300,
    "perClassMaxNames": 12,        # liquidity-cap per class before ranking
    "rankPerClass": 6,             # top movers per class to confirm with candles
    "volFloorPctOfMedian": 0.2,    # within-class liquidity gate (relative, no $ floor)
    "strongMomPct": 5.0,           # 24h move for the extra momentum point
    "rsiOverbought": 80,           # long: don't chase a blow-off
    "rsiOversold": 20,             # short: don't short a capitulation
    # Asset-class map (reference data; override to extend). xyz: names not in
    # metals/energy/indices fall through to "equity"; non-xyz names are "crypto".
    "classMetals": ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER"],
    "classEnergy": ["BRENTOIL", "CL", "WTI", "NATGAS", "GAS", "HEATOIL", "GASOLINE", "URNM"],
    "classIndices": ["SP500", "NASDAQ", "NDX", "US500", "US100", "DJIA", "US30", "RUSSELL", "RUT", "VIX"],
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


def atr_pct(candles, period=14):
    """Average True Range as % of last price — the volatility-parity input.
    Returns None if not computable (caller falls back to reference vol)."""
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
    if price <= 0:
        return None
    return atr / price * 100.0


# ═══════════════════════════════════════════════════════════════
# Universe / data
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def classify(asset, config):
    """Map an instrument to an asset class: crypto | equity | index | metal | energy."""
    name = asset.split(":", 1)[1] if ":" in asset else asset
    name = name.upper()
    if not asset.lower().startswith("xyz:"):
        return "crypto"
    if name in set(config.get("classMetals", _DEFAULTS["classMetals"])):
        return "metal"
    if name in set(config.get("classEnergy", _DEFAULTS["classEnergy"])):
        return "energy"
    if name in set(config.get("classIndices", _DEFAULTS["classIndices"])):
        return "index"
    return "equity"   # any other xyz name is a single-name stock


def get_universe_meta():
    """Return (meta_map, canonical_names) from one instrument-board call.
    Includes xyz instruments (stocks/indices/metals/energy) — Caribou trades
    every class."""
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
# Trend scoring (time-series, per asset) + vol-parity sizing
# ═══════════════════════════════════════════════════════════════

def score_trend(asset, config):
    """Confirm + score a trend on ONE asset. Returns the thesis (incl. vol_pct
    for vol-parity sizing) or None to disqualify. 4h is the hard gate; the daily
    aligns; momentum + RSI guard refine."""
    md = fetch_candles(asset, ["4h", "1d"])
    if not md:
        return None
    c4 = md["candles"].get("4h", [])
    cd = md["candles"].get("1d", [])
    if len(c4) < 6:
        return None
    closes4 = [_close(c) for c in c4]
    price = closes4[-1]
    if price <= 0:
        return None

    trend4, s4 = trend_structure(c4)
    trendd, sd = trend_structure(cd) if len(cd) >= 6 else ("NEUTRAL", 0)
    rsi = calc_rsi(closes4)
    own = ret_24h({"ctx": md["ctx"]})
    if own is None:
        own = 0.0
    # Vol estimate for parity sizing — prefer daily ATR, fall back to 4h.
    vol = atr_pct(cd) if len(cd) >= 3 else None
    if vol is None or vol <= 0:
        vol = atr_pct(c4)
    if vol is None or vol <= 0:
        vol = float(config.get("referenceVolPct", _DEFAULTS["referenceVolPct"]))

    strong = float(config.get("strongMomPct", _DEFAULTS["strongMomPct"]))
    score, reasons = 0, []

    if LEG == "long":
        if trend4 != "BULLISH":
            return None
        score += 3
        reasons.append(f"4h_uptrend_{s4:.0%}")
        if trendd == "BULLISH":
            score += 2
            reasons.append(f"1d_uptrend_{sd:.0%}")
        elif trendd == "BEARISH":
            score -= 2
            reasons.append("1d_conflict")
        if own > 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if own >= strong:
            score += 1
            reasons.append("mom_strong")
        rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # short
        if trend4 != "BEARISH":
            return None
        score += 3
        reasons.append(f"4h_downtrend_{s4:.0%}")
        if trendd == "BEARISH":
            score += 2
            reasons.append(f"1d_downtrend_{sd:.0%}")
        elif trendd == "BULLISH":
            score -= 2
            reasons.append("1d_conflict")
        if own < 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if own <= -strong:
            score += 1
            reasons.append("mom_strong")
        rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
        if rsi < rsi_os:
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset, "direction": DIRECTION, "score": score, "reasons": reasons,
        "price": price, "rsi": round(rsi, 1), "trend4h": trend4, "trend1d": trendd,
        "own24h": round(own, 2), "vol_pct": round(vol, 3),
    }


def vol_parity_margin(account_value, vol_pct, config):
    """Margin scales INVERSELY with the asset's volatility, normalized to a
    reference vol, then clamped to [minMarginPct, maxMarginPct] of equity. A calm
    asset gets more margin; a wild one gets less — equal-risk (vol parity)."""
    base = float(config.get("baseRiskPct", _DEFAULTS["baseRiskPct"]))
    ref = float(config.get("referenceVolPct", _DEFAULTS["referenceVolPct"]))
    lo = float(config.get("minMarginPct", _DEFAULTS["minMarginPct"]))
    hi = float(config.get("maxMarginPct", _DEFAULTS["maxMarginPct"]))
    if vol_pct <= 0:
        vol_pct = ref
    pct = base * (ref / vol_pct)
    pct = max(lo, min(hi, pct))
    return round(account_value * pct, 2)


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
        "assetClass": thesis.get("assetClass"),
        "trend4h": thesis.get("trend4h"),
        "trend1d": thesis.get("trend1d"),
        "volPct": thesis.get("vol_pct"),
        "own24h": thesis.get("own24h"),
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


def build_class_pools(config, meta_map, canonical):
    """Bucket the universe by asset class; within each class apply a top-N-by-vol
    liquidity cap + a relative-to-median gate; then rank by 24h momentum in the
    sleeve's direction and keep the top `rankPerClass` movers. Returns
    {class: [(name, meta), ...]}. Ranking is from context only — NO candle fetch
    here (candles are pulled later, only for these finalists)."""
    per_class_max = int(config.get("perClassMaxNames", _DEFAULTS["perClassMaxNames"]))
    rank_per = int(config.get("rankPerClass", _DEFAULTS["rankPerClass"]))
    vfloor = float(config.get("volFloorPctOfMedian", _DEFAULTS["volFloorPctOfMedian"]))

    buckets = {}
    seen = set()
    for name in canonical:
        if not isinstance(name, str):
            continue
        key = name.upper()
        if key in seen:
            continue
        meta = meta_map.get(name) or meta_map.get(key)
        if not meta:
            continue
        v = day_vol(meta)
        if v <= 0:
            continue
        seen.add(key)
        buckets.setdefault(classify(name, config), []).append((name, meta, v))

    pools = {}
    for cls, names in buckets.items():
        names.sort(key=lambda x: x[2], reverse=True)
        names = names[:per_class_max]
        if not names:
            continue
        vols = sorted(v for _, _, v in names)
        median = vols[len(vols) // 2]
        floor = vfloor * median
        liquid = [(n, m) for n, m, v in names if v >= floor]
        # rank by 24h momentum in the sleeve's direction
        scored = []
        for n, m in liquid:
            r = ret_24h(m)
            if r is None:
                continue
            scored.append((n, m, r))
        scored.sort(key=lambda x: x[2], reverse=(LEG == "long"))
        # long: most positive movers; short: most negative movers
        finalists = [(n, m) for n, m, r in scored
                     if (r > 0 if LEG == "long" else r < 0)][:rank_per]
        pools[cls] = finalists
    return pools


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. daemon owns the scanner_lock.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_caribou_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_caribou_producer_version": VERSION})
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    apex = config.get("apexScore", _DEFAULTS["apexScore"])
    base_lev = config.get("baseLeverage", _DEFAULTS["baseLeverage"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    class_cap_pct = float(config.get("classMarginCapPct", _DEFAULTS["classMarginCapPct"]))
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)),
                       float(config.get("venueMinNotionalUsd", 10)))

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_caribou_producer_version": VERSION})
        return

    meta_map, canonical = get_universe_meta()
    pools = build_class_pools(config, meta_map, canonical)

    # Confirm trend on each class's finalists (candle fetch happens here).
    candidates = []
    recently_skipped = []
    scanned = 0
    for cls, finalists in pools.items():
        for name, meta in finalists:
            if name.upper() in held_set:
                continue
            if cfg.was_recently_signaled(name):
                recently_skipped.append(name)
                continue
            scanned += 1
            th = score_trend(name, config)
            if th and th["score"] >= min_score:
                th["_meta"] = meta
                th["assetClass"] = cls
                candidates.append(th)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG, "scanned": scanned,
            "classes": {c: len(f) for c, f in pools.items()},
            "candidates": 0, "signals_pushed": 0, "min_score": min_score,
            "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no asset cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_caribou_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Current per-class deployed margin (for class caps).
    class_deployed = {}
    for p in positions:
        cls = classify(p.get("coin", ""), config)
        class_deployed[cls] = class_deployed.get(cls, 0.0) + float(p.get("margin", 0) or 0)
    class_cap = account_value * class_cap_pct

    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))

    pushed, emitted, skipped_cap = 0, [], []
    slots_left = open_slots
    for th in candidates:
        if slots_left <= 0:
            break
        cls = th["assetClass"]
        margin_usd = vol_parity_margin(account_value, th["vol_pct"], config)
        # Conviction leverage: apex trend -> maxLeverage, else baseLeverage.
        desired_lev = max_lev if th["score"] >= apex else base_lev
        leverage = clamp_leverage(desired_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        # Per-class margin cap — keep the book diversified across classes.
        if class_deployed.get(cls, 0.0) + margin_usd > class_cap:
            skipped_cap.append(f"{th['coin']}({cls})")
            continue
        # Affordability — never emit an entry the wallet can't fund.
        if margin_usd * 1.1 > free_margin:
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            slots_left -= 1
            free_margin -= margin_usd
            class_deployed[cls] = class_deployed.get(cls, 0.0) + margin_usd
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "class": cls, "direction": th["direction"],
                "score": th["score"], "leverage": leverage, "margin_usd": margin_usd,
                "vol_pct": th["vol_pct"], "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG, "scanned": scanned,
        "classes": {c: len(f) for c, f in pools.items()},
        "candidates": len(candidates), "open_slots": open_slots,
        "signals_pushed": pushed, "emitted": emitted,
        "class_deployed": {k: round(v, 2) for k, v in class_deployed.items()},
        "class_cap_usd": round(class_cap, 2),
        "skipped_class_cap": skipped_cap,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_caribou_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    _kwargs = {
        "fn": main,
        "interval_seconds": _tick,
        "name": f"caribou-{LEG}-producer-{_lock_id}",
        "tick_timeout": min(180, max(30, _tick - 10)),
    }
    # Signature-adaptive: pass wallet=/scanner= only if the installed helpers
    # daemon accepts them (older hosts reject the kwargs with a TypeError).
    try:
        _params = inspect.signature(producer_daemon).parameters
    except (TypeError, ValueError):
        _params = {}
    if "wallet" in _params:
        _kwargs["wallet"] = STRATEGY_ADDRESS
    if "scanner" in _params:
        _kwargs["scanner"] = SCANNER_NAME
    producer_daemon(**_kwargs)
