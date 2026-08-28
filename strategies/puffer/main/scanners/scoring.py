"""PUFFER — pure volatility-squeeze math. No I/O, no MCP, no clock.

Markets alternate between coiling and releasing. PUFFER measures how compressed a
market's volatility is RELATIVE TO ITS OWN recent history — an absolute band width is
meaningless across a universe — waits for the coil, and trades the release.

Users built this by hand repeatedly ("vol-squeeze") before we shipped it, which is the
strongest signal a template is missing. The fee gate is carried over from Swift.

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

# ── THE SQUEEZE: volatility contracting, then releasing ──

def stdev(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def bandwidth_pct(candles, n):
    """Bollinger-style band width over the last n closes, as a percent of the mean.

    This is the volatility *state* variable — the thing that contracts before it
    expands. Percent-of-mean so it is comparable across a $100k BTC and a $0.02 alt.
    """
    closes = [_close(c) for c in candles[-int(n):] if _close(c) > 0]
    if len(closes) < 2:
        return 0.0
    m = sum(closes) / len(closes)
    if m <= 0:
        return 0.0
    return (2.0 * stdev(closes)) / m * 100.0


def squeeze_ratio(candles, inputs):
    """current bandwidth ÷ its own trailing median. < 1 means coiled.

    Measuring against the asset's OWN history is the point: 0.4% band width is dead
    calm for an alt and a violent day for an index, so an absolute threshold is
    meaningless across a universe.
    """
    short_n = int(_f(inputs.get("bandPeriod"), 20))
    hist_n = int(_f(inputs.get("squeezeLookback"), 60))   # must span a REGIME, not a few bars
    cur = bandwidth_pct(candles, short_n)
    if cur <= 0 or len(candles) < short_n + hist_n:
        return None
    hist = []
    for k in range(1, hist_n + 1):
        window = candles[: len(candles) - k]
        bw = bandwidth_pct(window, short_n)
        if bw > 0:
            hist.append(bw)
    if not hist:
        return None
    hist.sort()
    med = hist[len(hist) // 2]
    if med <= 0:
        return None
    return cur / med


def build_thesis(asset, candles, inputs):
    """A coiled market that has just started to release, in the direction of the release.

    Two conditions, in order: the volatility was COMPRESSED (squeeze_ratio under the
    threshold across the recent window), and price has now pushed out of the coil.
    Trading the coil itself is how you die by a thousand cuts; the trade is the release.
    """
    n_band = int(_f(inputs.get("bandPeriod"), 20))
    if len(candles) < n_band + int(_f(inputs.get("squeezeLookback"), 60)) + 2:
        return None
    ok_fee, fee_detail = clears_fees(candles, inputs)
    if not ok_fee:
        return None

    # the coil must have existed just BEFORE the release, not now (release widens bands)
    prior = candles[:-1]
    ratio = squeeze_ratio(prior, inputs)
    if ratio is None or ratio > _f(inputs.get("maxSqueezeRatio"), 0.72):
        return None                                    # never was coiled — not our setup

    closes = [_close(c) for c in prior if _close(c) > 0]
    if len(closes) < n_band:
        return None
    window = closes[-n_band:]
    m = sum(window) / len(window)
    sd = stdev(window)
    if sd <= 0:
        return None
    k = _f(inputs.get("releaseStdev"), 2.0)
    last = _close(candles[-1])
    if last <= 0:
        return None

    if last > m + k * sd:
        direction = "LONG"
    elif last < m - k * sd:
        direction = "SHORT"
    else:
        return None                                    # still inside the coil — no release yet

    surge = vol_surge(candles, int(_f(inputs.get("volLookback"), 20)))
    if surge < _f(inputs.get("minVolSurge"), 1.4):
        return None                                    # a release with no participation is a fake

    tightness = max(0.0, 1.0 - ratio)                  # how coiled it was = how much stored energy
    score = round(min(20.0, tightness * 14.0 + (surge - 1.0) * 3.0 +
                      (fee_detail["expected_capture_pct"] / max(fee_detail["required_pct"], 1e-9))), 3)
    return {
        "asset": asset, "direction": direction, "score": score,
        "reasons": [f"volatility was coiled to {ratio:.2f}x its own median "
                    f"(threshold {_f(inputs.get('maxSqueezeRatio'), 0.72)})",
                    f"released {'above' if direction == 'LONG' else 'below'} {k:g} stdev",
                    f"vol x{surge:.2f}",
                    f"capture {fee_detail['expected_capture_pct']:.3f}% vs fees "
                    f"{fee_detail['required_pct']:.3f}% needed"],
        "squeeze_ratio": round(ratio, 4), "fees": fee_detail,
    }


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 13):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 9):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100] — never a fraction."""
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 3)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 8)
    lev = min(lev, _f(inputs.get("maxLeverage"), 5))
    if venue_max:
        lev = min(lev, _f(venue_max, lev))
    mgn = min(mgn, _f(inputs.get("maxMarginPct"), 22))
    return int(max(1, lev)), round(max(0.1, mgn), 4)
