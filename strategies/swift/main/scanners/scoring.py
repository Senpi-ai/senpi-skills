"""SWIFT — pure scalp math. No I/O, no MCP, no clock.

A scalper's only real enemy is its own cost. At 5-15 trades a day the round trip is
charged every time, so SWIFT refuses to look at a signal until the move it expects to
capture clears the fees by a stated multiple. That gate runs BEFORE the thesis, not
after, so a strong-looking setup on a market too quiet to pay for itself is never taken.

Everything here is deterministic and unit-testable.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None — distinguishes a real 0.0 from a missing field."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── candle accessors (dual shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# market_get_asset_data returns o/h/l/c/v as STRINGS; _f coerces.

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
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── THE FEE GATE — the reason this template exists ──

def roundtrip_fee_pct(inputs, maker_entry=True):
    """Cost of one full round trip, as a PERCENT OF PRICE (not of margin).

    Defaults are conservative placeholders, NOT verified venue rates — the exact
    per-wallet schedule is on HL's Info API (`userFees`) and varies with volume tier
    and Senpi points. Operators should set these from a real read; they are inputs so
    nobody has to edit code to correct them.
    """
    taker = _f(inputs.get("takerFeeBps"), 4.5) / 100.0     # bps -> percent
    maker = _f(inputs.get("makerFeeBps"), 1.5) / 100.0
    entry = maker if maker_entry else taker
    return entry + taker                                    # exits are taker-ish (DSL closes)


def atr_pct(candles, n=14):
    """Average true range over the last n candles, as a percent of the last close."""
    if not candles or len(candles) < 2:
        return 0.0
    rows = candles[-(n + 1):]
    trs = []
    for i in range(1, len(rows)):
        prev_c = _close(rows[i - 1])
        hi, lo = _high(rows[i]), _low(rows[i])
        if hi <= 0 or lo <= 0:
            continue
        trs.append(max(hi - lo, abs(hi - prev_c), abs(lo - prev_c)))
    last = _close(rows[-1])
    if not trs or last <= 0:
        return 0.0
    return (sum(trs) / len(trs)) / last * 100.0


def expected_capture_pct(candles, inputs):
    """What a scalp can realistically take out of this tape: a FRACTION of ATR.

    Capturing a whole ATR per trade is fantasy; the fraction is the honest planning
    number and is what the fee gate is measured against.
    """
    return atr_pct(candles, int(_f(inputs.get("atrPeriod"), 14))) * \
        _f(inputs.get("atrCaptureFraction"), 0.5)


def clears_fees(candles, inputs, maker_entry=True):
    """(bool, detail) — is this market moving enough to pay for trading it?

    The whole point: a scalper that trades a market quieter than its own cost base
    loses by construction, no matter how good the entry signal looks.
    """
    cap = expected_capture_pct(candles, inputs)
    fee = roundtrip_fee_pct(inputs, maker_entry)
    need = fee * _f(inputs.get("feeCoverMultiple"), 3.0)
    return cap >= need, {"expected_capture_pct": round(cap, 4),
                         "roundtrip_fee_pct": round(fee, 4),
                         "required_pct": round(need, 4)}


# ── the scalp thesis ──

def ema(values, period):
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses <= 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def vol_surge(candles, lookback=20):
    """Latest candle volume vs the trailing mean (1.0 = average)."""
    if len(candles) < lookback + 1:
        return 1.0
    hist = [_vol(c) for c in candles[-(lookback + 1):-1]]
    mean = sum(hist) / len(hist) if hist else 0.0
    if mean <= 0:
        return 1.0
    return _vol(candles[-1]) / mean


def build_thesis(asset, fast, slow, inputs):
    """Micro-momentum aligned across two short timeframes, volume-confirmed.

    fast = 1m/5m candles (the trigger), slow = 15m (the filter). Returns None when
    the tape does not qualify — including when it cannot pay its own fees.
    """
    if len(fast) < 25 or len(slow) < 10:
        return None
    ok_fee, fee_detail = clears_fees(fast, inputs)
    if not ok_fee:
        return None                                    # priced out — never reaches the thesis

    closes = [_close(c) for c in fast if _close(c) > 0]
    if len(closes) < 25:
        return None
    ef = ema(closes[-9:], 9)
    es = ema(closes[-21:], 21)
    last = closes[-1]
    slow_closes = [_close(c) for c in slow if _close(c) > 0]
    if len(slow_closes) < 5:
        return None
    slow_trend = slow_closes[-1] - ema(slow_closes[-10:], 10)

    if ef > es and slow_trend > 0:
        direction = "LONG"
    elif ef < es and slow_trend < 0:
        direction = "SHORT"
    else:
        return None                                    # timeframes disagree — no trade

    r = rsi(closes, int(_f(inputs.get("rsiPeriod"), 14)))
    if direction == "LONG" and r > _f(inputs.get("rsiMaxLong"), 78):
        return None                                    # already extended; the scalp is late
    if direction == "SHORT" and r < _f(inputs.get("rsiMinShort"), 22):
        return None

    surge = vol_surge(fast, int(_f(inputs.get("volLookback"), 20)))
    if surge < _f(inputs.get("minVolSurge"), 1.2):
        return None                                    # no participation behind the move

    sep = abs(ef - es) / last * 100.0 if last > 0 else 0.0
    score = round(min(20.0, sep * 4.0 + (surge - 1.0) * 3.0 +
                      (fee_detail["expected_capture_pct"] / max(fee_detail["required_pct"], 1e-9))), 3)
    return {
        "asset": asset, "direction": direction, "score": score,
        "reasons": [f"ema9{'>' if direction == 'LONG' else '<'}ema21 sep {sep:.2f}%",
                    f"15m trend {'up' if slow_trend > 0 else 'down'}",
                    f"vol x{surge:.2f}", f"rsi {r:.0f}",
                    f"capture {fee_detail['expected_capture_pct']:.3f}% vs fees "
                    f"{fee_detail['required_pct']:.3f}% needed"],
        "fees": fee_detail,
    }


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 12):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 8):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100] — never a fraction."""
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 3)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 6)
    lev = min(lev, _f(inputs.get("maxLeverage"), 4))
    if venue_max:
        lev = min(lev, _f(venue_max, lev))
    mgn = min(mgn, _f(inputs.get("maxMarginPct"), 20))
    return int(max(1, lev)), round(max(0.1, mgn), 4)
