"""PANGOLIN — pure funding-fade math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Pangolin producer (pangolin-producer.py,
thesis v2.2.0). Given instrument context + funding-history + regime + smart-money
rows already fetched by scan.py, the funding-persistence + exhaustion + fade scoring
is reproduced VERBATIM (every block marked `# v2-quirk` is copied byte-for-byte from
the v2 `scan_funding_extremes()` so a fidelity harness can diff this against the v2
producer on the same market snapshot).

The thesis: persistent extreme funding is a crowding signal. Fade the crowd (enter
OPPOSITE the funding-payer side) once funding has been extreme for >= 3h AND the market
regime confirms the fade (or is neutral/unavailable). Score the conviction off funding
extremity + persistence + funding trend + regime + smart-money lean + sticky OI +
price-already-reversing, then size leverage to conviction. Collect funding hourly while
the over-stretched position mean-reverts (24-48h horizon).

Caller (scan.py) owns ALL I/O and the clock: it passes the UTC `hour` for the
quiet-hours gate (keeps this module pure)."""


# ═══════════════════════════════════════════════════════════════
# UTILITIES (ported from pangolin-producer.safe_float)
# ═══════════════════════════════════════════════════════════════

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage(score, tiers, default_leverage=3):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score.
    v2-quirk: LEVERAGE_TIERS = [{min_score:13, leverage:5}, {min_score:9, leverage:3}],
    DEFAULT_LEVERAGE = 3 — ported verbatim from v1.4 (crowded unwinds are violent)."""
    for t in tiers:
        if score >= t[0]:
            return int(t[1])
    return int(default_leverage)


# ═══════════════════════════════════════════════════════════════
# REGIME CONFIRMATION (ported verbatim from regime_confirms_fade)
# ═══════════════════════════════════════════════════════════════

def regime_confirms_fade(fade_direction, regime):
    """Returns True/False/None (None = regime unavailable/neutral).
    v2-quirk: copied verbatim from pangolin-producer.regime_confirms_fade."""
    if regime is None or regime == "NEUTRAL":
        return None
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# QUIET-HOURS GATE
# ═══════════════════════════════════════════════════════════════
# TASK-SPEC requirement (NOT present in the v2 producer source — see the
# fidelity note in scan.py). Hard gate: no new entries during the quiet window
# (default 00:00-04:00 UTC). The caller passes the UTC hour to keep this pure.

def in_quiet_hours(hour, start_utc=0, end_utc=4):
    """True when `hour` (0-23 UTC) is within [start_utc, end_utc).
    Supports a wrap window (e.g. start=22, end=2)."""
    try:
        h = int(hour) % 24
        s = int(start_utc) % 24
        e = int(end_utc) % 24
    except (TypeError, ValueError):
        return False
    if s == e:
        return False
    if s < e:
        return s <= h < e
    # wrap past midnight
    return h >= s or h < e


# ═══════════════════════════════════════════════════════════════
# FUNDING-FADE SCORING — ported VERBATIM from scan_funding_extremes()
# ═══════════════════════════════════════════════════════════════

def score_candidate(name, ctx_block, fh, regime, sm, volume_24h):
    """Score ONE candidate that has already passed the hard gates in scan.py
    (XYZ ban, OI >= minOiUsd, |funding| >= minFundingRate, regime confirms-or-neutral,
    persistence >= minPersistenceHours). Returns a candidate dict, or None if the row
    is unusable.

    Args (all plain data — no MCP, no clock):
      name        : asset symbol (uppercase)
      ctx_block   : instrument context dict ({funding, openInterest, markPx/midPx, ...})
      fh          : funding-history dict from scan.py's parser
                    ({persistence_hours, funding_direction, trend, annualized_pct})
      regime      : market funding regime string (or None)
      sm          : smart-money market row for this asset (dict or None)
      volume_24h  : 24h notional volume (float)

    Every scoring block below is COPIED VERBATIM from the v2 producer
    `scan_funding_extremes()` (marked `# v2-quirk`). Do NOT redesign."""
    funding = _f(ctx_block.get("funding", 0))
    oi = _f(ctx_block.get("openInterest", 0))
    mark_px = _f(ctx_block.get("markPx", ctx_block.get("midPx", 0)))
    oi_usd = oi * mark_px if mark_px > 0 else 0

    persistence_hours = fh.get("persistence_hours")
    try:
        persistence_hours = float(persistence_hours)
    except (TypeError, ValueError):
        return None

    # v2-quirk: crowd is the funding-PAYER side; fade is the opposite side.
    crowd_direction = "LONG" if funding > 0 else "SHORT"
    fade_direction = "SHORT" if funding > 0 else "LONG"

    regime_confirms = regime_confirms_fade(fade_direction, regime)

    # ── Scoring (VERBATIM from v2 scan_funding_extremes) ──
    score = 0
    reasons = []

    # v2-quirk: Funding extremity
    abs_funding = abs(funding)
    annualized = abs_funding * 8760 * 100   # HL funding is HOURLY -> *24*365
    if abs_funding >= 0.001:
        score += 4
        reasons.append(f"funding extreme at {funding*100:.4f}% ({annualized:.0f}% a year)")
    elif abs_funding >= 0.0006:
        score += 3
        reasons.append(f"funding high at {funding*100:.4f}% ({annualized:.0f}% a year)")
    elif abs_funding >= 0.0003:
        score += 2
        reasons.append(f"funding elevated at {funding*100:.4f}% ({annualized:.0f}% a year)")

    # v2-quirk: Persistence
    if persistence_hours >= 12:
        score += 3
        reasons.append(f"crowded trade mature, {persistence_hours:.0f}h old")
    elif persistence_hours >= 6:
        score += 2
        reasons.append(f"crowding steady for {persistence_hours:.0f}h")
    else:
        score += 1
        reasons.append(f"crowding fresh, {persistence_hours:.0f}h old")

    # v2-quirk: Trend
    trend = fh.get("trend", "").upper() if fh.get("trend") else ""
    if trend == "INCREASING":
        score += 1
        reasons.append("crowding still building")
    elif trend == "DECREASING":
        score -= 1
        reasons.append("crowding easing off")

    # v2-quirk: Regime confirmation
    if regime_confirms is True:
        score += 2
        reasons.append(f"funding regime backs the fade ({regime})")
    elif regime_confirms is None and regime is not None:
        reasons.append(f"funding regime is {regime}")

    # v2-quirk: SM concentration
    sm_pct = 0.0
    sm_traders = 0
    sm_dir = ""
    if sm:
        sm_dir = str(sm.get("direction", "")).upper()
        sm_pct = _f(sm.get("pct_of_top_traders_gain", 0))
        sm_traders = int(sm.get("trader_count", 0) or 0)

        if sm_dir == fade_direction:
            if sm_pct >= 10:
                score += 3
                reasons.append(f"smart money fading the crowd, {sm_pct:.1f}% ({sm_traders} traders)")
            elif sm_pct >= 5:
                score += 2
                reasons.append(f"smart money leans our way, {sm_pct:.1f}% ({sm_traders} traders)")
            else:
                score += 1
                reasons.append(f"smart money agrees, {sm_pct:.1f}% ({sm_traders} traders)")
        elif sm_dir == crowd_direction:
            if sm_pct >= 10:
                score -= 2
                reasons.append(f"careful: smart money with the crowd, {sm_pct:.1f}%")
            else:
                score -= 1
                reasons.append(f"smart money slightly with the crowd, {sm_pct:.1f}%")

        cc_15m = _f(sm.get("contribution_pct_change_15m", 0))
        if sm_dir == crowd_direction and cc_15m < -0.5:
            score += 1
            reasons.append(f"smart-money momentum fading ({cc_15m:.2f})")

    # v2-quirk: OI turnover bonus (sticky positions)
    oi_turnover = 0.0
    if oi > 0 and volume_24h > 0:
        oi_turnover = volume_24h / oi
        if oi_turnover < 0.5:
            score += 1
            reasons.append(f"positions look sticky, turnover {oi_turnover:.2f}x")

    # v2-quirk: Price already reversing
    p4h = _f(sm.get("token_price_change_pct_4h", 0)) if sm else 0
    if crowd_direction == "LONG" and p4h < -0.5:
        score += 1
        reasons.append(f"price already turning, {p4h:+.1f}% in 4h")
    elif crowd_direction == "SHORT" and p4h > 0.5:
        score += 1
        reasons.append(f"price already turning, {p4h:+.1f}% in 4h")

    # v2-quirk: lead reason
    reasons.insert(0, f"betting against the {crowd_direction} crowd on {name}")

    return {
        "token": name,
        "funding": funding,
        "crowd_direction": crowd_direction,
        "fade_direction": fade_direction,
        "score": score,
        "reasons": reasons,
        "annualized_pct": annualized,
        "persistence_hours": persistence_hours,
        "trend": trend,
        "regime": regime,
        "regime_confirms": bool(regime_confirms),
        "sm_pct": sm_pct,
        "sm_traders": sm_traders,
        "sm_direction": sm_dir,
        "p4h": p4h,
        "oi_usd": oi_usd,
        "oi_turnover": oi_turnover,
    }


def build_signal_data(c, leverage):
    """Build the validated data{} block for an emitted signal — the same field set the
    v2 producer's build_signal_payload put on `data` (minus heldAssets, which the runtime
    now owns via per-asset cooldown + reconcile). marginUsd/leverage are top-level on the
    signal (not here) per the 3.0 scan contract."""
    return {
        "mode": "FUNDING_FADE",
        "score": c["score"],
        "funding": c["funding"],
        "annualizedPct": round(c["annualized_pct"], 2),
        "persistenceHours": round(c["persistence_hours"], 1),
        "leverage": leverage,
        "crowdDirection": c["crowd_direction"],
        "regime": c.get("regime") or "UNKNOWN",
        "regimeConfirms": bool(c.get("regime_confirms", False)),
        "trend": c.get("trend") or "STABLE",
        "smPctOfTopTraders": float(c.get("sm_pct", 0)),
        "smTraderCount": int(c.get("sm_traders", 0)),
        "smDirection": c.get("sm_direction") or "",
        "priceChg4hPct": float(c.get("p4h", 0)),
        "oiUsd": float(c.get("oi_usd", 0)),
        "oiTurnover": round(c.get("oi_turnover", 0), 3),
        "reasons": c.get("reasons", []),
    }
