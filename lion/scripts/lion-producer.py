#!/usr/bin/env python3
# Senpi LION Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""LION v1.0.0 Producer — Two-Speed-Market (K-shaped) cross-asset long/short.

Lion bets on a K-SHAPED divergence: the AI complex and HYPE/SOL keep booming
while the broad U.S. economy and the rest of crypto struggle. ONE producer
script serves both books; the LION_LEG env var selects which:

  LION_LEG=long   "Haves" — LONG the AI complex on Hyperliquid XYZ (NVDA, AMD,
                  MRVL, TSM, ASML, ARM, AVGO, CRWV, PLTR, ORCL, …) + the crypto
                  winners (HYPE large, SOL modest), trend-confirmed.
  LION_LEG=short  "Have-nots" — SHORT the broad U.S. market via the SP500 index
                  product (the "economy suffers" core) + a curated, gated basket
                  of laggard crypto alts, trend-confirmed (capitulation guard).

Each book scores its OWN curated, CROSS-ASSET universe (xyz equities + main-DEX
crypto on one cross-margined wallet) on ABSOLUTE trend confirmation, with
cross-sectional relative strength as a tiebreaker — so a structural winner that
is genuinely trending gets longed even on a day its peers ran harder, and a
laggard only gets shorted while it is actually rolling over. Per-group SIZING
WEIGHTS express conviction (HYPE large, SOL modest, SP500 core) without
hardcoding dollar amounts — every size scales with account value. The edge is
the DISPERSION between the two speeds, not market direction.

Net exposure (the long/short funding balance) is an explicit operator decision —
see each config's `_net_exposure_note`. The two books run on separate wallets, so
the operator sets the posture by how much capital each wallet holds (plus the
per-leg slots / margin / sizing knobs); the default is a modest net-long tilt.

Each book pushes signals via SenpiClient.push_signal(); runtime owns the LLM gate
(pass-through), DSL exits, and all risk.guard_rails. NOT a copy-trader.

Environment / config resolution:
  LION_LEG            — REQUIRED. "long" or "short".
  SENPI_AUTH_TOKEN    — REQUIRED. Bearer token for MCP + signal POST.
  LION_LONG_WALLET    — long-book strategy wallet (or config.wallet)
  LION_SHORT_WALLET   — short-book strategy wallet (or config.wallet)
  LION_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL       — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lion_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "long" | "short"
SCANNER_NAME = f"lion_{LEG}_signals"
SIGNAL_TYPE = "LION_HAVES_LONG" if LEG == "long" else "LION_HAVENOTS_SHORT"
DIRECTION = "LONG" if LEG == "long" else "SHORT"

NORM_DIV = 9.0

# ── Curated, cross-asset thematic universes (config.universe overrides) ──
# LONG = the "haves": AI complex (xyz equities) + crypto winners (main DEX).
# SHORT = the "have-nots": broad U.S. market (SP500 index) + laggard alts.
# Names not live on the board are skipped at universe-build time, so new
# listings auto-join once added here (or in config.universe).
_HAVES = [
    # AI complex — semis / AI hardware / AI infra / AI software
    "xyz:NVDA", "xyz:AMD", "xyz:MRVL", "xyz:MU", "xyz:TSM", "xyz:ASML",
    "xyz:ARM", "xyz:AVGO", "xyz:CRWV", "xyz:PLTR", "xyz:ORCL", "xyz:SMCI",
    "xyz:DELL",
    # crypto winners
    "HYPE", "SOL",
]
_HAVE_NOTS = [
    # broad U.S. market — the "economy suffers" core
    "xyz:SP500",
    # laggard crypto majors/alts — the "rest of crypto struggles" basket
    "ETH", "XRP", "DOGE", "AVAX", "LINK", "ADA", "LTC", "NEAR", "APT",
]

# Per-group sizing weights (conviction, NOT dollars). margin = account_value *
# marginPct * weight. config.sizingWeights overrides; "_default" is the fallback.
_HAVES_WEIGHTS = {"HYPE": 1.5, "SOL": 0.6, "_default": 1.0}
_HAVE_NOTS_WEIGHTS = {"SP500": 1.2, "_default": 0.7}

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
        "liqVolMultiple": 50,          # 24h vol must be >= 50x the position notional (budget-relative; NO hardcoded $ floor)
        "rankPoolSize": 16,
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
        "liqVolMultiple": 50,          # 24h vol must be >= 50x the position notional (budget-relative; NO hardcoded $ floor)
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
# Unlike a pure cross-sectional book (Cougar), Lion's universe is THEMATIC: every
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

def build_universe(config, meta_map, min_day_vol):
    """The curated thematic whitelist (config.universe — haves for the long leg,
    have-nots for the short leg), intersected with the live instrument board and
    a BUDGET-RELATIVE liquidity floor. An instrument's 24h notional volume must be
    >= min_day_vol (= liqVolMultiple x the standard position notional, computed
    from account value in main()), so a bigger book demands a deeper market and we
    never take a position that is a large fraction of an instrument's daily flow.
    No hardcoded dollar floor — it scales with the account. Names not live / too
    thin are skipped, so new listings auto-join once added to config.universe."""
    wl = config.get("universe", _DEFAULTS["universe"])
    out = []
    for name in wl:
        if not isinstance(name, str):
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        if day_vol(meta) < min_day_vol:
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
                    "_lion_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_lion_producer_version": VERSION})
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
                    "_lion_producer_version": VERSION})
        return

    # Budget-relative liquidity floor: an instrument's 24h volume must dwarf the
    # position we'd take in it. std position notional = account_value * marginPct *
    # maxLeverage; require 24h vol >= liqVolMultiple x that. No hardcoded $ floor —
    # a $2k book needs a far shallower market than a $2M book.
    liq_mult = float(config.get("liqVolMultiple", _DEFAULTS["liqVolMultiple"]))
    min_day_vol = liq_mult * (account_value * float(margin_pct) * float(max_lev))

    meta_map, _canonical = get_universe_meta()
    universe = build_universe(config, meta_map, min_day_vol)

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
                    "_lion_producer_version": VERSION})
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
            "_lion_producer_version": VERSION,
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
        "account_value": round(account_value, 2), "min_day_vol": round(min_day_vol, 0),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_lion_producer_version": VERSION,
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
        "name": f"lion-{LEG}-producer-{_lock_id}",
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
