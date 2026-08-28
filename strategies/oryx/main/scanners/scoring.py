"""ORYX — pure intraday-breakout math for the XYZ venue. No I/O, no MCP, no clock.

Users kept asking to trade gold, crude, and the indices INTRADAY — and every one of our
27 XYZ templates is swing or position. ORYX is the intraday one: an opening-range break
with a volume confirm, on the 24/7 XYZ venue.

It carries SWIFT's fee gate unchanged, because XYZ books are thinner than crypto majors
and the cost floor matters more here, not less. The gate runs BEFORE the thesis.

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


def opening_range(candles, n):
    """(high, low) of the first n candles of the session window — the range to break."""
    rows = candles[:max(1, int(n))]
    highs = [_high(c) for c in rows if _high(c) > 0]
    lows = [_low(c) for c in rows if _low(c) > 0]
    if not highs or not lows:
        return None
    return max(highs), min(lows)


def build_thesis(asset, candles, inputs):
    """Break of the session's opening range, confirmed by volume and not yet extended.

    Returns None when the tape does not qualify — including when the market is too
    quiet to pay its own round trip.
    """
    if len(candles) < 30:
        return None
    ok_fee, fee_detail = clears_fees(candles, inputs)
    if not ok_fee:
        return None                                    # priced out — never reaches the thesis

    orn = int(_f(inputs.get("openingRangeCandles"), 12))
    rng = opening_range(candles, orn)
    if rng is None:
        return None
    hi, lo = rng
    if hi <= lo:
        return None
    last = _close(candles[-1])
    if last <= 0:
        return None

    buf = _f(inputs.get("breakBufferPct"), 0.05) / 100.0
    if last > hi * (1 + buf):
        direction, ref = "LONG", hi
    elif last < lo * (1 - buf):
        direction, ref = "SHORT", lo
    else:
        return None                                    # still inside the range — nothing to trade

    surge = vol_surge(candles, int(_f(inputs.get("volLookback"), 20)))
    if surge < _f(inputs.get("minVolSurge"), 1.3):
        return None                                    # a break on no volume is a fake-out

    r = rsi([_close(c) for c in candles if _close(c) > 0], int(_f(inputs.get("rsiPeriod"), 14)))
    if direction == "LONG" and r > _f(inputs.get("rsiMaxLong"), 92):
        return None
    if direction == "SHORT" and r < _f(inputs.get("rsiMinShort"), 8):
        return None

    ext = abs(last - ref) / ref * 100.0
    if ext > _f(inputs.get("maxChasePct"), 1.5):
        return None                                    # too far past the level — chasing

    width = (hi - lo) / last * 100.0
    score = round(min(20.0, (surge - 1.0) * 5.0 + width * 2.0 +
                      (fee_detail["expected_capture_pct"] / max(fee_detail["required_pct"], 1e-9))), 3)
    return {
        "asset": asset, "direction": direction, "score": score,
        "reasons": [f"broke {'above' if direction == 'LONG' else 'below'} the "
                    f"{orn}-candle opening range ({lo:.4f}-{hi:.4f})",
                    f"range width {width:.2f}%", f"vol x{surge:.2f}",
                    f"{ext:.2f}% past the level (chase cap {_f(inputs.get('maxChasePct'), 1.5)}%)",
                    f"rsi {r:.0f}",
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
