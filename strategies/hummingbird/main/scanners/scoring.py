"""HUMMINGBIRD — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Hummingbird producer's `build_thesis`
(SKILL.md v1.0.0): an onboarding-tier HYPE trend follower with a Smart-Money
direction gate. The math/indexing is reproduced VERBATIM from the v2 producer
so a fidelity harness can diff this against it on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix them
only as a separate, labelled change AFTER the port is validated.

Single-asset (HYPE), single-pass, unit-testable on plain candle lists. The
smart-money direction is fetched by the caller and passed in as `sm` so this
stays pure (no MCP, no clock)."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict
# candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _high(c):
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[3])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from the v2 producer) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH.

    Returns (label, strength) where strength is the fraction of recent candles
    that confirmed the structure. Direction fires at >= 0.6 of the lookback.

    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1], highs[i] < highs[i-1]),
    # unlike the Kodiak family which uses >= / <=. Reproduced verbatim — do not
    # relax to >=/<= inside the port.
    # v2-quirk: the NEUTRAL branch returns strength 0.0 (not max(bullish,bearish));
    # reproduced verbatim.
    """
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def volume_trend(candles, lookback=6):
    """% change in avg(recent half) vs avg(earlier half) volume.

    # v2-quirk: when len < lookback returns 0.0, and the earlier/recent windows
    # are computed off the LAST `lookback` candles via [:half] and [-half:].
    # Reproduced verbatim from the v2 producer.
    """
    if len(candles) < lookback:
        return 0.0
    vols = [_vol(c) for c in candles[-lookback:]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


def sm_split(long_pct, short_pct):
    """Net smart-money lean from leaderboard long/short concentration.

    Returns (direction, tilt_pct). `direction` in {LONG, SHORT, NEUTRAL}; tilt_pct
    is the winning side's share of the total (e.g. 70 = 70% leaning that way).

    # v2-quirk: the split uses a hard >= 50 boundary (no neutral dead-band like
    # the Kodiak family's 42/58). Exactly 50% resolves to LONG. Reproduced verbatim.
    """
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


# ── the thesis (gates + 5-component score), ported verbatim from build_thesis ──

def build_thesis(asset, c1h, c4h, sm, inputs):
    """Score a HYPE entry. Returns a thesis dict (with `score`) or None if any
    gate blocks. `sm` is the smart-money dict {direction, tilt} or None (the
    caller fetches it via leaderboard_get_markets).

    Direction gate (all required):
      1. 4h trend != NEUTRAL
      2. SM direction in {LONG, SHORT} AND tilt >= smTiltMinPct (default 60)
      3. SM direction agrees with the 4h trend direction

    Score components (max ~9):
      4h trend aligned   +3
      1h confirms 4h     +2
      SM aligned         +2
      SM strongly tilted +1  (when sm_tilt >= smStrongTiltPct, default 70)
      Volume rising      +1  (1h volume_trend > 10%)
    """
    sm_tilt_min = float(inputs.get("smTiltMinPct", 60))
    sm_strong_pct = float(inputs.get("smStrongTiltPct", 70))

    # v2-quirk: requires >= 6 candles on BOTH 1h and 4h (build_thesis len-guard).
    if len(c4h) < 6 or len(c1h) < 6:
        return None

    trend_4h, str_4h = trend_structure(c4h)
    trend_1h, str_1h = trend_structure(c1h)
    if trend_4h == "NEUTRAL":
        return None

    # ── Smart-Money direction gate (hard) ──
    sm_dir = (sm or {}).get("direction")
    sm_tilt = _f((sm or {}).get("tilt", 0.0))
    if sm_dir not in ("LONG", "SHORT"):
        return None
    if sm_tilt < sm_tilt_min:
        return None

    # 4h trend direction and SM direction must agree
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if sm_dir != direction:
        return None

    # ── ALL GATES PASSED — SCORE ──
    score = 0
    reasons = []

    # 4h trend aligned
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{str_4h:.0%}")

    # 1h confirms 4h
    if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{trend_1h.lower()}")

    # SM aligned (gate-confirmed)
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    # SM strongly tilted
    if sm_tilt >= sm_strong_pct:
        score += 1
        reasons.append("sm_strongly_tilted")

    # Volume rising
    vol_pct = volume_trend(c1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": trend_4h,
        "trend_4h_strength": round(str_4h, 3),
        "trend_1h": trend_1h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": round(sm_tilt, 2),
        "volume_trend_pct": round(vol_pct, 2),
    }
