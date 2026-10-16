"""CONDOR CONCENTRATED — pure thesis math (no I/O, no MCP, no clock).

Forked from Condor v1.4.1 scoring.py. Changes:
  - 24h price change added as a scoring factor (4h<->24h alignment)
  - 1h<->4h strength alignment scoring added
  - Smart money replaced: proven cohort conviction (headcount-based) replaces
    the 4h leaderboard gain-share scoring
  - Signal direction is determined by the 3TF price changes, confirmed by the
    proven cohort (was: direction came from the leaderboard's dominant side)
  - XYZ assets no longer banned from scoring

WHY HEADCOUNT, NOT GAIN SHARE. condor reads `leaderboard_get_markets`, whose own
docstring concedes it is "a momentum read of who is winning right now, not a proven
cohort" — and it is circular: if a coin falls, every wallet short it is mechanically
at the top of the 4h board, so the board agrees with the move it is supposed to
confirm. The proven cohort is the ~150 wallets with >= $1M LIFETIME realized PnL, and
direction comes from how many of them hold long vs short. Price cannot change a
headcount, so the confirmation is independent of the move being scored.

Unit-testable on plain dicts. The caller (scan.py) owns the clock and the MCP
reads; the UTC hour is passed in so this module stays pure.
"""


def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000
STABLECOINS_BANNED = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "BUSD"}

# 3TF alignment thresholds
MIN_4H_MAGNITUDE = 1.0
MIN_1H_MAGNITUDE = 0.3
MIN_15M_VELOCITY = 0.1            # v2-quirk: same sign for SHORT (c15m >= +floor)

# Macro trend gate
MACRO_GATE_THRESHOLD_PCT = 10.0

# Proven cohort gates
MIN_POSITIONED_COUNT = 10
MIN_COHORT_CONVICTION_PCT = 60.0
STRONGLY_TILTED_CONVICTION_PCT = 80.0
CONVERGENT_CONVICTION_PCT = 70.0
DEEP_CONSENSUS_COUNT = 50

# Score floor
MIN_SCORE = 13

# Sizing — flat 80% for all scores above floor
MAX_LEVERAGE = 10
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 10, "margin_pct": 80},
]

# 24h alignment scoring
ALIGN_24H_STRONG_PCT = 3.0        # |24h| >= 3% for +3

# 1h<->4h strength alignment scoring
ALIGN_1H_STRONG_RATIO = 0.5       # |1h| >= 0.5 x |4h| for +2
ALIGN_1H_MODERATE_RATIO = 0.25    # |1h| >= 0.25 x |4h| for +1


def get_sizing_for_score(score, tiers=None):
    """Returns (leverage, margin_pct) for the score tier. Flat 80% by default."""
    for tier in (tiers or LEVERAGE_TIERS):
        if score >= tier["min_score"]:
            return tier["leverage"], tier["margin_pct"]
    return 10, 80


# ═══════════════════════════════════════════════════════════════
# SIGNAL EVALUATION
# ═══════════════════════════════════════════════════════════════

def evaluate_trend_continuation(asset_info, price_data, cohort, btc_macro, hour, inputs=None):
    """Score an asset for trend-continuation with proven-cohort confirmation.

    `asset_info`  = {coin, oi_usd, volume_24h, price, funding, p24h}
    `price_data`  = {p4h, p1h, c15m} — from leaderboard_get_markets (market data only)
    `cohort`      = {direction, long_count, short_count, total_positioned, conviction_pct}
                    or None if no proven traders are positioned in this coin
    `btc_macro`   = {direction, p4h} or None
    `hour`        = current UTC hour (caller owns the clock)

    Signal direction is determined by the 3TF price changes and confirmed by the
    proven cohort. Returns a scored signal dict, or None if any hard gate fails.
    """
    inputs = inputs or {}
    min_score = float(inputs.get("minScore", MIN_SCORE))

    # 3TF alignment thresholds
    min_4h = float(inputs.get("min4hMagnitude", MIN_4H_MAGNITUDE))
    min_1h = float(inputs.get("min1hMagnitude", MIN_1H_MAGNITUDE))
    min_15m = float(inputs.get("min15mVelocity", MIN_15M_VELOCITY))

    # Macro gate
    macro_gate = float(inputs.get("macroGateThresholdPct", MACRO_GATE_THRESHOLD_PCT))

    # Proven cohort gates
    min_positioned = int(inputs.get("minPositionedCount", MIN_POSITIONED_COUNT))
    min_cohort_conviction = float(inputs.get("minCohortConvictionPct", MIN_COHORT_CONVICTION_PCT))
    strongly_tilted_conv = float(inputs.get("stronglyTiltedConvictionPct",
                                            STRONGLY_TILTED_CONVICTION_PCT))
    convergent_conv = float(inputs.get("convergentConvictionPct", CONVERGENT_CONVICTION_PCT))
    deep_consensus = int(inputs.get("deepConsensusCount", DEEP_CONSENSUS_COUNT))

    # 24h alignment threshold
    align_24h_strong = float(inputs.get("align24hStrongPct", ALIGN_24H_STRONG_PCT))

    # 1h<->4h strength ratios
    align_1h_strong = float(inputs.get("align1hStrongRatio", ALIGN_1H_STRONG_RATIO))
    align_1h_moderate = float(inputs.get("align1hModerateRatio", ALIGN_1H_MODERATE_RATIO))

    coin = asset_info["coin"]

    # ─── Extract price changes ───
    p4h = _f(price_data.get("p4h", 0))
    p1h = _f(price_data.get("p1h", 0))
    c15m = _f(price_data.get("c15m", 0))
    p24h = _f(asset_info.get("p24h", 0))

    # ─── HARD GATE: 3TF alignment — determines signal direction ───
    if p4h >= min_4h and p1h >= min_1h and c15m >= min_15m:
        signal_dir = "LONG"
    elif p4h <= -min_4h and p1h <= -min_1h and c15m >= min_15m:
        signal_dir = "SHORT"   # v2-quirk: velocity floor same sign for SHORT
    else:
        return None

    # ─── HARD GATE: MACRO TREND (Wolverine's lesson) ───
    if signal_dir == "LONG" and p4h < -macro_gate:
        return None
    if signal_dir == "SHORT" and p4h > macro_gate:
        return None

    # ─── HARD GATE: proven cohort confirmation ───
    if not cohort:
        return None
    if cohort.get("total_positioned", 0) < min_positioned:
        return None
    if cohort.get("direction", "") != signal_dir:
        return None
    conviction = _f(cohort.get("conviction_pct", 0))
    if conviction < min_cohort_conviction:
        return None

    # ─── SCORING ───
    score = 0
    reasons = []

    # 4h magnitude
    p4h_abs = abs(p4h)
    if p4h_abs >= 6.0:
        score += 4
        reasons.append(f"4H_MOMENTUM_STRONG {p4h:+.1f}%")
    elif p4h_abs >= 4.0:
        score += 3
        reasons.append(f"4H_MOMENTUM {p4h:+.1f}%")
    elif p4h_abs >= 2.0:
        score += 2
        reasons.append(f"4H_TREND_BUILDING {p4h:+.1f}%")
    else:
        score += 1
        reasons.append(f"4H_TREND_LIGHT {p4h:+.1f}%")

    # 1h confirmation
    p1h_abs = abs(p1h)
    if p1h_abs >= 1.0:
        score += 2
        reasons.append(f"1H_STRONG {p1h:+.2f}%")
    elif p1h_abs >= 0.5:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # 15m velocity
    if c15m >= 2.0:
        score += 2
        reasons.append(f"15M_SPIKE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # 3TF alignment bonus
    score += 3
    reasons.append(f"3TF_ALIGNED_{signal_dir}")

    # ─── 4h<->24h alignment (NEW) ───
    p24h_abs = abs(p24h)
    same_dir_24h = (
        (signal_dir == "LONG" and p24h > 0) or
        (signal_dir == "SHORT" and p24h < 0)
    )
    if same_dir_24h and p24h_abs >= align_24h_strong:
        score += 3
        reasons.append(f"24H_ALIGNED_STRONG {p24h:+.1f}%")
    elif same_dir_24h:
        score += 2
        reasons.append(f"24H_ALIGNED {p24h:+.1f}%")
    # else: 0 — the higher timeframe disagrees, or is flat

    # ─── 1h<->4h strength alignment (NEW) ───
    if p4h_abs > 0:
        ratio = p1h_abs / p4h_abs
        if ratio >= align_1h_strong:
            score += 2
            reasons.append(f"1H_4H_STRONG_ALIGN ratio={ratio:.2f}")
        elif ratio >= align_1h_moderate:
            score += 1
            reasons.append(f"1H_4H_ALIGN ratio={ratio:.2f}")

    # Proven-cohort conviction (replaces the board's gain share)
    if conviction >= strongly_tilted_conv:
        score += 4
        reasons.append(f"COHORT_STRONGLY_TILTED {conviction:.0f}%")
    elif conviction >= convergent_conv:
        score += 3
        reasons.append(f"COHORT_CONVERGENT {conviction:.0f}%")
    else:
        score += 2
        reasons.append(f"COHORT_ALIGNED {conviction:.0f}%")

    # Cohort depth
    total_pos = int(cohort.get("total_positioned", 0))
    if total_pos >= deep_consensus:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({total_pos}t)")

    # Funding alignment
    funding = asset_info.get("funding", 0)
    if (signal_dir == "SHORT" and funding > 0.0002) or (signal_dir == "LONG" and funding < -0.0002):
        score += 1
        reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # BTC macro confirmation bonus
    if btc_macro and coin != "BTC":
        if btc_macro["direction"] == signal_dir and abs(btc_macro["p4h"]) >= 1.5:
            score += 1
            reasons.append(f"BTC_CONFIRMS {btc_macro['p4h']:+.1f}%")

    # Peak session bonus (13-19 UTC or 00-05 UTC)
    if (13 <= hour <= 19) or (0 <= hour <= 5):
        score += 1
        reasons.append(f"PEAK_SESSION_{hour:02d}UTC")

    if score < min_score:
        return None

    return {
        "coin": coin,
        "direction": signal_dir,
        "score": score,
        "reasons": reasons,
        "p4h": p4h,
        "p1h": p1h,
        "c15m": c15m,
        "p24h": p24h,
        "cohort_conviction": conviction,
        "cohort_positioned": total_pos,
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }
