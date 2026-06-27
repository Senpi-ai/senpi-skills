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
MIN_SM_CONSENSUS_PCT = 70.0       # v2: v3.4 calibration (65 in v3.0/v3.1, 75 in v3.2)
STRONGLY_TILTED_PCT = 80.0

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
    `sm`         = {direction, consensus_pct, traders, p4h, p1h, c15m, c1h}
    `btc_macro`  = {direction, p4h} or None
    `hour`       = current UTC hour (caller owns the clock — keeps this pure)

    Returns scored signal dict or None if any hard gate fails. Scoring
    tables, gate thresholds, and bonus magnitudes are VERBATIM from v2."""
    inputs = inputs or {}
    min_score = float(inputs.get("minScore", MIN_SCORE))
    min_consensus = float(inputs.get("minSmConsensusPct", MIN_SM_CONSENSUS_PCT))
    min_traders = int(inputs.get("minTraderCount", MIN_TRADER_COUNT))
    min_4h = float(inputs.get("min4hMagnitude", MIN_4H_MAGNITUDE))
    min_1h = float(inputs.get("min1hMagnitude", MIN_1H_MAGNITUDE))
    min_15m = float(inputs.get("min15mVelocity", MIN_15M_VELOCITY))
    macro_gate = float(inputs.get("macroGateThresholdPct", MACRO_GATE_THRESHOLD_PCT))
    strongly_tilted = float(inputs.get("stronglyTiltedPct", STRONGLY_TILTED_PCT))

    coin = asset_info["coin"]
    sm_dir = sm["direction"]
    if sm_dir not in ("LONG", "SHORT"):
        return None

    # HARD GATE: SM consensus
    if sm["consensus_pct"] < min_consensus:
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

    # SM consensus tier
    if sm["consensus_pct"] >= strongly_tilted:
        score += 4
        reasons.append(f"SM_STRONGLY_TILTED {sm['consensus_pct']:.0f}%")
    elif sm["consensus_pct"] >= 75:        # v2-quirk: literal 75 (not min_consensus). Preserved.
        score += 3
        reasons.append(f"SM_CONVERGENT {sm['consensus_pct']:.0f}%")
    else:
        score += 2
        reasons.append(f"SM_ALIGNED {sm['consensus_pct']:.0f}%")

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
        "sm_consensus": sm["consensus_pct"],
        "sm_traders": sm["traders"],
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }
