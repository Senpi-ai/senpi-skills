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

# SM gates
#
# The v2 port gated `pct_of_top_traders_gain >= 70` under the name
# MIN_SM_CONSENSUS_PCT, believing it was a "% of top traders leaning this way".
# It is not: it is a share of TOTAL top-trader gain spread across every market
# in the leaderboard response, so it sums to ~100 board-wide and no single
# market can reach 70. That gate was unreachable and blocked 100% of signals.
#
# Directional agreement now comes from `is_dominant_direction` (the field that
# actually carries it). The gain share is kept as a separate CONCENTRATION
# filter, on its own real scale.
#
# CALIBRATION NOTE: the thresholds below are derived from a single live
# leaderboard snapshot (2026-08-13, 271 markets; among the 12 with >=50 traders
# the gain share ran max 25.6 / median 5.2 / min 1.2). They restore signal flow
# but are NOT backtested — the strategy owner should tune them.
REQUIRE_DOMINANT_DIRECTION = True  # `is_dominant_direction` must be true
MIN_SM_GAIN_SHARE_PCT = 5.0        # concentration floor (~median of deep markets)
STRONGLY_TILTED_PCT = 15.0         # top-tier concentration  (+4)
CONVERGENT_PCT = 8.0               # mid-tier concentration   (+3)

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

def evaluate_trend_continuation(asset_info, sm, btc_macro, hour, inputs=None):
    """Score an asset for trend-continuation apex setup.

    `asset_info` = {coin, oi_usd, volume_24h, price, funding}
    `sm`         = {direction, gain_share_pct, is_dominant, traders, p4h, p1h,
                    c15m, c1h}
    `btc_macro`  = {direction, p4h} or None
    `hour`       = current UTC hour (caller owns the clock — keeps this pure)

    Returns scored signal dict or None if any hard gate fails. Scoring tables and
    bonus magnitudes are VERBATIM from v2; the smart-money gate is not — see the
    MIN_SM_GAIN_SHARE_PCT block above for why the v2 threshold was unreachable.

    The legacy `minSmConsensusPct` input is deliberately IGNORED: it was set to 70
    on the wrong scale, and honouring a stale runtime.yaml carrying it would
    re-brick the strategy. Use `minSmGainSharePct` instead."""
    inputs = inputs or {}
    min_score = float(inputs.get("minScore", MIN_SCORE))
    min_gain_share = float(inputs.get("minSmGainSharePct", MIN_SM_GAIN_SHARE_PCT))
    require_dominant = bool(inputs.get("requireDominantDirection",
                                       REQUIRE_DOMINANT_DIRECTION))
    min_traders = int(inputs.get("minTraderCount", MIN_TRADER_COUNT))
    min_4h = float(inputs.get("min4hMagnitude", MIN_4H_MAGNITUDE))
    min_1h = float(inputs.get("min1hMagnitude", MIN_1H_MAGNITUDE))
    min_15m = float(inputs.get("min15mVelocity", MIN_15M_VELOCITY))
    macro_gate = float(inputs.get("macroGateThresholdPct", MACRO_GATE_THRESHOLD_PCT))
    strongly_tilted = float(inputs.get("stronglyTiltedPct", STRONGLY_TILTED_PCT))
    convergent = float(inputs.get("convergentPct", CONVERGENT_PCT))

    coin = asset_info["coin"]
    sm_dir = sm["direction"]
    if sm_dir not in ("LONG", "SHORT"):
        return None

    # HARD GATE: smart money agrees on the direction
    if require_dominant and not sm.get("is_dominant", False):
        return None
    # HARD GATE: this market carries a meaningful share of top-trader gain
    if sm.get("gain_share_pct", 0.0) < min_gain_share:
        return None
    # HARD GATE: trader depth
    if sm["traders"] < min_traders:
        return None

    p4h = sm["p4h"]
    p1h = sm["p1h"]
    c15m = sm["c15m"]

    # HARD GATE: 3TF alignment (4h + 1h + 15m velocity)
    if sm_dir == "LONG":
        tf_ok = (p4h >= min_4h and
                 p1h >= min_1h and
                 c15m >= min_15m)
    else:
        tf_ok = (p4h <= -min_4h and
                 p1h <= -min_1h and
                 c15m >= min_15m)        # v2-quirk: velocity floor is the SAME sign for
                                         # SHORT (c15m >= +floor), not mirrored. Preserved.
    if not tf_ok:
        return None

    # HARD GATE: MACRO TREND (Wolverine's lesson)
    if sm_dir == "LONG" and p4h < -macro_gate:
        return None
    if sm_dir == "SHORT" and p4h > macro_gate:
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

    # SM concentration tier. Tier boundaries rescaled onto the gain-share scale
    # (v2 used 80/75 on the unreachable 0-100 "consensus" scale, so every scored
    # candidate would have landed in the base +2 bucket). Tier magnitudes (+4/+3/+2)
    # are unchanged, so the maximum score is still 19.
    gain_share = sm.get("gain_share_pct", 0.0)
    if gain_share >= strongly_tilted:
        score += 4
        reasons.append(f"SM_STRONGLY_TILTED {gain_share:.1f}%")
    elif gain_share >= convergent:
        score += 3
        reasons.append(f"SM_CONVERGENT {gain_share:.1f}%")
    else:
        score += 2
        reasons.append(f"SM_ALIGNED {gain_share:.1f}%")

    # Trader depth
    if sm["traders"] >= 100:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({sm['traders']}t)")

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
        "sm_gain_share": gain_share,
        "sm_traders": sm["traders"],
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }
