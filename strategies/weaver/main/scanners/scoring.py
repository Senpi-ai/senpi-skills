"""WEAVER — pure range-harvest math. No I/O, no MCP, no clock.

Most templates go quiet when nothing is trending — which is exactly when users ask
"why isn't it firing?". WEAVER is the one that works a RANGE: it buys the low end and
sells the high end of a well-contained band.

The thing that kills a range harvester is a trend: gridding into a market that has
started to run is how you average into a loss. So the trend filter is a HARD gate here,
not a score component, and the fee gate from Swift is carried over because a harvester
trades often.

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

# ── is this a RANGE, or a trend wearing a range's clothes? ──

def efficiency_ratio(candles, n):
    """Kaufman efficiency ratio: |net move| / total path travelled, over the last n.

    ~0 means price went back and forth and ended where it started (a RANGE); ~1 means it
    went in a straight line (a TREND). This is the gate that keeps WEAVER out of the one
    market type that reliably kills a harvester.

    Chosen over a simple |last - first| drift because that measure depends on where the
    window happens to start — the same range reads as trending or not depending on its
    phase, which made the floor and ceiling of an identical band behave differently.
    """
    closes = [_close(c) for c in candles[-int(n):] if _close(c) > 0]
    if len(closes) < 3:
        return 1.0                                     # too little data — assume the worst, veto
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        return 0.0
    return net / path


def range_bounds(candles, n):
    """(high, low, mid) of the containing band over the last n candles."""
    rows = candles[-int(n):]
    highs = [_high(c) for c in rows if _high(c) > 0]
    lows = [_low(c) for c in rows if _low(c) > 0]
    if not highs or not lows:
        return None
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return None
    return hi, lo, (hi + lo) / 2.0


def position_in_range(price, hi, lo):
    """0.0 at the range low, 1.0 at the range high."""
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (price - lo) / (hi - lo)))


def build_thesis(asset, candles, inputs):
    """Fade the edge of a well-contained range — and refuse anything that is trending.

    Order matters: fees, then the TREND VETO, then the edge. A harvester that grids a
    trend averages into a loss, so that veto is a hard gate rather than a score input.
    """
    n = int(_f(inputs.get("rangePeriod"), 48))
    if len(candles) < n + 2:
        return None
    ok_fee, fee_detail = clears_fees(candles, inputs)
    if not ok_fee:
        return None

    bounds = range_bounds(candles, n)
    if bounds is None:
        return None
    hi, lo, mid = bounds
    last = _close(candles[-1])
    if last <= 0:
        return None

    width = (hi - lo) / mid * 100.0
    if width < _f(inputs.get("minRangeWidthPct"), 1.5):
        return None                                    # too tight to harvest anything

    # ── THE TREND VETO — the gate that keeps a harvester alive ──
    er = efficiency_ratio(candles, n)
    if er > _f(inputs.get("maxEfficiencyRatio"), 0.35):
        return None                                    # it is going somewhere; do not grid it

    pos = position_in_range(last, hi, lo)
    edge = _f(inputs.get("edgeFraction"), 0.2)
    if pos <= edge:
        direction = "LONG"                             # at the floor of the band
    elif pos >= 1.0 - edge:
        direction = "SHORT"                            # at the ceiling
    else:
        return None                                    # mid-range: nothing to fade

    containment = 1.0 - min(1.0, er)
    score = round(min(20.0, containment * 10.0 + width * 0.8 +
                      (fee_detail["expected_capture_pct"] / max(fee_detail["required_pct"], 1e-9))), 3)
    return {
        "asset": asset, "direction": direction, "score": score,
        "reasons": [f"range {lo:.4f}-{hi:.4f} ({width:.2f}% wide) held for {n} candles",
                    f"price at {pos * 100:.0f}% of the range "
                    f"({'floor' if direction == 'LONG' else 'ceiling'})",
                    f"efficiency ratio {er:.2f} (0=pure range, 1=pure trend)",
                    f"capture {fee_detail['expected_capture_pct']:.3f}% vs fees "
                    f"{fee_detail['required_pct']:.3f}% needed"],
        "range_pos": round(pos, 4), "width_pct": round(width, 4),
        "efficiency_ratio": round(er, 4),
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
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 2)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 6)
    lev = min(lev, _f(inputs.get("maxLeverage"), 3))
    if venue_max:
        lev = min(lev, _f(venue_max, lev))
    mgn = min(mgn, _f(inputs.get("maxMarginPct"), 18))
    return int(max(1, lev)), round(max(0.1, mgn), 4)
