"""CARIBOU — pure cross-asset trend math (no I/O, no MCP, no clock). Shared verbatim by
both sleeves; direction is passed in (`leg` = "long" for the uptrend book, "short" for the
downtrend book). A faithful port of the v2 caribou-producer.py scoring + vol-parity sizing +
asset-class classification — the gates, point weights, vol-parity formula and class map are
copied EXACTLY (marked `# v2-quirk` where the v2 behaviour is load-bearing and must not be
redesigned). Unit-testable on plain candle lists.

FIDELITY: this module reproduces, verbatim, the v2 producer's:
  - trend_structure / calc_rsi / atr_pct technicals
  - score_trend long/short scoring table (4h hard gate, +3; daily align +/-2; 24h mom +1;
    strong-mom +1; RSI blow-off/capitulation guard -2)
  - vol-parity sizing: pct = baseRiskPct * (referenceVolPct / asset_ATR%), clamped to
    [minMarginPct, maxMarginPct]. v2 emitted this as a FRACTION * account_value -> marginUsd;
    the 3.0 scan emits the PERCENT (pct*100) and the runtime sizes the dollars.
  - classify(): crypto | equity | index | metal | energy.
"""

# v2-quirk: raw-score normaliser. v2 emitted min(score/8.0, 1.0) as the [0,1] wire score
# (NORM_DIV = 8.0, max raw ~3+2+1+1 = 7). The 3.0 scaffold owns the wire envelope, so the raw
# integer score is kept on data{}; NORM_DIV is retained only for a v2-equivalent normalised score.
NORM_DIV = 8.0

# Per-sleeve defaults (config.json / runtime inputs override every one). Copied from the v2
# producer _DEFAULTS so the pure module can be exercised standalone.
DEFAULTS = {
    "minScore": 5,
    "apexScore": 7,
    "baseLeverage": 3,
    "maxLeverage": 5,
    "maxSlots": 8,
    "baseRiskPct": 0.08,         # FRACTION of equity a REFERENCE-vol asset gets (vol-parity anchor)
    "referenceVolPct": 3.0,      # "typical" daily ATR% — the vol-parity normaliser
    "minMarginPct": 0.03,        # FRACTION floor per position
    "maxMarginPct": 0.15,        # FRACTION cap per position
    "classMarginCapPct": 0.40,   # FRACTION — max total margin per asset CLASS
    "perClassMaxNames": 12,
    "rankPerClass": 6,
    "volFloorPctOfMedian": 0.2,
    "strongMomPct": 5.0,
    "rsiOverbought": 80,
    "rsiOversold": 20,
    "classMetals": ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER"],
    "classEnergy": ["BRENTOIL", "CL", "WTI", "NATGAS", "GAS", "HEATOIL", "GASOLINE", "URNM"],
    "classIndices": ["SP500", "NASDAQ", "NDX", "US500", "US100", "DJIA", "US30", "RUSSELL", "RUT", "VIX"],
}


def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4])                                  # [t,o,h,l,c,v] -> close
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    return 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2])
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    return 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3])
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    return 0.0


# ── technicals (verbatim v2) ──────────────────────────────────────────────────

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim v2."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    """Simple-average RSI. Verbatim v2."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def atr_pct(candles, period=14):
    """Average True Range as % of last price — the volatility-parity input. Returns None if
    not computable (caller falls back to reference vol). Verbatim v2."""
    if len(candles) < 3:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, lo, pc = _high(candles[i]), _low(candles[i]), _close(candles[i - 1])
        if h <= 0 or lo <= 0 or pc <= 0:
            continue
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    if not trs:
        return None
    atr = sum(trs[-period:]) / len(trs[-period:])
    price = _close(candles[-1])
    if price <= 0:
        return None
    return atr / price * 100.0


# ── asset-class classification (verbatim v2) ──────────────────────────────────

def classify(asset, inputs):
    """Map an instrument to an asset class: crypto | equity | index | metal | energy.
    Verbatim v2 classify(): xyz: names not in metals/energy/indices fall through to "equity";
    non-xyz names are "crypto"."""
    name = asset.split(":", 1)[1] if ":" in asset else asset
    name = name.upper()
    if not asset.lower().startswith("xyz:"):
        return "crypto"
    if name in set(inputs.get("classMetals", DEFAULTS["classMetals"])):
        return "metal"
    if name in set(inputs.get("classEnergy", DEFAULTS["classEnergy"])):
        return "energy"
    if name in set(inputs.get("classIndices", DEFAULTS["classIndices"])):
        return "index"
    return "equity"   # any other xyz name is a single-name stock


# ── trend scoring (time-series, per asset) — verbatim v2 score_trend ──────────

def score_trend(asset, candles_4h, candles_1d, own24h, leg, inputs):
    """Confirm + score a trend on ONE asset. Returns the thesis (incl. vol_pct for vol-parity
    sizing) or None to disqualify. 4h is the HARD gate; the daily aligns; momentum + RSI guard
    refine. Point weights + gates copied VERBATIM from the v2 producer score_trend() — do NOT
    redesign.

    leg == "long":  long a confirmed 4h UPTREND   (BULLISH 4h structure is the hard gate)
    leg == "short": short a confirmed 4h DOWNTREND (BEARISH 4h structure is the hard gate)
    """
    if len(candles_4h) < 6:
        return None
    closes4 = [_close(c) for c in candles_4h]
    price = closes4[-1]
    if price <= 0:
        return None

    trend4, s4 = trend_structure(candles_4h)
    trendd, sd = trend_structure(candles_1d) if len(candles_1d) >= 6 else ("NEUTRAL", 0)
    rsi = calc_rsi(closes4)
    own = own24h if own24h is not None else 0.0

    # Vol estimate for parity sizing — prefer daily ATR, fall back to 4h, then referenceVolPct.
    vol = atr_pct(candles_1d) if len(candles_1d) >= 3 else None
    if vol is None or vol <= 0:
        vol = atr_pct(candles_4h)
    if vol is None or vol <= 0:
        vol = float(inputs.get("referenceVolPct", DEFAULTS["referenceVolPct"]))

    strong = float(inputs.get("strongMomPct", DEFAULTS["strongMomPct"]))
    score, reasons = 0, []

    if leg == "long":
        if trend4 != "BULLISH":                              # v2-quirk: 4h uptrend is the hard gate
            return None
        score += 3
        reasons.append(f"4h_uptrend_{s4:.0%}")
        if trendd == "BULLISH":
            score += 2
            reasons.append(f"1d_uptrend_{sd:.0%}")
        elif trendd == "BEARISH":
            score -= 2
            reasons.append("1d_conflict")
        if own > 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if own >= strong:
            score += 1
            reasons.append("mom_strong")
        rsi_ob = float(inputs.get("rsiOverbought", DEFAULTS["rsiOverbought"]))
        if rsi > rsi_ob:                                     # v2-quirk: don't chase a blow-off
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # short
        if trend4 != "BEARISH":                              # v2-quirk: 4h downtrend is the hard gate
            return None
        score += 3
        reasons.append(f"4h_downtrend_{s4:.0%}")
        if trendd == "BEARISH":
            score += 2
            reasons.append(f"1d_downtrend_{sd:.0%}")
        elif trendd == "BULLISH":
            score -= 2
            reasons.append("1d_conflict")
        if own < 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if own <= -strong:
            score += 1
            reasons.append("mom_strong")
        rsi_os = float(inputs.get("rsiOversold", DEFAULTS["rsiOversold"]))
        if rsi < rsi_os:                                     # v2-quirk: don't short a capitulation
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": "LONG" if leg == "long" else "SHORT",
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": round(rsi, 1),
        "trend4h": trend4,
        "trend1d": trendd,
        "own24h": round(own, 2),
        "vol_pct": round(vol, 3),
    }


# ── vol-parity sizing — verbatim v2 vol_parity_margin, in PERCENT ─────────────

def vol_parity_margin_pct(vol_pct, inputs):
    """Vol-parity sizing as a PERCENT of equity in (0,100]. Margin scales INVERSELY with the
    asset's volatility, normalized to a reference vol, then clamped to [minMarginPct,
    maxMarginPct] of equity. A calm asset gets more margin; a wild one gets less — equal risk.

    v2-quirk: the v2 vol_parity_margin() computed this fraction then multiplied by
    account_value to get marginUsd. The 3.0 runtime sizes the dollars from a PERCENT intent,
    so we return the same fraction * 100 (the formula + clamps are identical)."""
    base = float(inputs.get("baseRiskPct", DEFAULTS["baseRiskPct"]))
    ref = float(inputs.get("referenceVolPct", DEFAULTS["referenceVolPct"]))
    lo = float(inputs.get("minMarginPct", DEFAULTS["minMarginPct"]))
    hi = float(inputs.get("maxMarginPct", DEFAULTS["maxMarginPct"]))
    # Defensive: a pasted PERCENT (e.g. 8) instead of a FRACTION (0.08) for baseRiskPct/clamps
    # would blow sizing up 100x. v2 stored these as FRACTIONS (<=1.0); coerce anything >1.0
    # back to a fraction (matching the dire/koala fraction-vs-percent guard).
    base = base / 100.0 if base > 1.0 else base
    lo = lo / 100.0 if lo > 1.0 else lo
    hi = hi / 100.0 if hi > 1.0 else hi
    if vol_pct <= 0:
        vol_pct = ref
    pct = base * (ref / vol_pct)
    pct = max(lo, min(hi, pct))
    return round(pct * 100.0, 4)   # -> PERCENT in (0,100]


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def conviction_leverage(score, inputs):
    """apex trend -> maxLeverage, else baseLeverage (verbatim v2). Returns the DESIRED leverage
    before the per-name venue clamp."""
    apex = int(inputs.get("apexScore", DEFAULTS["apexScore"]))
    base_lev = int(inputs.get("baseLeverage", DEFAULTS["baseLeverage"]))
    max_lev = int(inputs.get("maxLeverage", DEFAULTS["maxLeverage"]))
    return max_lev if score >= apex else base_lev
