"""Senpi Hyperfeed Scoring Helpers v1.0

Universal scoring functions for the new Hyperfeed fields shipped 2026-04-06:
- contribution_pct_change_15m / 1h (leaderboard_get_markets)
- trader_tags {tcs, tas} (leaderboard_get_top)
- account_value on trader positions (leaderboard_get_trader_positions)

Import into any scanner:
    from hyperfeed_scoring import score_contribution_velocity, score_trader_quality, score_position_concentration
"""


def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# CONTRIBUTION VELOCITY — 15m / 1h / 4h
# ═══════════════════════════════════════════════════════════════

def score_contribution_velocity(market_data, direction):
    """Score contribution velocity using 15m, 1h, and 4h windows.

    The 15m window is the entry timing signal — detects the INFLECTION POINT
    when SM interest starts spiking. The 4h window confirms the trend.

    Returns (points, reasons) where points is -1 to +4.

    Scoring:
      +2: 15m acceleration (15m change > 0 in direction = SM building RIGHT NOW)
      +1: 1h confirms (1h change > 0 in direction)
      +1: 4h strong (4h change > 3% absolute = major shift)
      -1: 15m deceleration (15m change against direction = SM pulling out)

    Args:
        market_data: dict from leaderboard_get_markets for one asset
        direction: "LONG" or "SHORT" (or "long"/"short")
    """
    d = direction.upper()
    cc_15m = safe_float(market_data.get("contribution_pct_change_15m"))
    cc_1h = safe_float(market_data.get("contribution_pct_change_1h"))
    cc_4h = safe_float(market_data.get("contribution_pct_change_4h"))

    score = 0
    reasons = []

    # 15-minute contribution acceleration — the entry timing signal
    if cc_15m > 0.5:
        score += 2
        reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1:
        score += 1
        reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
    elif cc_15m < -0.5:
        score -= 1
        reasons.append(f"15M_FADING {cc_15m:.2f}")

    # 1-hour contribution trend
    if cc_1h > 1.0:
        score += 1
        reasons.append(f"1H_ACCEL +{cc_1h:.2f}")
    elif cc_1h < -1.0:
        # Don't penalize again if 15m already penalized
        if cc_15m >= -0.5:
            score -= 1
            reasons.append(f"1H_DECLINING {cc_1h:.2f}")

    # 4-hour major contribution shift
    if abs(cc_4h) >= 5.0:
        score += 1
        reasons.append(f"4H_MAJOR_SHIFT {cc_4h:+.1f}")

    # Acceleration pattern detection: 15m > 1h > 0 = accelerating inflow
    if cc_15m > 0 and cc_1h > 0 and cc_15m > cc_1h:
        if "15M_SPIKE" not in str(reasons) and "15M_BUILDING" not in str(reasons):
            score += 1
            reasons.append(f"ACCEL_PATTERN 15m({cc_15m:.2f})>1h({cc_1h:.2f})")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# TRADER QUALITY — TCS / TAS from leaderboard_get_top
# ═══════════════════════════════════════════════════════════════

# TCS hierarchy: ELITE > RELIABLE > STREAKY > CHOPPY
# TAS categories: Patient, Tactical, Active, Degen
TCS_SCORES = {
    "ELITE": 3,
    "RELIABLE": 2,
    "STREAKY": 1,
    "CHOPPY": 0,
}

TAS_SCORES = {
    "Patient": 2,     # Fewer trades, higher conviction
    "Tactical": 1,    # Moderate frequency
    "Active": 0,      # High frequency
    "Degen": -1,      # Very high frequency, noise
}


def score_trader_quality(trader_tags):
    """Score a trader's quality from TCS/TAS tags.

    Returns (points, reasons) where points is -1 to +5.

    Args:
        trader_tags: dict with {tcs, tas} from leaderboard_get_top
    """
    if not trader_tags or not isinstance(trader_tags, dict):
        return 0, []

    score = 0
    reasons = []

    tcs = str(trader_tags.get("tcs", "")).upper()
    tas = str(trader_tags.get("tas", ""))

    if tcs in TCS_SCORES:
        pts = TCS_SCORES[tcs]
        score += pts
        if pts >= 2:
            reasons.append(f"TCS_{tcs}")

    if tas in TAS_SCORES:
        pts = TAS_SCORES[tas]
        score += pts
        if pts >= 1:
            reasons.append(f"TAS_{tas}")
        elif pts < 0:
            reasons.append(f"TAS_DEGEN")

    return score, reasons


def is_quality_trader(trader_tags, min_tcs="RELIABLE"):
    """Quick check: is this trader at or above a TCS threshold?

    Args:
        trader_tags: dict with {tcs, tas}
        min_tcs: minimum TCS level ("ELITE", "RELIABLE", "STREAKY")
    Returns True if trader meets threshold.
    """
    if not trader_tags:
        return False
    tcs = str(trader_tags.get("tcs", "")).upper()
    hierarchy = ["CHOPPY", "STREAKY", "RELIABLE", "ELITE"]
    try:
        trader_level = hierarchy.index(tcs)
        required_level = hierarchy.index(min_tcs.upper())
        return trader_level >= required_level
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════════════
# POSITION CONCENTRATION — account_value from trader positions
# ═══════════════════════════════════════════════════════════════

def score_position_concentration(position_data):
    """Score a trader's position concentration (conviction proxy).

    A trader putting 80% of their capital in one trade = max conviction.
    A trader with 2% in each of 50 positions = low conviction.

    Returns (points, reasons, concentration_pct) where points is 0-2.

    Args:
        position_data: dict from leaderboard_get_trader_positions
            Must have: notional_size, account_value
    """
    notional = safe_float(position_data.get("notional_size", 0))
    account_value = safe_float(position_data.get("account_value", 0))

    if account_value <= 0 or notional <= 0:
        return 0, [], 0

    concentration = (notional / account_value) * 100  # as percentage

    score = 0
    reasons = []

    if concentration >= 50:
        score += 2
        reasons.append(f"MAX_CONVICTION {concentration:.0f}% of portfolio")
    elif concentration >= 25:
        score += 1
        reasons.append(f"HIGH_CONVICTION {concentration:.0f}% of portfolio")
    elif concentration < 5:
        score -= 1
        reasons.append(f"LOW_CONVICTION {concentration:.1f}% of portfolio")

    return score, reasons, concentration


# ═══════════════════════════════════════════════════════════════
# COMBINED SCORING — drop-in for any scanner
# ═══════════════════════════════════════════════════════════════

def score_market_enriched(market_data, direction):
    """Combined scoring using all new Hyperfeed fields on a market entry.

    Drop-in addition to any scanner's scoring function.
    Call after your existing SM/price scoring and ADD the result.

    Returns (total_points, all_reasons).

    Example usage in a scanner:
        # ... existing scoring ...
        enriched_pts, enriched_reasons = score_market_enriched(btc_data, direction)
        score += enriched_pts
        reasons.extend(enriched_reasons)
    """
    vel_pts, vel_reasons = score_contribution_velocity(market_data, direction)
    return vel_pts, vel_reasons
