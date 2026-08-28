"""RHINO — pure thesis math + cross-asset STRESS detector (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Rhino producer (rhino-producer.py, SKILL.md
v1.0.0). The indicator math, the directional scoring, and the stress-probe logic are
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on the
same market snapshot. v2 behaviour-preserving quirks are kept and flagged `# v2-quirk`;
fix them only as a separate labelled change AFTER the port is validated.

Shared verbatim by BOTH instances (hedge + escalation). Direction and book are passed
in via `inputs`; the caller (scan.py) fetches candles + universe meta and hands plain
lists here. Pure + unit-testable on plain candle lists.

The candle accessors are dual-shape (dict {close|c|high|h|low|l} OR list [t,o,h,l,c,v]).
v2 read dicts only; the list branch is defensive and never fires on dict candles, so it
does not change v2 behaviour."""


# ── candle accessors ──────────────────────────────────────────

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


# ── indicators (ported verbatim from v2 rhino-producer.py) ─────

def trend_structure(candles, lookback=6):
    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1] / highs[i] < highs[i-1]) and
    # the 0.6 threshold is taken against `total = lookback - 1`. Reproduced verbatim —
    # NOTE this differs from Kodiak's >= variant; keep Rhino's strict form for fidelity.
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
    # v2-quirk: uses the LAST period gains/losses (gains[-period:]) — the recent window.
    # Reproduced verbatim from v2 rhino calc_rsi.
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


def true_range(c, prev_close):
    h, l = _high(c), _low(c)
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr(candles, period):
    """Average true range over the last `period` bars (ported verbatim)."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], _close(candles[i - 1])))
    w = trs[-period:] if len(trs) >= period else trs
    return sum(w) / len(w) if w else 0.0


def range_break(candles, look):
    """Return 'up' / 'down' / None for a close beyond the prior `look`-bar range
    (excluding the current bar). Ported verbatim from v2 rhino range_break."""
    if len(candles) < look + 2:
        return None
    highs = [_high(c) for c in candles]
    lows = [_low(c) for c in candles]
    price = _close(candles[-1])
    prior_high = max(highs[-(look + 1):-1])
    prior_low = min(lows[-(look + 1):-1])
    if price > prior_high:
        return "up"
    if price < prior_low:
        return "down"
    return None


def ret_24h_from_ctx(ctx_block):
    """24h own-asset return from the asset_context markPx/prevDayPx. Ported verbatim
    from v2 ret_24h. Returns a PERCENT."""
    ctx = ctx_block or {}
    try:
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if prev <= 0 or mark <= 0:
        return 0.0
    return (mark - prev) / prev * 100.0


# ── STRESS DETECTOR — the shared brain (escalation gate + hedge telemetry) ──
#
# Each probe fires when its asset confirms its stress direction ("up" for crisis
# assets spiking, "down" for risk assets cratering), via a 4h trend OR a 1h range
# break + ATR surge. Ported verbatim from v2 _stress_probe / _vol_ratio / detect_stress.
# The caller (scan.py) does the candle fetch + fallback and hands the candle lists in.

def stress_probe(c1, c4, want, inputs):
    """True if a probe's asset confirms its stress direction `want` ("up"/"down").
    `c1` = 1h candles, `c4` = 4h candles (already fetched, with v2's fallback applied
    by the caller). Returns (bool, reason)."""
    look = int(inputs.get("breakoutBars", 20))
    base_bars = int(inputs.get("baseBars", 30))
    surge_mod = float(inputs.get("surgeMod", 1.3))
    if len(c4) < 6:
        return False, "no_data"
    want_struct = "BULLISH" if want == "up" else "BEARISH"
    trend4, _ = trend_structure(c4)
    if trend4 == want_struct:
        return True, f"4h_{trend4.lower()}"
    brk = range_break(c1, look) if len(c1) >= look + 2 else None
    if brk == want:
        a_base = atr(c1[-(base_bars + 1):], base_bars)
        last_tr = true_range(c1[-1], _close(c1[-2]))
        surge = (last_tr / a_base) if a_base > 0 else 0.0
        if surge >= surge_mod:
            return True, f"break_{want}_{surge:.1f}x"
    return False, "calm"


def vol_ratio(c1, inputs):
    """recent-ATR / baseline-ATR on `c1` (1h candles) — a vol-expansion proxy.
    Ported verbatim from v2 _vol_ratio."""
    base_bars = int(inputs.get("baseBars", 30))
    recent_bars = int(inputs.get("recentBars", 10))
    if len(c1) < base_bars + 2:
        return 0.0
    a_recent = atr(c1[-(recent_bars + 1):], recent_bars)
    a_base = atr(c1[-(base_bars + 1):], base_bars)
    return (a_recent / a_base) if a_base > 0 else 0.0


# ── Directional scoring — score a clean trend in the WANTED direction ──
# Ported verbatim from v2 score_directional. The 4h structure must BACK the wanted
# direction or we skip. Max raw score ~ 7 (base 2 + 1h confirm 2 + momentum 2 + rsi room 1).

def score_directional(c1, c4, ctx_block, want, inputs):
    """Score a name for a mandated `want` direction ("LONG"/"SHORT"). `c1`/`c4` are the
    1h/4h candle lists, `ctx_block` the asset_context (for the 24h return). Returns a
    thesis dict or None. Ported verbatim from v2 score_directional."""
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    own = ret_24h_from_ctx(ctx_block)
    mom = float(inputs.get("momThresholdPct", 1.0))
    rsi_ob = float(inputs.get("rsiOverbought", 80))
    rsi_os = float(inputs.get("rsiOversold", 20))

    want_struct = "BULLISH" if want == "LONG" else "BEARISH"
    opp_struct = "BEARISH" if want == "LONG" else "BULLISH"
    if trend4 != want_struct:
        return None

    score = 2
    reasons = [f"4h trend {trend4.lower()}, {s4:.0%} strength"]

    if trend1 == want_struct:
        score += 2
        reasons.append(f"1h trend confirms, {s1:.0%} strength")
    elif trend1 == opp_struct:
        score -= 1
        reasons.append("1h trend disagrees")

    if want == "LONG":
        if own >= mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"RSI at {rsi:.0f}, room to run")
    else:
        if own <= -mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        if rsi > rsi_os:
            score += 1
            reasons.append(f"RSI at {rsi:.0f}, room to run")

    return {
        "coin": "", "direction": want, "score": score,
        "reasons": reasons, "price": price, "rsi": round(rsi, 1),
        "trend4h": trend4, "own24h": round(own, 2),
    }


# ── Leverage clamp — flat maxLeverage clamped to venue max (v2 is NOT tiered) ──
# Ported verbatim from v2 clamp_leverage. Rhino emits a FLAT per-signal leverage
# (default 5) clamped to each asset's HL venue maximum — no conviction tiers.

def clamp_leverage(desired, venue_max):
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
