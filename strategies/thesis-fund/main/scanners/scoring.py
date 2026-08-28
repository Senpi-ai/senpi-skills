"""THESIS FUND — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Thesis Fund producer's `score_thesis` +
trend/RSI indicators (SKILL.md v1.0.0). The math/indexing is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix them only as
a separate, labelled change AFTER the port is validated.

Each basket name's `target_dir` ("LONG"/"SHORT") is FIXED by the preset (it is the
thesis's directional bias for that asset). The score measures how strongly the market is
CONFIRMING that direction. `score_thesis` returns None when the market OPPOSES the thesis
leg (don't fight the tape) — exactly as v2. Unit-testable on plain candle lists; the
caller passes the 24h return (keeps this pure)."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only (close/c, high/h, low/l); the list branch is defensive and never
# fires on dict candles, so it does not change v2 behaviour.

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


# ── indicators (ported VERBATIM from v2 thesis-producer.py) ──

def trend_structure(candles, lookback=6):
    """(label, strength): fraction of higher-lows (BULLISH) / lower-highs (BEARISH) over
    the last `lookback` bars. v2-quirk: uses STRICT > comparisons (kodiak uses >=) and
    the `>= total*0.6` threshold over `total = lookback-1`. Reproduced verbatim."""
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
    """v2-quirk: uses the LAST `period` gains/losses (gains[-period:]), i.e. the most
    recent window. Reproduced verbatim (differs from kodiak's first-window quirk)."""
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


# ── the thesis (confirmation score), ported VERBATIM from score_thesis ──

def score_thesis(target_dir, c1, c4, own24h, inputs):
    """Score one basket name. `target_dir` ("LONG"/"SHORT") is FIXED by the preset. The
    score measures how strongly the market is CONFIRMING that direction. Returns a thesis
    dict (with `score`), or None when the market opposes the thesis leg (don't fight the
    tape) or there is insufficient data. The caller fetches candles + the 24h return.

    c1 = 1h candle list, c4 = 4h candle list. `own24h` is the asset's 24h % return."""
    if not c1 or not c4:
        return None
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    own = own24h
    mom = float(inputs.get("momThresholdPct", 1.0))

    score = 0
    reasons = []

    if target_dir == "LONG":
        # Don't long into a confirmed downtrend — wait for the thesis to work.
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 3
            reasons.append(f"4h trend rising, {s4:.0%} higher lows")
        else:
            score += 1
            reasons.append("4h trend flat")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h trend rising, {s1:.0%} higher lows")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h trend falling")
        if own >= mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        rsi_ob = float(inputs.get("rsiOverbought", 78))
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"RSI {rsi:.0f}, room before overbought")
    else:  # SHORT
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h trend falling, {s4:.0%} lower highs")
        else:
            score += 1
            reasons.append("4h trend flat")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h trend falling, {s1:.0%} lower highs")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h trend rising")
        if own <= -mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        rsi_os = float(inputs.get("rsiOversold", 22))
        if rsi > rsi_os:
            score += 1
            reasons.append(f"RSI {rsi:.0f}, room before oversold")

    return {
        "coin": None,            # caller fills in the asset name
        "direction": target_dir,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": round(rsi, 1),
        "trend4h": trend4,
        "own24h": round(own, 2),
    }


def clamp_leverage(desired, venue_max):
    """Strict desired-leverage clamp, then the asset's HL venue max. Ported verbatim from
    v2 clamp_leverage. `venue_max` is the instrument's max_leverage (or None/0 to fall back
    to desired)."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
