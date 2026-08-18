"""BEAVER — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Beaver producer's `build_thesis` +
5-component scoring (SKILL.md v1.0.0 / producer v1.0.1). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same market snapshot. Behaviour-preserving quirks from v2 are kept and
flagged `# v2-quirk`; fix them only as a separate, labelled change AFTER the port
is validated.

Onboarding-tier BTC trend follower with a Smart-Money direction gate. SIMPLER
than kodiak's alpha-hunter engine: no RSI, no funding, no BTC-macro, no
time-of-day, no 5m/15m bars, no conviction-tiered leverage (flat 5x). The
`trend_structure` here is BEAVER's own (strict >/<, NEUTRAL -> strength 0.0) and
is intentionally NOT kodiak's (>=/<=, NEUTRAL -> max(bullish,bearish)).

Single-asset, single-pass, unit-testable on plain candle lists."""


# ── candle accessors (dual-shape: dict {high|h, low|l, volume|v|vlm} OR list) ──
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


# ── indicators (ported VERBATIM from beaver-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH.

    Returns (label, strength_pct) where strength_pct is the fraction of recent
    candles that confirmed the structure.

    # v2-quirk: BEAVER uses STRICT inequalities (lows[i] > lows[i-1],
    # highs[i] < highs[i-1]) and a 0.6 threshold on (lookback-1), and returns
    # strength 0.0 on NEUTRAL. This DIFFERS from kodiak's trend_structure
    # (>=/<= and NEUTRAL -> max(bullish,bearish)). Do NOT swap in kodiak's.
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
    """% change in avg(recent 3) vs avg(prior 3) volume. Ported verbatim."""
    if len(candles) < lookback:
        return 0.0
    vols = [_vol(c) for c in candles[-lookback:]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ── the thesis (gates + 5-component score), ported verbatim from build_thesis ──

def build_thesis(candles_1h, candles_4h, sm_dir, sm_tilt, inputs):
    """Score a BTC entry. Returns a thesis dict (with `score`) or None if any gate
    blocks. `sm_dir`/`sm_tilt` are the smart-money direction + tilt% (the caller
    fetches them via leaderboard_get_markets).

    Direction logic (all required):
      1. 4h trend != NEUTRAL
      2. SM direction in {LONG, SHORT}
      3. SM tilt >= smTiltMinPct (default 60)
      4. SM direction agrees with the 4h-trend direction

    Score components (max ~9):
      4h trend aligned     +3
      1h confirms 4h       +2
      SM aligned           +2
      SM strongly tilted   +1  (when smTilt >= smStrongTiltPct, default 70)
      Volume rising        +1  (1h volume_trend > 10%)
    """
    # v2-quirk: producer requires >=6 candles on BOTH 1h and 4h (not the
    # per-interval minimums kodiak uses). Reproduced verbatim.
    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    trend_4h, str_4h = trend_structure(candles_4h)
    trend_1h, str_1h = trend_structure(candles_1h)
    if trend_4h == "NEUTRAL":
        return None

    # SM gate
    sm_tilt_min = float(inputs.get("smTiltMinPct", 60))
    sm_strong_pct = float(inputs.get("smStrongTiltPct", 70))
    if sm_dir not in ("LONG", "SHORT"):
        return None
    if sm_tilt < sm_tilt_min:
        return None

    # 4h trend direction and SM direction must agree
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if sm_dir != direction:
        return None

    score = 0
    reasons = []

    # 4h trend aligned
    score += 3
    reasons.append(f"4h trend {trend_4h.lower()} ({str_4h:.0%} strength)")

    # 1h confirms 4h
    if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
        score += 2
        reasons.append(f"1h trend also {trend_1h.lower()}")

    # SM aligned
    score += 2
    reasons.append(f"smart money aligned at {sm_tilt:.0f}%")

    # SM strongly tilted
    if sm_tilt >= sm_strong_pct:
        score += 1
        reasons.append("smart money heavily tilted this way")

    # Volume rising (1h)
    vol_pct = volume_trend(candles_1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"volume rising {vol_pct:+.0f}%")

    return {
        "coin": "BTC",
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": trend_4h,
        "trend_4h_strength": round(str_4h, 3),
        "trend_1h": trend_1h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": round(_f(sm_tilt), 2),
        "volume_trend_pct": round(vol_pct, 3),
    }
