"""CONDOR — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 CONDOR producer's
`evaluate_trend_continuation` + multi-factor scoring (SKILL.md v4.0.1,
"One Amazing Trade per Day"). The hard gates, scoring tables, and
sizing tiers are reproduced VERBATIM so a fidelity harness can diff
this against the v2 producer on the same market snapshot. Behaviour-
preserving v2 quirks are kept and flagged `# v2-quirk`; fix them only
as a separate, labelled change AFTER the port is validated.

Multi-asset universe, single-pass, unit-testable on plain dicts. The
caller (scan.py) owns the clock and the MCP reads; `peak_session`
takes the UTC hour as an argument so this module stays pure."""

# ── numeric coercion (matches v2 safe_float) ──


def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2 v3.4/v4.0.1 producer
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000
MIN_TRADER_COUNT = 50
STABLECOINS_BANNED = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "BUSD"}

# 3TF alignment thresholds
MIN_4H_MAGNITUDE = 1.0
MIN_1H_MAGNITUDE = 0.3
MIN_15M_VELOCITY = 0.1            # v2: v3.4 calibration (was 0.2 in v3.2)

# MACRO TREND GATE — Wolverine's lesson (no fighting runaway trends)
MACRO_GATE_THRESHOLD_PCT = 10.0

# PROVEN-COHORT GATES (replaced the 4h gain board, 2026-10-12)
#
# condor used to take its DIRECTION from `leaderboard_get_markets` — the 4h
# rolling gain board. That read is CIRCULAR: when a coin falls, every wallet
# short it is mechanically at the top of the 4h board, so the board "confirms"
# the very move it is being asked to confirm. The module's own docstring
# conceded it was "a momentum read of who is winning right now, not a proven
# cohort".
#
# Direction now comes from the 3TF price action, and confirmation comes from
# HEADCOUNT among the proven cohort: the wallets with >= $1M LIFETIME realized
# PnL, which is how senpi-signals defines smart money. Price cannot change how
# many wallets hold a side, so the confirmation is independent of the move.
#
# The old gain-share thresholds are gone with it. They were never backtested —
# derived from ONE live snapshot (2026-08-13, 271 markets) and the file said so.
MIN_POSITIONED_COUNT = 10          # minimum proven wallets positioned in a coin
MIN_COHORT_CONVICTION_PCT = 60.0   # the dominant side must hold >= this share
STRONGLY_TILTED_CONVICTION_PCT = 80.0   # top-tier conviction  (+4)
CONVERGENT_CONVICTION_PCT = 70.0        # mid-tier conviction  (+3)
DEEP_CONSENSUS_COUNT = 50               # cohort depth bonus   (+1)

# Score floor
MIN_SCORE = 12                    # v2: v3.4 calibration

# Sizing tiers (Kodiak empirical 10x cap).
# marginPct here is a PERCENT of withdrawable in (0,100] — the Runtime 3.0
# wire convention — NOT the v2 fraction (0.80). v2 emitted marginUsd =
# account_value * fraction; the runtime now sizes (marginPct/100)*withdrawable.
MAX_LEVERAGE = 10
LEVERAGE_TIERS = [
    {"min_score": 15, "leverage": 10, "margin_pct": 80},   # APEX
    {"min_score": 13, "leverage": 10, "margin_pct": 70},   # HIGH
    {"min_score": 11, "leverage": 10, "margin_pct": 50},   # base
]


def get_sizing_for_score(score, tiers=None):
    """Returns (leverage, margin_pct) for the score tier. margin_pct is a
    PERCENT in (0,100]. Ported verbatim from v2 get_sizing_for_score; the
    fallback (10x, 50%) matches v2."""
    for tier in (tiers or LEVERAGE_TIERS):
        if score >= tier["min_score"]:
            return tier["leverage"], tier["margin_pct"]
    return 10, 50


# ═══════════════════════════════════════════════════════════════
# SIGNAL EVALUATION — preserved verbatim from v2 v3.4
# ═══════════════════════════════════════════════════════════════

def evaluate_trend_continuation(asset_info, price_data, cohort, btc_macro, hour, inputs=None):
    """Score an asset for a trend-continuation apex setup.

    `asset_info` = {coin, oi_usd, volume_24h, price, funding}
    `price_data` = {p4h, p1h, c15m, c1h} — market data from leaderboard_get_markets
    `cohort`     = {direction, long_count, short_count, total_positioned,
                    conviction_pct}, or None if no proven wallet holds this coin
    `btc_macro`  = {direction, p4h} or None
    `hour`       = current UTC hour (caller owns the clock — keeps this pure)

    Direction is set by the 3TF price action and CONFIRMED by the proven cohort;
    the cohort can only veto, never originate. Returns a scored signal dict, or
    None if any hard gate fails. Scoring tables and bonus magnitudes are verbatim
    from v2 apart from the concentration tier, which now reads cohort conviction
    instead of the board's gain share."""
    inputs = inputs or {}
    min_score = float(inputs.get("minScore", MIN_SCORE))
    min_4h = float(inputs.get("min4hMagnitude", MIN_4H_MAGNITUDE))
    min_1h = float(inputs.get("min1hMagnitude", MIN_1H_MAGNITUDE))
    min_15m = float(inputs.get("min15mVelocity", MIN_15M_VELOCITY))
    macro_gate = float(inputs.get("macroGateThresholdPct", MACRO_GATE_THRESHOLD_PCT))
    min_positioned = int(inputs.get("minPositionedCount", MIN_POSITIONED_COUNT))
    min_cohort_conviction = float(inputs.get("minCohortConvictionPct",
                                             MIN_COHORT_CONVICTION_PCT))
    strongly_tilted = float(inputs.get("stronglyTiltedConvictionPct",
                                       STRONGLY_TILTED_CONVICTION_PCT))
    convergent = float(inputs.get("convergentConvictionPct", CONVERGENT_CONVICTION_PCT))
    deep_consensus = int(inputs.get("deepConsensusCount", DEEP_CONSENSUS_COUNT))

    coin = asset_info["coin"]
    p4h = _f(price_data.get("p4h", 0))
    p1h = _f(price_data.get("p1h", 0))
    c15m = _f(price_data.get("c15m", 0))

    # HARD GATE: 3TF alignment — this is what DETERMINES the direction
    if p4h >= min_4h and p1h >= min_1h and c15m >= min_15m:
        sm_dir = "LONG"
    elif p4h <= -min_4h and p1h <= -min_1h and c15m >= min_15m:
        sm_dir = "SHORT"        # v2-quirk: velocity floor is the SAME sign for SHORT
    else:
        return None

    # HARD GATE: MACRO TREND (Wolverine's lesson)
    if sm_dir == "LONG" and p4h < -macro_gate:
        return None
    if sm_dir == "SHORT" and p4h > macro_gate:
        return None

    # HARD GATE: the proven cohort must hold this side, with depth and conviction
    if not cohort:
        return None
    if cohort.get("total_positioned", 0) < min_positioned:
        return None
    if cohort.get("direction", "") != sm_dir:
        return None
    conviction = _f(cohort.get("conviction_pct", 0))
    if conviction < min_cohort_conviction:
        return None

    # ─── SCORING (verbatim v2 tables) ───
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

    # 15m SM velocity
    if c15m >= 2.0:
        score += 2
        reasons.append(f"15M_SPIKE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # 3TF alignment bonus
    score += 3
    reasons.append(f"3TF_ALIGNED_{sm_dir}")

    # Concentration tier — now the cohort's conviction (share of positioned proven
    # wallets on the dominant side), not the board's gain share. Tier magnitudes
    # (+4/+3/+2) are unchanged, so the maximum score is still 19.
    if conviction >= strongly_tilted:
        score += 4
        reasons.append(f"COHORT_STRONGLY_TILTED {conviction:.0f}%")
    elif conviction >= convergent:
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
    if (sm_dir == "SHORT" and funding > 0.0002) or (sm_dir == "LONG" and funding < -0.0002):
        score += 1
        reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # BTC macro confirmation bonus
    if btc_macro and coin != "BTC":
        if btc_macro["direction"] == sm_dir and abs(btc_macro["p4h"]) >= 1.5:
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
        "direction": sm_dir,
        "score": score,
        "reasons": reasons,
        "p4h": p4h,
        "p1h": p1h,
        "c15m": c15m,
        "cohort_conviction": conviction,
        "cohort_positioned": total_pos,
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }
