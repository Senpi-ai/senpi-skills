"""KITE — pure market-structure + divergence math (no I/O, no MCP, no clock).

Implements the ICT/SMC entry the desk asks for: find 3-bar fractal swings, detect a BREAK OF
STRUCTURE (BOS), then on a retracement into the 0.618 fib of the BOS impulse leg (invalidation
behind 0.786) enter in the BOS direction, confirmed by an RSI divergence. The 0.786 stop and
1R/2R/3R targets are computed and surfaced on the signal; the DSL approximates the step-up exit
until native partial-take-profit ships. scan.py owns the reads/state; this is the numbers.

Candles keyed o/h/l/c/v (string-safe)."""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    return _f(c.get("close", c.get("c", 0))) if isinstance(c, dict) else 0.0


def _high(c):
    return _f(c.get("high", c.get("h", 0))) if isinstance(c, dict) else 0.0


def _low(c):
    return _f(c.get("low", c.get("l", 0))) if isinstance(c, dict) else 0.0


# ── momentum ──

def rsi(closes, n=14):
    """Wilder-simplified RSI over the last n deltas. None if too short."""
    if len(closes) < n + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    win = deltas[-n:]
    g = sum(d for d in win if d > 0) / n
    l = sum(-d for d in win if d < 0) / n
    if l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + g / l)


def rsi_series(closes, n=14):
    """RSI at each bar (None for the first n). Aligned to `closes`."""
    return [rsi(closes[:k + 1], n) if k >= n else None for k in range(len(closes))]


# ── structure ──

def fractal_swings(candles, wing=1):
    """3-bar fractal swings (wing=1): swing HIGH at i if high[i] strictly exceeds its `wing`
    neighbours on each side; swing LOW symmetrically. Returns (highs, lows) as [(index, price)]
    oldest first."""
    highs, lows = [], []
    for i in range(wing, len(candles) - wing):
        h, l = _high(candles[i]), _low(candles[i])
        if (all(h > _high(candles[i - k]) for k in range(1, wing + 1))
                and all(h > _high(candles[i + k]) for k in range(1, wing + 1))):
            highs.append((i, h))
        if (all(l < _low(candles[i - k]) for k in range(1, wing + 1))
                and all(l < _low(candles[i + k]) for k in range(1, wing + 1))):
            lows.append((i, l))
    return highs, lows


def detect_bos(candles, wing=1):
    """Most recent break of structure. Bullish BOS = the last close breaks the most recent
    prior swing HIGH; bearish = breaks the most recent swing LOW. The impulse leg runs from the
    origin swing (the swing low before a bullish break / swing high before a bearish break) to
    the break. Returns {dir, leg_low, leg_high, break_i} or None."""
    if len(candles) < 2 * wing + 3:
        return None
    highs, lows = fractal_swings(candles, wing)
    last = _close(candles[-1])
    bull = bear = None
    if highs:
        sh_i, sh = highs[-1]
        prior_lows = [x for x in lows if x[0] < sh_i]
        if last > sh and prior_lows:
            leg_low = min(prior_lows, key=lambda x: x[1])[1]
            bull = {"dir": "LONG", "leg_low": leg_low, "leg_high": max(sh, last), "break_i": sh_i}
    if lows:
        sl_i, sl = lows[-1]
        prior_highs = [x for x in highs if x[0] < sl_i]
        if last < sl and prior_highs:
            leg_high = max(prior_highs, key=lambda x: x[1])[1]
            bear = {"dir": "SHORT", "leg_low": min(sl, last), "leg_high": leg_high, "break_i": sl_i}
    if bull and bear:
        return bull if bull["break_i"] >= bear["break_i"] else bear     # the more recent break
    return bull or bear


def fib_zone(bos, entry_ratio=0.618, stop_ratio=0.786):
    """Fib retracement levels of the BOS impulse leg. LONG: entry below the high (a pullback),
    stop deeper at 0.786; targets 1R/2R/3R above. SHORT mirrors. Returns None on a degenerate leg."""
    lo, hi = bos["leg_low"], bos["leg_high"]
    rng = hi - lo
    if rng <= 0:
        return None
    if bos["dir"] == "LONG":
        entry = hi - entry_ratio * rng
        stop = hi - stop_ratio * rng
        risk = entry - stop
        targets = [round(entry + i * risk, 8) for i in (1, 2, 3)]
    else:
        entry = lo + entry_ratio * rng
        stop = lo + stop_ratio * rng
        risk = stop - entry
        targets = [round(entry - i * risk, 8) for i in (1, 2, 3)]
    if risk <= 0:
        return None
    return {"entry": entry, "stop": stop, "risk": risk, "targets": targets,
            "risk_pct": round(risk / entry * 100.0, 4) if entry > 0 else 0.0}


def in_entry_zone(price, zone, direction, band_pct):
    """Is price currently inside the 0.618 entry zone and not past the 0.786 invalidation?
    LONG: stop < price <= entry*(1+band). SHORT: entry*(1-band) <= price < stop."""
    if price <= 0 or zone is None:
        return False
    band = band_pct / 100.0
    if direction == "LONG":
        return zone["stop"] < price <= zone["entry"] * (1.0 + band)
    return zone["entry"] * (1.0 - band) <= price < zone["stop"]


def divergence(candles, direction, inputs):
    """Regular RSI divergence over the last `divLookback` bars, split into two halves. Bullish
    (LONG): a lower price low with a HIGHER RSI low. Bearish (SHORT): a higher price high with a
    LOWER RSI high. Returns True/False."""
    n = int(inputs.get("rsiPeriod", 14))
    look = int(inputs.get("divLookback", 20))
    closes = [_close(c) for c in candles]
    if len(candles) < look + n or look < 4:
        return False
    seg = candles[-look:]
    rseg = rsi_series(closes, n)[-look:]
    half = look // 2
    a, b = range(0, half), range(half, look)

    def pick(idxs, key, want_min):
        vals = [(i, key(seg[i]), rseg[i]) for i in idxs if rseg[i] is not None]
        if not vals:
            return None
        return (min if want_min else max)(vals, key=lambda x: x[1])

    if direction == "LONG":
        p1, p2 = pick(a, _low, True), pick(b, _low, True)
        return bool(p1 and p2 and p2[1] < p1[1] and p2[2] > p1[2])
    p1, p2 = pick(a, _high, False), pick(b, _high, False)
    return bool(p1 and p2 and p2[1] > p1[1] and p2[2] < p1[2])


# ── the thesis ──

def smc_thesis(coin, htf, ltf, inputs):
    """BOS on the higher timeframe + retracement into the 0.618 zone on the lower timeframe +
    an RSI divergence confirm. Returns a full setup dict (entry/stop/targets/score) or None."""
    wing = int(inputs.get("fractalWing", 1))
    bos = detect_bos(htf, wing)
    if not bos:
        return None
    zone = fib_zone(bos, float(inputs.get("entryFib", 0.618)), float(inputs.get("stopFib", 0.786)))
    if not zone:
        return None
    ltf = ltf or htf
    price = _close(ltf[-1])
    if not in_entry_zone(price, zone, bos["dir"], float(inputs.get("entryBandPct", 0.3))):
        return None
    div = divergence(ltf, bos["dir"], inputs)
    if bool(inputs.get("requireDivergence", True)) and not div:
        return None
    score = 4 + (1 if div else 0) + (1 if zone["risk_pct"] >= float(inputs.get("strongRiskPct", 1.0)) else 0)
    reasons = [f"{bos['dir']} BOS — impulse leg {bos['leg_low']:.6g}->{bos['leg_high']:.6g}",
               f"retraced into 0.618 entry {zone['entry']:.6g} (stop 0.786 {zone['stop']:.6g})",
               f"risk {zone['risk_pct']:.2f}% · targets 1R/2R/3R {['%.6g' % t for t in zone['targets']]}",
               f"RSI divergence {'CONFIRMED' if div else 'absent'}"]
    return {"coin": coin, "direction": bos["dir"], "score": max(0, score), "entry": zone["entry"],
            "stop": zone["stop"], "targets": zone["targets"], "risk_pct": zone["risk_pct"],
            "divergence": div, "reasons": reasons}


def sizing(score, inputs):
    """Conviction-tiered (marginPct PERCENT of withdrawable, leverage) for a structure swing."""
    base_m = float(inputs.get("marginPct", 12))
    cap = float(inputs.get("maxMarginPct", 22))
    std_l = int(inputs.get("stdLeverage", 3))
    max_l = int(inputs.get("maxLeverage", 5))
    if score >= 6:
        return round(min(base_m * 1.4, cap), 4), max_l
    if score >= 5:
        return round(min(base_m * 1.2, cap), 4), min(std_l + 1, max_l)
    return round(base_m, 4), std_l
