"""WOLF — pure thesis math (no I/O, no MCP, no clock). Shared VERBATIM by both
instances (risk_on / risk_off); the book's direction mandate is passed in.

A faithful Runtime 3.0 port of the v2 wolf producer's technical helpers + regime
vote-tallying + `score_directional`. The math/indexing is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`.

scan.py does the MCP reads (candle fetches for the regime probes + each candidate)
and hands plain candle lists here; scoring.py does the numbers. Unit-testable on
plain candle lists."""


# Max raw score ~ 7 (base 2 + 1h confirm 2 + momentum 2 + rsi room 1). Ported verbatim.
NORM_DIV = 8.0


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

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


# ── indicators (ported verbatim from v2 wolf-producer.py) ──

def trend_structure(candles, lookback=6):
    """(label, strength): fraction of higher-lows (BULLISH) / lower-highs (BEARISH)
    over the last `lookback` bars. v2 thresholds reproduced verbatim."""
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
    # v2-quirk: uses the LAST `period` deltas (gains[-period:]/losses[-period:]) of
    # the full delta series. Reproduced verbatim for fidelity — do not "fix" in the port.
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


def ret_24h(ctx):
    """24h return from an asset_context dict (markPx vs prevDayPx). Ported verbatim."""
    if not ctx:
        return 0.0
    try:
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if prev <= 0 or mark <= 0:
        return 0.0
    return (mark - prev) / prev * 100.0


# ── REGIME vote tally — pure given the per-probe 4h trend labels ──
# scan.py fetches each probe's 4h candles + runs trend_structure; this tallies the
# votes and declares the regime. Ported verbatim from detect_regime's vote logic.

def tally_regime(probe_trends, probes, threshold):
    """probe_trends: {label: "BULLISH"|"BEARISH"|"NEUTRAL"|"no_data"}.
    probes: the regime-probe spec list (each {label, risk_on_when}).
    Returns {regime, net, on_votes, off_votes, threshold, detail}."""
    on_votes, off_votes, detail = 0, 0, {}
    for p in probes:
        label = p.get("label", p.get("asset"))
        on_when = p.get("risk_on_when", "BULLISH")
        trend4 = probe_trends.get(label)
        if trend4 in (None, "no_data"):
            detail[label] = "no_data"
            continue
        off_when = "BEARISH" if on_when == "BULLISH" else "BULLISH"
        if trend4 == on_when:
            on_votes += 1
            detail[label] = "risk_on"
        elif trend4 == off_when:
            off_votes += 1
            detail[label] = "risk_off"
        else:
            detail[label] = "neutral"
    net = on_votes - off_votes
    if net >= threshold:
        regime = "RISK_ON"
    elif net <= -threshold:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"
    return {"regime": regime, "net": net, "on_votes": on_votes,
            "off_votes": off_votes, "threshold": threshold, "detail": detail}


# ── Directional scoring — score a clean trend in the WANTED direction ──
# (direction is set by the regime + asset class upstream, not picked here)

def score_directional(asset, c1, c4, asset_ctx, want, inputs):
    """Score a name for a regime-mandated `want` direction (LONG/SHORT). The 4h
    structure must BACK the wanted direction or we skip. Ported VERBATIM from the v2
    producer's score_directional. `c1`/`c4` are 1h/4h candle lists; `asset_ctx` is the
    asset_context dict (for 24h return). Returns a thesis dict or None."""
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    own = ret_24h(asset_ctx)
    mom = float(inputs.get("momThresholdPct", 1.0))
    rsi_ob = float(inputs.get("rsiOverbought", 78))
    rsi_os = float(inputs.get("rsiOversold", 22))

    want_struct = "BULLISH" if want == "LONG" else "BEARISH"
    opp_struct = "BEARISH" if want == "LONG" else "BULLISH"
    if trend4 != want_struct:
        return None

    score = 2
    reasons = [f"4h trend {trend4.lower()} at {s4:.0%} strength"]

    if trend1 == want_struct:
        score += 2
        reasons.append(f"1h trend confirms at {s1:.0%} strength")
    elif trend1 == opp_struct:
        score -= 1
        reasons.append("1h trend pushing the other way")

    if want == "LONG":
        if own >= mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"RSI {rsi:.0f}, room before overbought")
    else:
        if own <= -mom:
            score += 2
            reasons.append(f"24h move {own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"24h move {own:+.1f}%")
        if rsi > rsi_os:
            score += 1
            reasons.append(f"RSI {rsi:.0f}, room before oversold")

    return {
        "coin": asset, "direction": want, "score": score,
        "reasons": reasons, "price": price, "rsi": round(rsi, 1),
        "trend4h": trend4, "own24h": round(own, 2),
    }
