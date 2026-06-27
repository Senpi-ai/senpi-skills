"""VULTURE — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 VULTURE producer's LONG_TAIL_MOMENTUM
scoring (vulture-producer.py v4.2.0 -> `score_market`). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same `leaderboard_get_markets` snapshot. Behaviour-preserving quirks from
v2 are kept and flagged `# v2-quirk`; fix them only as a separate, labelled change
AFTER the port is validated.

Multi-asset: scores ONE `leaderboard_get_markets` row against the whitelist and
returns a candidate dict or None. The caller (scan.py) iterates the universe,
read-guards each enrichment fetch, and emits all gated candidates. Pure +
unit-testable on plain dicts."""


# ── helpers (ported verbatim from v2) ──

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# Default whitelist — 27 small/mid-cap Hyperliquid perps. Excludes majors covered
# by other predators (BTC/ETH/SOL banned). The producer reads `inputs.whitelist`;
# this is the fallback. Includes the #1 Arena winner's small caps (HEMI, WLD, MON,
# XPL, AIXBT, ARB) plus liquid small caps with sufficient SM trader counts.
DEFAULT_WHITELIST = [
    "HYPE", "HEMI", "WLD", "MON", "XPL", "AIXBT", "ARB", "ASTER",
    "POLYX", "LDO", "APT", "DYDX", "ONDO", "SUI", "kBONK", "kPEPE",
    "TAO", "GRASS", "ZEC", "LIT", "FARTCOIN", "MORPHO", "NEAR", "INJ",
    "AVAX", "LINK", "DOGE",
]

# Banned — crypto majors handled by other predators; XYZ handled by Bald Eagle.
BANNED_ASSETS = {"BTC", "ETH", "SOL"}


def resolve_sizing_tier(score, tiers):
    """Highest-applicable sizing tier for a given score.
    tiers = [{min_score, leverage, label}, ...]. Returns the matching tier or None.
    Ported verbatim from v2 resolve_sizing_tier."""
    applicable = [t for t in tiers if score >= t["min_score"]]
    if not applicable:
        return None
    return max(applicable, key=lambda t: t["min_score"])


# ── SCORING — LONG_TAIL_MOMENTUM (ported verbatim from v2 score_market) ──

def score_market(m, regime, persistence_map, whitelist, tiers, inputs):
    """Score a single `leaderboard_get_markets` row. Returns a candidate dict or
    None if rejected. Same scoring + hard gates as v2's score_market (which itself
    preserved v2.4's evaluate_momentum): the 1h-alignment gate and the move-
    exhaustion penalty are kept verbatim.

    `whitelist` is the per-instance tracked-asset list; `tiers` the sizing tiers;
    `inputs` carries the gate thresholds (with v2 defaults)."""
    min_score = float(inputs.get("minScore", 10))
    min_sm_pct = float(inputs.get("minSmPct", 3.0))
    min_sm_traders = int(inputs.get("minSmTraders", 15))
    min_4h_aligned = float(inputs.get("min4hAlignedPct", 1.0))
    min_1h_aligned = float(inputs.get("min1hAlignedPct", 0.1))
    min_15m_velocity = float(inputs.get("min15mVelocity", 0.3))
    xyz_banned = bool(inputs.get("xyzBanned", True))

    token_raw = str(m.get("token", "")).upper()
    dex = str(m.get("dex", "")).lower()

    if xyz_banned and dex == "xyz":
        return None
    if token_raw in BANNED_ASSETS:
        return None

    # Match against whitelist (case-preserved for kPEPE/kBONK)
    matched = None
    for tracked in whitelist:
        if token_raw == tracked.upper():
            matched = tracked
            break
    if matched is None:
        return None

    direction = str(m.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                            m.get("price_change_1h", 0)))
    p15m = safe_float(m.get("token_price_change_pct_15m",
                             m.get("price_change_15m", 0)))
    c15m = safe_float(m.get("contribution_pct_change_15m", 0))
    c1h = safe_float(m.get("contribution_pct_change_1h", 0))
    c4h = safe_float(m.get("contribution_pct_change_4h", 0))

    # ─── HARD GATES ───
    if pct < min_sm_pct or traders < min_sm_traders:
        return None

    # 4h price must be ALIGNED with SM direction (momentum confirmation)
    p4h_aligned = p4h if direction == "LONG" else -p4h
    if p4h_aligned < min_4h_aligned:
        return None

    # v2.4 1h-alignment gate — catches "4h up + 1h rejecting + 15m spike"
    # false-breakout pattern. # v2-quirk: gate kept verbatim.
    p1h_aligned = p1h if direction == "LONG" else -p1h
    if p1h_aligned < min_1h_aligned:
        return None

    # 15m velocity must be actively building in SM direction
    if c15m < min_15m_velocity:
        return None

    score = 0
    reasons = []

    # ─── SM CONCENTRATION TIER (0-4) ───
    if pct >= 18:
        score += 4
        reasons.append(f"HEAVY_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"STRONG_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"MODERATE_FLOW {pct:.1f}% ({traders}t) {direction}")
    else:
        score += 1
        reasons.append(f"LIGHT_FLOW {pct:.1f}% ({traders}t) {direction}")

    # ─── 4H PRICE MOMENTUM (0-3) ───
    if p4h_aligned >= 8.0:
        score += 3
        reasons.append(f"TREND_RUNNING {p4h:+.1f}%")
    elif p4h_aligned >= 4.0:
        score += 2
        reasons.append(f"TREND_STRONG {p4h:+.1f}%")
    elif p4h_aligned >= 2.0:
        score += 1
        reasons.append(f"TREND_BUILDING {p4h:+.1f}%")

    # ─── 15M VELOCITY TIER (0-3) ───
    if c15m >= 3.0:
        score += 3
        reasons.append(f"15M_EXPLOSIVE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 2
        reasons.append(f"15M_STRONG +{c15m:.2f}")
    elif c15m >= 0.5:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # ─── 1H ACCELERATION (0-2) ───
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"ACCELERATING 15m({c15m:.2f})>1h({c1h:.2f})")
    elif c1h >= 1.0:
        score += 1
        reasons.append(f"1H_POSITIVE +{c1h:.2f}")

    # ─── 4H CONTRIBUTION (0-1) ───
    if c4h >= 2.0:
        score += 1
        reasons.append(f"4H_CONTINUATION +{c4h:.1f}")

    # ─── TRADER DEPTH (0-1) ───
    if traders >= 50:
        score += 1
        reasons.append(f"DEEP_DEPTH ({traders}t)")

    # ─── MOVE EXHAUSTION PENALTY ───
    if p4h_aligned >= 15.0:
        score -= 3
        reasons.append(f"LATE_ENTRY_PENALTY {p4h:+.1f}%")
    elif p4h_aligned >= 12.0:
        score -= 2
        reasons.append(f"MOVE_EXTENDED {p4h:+.1f}%")

    # ─── REGIME ALIGNMENT (-1, 0, +1) ───
    if regime == "LONG_CROWDED" and direction == "LONG":
        score += 1
        reasons.append("REGIME_LONG_CROWDED_aligned")
    elif regime == "SHORT_CROWDED" and direction == "SHORT":
        score += 1
        reasons.append("REGIME_SHORT_CROWDED_aligned")
    elif regime == "LONG_CROWDED" and direction == "SHORT":
        score -= 1
        reasons.append("REGIME_LONG_CROWDED_fighting")
    elif regime == "SHORT_CROWDED" and direction == "LONG":
        score -= 1
        reasons.append("REGIME_SHORT_CROWDED_fighting")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # ─── PERSISTENCE (0-1) ───
    ph_val = persistence_map.get(matched) if persistence_map else None
    if ph_val is not None and ph_val >= 6:
        score += 1
        reasons.append(f"TREND_PERSISTENT_{ph_val:.0f}h")

    if score < min_score:
        return None

    tier = resolve_sizing_tier(score, tiers)
    if not tier:
        return None

    reasons.insert(0, f"LONG_TAIL_MOMENTUM {matched} {direction}")

    return {
        "asset": matched,
        "direction": direction,
        "score": score,
        "leverage": tier["leverage"],
        "tier_label": tier["label"],
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "p15m": p15m,
        "c15m": c15m,
        "c1h": c1h,
        "c4h": c4h,
        "regime": regime or "UNKNOWN",
        "persistence_hours": ph_val,
    }


def in_quiet_hours(hour, inputs):
    """FP-001: is `hour` (UTC, 0-23) inside the low-liquidity quiet window?
    Reads quietStartUtc/quietEndUtc/apexBypassScore from inputs. start==end disables.
    Returns (in_quiet: bool, apex_bypass: int). Ported verbatim from v2 in_quiet_hours
    (clock owned by the caller per the pure-scoring rule)."""
    start = int(inputs.get("quietStartUtc", 0))
    end = int(inputs.get("quietEndUtc", 4))
    apex = int(inputs.get("apexBypassScore", 11))
    if start == end:
        return False, apex
    if start < end:
        return (start <= hour < end), apex
    return (hour >= start or hour < end), apex
