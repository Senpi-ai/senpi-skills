#!/usr/bin/env python3
# Senpi MAGPIE Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""MAGPIE v1.0.0 Producer — IPO / new-listing event, two books, one script.

Magpie trades the pre-IPO → listing → graduation arc of tokenized equities on
Hyperliquid XYZ (trade.xyz IPOPs like SpaceX). ONE producer script serves both
books; the MAGPIE_LEG env var selects which:

  MAGPIE_LEG=pre_listing  Pre-listing accumulation book.
    Auto-discovers IPOPs (pre-IPO perpetuals) by their funding signature
    (|funding| <= ipopFundingMaxAbs, max_leverage <= cap, min daily volume) and
    rides the pre-listing trend into the IPO — 4h structure sets direction, 1h +
    Smart-Money confirm. Moderate DSL (Discovery Bounds throttle IPOP velocity).

  MAGPIE_LEG=graduation   Graduation-momentum book.
    Classifies every xyz instrument IPOP vs STANDARD each tick, detects the
    IPOP→STANDARD CONVERSION flip (funding jumps ~100x, leverage cap lifts,
    throttle off), stamps it into a conversion window, and rides the explosive
    post-conversion price discovery in the momentum direction — the SpaceX
    $1.4B-day-1 pattern. Wide let-winners-run DSL.

The edge is EVENT alpha around new equity listings — distinct from Cougar
(ongoing equity long/short). NOT a copy-trader. Each book scores its own set and
pushes signals via SenpiClient.push_signal(); runtime owns the LLM gate
(pass-through), DSL exits, and all risk.guard_rails.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets (Smart-Money confirmation).

Environment / config resolution:
  MAGPIE_LEG                 — REQUIRED. "pre_listing" or "graduation".
  SENPI_AUTH_TOKEN           — REQUIRED. Bearer token for MCP + signal POST.
  MAGPIE_PRE_LISTING_WALLET  — pre-listing-book strategy wallet (or config.wallet)
  MAGPIE_GRADUATION_WALLET   — graduation-book strategy wallet (or config.wallet)
  MAGPIE_DECISION_MODEL      — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL              — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import magpie_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "pre_listing" | "graduation"
SCANNER_NAME = f"magpie_{LEG}_signals"
SIGNAL_TYPE = "MAGPIE_PRE_IPO" if LEG == "pre_listing" else "MAGPIE_GRADUATION_MOMENTUM"
NORM_DIV = 9.0 if LEG == "pre_listing" else 8.0

_DEFAULTS = {
    "pre_listing": {
        "minScore": 5,
        "marginPct": 0.12,
        "maxLeverage": 3,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 900,
        "ipopFundingMaxAbs": 1e-7,
        "ipopMaxLeverageCap": 5,
        "ipopMinDailyVolUsd": 100000,
        "smTiltMinPct": 55,
        "smStrongTiltPct": 70,
    },
    "graduation": {
        "minScore": 5,
        "marginPct": 0.15,
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 600,
        "ipopFundingMaxAbs": 1e-7,
        "ipopMaxLeverageCap": 5,
        "momentumLookbackBars": 6,
        "minMomentumPct": 3.0,
        "strongMomentumPct": 8.0,
        "conversionWindowHours": 72,
        "smTiltMinPct": 55,
        "smStrongTiltPct": 70,
    },
}[LEG]


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0.0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Shared technical helpers
# ═══════════════════════════════════════════════════════════════

def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_f(c, "low", "l") for c in candles[-lookback:]]
    highs = [_f(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def momentum_pct(closes, lookback):
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def conversion_direction(momentum, min_momentum_pct):
    if momentum is None or abs(momentum) < min_momentum_pct:
        return None
    return "LONG" if momentum > 0 else "SHORT"


def volume_trend(candles, lookback=6):
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


def classify_instrument(funding_abs, max_leverage, ipop_funding_max, ipop_lev_cap):
    """IPOP = |funding| <= ipop_funding_max AND max_leverage <= ipop_lev_cap;
    else STANDARD."""
    try:
        f = abs(float(funding_abs))
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return "STANDARD"
    if f <= ipop_funding_max and lev <= ipop_lev_cap:
        return "IPOP"
    return "STANDARD"


def detect_conversion(prev_class, curr_class):
    """A conversion = an IPOP that became STANDARD since last tick (known prior)."""
    return prev_class == "IPOP" and curr_class == "STANDARD"


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_candles(asset, intervals):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=intervals,
        dex="xyz",
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return None
    d = data.get("data", data)
    return {"candles": d.get("candles", {}) or {}}


def fetch_sm_direction(asset):
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw or not raw.get("success", True):
        return None, 0.0
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ═══════════════════════════════════════════════════════════════
# PRE-LISTING book — IPOP universe discovery + trend/SM scoring (Lemur method)
# ═══════════════════════════════════════════════════════════════

def fetch_ipop_universe(config):
    raw = cfg.mcp_call("market_list_instruments", dex="xyz")
    if not raw or not raw.get("success", True):
        return []
    data = raw.get("data", raw)
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []
    max_funding = float(config.get("ipopFundingMaxAbs", _DEFAULTS["ipopFundingMaxAbs"]))
    max_lev = int(config.get("ipopMaxLeverageCap", _DEFAULTS["ipopMaxLeverageCap"]))
    min_vol = float(config.get("ipopMinDailyVolUsd", _DEFAULTS.get("ipopMinDailyVolUsd", 100000)))
    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith("xyz:") or inst.get("is_delisted", False):
            continue
        if int(inst.get("max_leverage", 999)) > max_lev:
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(ctx.get("funding", 0)))
        if funding_abs > max_funding:
            continue
        if float(ctx.get("dayNtlVlm", 0)) < min_vol:
            continue
        universe.append({"name": name, "max_leverage": int(inst.get("max_leverage", 5)),
                         "funding": funding_abs, "vol_usd": float(ctx.get("dayNtlVlm", 0))})
    return universe


def build_thesis_pre_listing(asset_name, config):
    md = fetch_candles(asset_name, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c4) < 6 or len(c1) < 6:
        return None
    t4, s4 = trend_structure(c4)
    t1, _ = trend_structure(c1)
    if t4 == "NEUTRAL":
        return None
    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_dir, sm_tilt = fetch_sm_direction(asset_name)
    sm_min = float(config.get("smTiltMinPct", _DEFAULTS["smTiltMinPct"]))
    sm_strong = float(config.get("smStrongTiltPct", _DEFAULTS["smStrongTiltPct"]))
    # IPOP SM data is sparse pre-listing — fall back to trend-only if absent.
    if sm_dir is None:
        sm_dir, sm_tilt = direction, sm_min
    elif sm_dir == "NEUTRAL" or sm_dir != direction or sm_tilt < sm_min:
        return None

    score = 3
    reasons = [f"4h_{t4.lower()}_{s4:.0%}"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{t1.lower()}")
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%" if sm_tilt > sm_min else "sm_sparse_assumed_aligned")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strong")
    return {"coin": asset_name, "direction": direction, "score": score, "reasons": reasons,
            "trend4h": t4, "sm_tilt": sm_tilt}


# ═══════════════════════════════════════════════════════════════
# GRADUATION book — conversion detection + post-conversion momentum (Falcon)
# ═══════════════════════════════════════════════════════════════

def scan_instruments(config):
    raw = cfg.mcp_call("market_list_instruments", dex="xyz")
    if not raw or not raw.get("success", True):
        return {}
    data = raw.get("data", raw)
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return {}
    ipop_funding_max = float(config.get("ipopFundingMaxAbs", _DEFAULTS["ipopFundingMaxAbs"]))
    ipop_lev_cap = int(config.get("ipopMaxLeverageCap", _DEFAULTS["ipopMaxLeverageCap"]))
    out = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith("xyz:") or inst.get("is_delisted", False):
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(ctx.get("funding", 0)))
        max_lev = int(inst.get("max_leverage", 5))
        out[name] = {
            "class": classify_instrument(funding_abs, max_lev, ipop_funding_max, ipop_lev_cap),
            "max_leverage": max_lev,
            "vol_usd": float(ctx.get("dayNtlVlm", 0)),
        }
    return out


def reconcile_conversions(scan, prev_state, config, now):
    window_hours = float(config.get("conversionWindowHours", _DEFAULTS.get("conversionWindowHours", 72)))
    new_class_state = {}
    for name, info in scan.items():
        curr_class = info["class"]
        prev_class = (prev_state.get(name) or {}).get("class")
        if detect_conversion(prev_class, curr_class):
            cfg.record_conversion(name, now)
        new_class_state[name] = {"class": curr_class, "ts": now}
    cfg.write_class_state(new_class_state)
    return set(cfg.prune_conversions(window_hours).keys())


def build_thesis_graduation(name, scan_info, config):
    md = fetch_candles(name, ["1h"])
    if not md:
        return None
    candles = md["candles"].get("1h", [])
    if len(candles) < 8:
        return None
    closes = [_f(c, "close", "c") for c in candles]
    lookback = int(config.get("momentumLookbackBars", _DEFAULTS["momentumLookbackBars"]))
    min_mom = float(config.get("minMomentumPct", _DEFAULTS["minMomentumPct"]))
    strong_mom = float(config.get("strongMomentumPct", _DEFAULTS["strongMomentumPct"]))

    mom = momentum_pct(closes, lookback)
    direction = conversion_direction(mom, min_mom)
    if direction is None:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(name)
    sm_min = float(config.get("smTiltMinPct", _DEFAULTS["smTiltMinPct"]))
    sm_strong = float(config.get("smStrongTiltPct", _DEFAULTS["smStrongTiltPct"]))
    vt = volume_trend(candles)

    score = 3
    reasons = ["converted_ipop_to_equity", f"mom_{mom:+.1f}%"]
    if abs(mom) >= strong_mom:
        score += 2
        reasons.append(f"mom_strong_{mom:+.1f}%")
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")
    return {"coin": name, "direction": direction, "score": score, "reasons": reasons,
            "momentum_pct": round(mom, 2), "max_leverage_cap": scan_info.get("max_leverage", 10)}


# ═══════════════════════════════════════════════════════════════
# Leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def clamp_leverage(desired, cap):
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = desired
    if cap <= 0:
        cap = desired
    return max(1, min(int(desired), cap))


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "heldAssets": held_assets,
    }
    if LEG == "pre_listing":
        data_block["ipopFlag"] = True
        data_block["trend4h"] = thesis.get("trend4h")
    else:
        data_block["conversionEvent"] = True
        data_block["momentumPct"] = thesis.get("momentum_pct", 0.0)
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {coin}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — leg-branched
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    now = time.time()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_magpie_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_magpie_producer_version": VERSION})
        return

    min_score = int(config.get("minScore", _DEFAULTS["minScore"]))
    margin_pct = float(config.get("marginPct", _DEFAULTS["marginPct"]))
    max_lev = int(config.get("maxLeverage", _DEFAULTS["maxLeverage"]))
    max_slots = int(config.get("maxSlots", _DEFAULTS["maxSlots"]))
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_magpie_producer_version": VERSION})
        return

    # ── Build the per-leg candidate set ──
    candidates = []
    extra_out = {}
    if LEG == "pre_listing":
        universe = fetch_ipop_universe(config)
        extra_out["ipop_universe"] = [u["name"] for u in universe]
        if not universe:
            cfg.output({"status": "ok", "leg": LEG, "signals_pushed": 0,
                        "note": "no IPOPs in universe (nothing matches the funding signature)",
                        "elapsed_sec": round(time.time() - run_start, 2),
                        "_magpie_producer_version": VERSION})
            return
        for inst in universe:
            coin = inst["name"]
            if coin.upper() in held_set or cfg.was_recently_signaled(coin):
                continue
            th = build_thesis_pre_listing(coin, config)
            if th and th["score"] >= min_score:
                th["max_leverage_cap"] = inst["max_leverage"]
                candidates.append(th)
    else:  # graduation
        scan = scan_instruments(config)
        prev_state = cfg.read_class_state()
        in_window = reconcile_conversions(scan, prev_state, config, now)
        extra_out["conversions_in_window"] = sorted(in_window)
        extra_out["ipops_now"] = [n for n, i in scan.items() if i["class"] == "IPOP"]
        if not in_window:
            cfg.output({"status": "ok", "leg": LEG, "signals_pushed": 0,
                        "note": "WAITING — no IPOP→equity conversion inside the eligibility window",
                        "tracked_instruments": len(scan), "ipops_now": extra_out["ipops_now"],
                        "held_assets": held_assets,
                        "elapsed_sec": round(time.time() - run_start, 2),
                        "_magpie_producer_version": VERSION})
            return
        for name in sorted(in_window):
            if name.upper() in held_set or cfg.was_recently_signaled(name):
                continue
            info = scan.get(name)
            if not info:
                continue
            th = build_thesis_graduation(name, info, config)
            if th and th["score"] >= min_score:
                candidates.append(th)

    if not candidates:
        note = ("WAITING — no IPOP setup with 4h trend + SM agreement"
                if LEG == "pre_listing"
                else "WAITING — conversion(s) in window but no confirmed post-conversion momentum")
        out = {"status": "ok", "leg": LEG, "signals_pushed": 0, "note": note,
               "held_assets": held_assets,
               "elapsed_sec": round(time.time() - run_start, 2),
               "_magpie_producer_version": VERSION}
        out.update(extra_out)
        cfg.output(out)
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    margin_usd = round(account_value * margin_pct, 2)
    # Cap emissions to what the wallet can actually FUND — never emit an entry we
    # can't afford. Without this, an open slot with no free margin re-emits an
    # un-fillable order every tick (insufficient-funds create_position spam).
    # free margin = equity minus on-chain committed margin (marginUsed).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0  # 1.1 = fee/slippage headroom
    to_emit = candidates[:min(open_slots, affordable)]

    pushed, emitted = 0, []
    for th in to_emit:
        leverage = clamp_leverage(max_lev, th.get("max_leverage_cap", max_lev))
        if leverage <= 0 or margin_usd * leverage < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({"coin": th["coin"], "direction": th["direction"],
                            "score": th["score"], "leverage": leverage,
                            "margin_usd": margin_usd, "reasons": th["reasons"][:5]})

    out = {
        "status": "ok", "leg": LEG,
        "candidates": len(candidates), "open_slots": open_slots,
        "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_magpie_producer_version": VERSION,
    }
    out.update(extra_out)
    cfg.output(out)


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"magpie-{LEG}-producer-{_lock_id}",
        tick_timeout=min(240, max(30, _tick - 10)),
    )
