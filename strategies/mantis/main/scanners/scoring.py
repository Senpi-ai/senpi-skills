"""MANTIS — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Mantis producer's Slipstream logic
(mantis-producer.py v6.0.1 + mantis_config.py v6.0.0). Mantis is a CROSS-ASSET
LAG / catchup hunter: it does NOT compute its own indicators — the
`market_get_cross_asset_flows` MCP tool returns PRE-COMPUTED per-laggard scores
(follow_rate, confidence, gap_pct, avg_lag_minutes, lag_stddev_minutes,
sm_starting_to_rotate). This module holds the PURE decision math applied on top
of those tool fields: the entry filters, the conviction sizing tiers, the
leader-follow direction, and the dynamic hard-timeout clamp. The flow-response
unwrap + the MCP reads live in scan.py (so this module stays pure and testable).

All thresholds/tiers reproduced VERBATIM from mantis_config.py; behaviour-
preserving quirks flagged `# v2-quirk`.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── v2 config defaults (mantis_config.py) — overridable via runtime inputs ──
DEFAULT_MIN_FOLLOW_RATE = 0.85
DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_MIN_GAP_PCT = 1.5
DEFAULT_REQUIRE_SM_ROTATION = True
DEFAULT_MAX_LAG_STDDEV_MINUTES = 90

# Sizing tiers — conviction-scaled off the tool's confidence score.
# margin_pct is a PERCENT in (0,100]. VERBATIM from cfg.SIZING_TIERS.
DEFAULT_SIZING_TIERS = [
    {"confidence_min": 0.92, "margin_pct": 75, "leverage": 8},
    {"confidence_min": 0.85, "margin_pct": 50, "leverage": 7},
    {"confidence_min": 0.75, "margin_pct": 25, "leverage": 5},
]
DEFAULT_MAX_LEVERAGE = 8

DEFAULT_HARD_TIMEOUT_LAG_MULTIPLIER = 1.5
DEFAULT_HARD_TIMEOUT_FLOOR_MINUTES = 30
DEFAULT_HARD_TIMEOUT_CEILING_MINUTES = 240
DEFAULT_LEADER_REVERSAL_VETO_PCT = 1.0


def unwrap_flow_response(result):
    """MCP returns {success: bool, data: {...}}. Unwrap to inner dict.
    VERBATIM port of producer._unwrap_flow_response. Returns dict or None."""
    if not result or not isinstance(result, dict):
        return None
    if "data" in result and isinstance(result["data"], dict):
        return result["data"]
    if "leader" in result or "laggards" in result:
        return result
    return None


def leader_move_from_flow(flow_data):
    """Read the leader's current 4h move from an unwrapped flow dict.

    v2-quirk: producer reads `data['leader']['move_pct']` (a `leader` BLOCK with a
    `move_pct` field). The cross-asset-flow-guide documents the alternate shape
    with TOP-LEVEL `leader_move_pct`. We honour the producer's primary path first
    (leader block) then fall back to the top-level field, so either tool shape
    yields the same number. Returns float (0.0 if absent)."""
    if not isinstance(flow_data, dict):
        return 0.0
    leader_block = flow_data.get("leader") or {}
    if isinstance(leader_block, dict) and "move_pct" in leader_block:
        return _f(leader_block.get("move_pct"))
    return _f(flow_data.get("leader_move_pct"))


def passes_entry_filters(laggard, inputs):
    """All filters must pass — VERBATIM from producer.passes_entry_filters.

    follow_rate >= MIN_FOLLOW_RATE; confidence >= MIN_CONFIDENCE;
    |gap_pct| >= MIN_GAP_PCT; (REQUIRE_SM_ROTATION -> sm_starting_to_rotate true);
    0 < lag_stddev_minutes <= MAX_LAG_STDDEV_MINUTES."""
    min_follow = float(inputs.get("minFollowRate", DEFAULT_MIN_FOLLOW_RATE))
    min_conf = float(inputs.get("minConfidence", DEFAULT_MIN_CONFIDENCE))
    min_gap = float(inputs.get("minGapPct", DEFAULT_MIN_GAP_PCT))
    require_sm = bool(inputs.get("requireSmRotation", DEFAULT_REQUIRE_SM_ROTATION))
    max_lag_std = float(inputs.get("maxLagStddevMinutes", DEFAULT_MAX_LAG_STDDEV_MINUTES))

    if _f(laggard.get("follow_rate")) < min_follow:
        return False
    if _f(laggard.get("confidence")) < min_conf:
        return False
    if abs(_f(laggard.get("gap_pct"))) < min_gap:
        return False
    if require_sm and not laggard.get("sm_starting_to_rotate"):
        return False
    lag_stddev = _f(laggard.get("lag_stddev_minutes"))
    if lag_stddev <= 0 or lag_stddev > max_lag_std:
        return False
    return True


def sizing_tier_for(confidence, tiers):
    """Pick highest tier the confidence qualifies for.
    VERBATIM from producer.sizing_tier_for: walks tiers in order (descending
    confidence_min), returns the first whose floor is cleared, else the last."""
    conf = _f(confidence)
    for tier in tiers:
        if conf >= _f(tier.get("confidence_min")):
            return tier
    return tiers[-1]


def direction_from_leader_move(leader_move_pct):
    """Follow the leader: positive move -> LONG laggard, negative -> SHORT.
    VERBATIM from producer.direction_from_leader_move."""
    return "LONG" if _f(leader_move_pct) >= 0 else "SHORT"


def compute_hard_timeout(avg_lag_minutes, inputs):
    """Dynamic hard_timeout = avg_lag_minutes * multiplier, clamped to
    [floor, ceiling]. VERBATIM from producer.compute_hard_timeout."""
    mult = float(inputs.get("hardTimeoutLagMultiplier", DEFAULT_HARD_TIMEOUT_LAG_MULTIPLIER))
    floor = float(inputs.get("hardTimeoutFloorMinutes", DEFAULT_HARD_TIMEOUT_FLOOR_MINUTES))
    ceil = float(inputs.get("hardTimeoutCeilingMinutes", DEFAULT_HARD_TIMEOUT_CEILING_MINUTES))
    minutes = max(1.0, _f(avg_lag_minutes, 60.0)) * mult
    minutes = max(floor, min(ceil, minutes))
    return int(minutes)


def leader_reversed(leader_pct_at_entry, current_leader_move_pct, veto_pct):
    """True if the leader has reversed by more than veto_pct from its move at
    entry time. Direction matters. VERBATIM from producer.leader_reversed.

    NOTE: This predicate is preserved for fidelity/reference (the v2 leader-
    reversal veto thesis). In Runtime 3.0 the veto's ACTION (close_position) is a
    MUTATION that read-only scan() cannot perform — see scan.py FIDELITY NOTES.
    The math is kept here so the thesis stays documented + unit-testable."""
    delta = _f(current_leader_move_pct) - _f(leader_pct_at_entry)
    if _f(leader_pct_at_entry) >= 0:
        return delta < -_f(veto_pct)
    return delta > _f(veto_pct)


def build_strike(candidate, inputs):
    """Build the strike dict from a filtered laggard + its attached leader fields.

    Mirrors producer.build_strike, but emits a margin PERCENT (not marginUsd) —
    the Runtime 3.0 runtime sizes the dollars from (marginPct/100)*withdrawable.
    The TIER margin_pct values + leverage clamp + direction + dynamic hard_timeout
    are all VERBATIM. `candidate` must carry `_leader_asset` + `_leader_move_pct`
    (attached by scan.gather_candidates)."""
    max_lev = int(inputs.get("maxLeverage", DEFAULT_MAX_LEVERAGE))
    tiers = inputs.get("sizingTiers", DEFAULT_SIZING_TIERS)

    confidence = _f(candidate.get("confidence"))
    tier = sizing_tier_for(confidence, tiers)
    margin_pct = _f(tier.get("margin_pct"))
    # v2-quirk: if an operator pastes a v2 FRACTION (e.g. 0.75) into a tier's
    # margin_pct, convert to PERCENT so it never silently sizes ~100x small
    # (runtime sizes (marginPct/100)*withdrawable). Tier defaults are already
    # PERCENTs (75/50/25) so this is a defensive no-op for the shipped config.
    if 0 < margin_pct <= 1.0:
        margin_pct = margin_pct * 100.0
    leverage = min(int(_f(tier.get("leverage"))), max_lev)

    leader_move_pct = _f(candidate.get("_leader_move_pct"))
    direction = direction_from_leader_move(leader_move_pct)
    avg_lag = _f(candidate.get("avg_lag_minutes"), 60)
    hard_timeout = compute_hard_timeout(avg_lag, inputs)

    return {
        "asset": candidate.get("asset"),
        "direction": direction,
        "leverage": leverage,
        "margin_pct": margin_pct,
        "hard_timeout_minutes": hard_timeout,
        "confidence": confidence,
        "gap_pct": _f(candidate.get("gap_pct")),
        "follow_rate": _f(candidate.get("follow_rate")),
        "avg_lag_minutes": avg_lag,
        "lag_stddev_minutes": _f(candidate.get("lag_stddev_minutes")),
        "sm_starting_to_rotate": bool(candidate.get("sm_starting_to_rotate")),
        "leader_asset": candidate.get("_leader_asset"),
        "leader_move_pct": leader_move_pct,
        "reasons": [
            "{} historically follows {}".format(
                candidate.get("asset"), candidate.get("_leader_asset")),
            "leader moved {:+.2f}% in 4h".format(leader_move_pct),
            "still {:+.2f}% behind that move".format(_f(candidate.get("gap_pct"))),
            "follows the leader {:.0%} of the time".format(_f(candidate.get("follow_rate"))),
            "usually catches up in {:.0f}±{:.0f} min".format(
                avg_lag, _f(candidate.get("lag_stddev_minutes"))),
            "confidence score {:.2f} out of 1".format(confidence),
            "sized at {:.0f}% margin, {}x leverage".format(margin_pct, leverage),
        ],
    }
