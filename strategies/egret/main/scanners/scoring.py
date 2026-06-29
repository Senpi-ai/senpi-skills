"""EGRET — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Egret producer's `build_thesis` +
exhausted-crowd fade scoring (egret-producer.py v1.0.1 / SKILL.md v1.0.0). The
math/indexing is reproduced VERBATIM so a fidelity harness can diff this against
the v2 producer on the same market snapshot. Behaviour-preserving quirks from v2
are kept and flagged `# v2-quirk`.

Egret is a CONTRARIAN FADER, not a momentum scorer. The thesis: when the
Smart-Money crowd is extremely concentrated one way (>= crowdingMinPct) but price
is NOT confirming over the recent window (divergence), the crowded side is
exhausted and the unwind is the edge — fade it. Crowded LONG + price stalled/down
-> FADE SHORT; crowded SHORT + price stalled/up -> FADE LONG. RSI exhaustion adds
conviction.

`sm` (the crowded side + concentration pct) is fetched by the caller and passed
in, so this module stays pure. Multi-asset, single-pass, unit-testable on plain
candle lists.

Two HARD GATES (return None if either fails):
  1. SM concentration >= crowdingMinPct (the crowd must be crowded), AND
  2. price not confirming the crowd over the lookback (divergence).
minScore is applied by the CALLER (scan.py), not here.
"""

# v2 producer defaults (egret-producer.py)
DEFAULT_CROWDING_MIN_PCT = 70.0       # SM concentration must be >= this to be "crowded"
DEFAULT_CROWDING_EXTREME_PCT = 80.0   # ultra-crowded bonus threshold
DEFAULT_CONFIRM_MAX_PCT = 0.5         # if price moved less than this WITH the crowd, it's diverging
DEFAULT_DIVERGENCE_LOOKBACK = 4       # 1h bars for the price-confirmation window


def _f(c, primary="close", alt="c", default=0.0):
    """Numeric accessor matching v2 `_f(c, primary, alt, default)`: pull `primary`
    from a candle dict, fall back to `alt`, coerce to float. Plain-float callers
    pass the value directly via `_num`."""
    if isinstance(c, dict):
        val = c.get(primary)
        if val is None and alt:
            val = c.get(alt)
    else:
        val = c
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def _num(v, default=0.0):
    """Plain scalar -> float (for non-candle values)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── indicators (ported verbatim from v2 egret-producer.py) ──

def price_momentum(candles, n_bars):
    """% change over the last n_bars (1h bars). Verbatim from v2 price_momentum."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f(candles[-(n_bars + 1)], "close", "c")
    new = _f(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def calc_rsi(closes, period=14):
    """RSI over the LAST `period` gains/losses. Verbatim from v2 calc_rsi
    (trailing-window: gains[-period:] / losses[-period:])."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ── the thesis (two gates + exhausted-crowd fade score), ported verbatim ──

def build_thesis(asset, candles_1h, sm, entry_cfg):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when ANY of:
      - the crowded side is not LONG/SHORT (no SM read / NEUTRAL), OR
      - SM concentration < crowdingMinPct (GATE 1 — crowd not crowded), OR
      - insufficient 1h candle history (< 16 bars), OR
      - price IS confirming the crowd (GATE 2 — no divergence, don't fade a
        working trend).

    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is (crowded_direction, concentration_pct) or (None, 0); the caller
    fetches it (scan._get_sm_direction)."""
    sm_dir, sm_pct = sm if sm else (None, 0.0)

    # GATE 1 — extreme SM crowding
    if sm_dir not in ("LONG", "SHORT"):
        return None
    crowd_min = float(entry_cfg.get("crowdingMinPct", DEFAULT_CROWDING_MIN_PCT))
    crowd_extreme = float(entry_cfg.get("crowdingExtremePct", DEFAULT_CROWDING_EXTREME_PCT))
    if sm_pct < crowd_min:
        return None

    if len(candles_1h) < 16:
        return None
    closes = [_f(c, "close", "c") for c in candles_1h]

    lookback = int(entry_cfg.get("divergenceLookbackHours", DEFAULT_DIVERGENCE_LOOKBACK))
    confirm_max = float(entry_cfg.get("confirmMaxPct", DEFAULT_CONFIRM_MAX_PCT))
    mom = price_momentum(candles_1h, lookback)
    rsi = calc_rsi(closes)

    # GATE 2 — price NOT confirming the crowd (divergence)
    # Crowded LONG but price hasn't risen (mom <= confirm_max) -> exhaustion -> FADE SHORT.
    # Crowded SHORT but price hasn't fallen (mom >= -confirm_max) -> FADE LONG.
    if sm_dir == "LONG":
        if mom > confirm_max:
            return None  # crowd long AND price rising — no divergence, don't fade a working trend
        fade_direction = "SHORT"
    else:  # SHORT
        if mom < -confirm_max:
            return None
        fade_direction = "LONG"

    score = 0
    reasons = []

    # SM crowding (gate-confirmed) + ultra-crowded bonus
    score += 2
    reasons.append(f"sm_crowded_{sm_dir.lower()}_{sm_pct:.0f}%")
    if sm_pct >= crowd_extreme:
        score += 1
        reasons.append("sm_ultra_crowded")

    # Divergence magnitude — the further price is from confirming, the stronger
    if (sm_dir == "LONG" and mom <= -0.5) or (sm_dir == "SHORT" and mom >= 0.5):
        score += 3
        reasons.append(f"price_diverging_{mom:+.2f}%")
    else:
        score += 2
        reasons.append(f"price_stalled_{mom:+.2f}%")

    # RSI exhaustion confirms the fade
    if fade_direction == "SHORT" and rsi >= 68:
        score += 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif fade_direction == "LONG" and rsi <= 32:
        score += 2
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif (fade_direction == "SHORT" and rsi >= 60) or (fade_direction == "LONG" and rsi <= 40):
        score += 1
        reasons.append(f"rsi_stretched_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": fade_direction,
        "score": score,
        "reasons": reasons,
        "sm_crowd_direction": sm_dir,
        "sm_crowd_pct": _num(sm_pct),
        "price_momentum_pct": round(mom, 3),
        "rsi": round(rsi, 1),
    }
