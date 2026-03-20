# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

"""
HYDRA v1.0 — Multi-Source Squeeze Scanner
==========================================
Detects crowded positions across 200+ crypto assets using 6 independent
signal sources, scores candidates, and outputs entry signals with
complete DSL state.

Signal Sources:
  1. FDD — Funding Divergence Detector (30/110, PRIMARY GATE)
  2. LCD — Liquidation Cascade Detector (25/110)
  3. OIS — Open Interest Surge Detector (20/110)
  4. MED — Momentum Exhaustion Detector (-10 to +5, dual role)
  5. EM  — Emerging Movers from SM leaderboard (-8 to +15)
  6. OPP — Opportunity Scanner, hourly trend gate (-999 to +10)

DSL v1.1.1 — scanner generates COMPLETE state. No dsl-profile.json.
"""

import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydra_config import (
    load_config, load_runtime, save_runtime, load_cooldowns,
    get_positions, get_wallet_balance, get_deployed_margin,
    mcporter_call, output, log, is_on_cooldown,
    is_tier_enabled, check_gate, append_oi_snapshot,
    load_oi_history, get_oi_at,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def no_reply(note: str) -> dict:
    return {"status": "ok", "heartbeat": "NO_REPLY", "note": note}


def is_xyz(asset: str) -> bool:
    """Check if asset is an XYZ equity. HYDRA never trades these."""
    return asset.lower().startswith("xyz") or asset.lower().startswith("xyz:")


# ═══════════════════════════════════════════════════════════════════════════
# Source 1: Funding Divergence Detector (FDD) — 0-30 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_fdd(asset: str, config: dict) -> dict:
    """
    Analyze 7-day hourly funding rate history.
    Detect SHORT_CROWDING (deeply negative → longs profit)
    or LONG_CROWDING (deeply positive → shorts profit).
    """
    fdd_cfg = config.get("scanner", {}).get("sources", {}).get("fdd", {})
    max_score = fdd_cfg.get("maxScore", 30)
    min_confidence = config.get("scanner", {}).get("minFddConfidence", 40)

    data = mcporter_call("market_get_asset_data", {"asset": asset})
    if not data or not isinstance(data, dict):
        return {"score": 0, "signal": None, "confidence": 0}

    # Extract funding rate info
    funding_rate = safe_float(data.get("funding", data.get("fundingRate",
                              data.get("currentFunding", 0))))

    # Try to get funding history for percentile calculation
    funding_history = data.get("fundingHistory", data.get("funding_history", []))
    if isinstance(funding_history, list) and len(funding_history) >= 24:
        rates = [safe_float(h.get("rate", h.get("fundingRate", 0))) for h in funding_history]
        rates_sorted = sorted(rates)
        # Find percentile of current rate
        below = sum(1 for r in rates_sorted if r < funding_rate)
        percentile = (below / len(rates_sorted)) * 100
    else:
        # Fallback: use absolute funding rate as proxy
        # Typical range: -0.01% to +0.01% per hour
        # Extreme: beyond ±0.05%
        if abs(funding_rate) < 0.0001:
            return {"score": 0, "signal": None, "confidence": 0}
        percentile = 5 if funding_rate < -0.0003 else (95 if funding_rate > 0.0003 else 50)

    signal = None
    confidence = 0

    if percentile <= 10:
        signal = "SHORT_CROWDING"
        confidence = (10 - percentile) * 10  # 0-100
    elif percentile >= 90:
        signal = "LONG_CROWDING"
        confidence = (percentile - 90) * 10

    # Persistence bonus: if we can detect consecutive hours of crowding
    if isinstance(funding_history, list) and len(funding_history) >= 4:
        recent_4h = funding_history[-4:]
        if signal == "SHORT_CROWDING" and all(safe_float(h.get("rate", 0)) < 0 for h in recent_4h):
            confidence += 10
        elif signal == "LONG_CROWDING" and all(safe_float(h.get("rate", 0)) > 0 for h in recent_4h):
            confidence += 10

    confidence = min(confidence, 100)

    if confidence < min_confidence:
        return {"score": 0, "signal": signal, "confidence": confidence}

    score = int(max_score * min(confidence / 100, 1.0))

    direction = "long" if signal == "SHORT_CROWDING" else "short" if signal == "LONG_CROWDING" else None

    return {"score": score, "signal": signal, "confidence": confidence, "direction": direction,
            "fundingRate": funding_rate, "percentile": percentile}


# ═══════════════════════════════════════════════════════════════════════════
# Source 2: Liquidation Cascade Detector (LCD) — 0-25 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_lcd(asset: str, direction: str, config: dict) -> dict:
    """
    Estimate liquidation cluster proximity.
    Detect SHORT_LIQUIDATION_RISK / LONG_LIQUIDATION_RISK.
    Bonus for ACTIVE_CASCADE.
    """
    lcd_cfg = config.get("scanner", {}).get("sources", {}).get("lcd", {})
    max_score = lcd_cfg.get("maxScore", 25)
    cascade_bonus = lcd_cfg.get("cascadeBonus", 10)
    cluster_dist = lcd_cfg.get("clusterDistancePct", 2.0)

    data = mcporter_call("market_get_asset_data", {"asset": asset})
    if not data or not isinstance(data, dict):
        return {"score": 0, "signal": None, "confidence": 0}

    price = safe_float(data.get("price", data.get("markPrice", data.get("midPrice", 0))))
    oi = safe_float(data.get("openInterest", data.get("oi", 0)))
    funding = safe_float(data.get("funding", data.get("fundingRate", 0)))
    price_change_1h = safe_float(data.get("priceChange1h", data.get("price_change_1h", 0)))

    if price <= 0 or oi <= 0:
        return {"score": 0, "signal": None, "confidence": 0}

    # Estimate liquidation cluster proximity from funding skew
    # Deeply negative funding = shorts crowded = short liquidation clusters above price
    # Deeply positive funding = longs crowded = long liquidation clusters below price
    confidence = 0
    signal = None
    is_cascade = False

    if direction == "long" and funding < -0.0002:
        # Short liquidation risk — clusters above current price
        signal = "SHORT_LIQUIDATION_RISK"
        funding_magnitude = abs(funding) / 0.001  # normalize to 0-1 scale
        confidence = min(funding_magnitude * 60, 80)

        # Active cascade: price moving up sharply while shorts crowded
        if price_change_1h > cluster_dist:
            is_cascade = True
            signal = "ACTIVE_LIQUIDATION_CASCADE"
            confidence = min(confidence + 20, 100)

    elif direction == "short" and funding > 0.0002:
        signal = "LONG_LIQUIDATION_RISK"
        funding_magnitude = abs(funding) / 0.001
        confidence = min(funding_magnitude * 60, 80)

        if price_change_1h < -cluster_dist:
            is_cascade = True
            signal = "ACTIVE_LIQUIDATION_CASCADE"
            confidence = min(confidence + 20, 100)

    score = int(max_score * min(confidence / 100, 1.0))
    if is_cascade:
        score = min(score + cascade_bonus, max_score + cascade_bonus)

    return {"score": score, "signal": signal, "confidence": confidence,
            "isCascade": is_cascade, "openInterest": oi}


# ═══════════════════════════════════════════════════════════════════════════
# Source 3: Open Interest Surge Detector (OIS) — 0-20 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_ois(asset: str, direction: str, config: dict) -> dict:
    """
    Track OI changes via local snapshots.
    Detect OI_SURGE or OI_UNWIND.
    """
    ois_cfg = config.get("scanner", {}).get("sources", {}).get("ois", {})
    max_score = ois_cfg.get("maxScore", 20)
    surge_1h = ois_cfg.get("surgeThreshold1h", 1.05)
    surge_4h = ois_cfg.get("surgeThreshold4h", 1.10)

    # Get current OI
    data = mcporter_call("market_get_asset_data", {"asset": asset})
    if not data or not isinstance(data, dict):
        return {"score": 0, "signal": None, "confidence": 0}

    current_oi = safe_float(data.get("openInterest", data.get("oi", 0)))
    if current_oi <= 0:
        return {"score": 0, "signal": None, "confidence": 0}

    # Record snapshot for future use
    append_oi_snapshot(asset, current_oi)

    # Load history
    history = load_oi_history(asset)
    if len(history) < 3:
        return {"score": 0, "signal": None, "confidence": 0, "note": "Insufficient OI history"}

    oi_1h = get_oi_at(history, 1.0)
    oi_4h = get_oi_at(history, 4.0)

    signal = None
    confidence = 0

    if oi_1h and oi_4h:
        ratio_1h = current_oi / oi_1h if oi_1h > 0 else 1.0
        ratio_4h = current_oi / oi_4h if oi_4h > 0 else 1.0

        if ratio_1h >= surge_1h and ratio_4h >= surge_4h:
            signal = "OI_SURGE"
            magnitude = ((ratio_1h - 1.0) + (ratio_4h - 1.0)) / 2
            confidence = min(magnitude * 200, 100)

        elif ratio_1h <= (2.0 - surge_1h):  # e.g., <= 0.95
            signal = "OI_UNWIND"
            if direction == "long":
                signal = "OI_UNWIND_SHORT_SQUEEZE"
            else:
                signal = "OI_UNWIND_LONG_LIQUIDATION"
            magnitude = (1.0 - ratio_1h)
            confidence = min(magnitude * 200, 100)

    elif oi_1h:
        ratio_1h = current_oi / oi_1h if oi_1h > 0 else 1.0
        if ratio_1h >= surge_1h:
            signal = "OI_SURGE"
            confidence = min((ratio_1h - 1.0) * 200, 70)  # capped lower without 4h data

    score = int(max_score * min(confidence / 100, 1.0))

    return {"score": score, "signal": signal, "confidence": confidence,
            "currentOI": current_oi, "oi1h": oi_1h, "oi4h": oi_4h}


# ═══════════════════════════════════════════════════════════════════════════
# Source 4: Momentum Exhaustion Detector (MED) — -10 to +5 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_med(asset: str, direction: str, config: dict) -> dict:
    """
    Detect trend exhaustion.
    CLEAR = +5 (momentum intact), FADE = -5, EXHAUSTED = -10.
    Also outputs market regime (TRENDING/MIXED/RANGE).
    """
    med_cfg = config.get("scanner", {}).get("sources", {}).get("med", {})
    clear_bonus = med_cfg.get("clearBonus", 5)
    fade_penalty = med_cfg.get("fadePenalty", -5)
    exhaustion_penalty = med_cfg.get("exhaustionPenalty", -10)

    data = mcporter_call("market_get_asset_data", {"asset": asset})
    if not data or not isinstance(data, dict):
        return {"score": 0, "signal": "UNKNOWN", "regime": "UNKNOWN"}

    # Analyze momentum via price changes across timeframes
    price_change_1h = safe_float(data.get("priceChange1h", data.get("price_change_1h", 0)))
    price_change_4h = safe_float(data.get("priceChange4h", data.get("price_change_4h",
                                  data.get("token_price_change_pct_4h", 0))))
    price_change_24h = safe_float(data.get("priceChange24h", data.get("price_change_24h", 0)))

    # Volume trend (if available)
    vol_change = safe_float(data.get("volumeChange4h", data.get("volume_change_4h", 0)))

    # Exhaustion detection:
    # If 24h move is large but 1h move is small/opposite → trend exhausting
    # If 1h and 4h agree with 24h → trend intact
    is_long_trade = direction == "long"
    trend_24h = price_change_24h
    trend_1h = price_change_1h

    if is_long_trade:
        # For long trade: we want upward momentum intact
        if trend_24h > 2 and trend_1h < -0.5:
            signal = "EXHAUSTED"
            score = exhaustion_penalty
        elif trend_24h > 1 and trend_1h < 0:
            signal = "FADE"
            score = fade_penalty
        else:
            signal = "CLEAR"
            score = clear_bonus
    else:
        # For short trade: we want downward momentum intact
        if trend_24h < -2 and trend_1h > 0.5:
            signal = "EXHAUSTED"
            score = exhaustion_penalty
        elif trend_24h < -1 and trend_1h > 0:
            signal = "FADE"
            score = fade_penalty
        else:
            signal = "CLEAR"
            score = clear_bonus

    return {"score": score, "signal": signal}


def detect_market_regime(config: dict) -> str:
    """
    Scan multiple assets to determine market-wide regime.
    Returns: TRENDING, MIXED, or RANGE.
    """
    # Get broad market data from leaderboard
    raw = mcporter_call("leaderboard_get_markets")
    markets = []
    if isinstance(raw, dict):
        markets = raw.get("markets", raw.get("data", []))
    elif isinstance(raw, list):
        markets = raw

    if not markets:
        return "UNKNOWN"

    exhausted_count = 0
    total_checked = 0

    for m in markets[:50]:  # Check top 50 assets
        if not isinstance(m, dict):
            continue
        if str(m.get("dex", "")).lower() == "xyz":
            continue

        price_4h = safe_float(m.get("token_price_change_pct_4h", 0))
        # Simple heuristic: small 4h moves on many assets = range-bound
        if abs(price_4h) < 0.5:
            exhausted_count += 1
        total_checked += 1

    if total_checked == 0:
        return "UNKNOWN"

    exhaustion_pct = exhausted_count / total_checked
    if exhaustion_pct < 0.30:
        return "TRENDING"
    elif exhaustion_pct < 0.60:
        return "MIXED"
    else:
        return "RANGE"


# ═══════════════════════════════════════════════════════════════════════════
# Source 5: Emerging Movers (EM) — -8 to +15 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_em(asset: str, direction: str, config: dict) -> dict:
    """
    Check SM consensus from leaderboard_get_markets and leaderboard_get_top.
    """
    em_cfg = config.get("scanner", {}).get("sources", {}).get("em", {})
    max_score = em_cfg.get("maxScore", 15)
    opposing_penalty = em_cfg.get("opposingPenalty", -8)
    min_conv = em_cfg.get("minConviction", 2)
    strong_conv = em_cfg.get("strongConviction", 3)
    strong_traders = em_cfg.get("strongTraderCount", 50)

    # leaderboard_get_markets
    raw_markets = mcporter_call("leaderboard_get_markets")
    markets = []
    if isinstance(raw_markets, dict):
        markets = raw_markets.get("markets", raw_markets.get("data", []))
    elif isinstance(raw_markets, list):
        markets = raw_markets

    sm_direction = None
    sm_conviction = 0
    sm_traders = 0

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        if token != asset.upper():
            continue
        if str(m.get("dex", "")).lower() == "xyz":
            continue

        sm_direction = str(m.get("direction", "")).lower()
        contribution = safe_float(m.get("pct_of_top_traders_gain", 0))
        sm_traders = safe_int(m.get("trader_count", 0))

        # Conviction from contribution level
        if contribution >= 5.0:
            sm_conviction = 3
        elif contribution >= 2.0:
            sm_conviction = 2
        elif contribution >= 0.5:
            sm_conviction = 1
        break

    # Also check leaderboard_get_top for additional validation
    raw_top = mcporter_call("leaderboard_get_top")
    top_traders = []
    if isinstance(raw_top, dict):
        top_traders = raw_top.get("traders", raw_top.get("data", []))
    elif isinstance(raw_top, list):
        top_traders = raw_top

    top_has_asset = False
    for trader in top_traders:
        if not isinstance(trader, dict):
            continue
        top_markets = trader.get("top_markets", trader.get("topMarkets", []))
        if isinstance(top_markets, list):
            for mkt in top_markets:
                name = str(mkt.get("asset", mkt) if isinstance(mkt, dict) else mkt).upper()
                if name == asset.upper():
                    top_has_asset = True
                    break

    # Scoring
    if sm_conviction == 0:
        return {"score": 0, "signal": None, "smDirection": None, "smConviction": 0}

    same_direction = (sm_direction == direction)

    if same_direction:
        if sm_conviction >= strong_conv and sm_traders >= strong_traders:
            score = max_score  # +15
        elif sm_conviction >= min_conv:
            score = 9
        else:
            score = 0
        # Bonus for top trader confirmation
        if top_has_asset and score > 0:
            score = min(score + 2, max_score)
    else:
        if sm_conviction >= strong_conv:
            score = opposing_penalty  # -8
        else:
            score = 0

    return {"score": score, "signal": "SM_ALIGNED" if same_direction else "SM_OPPOSING",
            "smDirection": sm_direction, "smConviction": sm_conviction,
            "smTraders": sm_traders, "inTopTraders": top_has_asset}


# ═══════════════════════════════════════════════════════════════════════════
# Source 6: Opportunity Scanner (OPP) — -999 to +10 pts
# ═══════════════════════════════════════════════════════════════════════════

def source_opp(asset: str, direction: str, config: dict) -> dict:
    """
    Multi-pillar scoring with hourly trend alignment.
    Counter-trend = hard skip (-999).
    """
    opp_cfg = config.get("scanner", {}).get("sources", {}).get("opp", {})
    max_score = opp_cfg.get("maxScore", 10)
    counter_penalty = opp_cfg.get("counterTrendPenalty", -999)

    data = mcporter_call("market_get_asset_data", {"asset": asset})
    if not data or not isinstance(data, dict):
        return {"score": 0, "signal": None}

    price_change_1h = safe_float(data.get("priceChange1h", data.get("price_change_1h", 0)))

    # Hourly trend alignment check
    is_long = direction == "long"
    if is_long and price_change_1h < -1.0:
        return {"score": counter_penalty, "signal": "COUNTER_TREND",
                "note": f"Long entry but 1h change = {price_change_1h:.2f}%"}
    if not is_long and price_change_1h > 1.0:
        return {"score": counter_penalty, "signal": "COUNTER_TREND",
                "note": f"Short entry but 1h change = {price_change_1h:.2f}%"}

    # Multi-pillar scoring
    score = 0

    # Price alignment (0-4)
    if is_long and price_change_1h > 0.5:
        score += min(int(price_change_1h * 2), 4)
    elif not is_long and price_change_1h < -0.5:
        score += min(int(abs(price_change_1h) * 2), 4)

    # Volume confirmation (0-3)
    vol_change = safe_float(data.get("volumeChange1h", data.get("volume_change_1h", 0)))
    if vol_change > 20:
        score += 3
    elif vol_change > 10:
        score += 2
    elif vol_change > 5:
        score += 1

    # Spread quality (0-3)
    bid = safe_float(data.get("bid", data.get("bestBid", 0)))
    ask = safe_float(data.get("ask", data.get("bestAsk", 0)))
    price = safe_float(data.get("price", data.get("markPrice", 0)))
    if bid > 0 and ask > 0 and price > 0:
        spread_pct = (ask - bid) / price * 100
        if spread_pct <= 0.05:
            score += 3
        elif spread_pct <= 0.1:
            score += 2
        elif spread_pct <= 0.2:
            score += 1

    score = min(score, max_score)
    return {"score": score, "signal": "CONFIRMED" if score >= 5 else "WEAK"}


# ═══════════════════════════════════════════════════════════════════════════
# Candidate Discovery
# ═══════════════════════════════════════════════════════════════════════════

def discover_candidates(config: dict) -> list:
    """
    Get candidate assets from leaderboard_get_markets.
    Filter out xyz: assets. Returns list of asset names.
    """
    raw = mcporter_call("leaderboard_get_markets")
    markets = []
    if isinstance(raw, dict):
        markets = raw.get("markets", raw.get("data", []))
    elif isinstance(raw, list):
        markets = raw

    candidates = []
    seen = set()
    for m in markets:
        if not isinstance(m, dict):
            continue
        dex = str(m.get("dex", "")).lower()
        if dex == "xyz":
            continue
        token = str(m.get("token", m.get("asset", m.get("coin", "")))).upper()
        if not token or token in seen:
            continue
        seen.add(token)
        candidates.append(token)

    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# Conviction Tier Resolution
# ═══════════════════════════════════════════════════════════════════════════

def resolve_tier(score: int, config: dict) -> str | None:
    """Map score to conviction tier. Returns None if below threshold."""
    tiers = config.get("convictionTiers", {})
    if score >= tiers.get("HIGH", {}).get("minScore", 75):
        return "HIGH"
    elif score >= tiers.get("MEDIUM", {}).get("minScore", 55):
        return "MEDIUM"
    elif score >= tiers.get("LOW", {}).get("minScore", 40):
        return "LOW"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Sizing
# ═══════════════════════════════════════════════════════════════════════════

def calculate_sizing(score: int, tier: str, asset: str, wallet_balance: float,
                     config: dict) -> dict:
    """Calculate margin and leverage for a position."""
    sizing_cfg = config.get("sizing", {})
    tier_sizing = sizing_cfg.get("tiers", {}).get(tier, {})

    base_size_pct = sizing_cfg.get("baseSizePct", 0.15)
    margin_mult = tier_sizing.get("marginMultiplier", 0.60)
    lev_range_low = tier_sizing.get("leverageRangeLow", 0.50)
    lev_range_high = tier_sizing.get("leverageRangeHigh", 0.65)
    leverage_cap = sizing_cfg.get("leverageCap", 10)
    max_per_asset = sizing_cfg.get("maxPerAssetPct", 0.25)

    # Base margin
    margin = wallet_balance * base_size_pct * margin_mult

    # Cap per-asset
    margin = min(margin, wallet_balance * max_per_asset)

    margin_pct = (margin / wallet_balance * 100) if wallet_balance > 0 else 0

    # Leverage: lerp within tier range based on intra-tier position
    tier_cfg = config.get("convictionTiers", {}).get(tier, {})
    tier_min = tier_cfg.get("minScore", 55)
    tier_max = tier_cfg.get("maxScore", 74)
    intra_fraction = (score - tier_min) / max(tier_max - tier_min, 1)
    intra_fraction = max(0, min(1, intra_fraction))

    # Get asset max leverage
    asset_max_lev = get_asset_max_leverage(asset)
    lev_pct = lev_range_low + (lev_range_high - lev_range_low) * intra_fraction
    leverage = asset_max_lev * lev_pct

    # Cap
    leverage = min(leverage, leverage_cap)
    leverage = max(1, round(leverage, 1))

    return {
        "margin": round(margin, 2),
        "marginPercent": round(margin_pct, 2),
        "leverage": leverage,
        "assetMaxLeverage": asset_max_lev,
    }


def get_asset_max_leverage(asset: str) -> float:
    """Get max leverage for an asset from exchange. Cached in leaderboard data."""
    raw = mcporter_call("leaderboard_get_markets")
    markets = []
    if isinstance(raw, dict):
        markets = raw.get("markets", raw.get("data", []))
    elif isinstance(raw, list):
        markets = raw

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        if token == asset.upper():
            return safe_float(m.get("max_leverage", m.get("maxLeverage", 20)))

    return 20.0  # default


# ═══════════════════════════════════════════════════════════════════════════
# Leverage-Adjusted Floor
# ═══════════════════════════════════════════════════════════════════════════

def calculate_leverage_floor(leverage: float, tier: str, config: dict) -> float:
    """
    Calculate leverage-adjusted absolute floor.
    Higher leverage = tighter floor to limit dollar losses.
    """
    dsl_cfg = config.get("dsl", {})
    tier_dsl = dsl_cfg.get("tiers", {}).get(tier, {})
    floor_cfg = dsl_cfg.get("leverageFloor", {})

    price_move = floor_cfg.get("priceMoveLimit5x", 1.0) if leverage >= 5 else \
                 floor_cfg.get("priceMoveLimitBelow5x", 1.5)
    min_floor = floor_cfg.get("minFloor", -3.0)

    leverage_floor = -(price_move * leverage)

    # The conviction-tier floor from config (e.g., MEDIUM phase1 has its own floor)
    # We don't use a static conviction floor here — it comes from the conviction tiers
    # in the handoff spec (-20, -25, -30), but those are wide. The leverage-adjusted
    # floor is typically tighter and acts as the binding constraint.

    effective_floor = max(leverage_floor, -30.0)  # Never wider than -30%
    effective_floor = min(effective_floor, min_floor)  # Never tighter than -3%

    return round(effective_floor, 1)


# ═══════════════════════════════════════════════════════════════════════════
# DSL State Builder (v1.1.1)
# ═══════════════════════════════════════════════════════════════════════════

def build_dsl_state(asset: str, direction: str, score: int, tier: str,
                    leverage: float, config: dict) -> dict:
    """Build COMPLETE DSL state. No dsl-profile.json merging."""
    dsl_cfg = config.get("dsl", {})
    tier_dsl = dsl_cfg.get("tiers", {}).get(tier, {})

    timeout = tier_dsl.get("phase1MaxMinutes", 120)
    weak_peak = tier_dsl.get("weakPeakCutMinutes", 60)
    dead_weight = tier_dsl.get("deadWeightCutMin", 45)
    floor_roe = calculate_leverage_floor(leverage, tier, config)

    state = {
        "active": True,
        "asset": asset,
        "direction": direction,
        "score": score,
        "phase": 1,
        "highWaterPrice": None,
        "highWaterRoe": None,
        "currentTierIndex": -1,
        "consecutiveBreaches": 0,
        "lockMode": dsl_cfg.get("lockMode", "pct_of_high_water"),
        "phase2TriggerRoe": dsl_cfg.get("phase2TriggerRoe", 5),
        "phase1": {
            "enabled": True,
            "retraceThreshold": dsl_cfg.get("phase1RetraceThreshold", 0.03),
            "consecutiveBreachesRequired": 3,
            "phase1MaxMinutes": timeout,
            "weakPeakCutMinutes": weak_peak,
            "deadWeightCutMin": dead_weight,
            "absoluteFloorRoe": floor_roe,
            "weakPeakCut": {
                "enabled": True,
                "intervalInMinutes": weak_peak,
                "minValue": 3.0,
            },
        },
        "phase2": {
            "enabled": True,
            "retraceThreshold": dsl_cfg.get("phase2RetraceThreshold", 0.015),
            "consecutiveBreachesRequired": dsl_cfg.get("phase2ConsecutiveBreaches", 2),
        },
        "tiers": dsl_cfg.get("trailingTiers", [
            {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
            {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
            {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
            {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
        ]),
        "stagnationTp": dsl_cfg.get("stagnationTp", {"enabled": True, "roeMin": 10, "hwStaleMin": 45}),
        "execution": dsl_cfg.get("execution", {
            "phase1SlOrderType": "MARKET",
            "phase2SlOrderType": "MARKET",
            "breachCloseOrderType": "MARKET",
        }),
    }

    return state


# ═══════════════════════════════════════════════════════════════════════════
# Main Scanner
# ═══════════════════════════════════════════════════════════════════════════

def run():
    log("=" * 60)
    log(f"Scanner run — {datetime.now(timezone.utc).isoformat()}")
    log("=" * 60)

    config = load_config()
    runtime = load_runtime()

    # ── Gate check ───────────────────────────────────────────────────────
    gate_open, gate_reason = check_gate(runtime, config)
    save_runtime(runtime)
    if not gate_open:
        log(f"Gate: {gate_reason}")
        output(no_reply(f"Gate: {gate_reason}"))
        return

    # ── Position check ───────────────────────────────────────────────────
    sizing_cfg = config.get("sizing", {})
    max_positions = sizing_cfg.get("maxPositions", 3)
    positions = get_positions()
    active_coins = set()
    for p in positions:
        coin = str(p.get("coin", p.get("asset", ""))).upper()
        active_coins.add(coin)

    if len(positions) >= max_positions:
        log(f"Max positions ({len(positions)}/{max_positions}). NO_REPLY.")
        output(no_reply(f"Max positions reached: {len(positions)}/{max_positions}"))
        return

    # ── Deployment cap ───────────────────────────────────────────────────
    wallet_balance = get_wallet_balance()
    if wallet_balance <= 0:
        log("Cannot read wallet balance. NO_REPLY.")
        output(no_reply("Wallet balance unavailable"))
        return

    deployed = get_deployed_margin(positions)
    max_deployed_pct = sizing_cfg.get("maxDeployedPct", 0.55)
    if deployed / wallet_balance >= max_deployed_pct:
        log(f"Deployment cap reached ({deployed:.2f}/{wallet_balance * max_deployed_pct:.2f}). NO_REPLY.")
        output(no_reply("Deployment cap reached"))
        return

    # ── Market regime ────────────────────────────────────────────────────
    regime = detect_market_regime(config)
    log(f"Market regime: {regime}")

    # ── Discover candidates ──────────────────────────────────────────────
    candidates = discover_candidates(config)
    log(f"Discovered {len(candidates)} non-xyz candidates.")

    if not candidates:
        output(no_reply("No candidates from leaderboard"))
        return

    # ── Score each candidate ─────────────────────────────────────────────
    best_signal = None
    best_score = -999

    for asset in candidates:
        # Skip if already positioned
        if asset.upper() in active_coins:
            continue
        # Skip if on cooldown
        if is_on_cooldown(asset):
            continue
        # Skip xyz (double-check)
        if is_xyz(asset):
            continue

        # Source 1: FDD (primary gate)
        fdd = source_fdd(asset, config)
        if not fdd.get("signal") or fdd["score"] == 0:
            continue  # No FDD signal = no trade

        direction = fdd.get("direction")
        if not direction:
            continue

        log(f"  {asset}: FDD={fdd['score']} ({fdd['signal']}, conf={fdd.get('confidence', 0)})")

        # Source 2: LCD
        lcd = source_lcd(asset, direction, config)

        # Source 3: OIS
        ois = source_ois(asset, direction, config)

        # Source 4: MED
        med = source_med(asset, direction, config)

        # Source 5: EM
        em = source_em(asset, direction, config)

        # Source 6: OPP
        opp = source_opp(asset, direction, config)

        # Total score
        total = fdd["score"] + lcd["score"] + ois["score"] + med["score"] + em["score"] + opp["score"]

        breakdown = {
            "fdd": fdd["score"], "lcd": lcd["score"], "ois": ois["score"],
            "med": med["score"], "em": em["score"], "opp": opp["score"],
        }

        log(f"  {asset}: Total={total} "
            f"(fdd={fdd['score']}, lcd={lcd['score']}, ois={ois['score']}, "
            f"med={med['score']}, em={em['score']}, opp={opp['score']})")

        # OPP counter-trend hard skip
        if opp["score"] <= -999:
            log(f"  {asset}: OPP counter-trend. SKIP.")
            continue

        # Resolve tier
        tier = resolve_tier(total, config)
        if not tier:
            continue

        # Check tier enabled (config + self-learning)
        if not is_tier_enabled(tier, runtime, config):
            log(f"  {asset}: Tier {tier} disabled. SKIP.")
            continue

        # Regime filter
        if regime == "MIXED" and tier not in ("MEDIUM", "HIGH"):
            continue
        if regime == "RANGE" and tier != "HIGH":
            continue

        # Track best
        if total > best_score:
            best_score = total
            best_signal = {
                "asset": asset,
                "direction": direction,
                "score": total,
                "tier": tier,
                "breakdown": breakdown,
                "fddSignal": fdd.get("signal"),
                "fddConfidence": fdd.get("confidence", 0),
                "regime": regime,
                "sources": {
                    "fdd": fdd, "lcd": lcd, "ois": ois,
                    "med": med, "em": em, "opp": opp,
                },
            }

    if not best_signal:
        output(no_reply("No candidates reached conviction threshold"))
        return

    # ── Build entry ──────────────────────────────────────────────────────
    asset = best_signal["asset"]
    direction = best_signal["direction"]
    score = best_signal["score"]
    tier = best_signal["tier"]

    # Stack guard: verify no on-chain position for this asset
    if asset.upper() in active_coins:
        log(f"Stack guard: {asset} already has position. NO_REPLY.")
        output(no_reply(f"Stack guard: {asset} already positioned"))
        return

    sizing = calculate_sizing(score, tier, asset, wallet_balance, config)
    leverage = sizing["leverage"]
    margin_pct = sizing["marginPercent"]

    dsl_state = build_dsl_state(asset, direction, score, tier, leverage, config)

    log(f"*** SIGNAL: {direction.upper()} {asset} "
        f"(score={score}, tier={tier}, lev={leverage}x, margin={margin_pct}%) ***")

    # Update runtime
    runtime["entriesThisDay"] += 1
    save_runtime(runtime)

    result = {
        "status": "ok",
        "signal": {
            "asset": asset,
            "direction": direction,
            "score": score,
            "tier": tier,
            "breakdown": best_signal["breakdown"],
            "fddSignal": best_signal["fddSignal"],
            "fddConfidence": best_signal["fddConfidence"],
            "regime": regime,
        },
        "entry": {
            "asset": asset,
            "direction": direction,
            "leverage": leverage,
            "marginPercent": margin_pct,
            "marginUsd": sizing["margin"],
            "orderType": config.get("sizing", {}).get("orderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "dslState": dsl_state,
        "constraints": {
            "maxPositions": max_positions,
            "cooldownMinutes": config.get("risk", {}).get("cooldownMinutes", 120),
            "xyzBanned": True,
        },
    }

    output(result)


# ═══════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        output({"status": "error", "error": str(e)})
        sys.exit(1)
