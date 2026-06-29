"""BADGER — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Badger producer's technical helpers +
OI-confirmed-breakout scoring (SKILL.md v1.0.0, badger-producer.py v1.0.1). The
math/indexing is reproduced VERBATIM so a fidelity harness can diff this against
the v2 producer on the same market snapshot. v2 quirks are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port is
validated.

Multi-asset, single-pass, unit-testable on plain candle lists. The three HARD
gates (price breakout, OI rising, smart-money agreement) and the OI-velocity
read live in scan.py (they need MCP + cross-tick OI cache); this module is the
pure scorer over already-fetched candles + already-resolved gate values.

`build_thesis` here is called by scan.py ONLY after all three gates have passed,
so it never returns None — it assembles the score + reasons. The caller gates on
`thesis['score'] >= minScore`.
"""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only (via the _f primary/alt helper); the list branch is
# defensive and never fires on dict candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


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
        return _f(c.get("volume", c.get("v", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from v2 badger-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from v2.

    v2-quirk: thresholds use strict (>) for higher-lows / lower-highs counting,
    and the BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1.
    Returns (label, strength)."""
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


def breakout_signal(candles_1h, lookback):
    """Returns (direction, magnitude_pct) where direction is 'UP' (latest close
    above the prior `lookback`-bar high), 'DOWN' (below the prior low), or None.

    Verbatim from v2 breakout_signal: takes the last lookback+1 closes, compares
    the latest close against the max/min of the PRIOR `lookback` closes."""
    if len(candles_1h) < lookback + 1:
        return None, 0.0
    closes = [_close(c) for c in candles_1h[-(lookback + 1):]]
    latest = closes[-1]
    prior = closes[:-1]
    if not prior or latest <= 0:
        return None, 0.0
    hi, lo = max(prior), min(prior)
    if hi > 0 and latest > hi:
        return "UP", ((latest - hi) / hi) * 100
    if lo > 0 and latest < lo:
        return "DOWN", ((lo - latest) / lo) * 100
    return None, 0.0


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2.

    v2-quirk: Badger's volume_trend guards on `len(candles) < lookback` (NOT
    lookback+2 like Bison) and slices vols = last `lookback` bars, so recent/
    earlier are the two halves of exactly `lookback` bars. Reproduced exactly."""
    if len(candles) < lookback:
        return 0.0
    vols = [_vol(c) for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ── the thesis (OI-confirmed breakout score), ported verbatim from v2 build_thesis ──

def build_thesis(coin, direction, bo_dir, bo_mag, oi_pct, oi_src,
                 candles_1h, candles_4h, sm_dir, sm_tilt, inputs):
    """Port of v2 build_thesis's SCORING half (gates already passed in scan.py).

    Inputs:
      - direction: "LONG"/"SHORT" (= 'UP'->LONG / 'DOWN'->SHORT from breakout_signal)
      - bo_dir: "UP"/"DOWN"; bo_mag: breakout magnitude % (>=0)
      - oi_pct: OI 1h change % (gate already confirmed >= oiRisingMinPct); oi_src tag
      - sm_dir: "LONG"/"SHORT" (gate already confirmed == direction); sm_tilt: %
      - candles_4h: for 4h trend-structure confirmation bonus
      - candles_1h: for the volume-rising bonus

    Returns a thesis dict with `score` + `reasons`. minScore is applied by the
    CALLER (scan.py). Scoring weights/cutoffs are VERBATIM from v2:
      breakout magnitude: +3 (>=1.0%) / +2 (>=0.3%) / +1 (else)
      OI rising (gate-confirmed): +2 ; OI strongly building (>= oiStrongPct): +1
      SM aligned (gate-confirmed): +2 ; SM strongly tilted (>= smStrongTiltPct): +1
      4h structure confirms direction: +1
      volume rising (> 10%): +1
    """
    oi_strong = float(inputs.get("oiStrongPct", 5.0))
    sm_strong = float(inputs.get("smStrongTiltPct", 70))

    trend_4h, _str_4h = trend_structure(candles_4h)

    score = 0
    reasons = []

    # Breakout magnitude
    if bo_mag >= 1.0:
        score += 3
    elif bo_mag >= 0.3:
        score += 2
    else:
        score += 1
    reasons.append(f"breakout_{bo_dir.lower()}_{bo_mag:+.2f}%")

    # OI rising (gate-confirmed) + strongly-building bonus
    score += 2
    reasons.append(f"oi_rising_{oi_pct:+.1f}%_{oi_src}")
    if oi_pct >= oi_strong:
        score += 1
        reasons.append("oi_strongly_building")

    # SM aligned (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    # 4h structure confirms breakout direction
    if (direction == "LONG" and trend_4h == "BULLISH") or \
       (direction == "SHORT" and trend_4h == "BEARISH"):
        score += 1
        reasons.append(f"4h_confirms_{trend_4h.lower()}")

    # Volume rising bonus
    vol_pct = volume_trend(candles_1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "breakout_dir": bo_dir,
        "breakout_mag_pct": round(bo_mag, 3),
        "oi_change_pct": round(oi_pct, 3),
        "oi_source": oi_src,
        "trend_4h": trend_4h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": _f(sm_tilt),
        "volume_trend_pct": round(vol_pct, 2),
    }
