"""CHEETAH — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Cheetah producer's confluence scorer
(`score_confluence`) + `get_leverage_for_score` (SKILL.md v7.2.0). The math is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on
the same market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port is validated.

Universe scanner: `score_confluence` scores ONE normalized market dict; the caller
(scan.py) iterates the top-100 SM leaderboard, picks the single strongest candidate,
and resolves the leverage tier. Unit-testable on plain dicts."""


# ── score-component default thresholds (v2 producer constants, verbatim) ──
SM_MIN_PCT = 10.0
SM_MIN_TRADERS = 25
VEL_MIN_15M = 1.0
VEL_MIN_1H = 3.0
PRICE_MIN_4H = 2.0
VOL_MIN_RATIO = 2.0
RANK_CLIMB_MIN = 5
RANK_CLIMB_TOPN = 15

# v2 LEVERAGE_TIERS (verbatim) — desc by min_score; DEFAULT_LEVERAGE=5 fallback.
DEFAULT_LEVERAGE_TIERS = [[14, 8], [12, 7], [11, 5], [10, 3]]
DEFAULT_LEVERAGE = 5


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score, tiers=None):
    """v2 get_leverage_for_score — first tier whose min_score the candidate clears.
    tiers = [[min_score, leverage], ...] desc by score. Falls back to DEFAULT_LEVERAGE
    (5) when no tier matches — matches v2 exactly (a score below 10 can't reach here in
    practice because MIN_SCORE gates candidates, but the fallback is preserved verbatim)."""
    if tiers is None:
        tiers = DEFAULT_LEVERAGE_TIERS
    for t in tiers:
        if score >= t[0]:
            return int(t[1])
    return DEFAULT_LEVERAGE


def score_confluence(market, quality_positions, rank_climb, thresholds=None):
    """Score a single normalized market for confluence. Returns (score, reasons).
    Hard gates reject with score=0 and an empty reasons list.

    Ported VERBATIM from the v2 producer's `score_confluence`. The only structural
    change: `rank_climb` is passed in pre-computed (the v2 producer called
    `get_rank_climb(history, token, dex)` inside this function; scan.py now resolves it
    from ctx.state and hands it in, keeping this module pure / clock-free). The
    arithmetic, gate order, point weights, and direction logic are unchanged.

    market: normalized dict (see scan.normalize_market) with keys:
      direction, pct, traders, contrib_15m, contrib_1h, price_chg_4h, price_chg_1h,
      volume, avg_volume_6h, token, rank.
    quality_positions: { "ASSET:DIRECTION": [addr, ...] } overlap map.
    rank_climb: int >= 0, ranks gained over the last 2 scans (caller-computed)."""
    th = thresholds or {}
    sm_min_pct = safe_float(th.get("smMinPct", SM_MIN_PCT))
    sm_min_traders = int(th.get("smMinTraders", SM_MIN_TRADERS))
    vel_min_15m = safe_float(th.get("velMin15m", VEL_MIN_15M))
    vel_min_1h = safe_float(th.get("velMin1h", VEL_MIN_1H))
    price_min_4h = safe_float(th.get("priceMin4h", PRICE_MIN_4H))
    vol_min_ratio = safe_float(th.get("volMinRatio", VOL_MIN_RATIO))
    rank_climb_min = int(th.get("rankClimbMin", RANK_CLIMB_MIN))
    rank_climb_topn = int(th.get("rankClimbTopN", RANK_CLIMB_TOPN))

    direction = market["direction"]
    if not direction:
        return 0, []

    reasons = []
    score = 0

    # Hard gate: SM consensus must meet minimum
    sm_pct = market["pct"]
    sm_traders = market["traders"]
    if sm_pct < sm_min_pct or sm_traders < sm_min_traders:
        return 0, []
    score += 4
    reasons.append(f"drives {sm_pct:.1f}% of top-trader gains, {sm_traders} traders")

    # Hard gate: at least one velocity axis must pass
    c15m = market["contrib_15m"]
    c1h = market["contrib_1h"]
    if c15m < vel_min_15m and c1h < vel_min_1h:
        return 0, []
    if c15m >= vel_min_15m or c1h >= vel_min_1h:
        score += 2
        reasons.append(f"gain share up {c15m:.2f} pts in 15m, {c1h:.2f} in 1h")

    # Accelerating: 15m > 1h > 0
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"gain share accelerating, 15m {c15m:.2f} beats 1h {c1h:.2f}")

    # Dual price confirmation (4h + 1h aligned)
    p4h = market["price_chg_4h"]
    p1h = market["price_chg_1h"]
    if direction == "LONG":
        if p4h >= price_min_4h and p1h > 0:
            score += 2
            reasons.append(f"price +{p4h:.1f}% in 4h and +{p1h:.2f}% in 1h")
    else:
        if p4h <= -price_min_4h and p1h < 0:
            score += 2
            reasons.append(f"price {p4h:.1f}% in 4h and {p1h:.2f}% in 1h")

    # Volume spike
    vol = market["volume"]
    avg_vol = market["avg_volume_6h"]
    if avg_vol > 0 and vol >= avg_vol * vol_min_ratio:
        score += 1
        reasons.append(f"volume {vol/avg_vol:.1f}x its 6h average")

    # Quality trader alignment
    key = f"{market['token']}:{direction}"
    aligned_traders = quality_positions.get(key, [])
    if aligned_traders:
        score += 3
        reasons.append(f"{len(aligned_traders)} quality traders on the same side")

    # Rank climb (rank_climb precomputed by caller from scan-history)
    if rank_climb >= rank_climb_min and market["rank"] <= rank_climb_topn:
        score += 1
        reasons.append(f"jumped {rank_climb} leaderboard spots to #{market['rank']}")

    return score, reasons
