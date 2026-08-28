"""ORCA — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 ORCA producer's Gen-1 Vanilla Striker
detection (`detect_signals` body + `time_of_day_modifier` + `check_4h_alignment`),
SKILL.md / orca-producer.py v4.0.1. The arithmetic, gate order, point weights, and
direction logic are reproduced VERBATIM so a fidelity harness can diff this against
the v2 producer on the same scan snapshot.

Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`. Fix them only
as a separate, labelled change AFTER the port is validated.

UNIVERSE scanner: `score_market` scores ONE normalized market dict against the
previous scan + a short rank-history window (FIRST_JUMP / IMMEDIATE_MOVER / contrib
explosion / velocity / climbing). The caller (scan.py) iterates the top-50 SM
leaderboard markets, maintains the scan history in ctx.state, picks the single
strongest candidate, and resolves the fixed-7x leverage. Unit-testable on plain dicts.

The ONE non-pure thing the v2 detect_signals did — `time_of_day_modifier()` reads the
wall clock — is parameterized here: the caller passes the current UTC hour in so this
module stays clock-free and unit-testable. The US-session arithmetic is verbatim.
"""

# ── thresholds (v2 producer constants, verbatim) ──
TOP_N = 50                    # v2 TOP_N — score only the top-50 SM markets per scan
STRIKER_MIN_SCORE = 9         # v2 STRIKER_MIN_SCORE
STRIKER_MIN_REASONS = 4       # v2 STRIKER_MIN_REASONS — 4-reason floor
STRIKER_MIN_RANK_JUMP = 15    # v2 STRIKER_MIN_RANK_JUMP
STRIKER_MIN_PREV_RANK = 25    # v2 STRIKER_MIN_PREV_RANK
STRIKER_MIN_VOL_RATIO = 1.5   # v2 STRIKER_MIN_VOL_RATIO (volume confirmation, applied in scan.py)

# leverage is FIXED in v2 (MIN==MAX==DEFAULT==7); no score-tiering.
DEFAULT_LEVERAGE = 7          # v2 DEFAULT_LEVERAGE / MIN_LEVERAGE / MAX_LEVERAGE
MARGIN_PCT = 18.0             # v2 MARGIN_PCT=0.18 (FRACTION) -> 18 PERCENT


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# backward-compat alias mirroring the v2 helper name used across the fleet
_f = safe_float


def check_4h_alignment(direction, price_chg_4h):
    """v2 check_4h_alignment — the trade direction must agree with the 4h price move."""
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def time_of_day_modifier(hour_utc):
    """v2 time_of_day_modifier — +1 score during the US session (13:00–21:00 UTC).
    The v2 producer read the wall clock inside detect_signals; here the caller passes
    the current UTC hour so this module stays pure / unit-testable. Arithmetic verbatim."""
    if 13 <= hour_utc <= 21:
        return 1, "during US trading hours"
    return 0, None


def score_market(market, latest_prev, oldest_available, prev_top50_tokens,
                 recent_contribs, hour_utc):
    """Score a single normalized market for a Gen-1 Striker FIRST_JUMP/IMMEDIATE_MOVER.
    Returns (score, reasons, meta) or None when a hard gate rejects.

    Ported VERBATIM from the v2 producer's `detect_signals` per-market body. The only
    structural change: the rank-history lookups (latest_prev market, oldest market,
    the contribution-velocity window, prev-top50 membership) are resolved by the caller
    from ctx.state and handed in pre-computed, keeping this module pure / clock-free.
    The arithmetic, gate order, point weights, and direction logic are unchanged.
    The two reads that are NOT pure — wall clock (US session) and the volume-ratio
    confirmation (an MCP read) — are parameterized: hour_utc is passed in; the volume
    gate runs in scan.py AFTER this scorer clears, exactly as in v2 (score gate first,
    then the per-asset volume MCP call).

    market: normalized current-scan dict with keys:
      token, dex, rank, direction, contribution, traders, price_chg_4h, price_chg_1h, cc_15m.
    latest_prev: the same (token,dex) market dict from the most-recent prior scan, or None.
    oldest_available: the same (token,dex) market dict from the oldest scan in window, or None.
    prev_top50_tokens: set of (token,dex) present in the latest prior scan's top-50.
    recent_contribs: [contribution, ...] across the window ending with THIS scan's contribution.
    hour_utc: current UTC hour (for the US-session modifier).
    """
    token = market["token"]
    dex = market.get("dex", "")
    current_rank = market["rank"]
    direction = market["direction"]
    current_contrib = market["contribution"]

    # Hard gate: skip the already-top names (no rank-jump room) — v2 `if rank <= 10: continue`.
    if current_rank <= 10:
        return None
    # Hard gate: 4h trend must agree with the SM direction — v2 check_4h_alignment.
    if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
        return None

    # Need a prior observation of this exact (token,dex) to measure the jump — v2 `if not prev_market: continue`.
    prev_market = latest_prev
    old_market = oldest_available
    if not prev_market:
        return None

    rank_jump = prev_market["rank"] - current_rank

    is_first_jump = False
    is_immediate = False
    is_contrib_explosion = False
    reasons = []

    if rank_jump >= 10 and prev_market["rank"] >= STRIKER_MIN_PREV_RANK:
        is_immediate = True
        reasons.append(f"climbed {rank_jump} spots from #{prev_market['rank']}")
        was_in_prev = (token, dex) in prev_top50_tokens
        if not was_in_prev or prev_market["rank"] >= 30:
            is_first_jump = True
            reasons.append(f"first big jump, from #{prev_market['rank']} to #{current_rank}")

    if prev_market["contribution"] > 0:
        contrib_ratio = current_contrib / prev_market["contribution"]
        if contrib_ratio >= 3.0:
            is_contrib_explosion = True
            reasons.append(f"share of top-trader gains up {contrib_ratio:.1f}x")

    # Hard gates: must be at least a first-jump OR immediate mover, and clear the rank-jump floor.
    if not is_first_jump and not is_immediate:
        return None
    if rank_jump < STRIKER_MIN_RANK_JUMP:
        return None

    # Contribution velocity (mean per-scan delta * 100) — v2 verbatim.
    contrib_velocity = 0
    if len(recent_contribs) >= 2:
        deltas = [recent_contribs[i + 1] - recent_contribs[i]
                  for i in range(len(recent_contribs) - 1)]
        contrib_velocity = sum(deltas) / len(deltas) * 100

    # ── Base Striker scoring (v2 verbatim point weights) ──
    score = 0
    if is_first_jump:
        score += 3
    if is_immediate:
        score += 2
    if is_contrib_explosion:
        score += 2
    if abs(contrib_velocity) > 10:
        score += 2
        reasons.append(f"gain share shifting {abs(contrib_velocity):.1f} pts per scan")
    if prev_market["rank"] >= 40:
        score += 1
        reasons.append("started from rank #40 or lower")
    if old_market:
        total_climb = old_market["rank"] - current_rank
        if total_climb >= 10:
            score += 1
            reasons.append(f"up {total_climb} ranks over recent scans")

    tod_mod, tod_reason = time_of_day_modifier(hour_utc)
    score += tod_mod
    if tod_reason:
        reasons.append(tod_reason)

    # Score + reason floor — v2 verbatim.
    if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
        return None

    meta = {
        "mode": "STRIKER",
        "currentRank": current_rank,
        "rankJump": rank_jump,
        "isFirstJump": is_first_jump,
        "isImmediate": is_immediate,
        "isContribExplosion": is_contrib_explosion,
        "contribVelocity": round(contrib_velocity, 4),
        "contribution": round(current_contrib * 100, 3),
        "traders": market["traders"],
        "priceChg4h": market.get("price_chg_4h", 0),
    }
    return score, reasons, meta
