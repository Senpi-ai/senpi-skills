#!/usr/bin/env python3
# Senpi BOAR Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""BOAR v1.0.0 Producer — Hard Money vs Paper (the debasement long/short).

Boar bets on FISCAL DOMINANCE / currency debasement: scarce real assets (gold,
BTC, silver) outperform fiat-denominated financial CLAIMS as deficits compound
and the term premium rises. ONE producer script serves both books; the BOAR_LEG
env var selects which:

  BOAR_LEG=long   "Hard money" — LONG precious metals + BTC: GOLD, BTC (digital
                  gold), SILVER, PLATINUM, PALLADIUM, trend-confirmed.
  BOAR_LEG=short  "Paper" — SHORT the broad market + rate-sensitive long-duration
                  growth: SP500 + RIVN/DKNG/HIMS, trend-confirmed (capitulation
                  guard) — the financial claims that lose most as rates rise.

Each book scores its universe on ABSOLUTE trend confirmation, with cross-sectional
relative strength as a tiebreaker. Per-group SIZING WEIGHTS express conviction
(GOLD/BTC the cores; thin precious + meme-y growth shorts sized down) without
hardcoding dollar amounts — every size scales with account value. The edge is the
real-assets-beat-paper-claims spread.

HONEST CAVEAT — this is the LOOSEST hedge of the thesis-fund family: in a pure
liquidity MELT-UP, gold AND stocks can both rise (everything priced in debasing
fiat rises), so the legs can correlate POSITIVELY. The short leans toward
rate-sensitive names (which underperform even in a melt-up as the term premium
compresses their multiples) to tighten it, but the hedge is imperfect. Best in a
"real assets outperform" regime, not a blow-off. The DSL owns the drawdowns.

The long/short balance is an explicit operator decision — set by the wallet
funding split (see config `_hedge_note`); the default is a net-long-hard-money tilt.

Each book pushes signals via SenpiClient.push_signal(); runtime owns the LLM gate
(pass-through), DSL exits, and all risk.guard_rails. NOT a copy-trader.

Environment / config resolution:
  BOAR_LEG            — REQUIRED. "long" or "short".
  SENPI_AUTH_TOKEN    — REQUIRED. Bearer token for MCP + signal POST.
  BOAR_LONG_WALLET    — long-book strategy wallet (or config.wallet)
  BOAR_SHORT_WALLET   — short-book strategy wallet (or config.wallet)
  BOAR_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL       — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boar_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "long" | "short"
SCANNER_NAME = f"boar_{LEG}_signals"
SIGNAL_TYPE = "BOAR_HARDMONEY_LONG" if LEG == "long" else "BOAR_PAPER_SHORT"
DIRECTION = "LONG" if LEG == "long" else "SHORT"

NORM_DIV = 9.0

# ── Curated thematic universes (config.universe overrides) — the debasement bet ──
# LONG = hard money / scarce real assets (precious metals + BTC) — what holds up
#        when fiat is debased under fiscal dominance.
# SHORT = the "paper" economy — the broad market + rate-sensitive long-duration
#        growth (the financial claims that lose most as the term premium rises).
# Names not live on the board are skipped. (See SKILL caveat: in a pure liquidity
# melt-up gold AND stocks can rise together — the loosest hedge of the family.)
_HAVES = [
    "xyz:GOLD",       # the monetary metal
    "BTC",            # digital gold (main DEX)
    "xyz:SILVER",     # monetary + industrial
    "xyz:PLATINUM",   # precious (thinner)
    "xyz:PALLADIUM",  # precious (thinner)
]
_HAVE_NOTS = [
    "xyz:SP500",      # broad paper-claims anchor
    "xyz:RIVN",       # rate-sensitive / cash-burning long-duration growth
    "xyz:DKNG",       # high-multiple long-duration growth
    "xyz:HIMS",       # high-multiple long-duration growth
]

# Per-group sizing weights (conviction, NOT dollars). margin = account_value *
# marginPct * weight. config.sizingWeights overrides; "_default" is the fallback.
_HAVES_WEIGHTS = {
    "GOLD": 1.2,      # core hard money
    "BTC": 1.2,       # co-core (digital gold)
    "SILVER": 1.0,
    "PLATINUM": 0.7,  # thinner precious
    "PALLADIUM": 0.6, # thinner precious
    "_default": 1.0,
}
# Short tilts to the broad index (the clean "paper claims" proxy); the
# rate-sensitive growth names are small + squeeze-prone.
_HAVE_NOTS_WEIGHTS = {"SP500": 1.2, "_default": 0.6}

_DEFAULTS = {
    "long": {
        "universe": _HAVES,
        "sizingWeights": _HAVES_WEIGHTS,
        "minScore": 5,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 5,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorPctOfMedian": 0.2,    # relative-to-market liquidity gate (no $ floor)
        "rankPoolSize": 12,            # small hard-money basket — score it all each tick
        "rsThresholdPct": 3.0,
        "rsiOverbought": 82,
    },
    "short": {
        "universe": _HAVE_NOTS,
        "sizingWeights": _HAVE_NOTS_WEIGHTS,
        "minScore": 5,
        "marginPct": 0.15,
        "maxLeverage": 4,
        "maxSlots": 4,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorPctOfMedian": 0.2,    # relative-to-market liquidity gate (no $ floor)
        "rankPoolSize": 16,
        "rsThresholdPct": 3.0,
        "rsiOversold": 18,
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


def _bare(asset):
    """Bare ticker for sizing-weight + dedup lookups: 'xyz:NVDA' -> 'NVDA'."""
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


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
# Scoring — ABSOLUTE trend is the gate; relative strength is a tiebreaker
# ═══════════════════════════════════════════════════════════════
#
# Unlike a pure cross-sectional book (Cougar), Boar's universe is THEMATIC: every
# name in it is already a thesis pick (a "have" or a "have-not"). So the hard gate
# is ABSOLUTE trend — long a have only while it is actually trending up; short a
# have-not only while it is actually rolling over. Cross-sectional excess (vs the
# leg-universe mean) is a SCORE MODIFIER, not a disqualifier — it tilts size and
# ranking toward the strongest leaders / weakest laggards without benching a
# genuinely-trending winner on a day its peers ran harder.

def score_thematic(asset, meta, excess, config):
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
        # ── HARD GATE: never long a confirmed downtrend ──
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
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"abs_up_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"rs_lag_{excess:+.1f}%")  # noted, not penalized
        rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # short
        # ── HARD GATE: never short a confirmed uptrend ──
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
        if own <= 0:
            score += 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_up_{own:+.1f}%")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"rs_lead_{excess:+.1f}%")
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
# Sizing weight + leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def sizing_weight(asset, config):
    """Per-group conviction multiplier (HYPE large, SOL modest, SP500 core).
    Keyed by bare ticker; falls back to '_default'. Clamped to [0.1, 3.0]."""
    weights = config.get("sizingWeights") or _DEFAULTS["sizingWeights"]
    if not isinstance(weights, dict):
        weights = _DEFAULTS["sizingWeights"]
    try:
        w = float(weights.get(_bare(asset), weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


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
# Universe — curated thematic whitelist, liquid + live only
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map):
    """The curated thematic whitelist (config.universe — the long basket for the long leg,
    the short basket for the short leg), intersected with the live instrument board and a
    RELATIVE-TO-MARKET liquidity gate: an instrument's 24h volume must be >=
    volFloorPctOfMedian of the whitelist's median 24h volume. NO hardcoded dollar
    floor — it adapts to the market and drops an anomalously-thin name without a
    fixed threshold. Names not live are skipped, so new listings auto-join once
    added to config.universe."""
    wl = config.get("universe", _DEFAULTS["universe"])
    pct = float(config.get("volFloorPctOfMedian", _DEFAULTS["volFloorPctOfMedian"]))
    cand = []
    for name in wl:
        if not isinstance(name, str):
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        vol = day_vol(meta)
        if vol <= 0:
            continue
        cand.append((name, vol))
    if not cand:
        return []
    vols = sorted(v for _, v in cand)
    median = vols[len(vols) // 2]
    floor = pct * median
    return [n for n, v in cand if v >= floor]


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_boar_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_boar_producer_version": VERSION})
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
                    "_boar_producer_version": VERSION})
        return

    meta_map, _canonical = get_universe_meta()
    universe = build_universe(config, meta_map)

    # ── Cross-sectional relative strength over the thematic universe (used as a
    #    score tiebreaker; absolute trend is the gate inside score_thematic) ──
    rs = []  # (name, own_24h, meta)
    for name in universe:
        meta = meta_map.get(name) or meta_map.get(name.upper())
        own = ret_24h(meta)
        if own is None:
            continue
        rs.append((name, own, meta))
    if len(rs) < 2:
        cfg.output({"status": "ok", "leg": LEG, "scanned": len(universe),
                    "candidates": 0, "signals_pushed": 0,
                    "note": "WAITING — thematic universe too thin to evaluate",
                    "elapsed_sec": round(time.time() - run_start, 2),
                    "_boar_producer_version": VERSION})
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
        thesis = score_thematic(name, meta, excess, config)
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
            "note": f"WAITING — no name cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_boar_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Emit best-scoring first, sizing each by its conviction weight, and cap to
    # what the wallet can actually FUND. free margin = equity minus on-chain
    # committed margin (marginUsed); decrement as we commit so a mixed basket of
    # different-sized names never emits an un-fundable order (which would re-emit
    # an insufficient-funds create_position every tick).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))

    pushed = 0
    emitted = []
    for th in candidates:
        if open_slots <= 0:
            break
        weight = sizing_weight(th["coin"], config)
        margin_usd = round(account_value * margin_pct * weight, 2)
        leverage = clamp_leverage(max_lev, th["_meta"])
        notional = margin_usd * leverage
        if margin_usd <= 0 or leverage <= 0 or notional < min_notional:
            continue
        if margin_usd * 1.1 > free_margin:  # 1.1 = fee/slippage headroom
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            open_slots -= 1
            free_margin -= margin_usd * 1.1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "weight": weight,
                "excess": round(th["excess"], 2),
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
        "_boar_producer_version": VERSION,
    })


if __name__ == "__main__":
    # Long-lived daemon. producer_daemon owns the per-tick scanner_lock with
    # stale-PID auto-recovery. The lock id encodes leg + wallet so the long and
    # short daemons never collide.
    #
    # Host-package signature skew: older senpi_runtime_helpers expose only
    # (fn, interval_seconds, name, tick_timeout); newer ones additionally accept
    # (wallet, scanner) to drive the /state alive_check. Introspect the INSTALLED
    # signature and pass those two only if supported — runs unpatched on both:
    # old hosts omit them (no TypeError), upgraded hosts get the liveness check.
    import inspect
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    _kwargs = {
        "fn": main,
        "interval_seconds": _tick,
        "name": f"boar-{LEG}-producer-{_lock_id}",
        "tick_timeout": min(180, max(30, _tick - 10)),
    }
    try:
        _params = inspect.signature(producer_daemon).parameters
    except (TypeError, ValueError):
        _params = {}
    if "wallet" in _params:
        _kwargs["wallet"] = STRATEGY_ADDRESS
    if "scanner" in _params:
        _kwargs["scanner"] = SCANNER_NAME
    producer_daemon(**_kwargs)
