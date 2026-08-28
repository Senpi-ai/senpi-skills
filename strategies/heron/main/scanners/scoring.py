"""HERON — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Heron producer's `build_thesis` +
`trend_structure` + `volume_trend` (SKILL.md v1.0.0). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same market snapshot. Behaviour-preserving quirks from v2 are kept and
flagged `# v2-quirk`; fix them only as a separate, labelled change AFTER the
port is validated.

HERON IS THE ONBOARDING PORT — deliberately SIMPLER than kodiak/polar:
  - Only 1h + 4h candles (no 5m/15m).
  - NO RSI gate, NO funding gate, NO OI, NO BTC correlation, NO time-of-day,
    NO momentum gates. Just 4h trend structure + a Smart-Money direction gate.
  - 5-component score, max ~9 (NOT kodiak's ~20-scale composite).
  - FIXED 5x leverage — NO conviction tiering (kodiak/polar tier; heron does not).
The SM direction agreement is a HARD gate (required), not a soft bonus.

`sm` is the smart-money dict {direction, tilt} or None; the caller (scan.py)
fetches it. This module stays pure — single-asset, single-pass, unit-testable
on plain candle lists."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

def _f(v, d=0.0):
    if v is None:
        return d
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


# ── indicators (ported VERBATIM from v2 heron-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Returns (label, strength_pct)
    where strength_pct is the fraction of recent candles that confirmed the structure.

    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1], highs[i] < highs[i-1]) —
    # kodiak/polar use >= / <=. Heron is strict. And it returns strength 0.0 on
    # NEUTRAL (kodiak returns max(bullish,bearish)). Reproduced verbatim for fidelity."""
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
    """% change in avg(recent half) vs avg(first half) volume.

    # v2-quirk: 'earlier' is the FIRST `half` candles (vols[:half]), not the
    # middle slice — i.e. it compares the last `half` against the first `half`
    # of the lookback window. Reproduced verbatim."""
    if len(candles) < lookback:
        return 0.0
    vols = [_vol(c) for c in candles[-lookback:]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ── the thesis (gates + 5-component score), ported VERBATIM from build_thesis ──

def build_thesis(c1h, c4h, sm, inputs):
    """Score an ETH entry. Returns a thesis dict (with `score`) or None if any gate
    blocks. `sm` is the smart-money dict {direction, tilt} or None (the caller fetches it).

    Direction logic (all required):
      1. 4h trend != NEUTRAL
      2. SM direction in {LONG, SHORT} and SM tilt >= smTiltMinPct
      3. 4h-derived direction == SM direction

    Score components (max ~9):
      4h trend aligned   +3
      1h confirms 4h     +2
      SM aligned         +2  (always, gate-confirmed)
      SM strongly tilted +1  (tilt >= smStrongTiltPct)
      Volume rising      +1  (1h volume_trend > 10%)"""
    sm_tilt_min = float(inputs.get("smTiltMinPct", 60))
    sm_strong_pct = float(inputs.get("smStrongTiltPct", 70))

    # v2-quirk: gate requires >=6 candles on BOTH 1h and 4h (no 5m/15m at all).
    if len(c4h) < 6 or len(c1h) < 6:
        return None

    trend_4h, str_4h = trend_structure(c4h)
    trend_1h, str_1h = trend_structure(c1h)
    if trend_4h == "NEUTRAL":
        return None

    # ── SM gate (HARD) ──
    sm_dir = (sm or {}).get("direction")
    sm_tilt = _f((sm or {}).get("tilt", 0))
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
    reasons.append(f"4-hour trend {trend_4h.lower()} at {str_4h:.0%} strength")

    # 1h confirms 4h
    if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
        score += 2
        reasons.append(f"1-hour trend confirms, also {trend_1h.lower()}")

    # SM aligned (gate-confirmed, always scores)
    score += 2
    reasons.append(f"smart money {sm_tilt:.0f}% on the same side")

    # SM strongly tilted
    if sm_tilt >= sm_strong_pct:
        score += 1
        reasons.append("smart money is strongly one-sided")

    # Volume rising
    vol_pct = volume_trend(c1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"volume rising {vol_pct:+.0f}%")

    return {
        "direction": direction,
        "score": score,                       # v2-quirk: integer, NOT rounded (kodiak rounds)
        "trend_4h": trend_4h,
        "trend_4h_strength": round(str_4h, 3),
        "trend_1h": trend_1h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": round(sm_tilt, 2),
        "volume_trend_pct": round(vol_pct, 2),
        "reasons": reasons,
    }
