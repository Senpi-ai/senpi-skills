"""TERRAPIN — pure thesis math: Turtle-style breakout pyramid + MACD filter (no I/O, no clock).

A faithful port of the Turtle breakout, decomposed so each of the four pyramid UNITS lives on
its own wallet and is driven by the SAME code with a different `unitIndex` (0..3). One asset,
one direction at a time; the units stack into a single directional position as the trend runs.

The three ideas:

  DONCHIAN BREAKOUT   entry is a break of the prior-N-day channel — long above the N-day high,
                      short below the N-day low. The channel EXCLUDES the current bar, so a
                      break is price exceeding settled history, not itself.
  ATR LADDER (N)      unit k arms `k * addStep * N` beyond the breakout (½N steps by default),
                      the Turtle "add as it runs" rule expressed as a price level each wallet
                      can evaluate independently — no cross-wallet coordination.
  MACD FILTER         the user's "turtle con MACD": a long needs a positive MACD histogram, a
                      short a negative one. A breakout the momentum oscillator disagrees with is
                      the classic Turtle whipsaw, and this is what filters it.

The EXIT is not here. Classic Turtle exits the whole pyramid on one 2N / 10-day-low stop; our
twist hands each unit to the runtime DSL instead, calibrated per unit (base breathes, tip
ratchets). See the per-unit runtime.yaml. This module only decides ENTRY.

Pure + single-pass + unit-testable on plain candle lists. Candles are keyed o/h/l/c/v with
STRING values — `_f`/`_close` coerce both shapes.
"""


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


# ── indicators ───────────────────────────────────────────────────────────────

def donchian(candles, lookback):
    """(channel_high, channel_low) over the prior `lookback` bars, EXCLUDING the current
    (forming) bar — the standard breakout channel. None if too short."""
    if len(candles) < lookback + 1 or lookback < 2:
        return None
    window = candles[-(lookback + 1):-1]
    return max(_high(c) for c in window), min(_low(c) for c in window)


def atr(candles, period):
    """Wilder-style ATR (N) over `period` bars. 0.0 if too short.

    True range uses the prior close, so it captures gaps — which on 24/7 crypto are rare but on
    the XYZ weekend session are not."""
    if len(candles) < period + 1 or period < 1:
        return 0.0
    trs = []
    for i in range(len(candles) - period, len(candles)):
        hi, lo, pc = _high(candles[i]), _low(candles[i]), _close(candles[i - 1])
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _ema(vals, period):
    if len(vals) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(vals[:period]) / period]
    for v in vals[period:]:
        ema.append(v * k + ema[-1] * (1.0 - k))
    return ema


def macd_hist(candles, fast, slow, signal):
    """MACD histogram (macd_line - signal_line) on closes. None if too short.

    Sign is all the filter needs: > 0 confirms a long breakout, < 0 a short."""
    closes = [_close(c) for c in candles]
    if len(closes) < slow + signal:
        return None
    ef, es = _ema(closes, fast), _ema(closes, slow)
    if not ef or not es:
        return None
    n = min(len(ef), len(es))
    macd_line = [ef[-n + i] - es[-n + i] for i in range(n)]
    sig = _ema(macd_line, signal)
    if not sig:
        return None
    return macd_line[-1] - sig[-1]


def breakout_anchor(candles, lookback, direction):
    """The FROZEN channel level that started the current breakout run — computed statelessly by
    walking back to the bar that first broke out, so every unit's wallet derives the SAME anchor
    from the same candles with no shared state.

    Turtle adds units off the breakout price, not off a channel that keeps sliding up as price
    runs (which would leave the upper units perpetually un-armed in a normal trend). Anchoring to
    the frozen breakout level is what lets u3/u4 actually fire — the reason the four wallets exist.

    Returns the anchor price, or None if the current bar is not part of a breakout run.
    """
    if len(candles) < lookback + 2:
        return None
    i = len(candles) - 1
    anchor = None
    while i >= lookback:
        ch = donchian(candles[:i + 1], lookback)      # channel as of bar i (excludes bar i)
        if ch is None:
            break
        hi, lo = ch
        px = _close(candles[i])
        broken = (direction == "LONG" and px > hi) or (direction == "SHORT" and px < lo)
        if not broken:
            break                                      # walked back past the start of the run
        anchor = hi if direction == "LONG" else lo     # keep the earliest still-broken channel
        i -= 1
    return anchor


# ── the pyramid trigger ────────────────────────────────────────────────────────

def build_thesis(coin, candles, unit_index, inputs):
    """Decide whether THIS unit should be armed on `coin`. Returns a thesis dict or None.

    unit_index 0..3 is the unit's rung. Unit k arms only once price has extended
    `k * addStep * N` beyond the Donchian breakout in the trend direction — so u1 fires on the
    break, u4 only in a move that has already run ~1.5N. The MACD sign must agree.

    Scoring (max 9; `minScore` gates the MACD quality, the geometry is a hard gate):
      +4  price has cleared this unit's ladder level (the hard entry gate)
      +2  MACD histogram agrees with the breakout direction
      +2  MACD agreement is strong (|hist| vs price)
      +1  price is meaningfully beyond the rung (momentum, not a graze)
    """
    lookback = int(inputs.get("breakoutLookback", 20))
    atr_period = int(inputs.get("atrPeriod", 20))
    add_step = float(inputs.get("addStepN", 0.5))
    fast = int(inputs.get("macdFast", 12))
    slow = int(inputs.get("macdSlow", 26))
    signal = int(inputs.get("macdSignal", 9))
    require_macd = bool(inputs.get("requireMacd", True))
    strong_hist_pct = float(inputs.get("macdStrongHistPct", 0.05))

    need = max(lookback + 1, atr_period + 1, slow + signal)
    if len(candles) < need:
        return None

    ch = donchian(candles, lookback)
    n = atr(candles, atr_period)
    if ch is None or n <= 0:
        return None
    ch_high, ch_low = ch
    price = _close(candles[-1])
    if price <= 0:
        return None

    # direction is set by which channel edge price has broken (current bar excluded from the channel)
    if price > ch_high:
        direction = "LONG"
    elif price < ch_low:
        direction = "SHORT"
    else:
        return None                              # inside the channel — no breakout, no unit

    # arm off the FROZEN breakout anchor, not the live (sliding) channel — so u3/u4 fire in a
    # normal sustained trend instead of only in a fast spike.
    anchor = breakout_anchor(candles, lookback, direction)
    if anchor is None:
        anchor = ch_high if direction == "LONG" else ch_low   # fallback: current channel edge
    if direction == "LONG":
        rung = anchor + unit_index * add_step * n
        cleared = price >= rung
        beyond = (price - rung) / n if cleared else 0.0
    else:
        rung = anchor - unit_index * add_step * n
        cleared = price <= rung
        beyond = (rung - price) / n if cleared else 0.0

    if not cleared:
        return None                              # price hasn't reached THIS unit's rung yet

    hist = macd_hist(candles, fast, slow, signal)
    macd_ok = hist is not None and ((direction == "LONG" and hist > 0) or
                                    (direction == "SHORT" and hist < 0))
    if require_macd and not macd_ok:
        return None                              # the MACD filter vetoes the breakout

    score, reasons = 4, [
        f"unit {unit_index + 1} armed: {direction} breakout of the {lookback}-bar channel",
        f"rung {rung:.6g} = channel {anchor:.6g} {'+' if direction == 'LONG' else '-'} "
        f"{unit_index}x{add_step:g}N (N={n:.6g})",
    ]
    hist_pct = abs(hist) / price * 100.0 if hist is not None else 0.0
    if macd_ok:
        score += 2
        reasons.append(f"MACD hist {hist:+.6g} agrees")
        if hist_pct >= strong_hist_pct:
            score += 2
            reasons.append(f"MACD strong ({hist_pct:.3f}% of price)")
    if beyond >= float(inputs.get("beyondRungN", 0.15)):
        score += 1
        reasons.append(f"price {beyond:.2f}N beyond the rung")

    return {"coin": coin, "direction": direction, "score": score, "unit_index": unit_index,
            "atr": round(n, 8), "channel_high": round(ch_high, 8), "channel_low": round(ch_low, 8),
            "rung": round(rung, 8), "beyond_n": round(beyond, 4),
            "macd_hist": None if hist is None else round(hist, 10), "reasons": reasons}


def margin_tier_pct(score, base_pct):
    """Conviction sizing on the PERCENT scale (base_pct is a PERCENT in (0,100])."""
    if score >= 8:
        return base_pct * 1.25
    if score >= 6:
        return base_pct * 1.1
    return base_pct
