"""CUB — pure thesis math (no I/O, no MCP, no clock). Shared verbatim by all three books
(long "haves", short "have-nots", preipo pre-IPO ramp); the direction and the universe builder
differ per book but the scoring is one function. A faithful Runtime 3.0 port of the v2
cub-producer.py scoring (cub-producer.py v1.0.0) — the gates + point weights + the IPOP
discovery filter are copied EXACTLY (marked `# v2-quirk` where the v2 behaviour is load-bearing
and must not be redesigned). Unit-testable on plain candle lists + instrument dicts.

KEY DISTINCTION FROM COUGAR (do not "simplify" toward cougar): in Cub the hard gate is
ABSOLUTE TREND (long a have only while it actually trends up; short a have-not only while it
actually rolls over). Cross-sectional excess return vs the leg-universe mean is a SCORE
MODIFIER / TIEBREAKER, NOT a disqualifier — a genuinely-trending winner is NOT benched on a day
its peers ran harder. Cougar instead gates on excess (long requires excess>=0); Cub does not.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score. The
# 3.0 scaffold owns the wire envelope, so we keep the raw integer score on data{} and only use
# NORM_DIV if a caller wants the v2-equivalent normalised score.
NORM_DIV = 9.0

# v2 preipo defaults (cub-producer.py _DEFAULTS["preipo"] / cub-preipo-config.json)
DEFAULT_IPOP_FUNDING_MAX = 1e-7    # IPOP funding signature (throttled pre-listing)
DEFAULT_IPOP_LEV_CAP = 5           # IPOP leverage signature


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles.

def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])                                  # [t,o,h,l,c,v] -> close
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    return 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return _f(c[2])
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    return 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return _f(c[3])
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    return 0.0


# ── indicators (ported verbatim from v2 cub-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher-lows = BULLISH, lower-highs = BEARISH over the last `lookback` candles.
    Verbatim v2 trend_structure (>= total*0.6 gate, total = lookback-1)."""
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
    """Simple-average RSI over the last `period` deltas. Verbatim v2 calc_rsi."""
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


# ── bare-ticker helper (sizing-weight + dedup lookups: 'xyz:NVDA' -> 'NVDA') ──

def bare(asset):
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


# ── the thematic thesis: ABSOLUTE trend is the gate, excess is a tiebreaker ──

def score_thematic(asset, candles_1h, candles_4h, excess, own24h, direction, inputs):
    """Port of v2 score_thematic. Returns a thesis dict (with `score`) or None.

    `direction` is "LONG" (haves + preipo discovered IPOPs) or "SHORT" (have-nots).
    `excess` = the asset's 24h return minus the leg-universe mean (cross-sectional).
    `own24h`  = the asset's own 24h return (sign is an absolute-momentum component).

    None is returned ONLY when: insufficient candle history (len(c1h) < 8 or len(c4h) < 6),
    OR the ABSOLUTE-trend hard gate fails (long: 4h BEARISH; short: 4h BULLISH).
    minScore is applied by the CALLER (scan.py), not here.

    v2-quirk: excess is a SCORE MODIFIER (bonus only), NOT a gate — never disqualifies a
    genuinely-trending name. Do NOT add cougar's `excess < 0 -> return None` here.
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)
    rs_thresh = float(inputs.get("rsThresholdPct", 3.0))

    score = 0
    reasons = []

    if direction == "LONG":     # haves (curated) + preipo (discovered IPOPs)
        # ── HARD GATE: never long a confirmed downtrend ──
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 3
            reasons.append(f"4h_bullish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h_bullish_{s1:.0%}")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h_bearish")
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"abs_up_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"rs_lag_{excess:+.1f}%")  # noted, not penalized
        rsi_ob = float(inputs.get("rsiOverbought", 82))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # SHORT — have-nots
        # ── HARD GATE: never short a confirmed uptrend ──
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h_bearish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h_bearish_{s1:.0%}")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")
        if own <= 0:
            score += 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_up_{own:+.1f}%")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"rs_lead_{excess:+.1f}%")
        rsi_os = float(inputs.get("rsiOversold", 18))
        if rsi < rsi_os:
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "trend1h": trend1,
        "excess": excess,
        "own24h": own,
    }


# ── sizing weight + leverage clamp (ported verbatim from v2) ──

def sizing_weight(asset, weights):
    """Per-group conviction multiplier (HYPE large, SOL modest, SP500 core). Keyed by bare
    ticker; falls back to '_default'. Clamped to [0.1, 3.0]. Verbatim v2 sizing_weight()."""
    if not isinstance(weights, dict):
        weights = {"_default": 1.0}
    try:
        w = float(weights.get(bare(asset), weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: xyz equities + IPOPs
    cap LOW at the venue — over-leveraging is a venue reject, so this clamp is load-bearing.
    Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


# ── IPOP discovery filter (preipo leg only; ported verbatim from v2 ipop_universe) ──

def is_ipop(name, meta, max_funding=DEFAULT_IPOP_FUNDING_MAX, lev_cap=DEFAULT_IPOP_LEV_CAP,
            min_day_vol=0.0):
    """True iff a live xyz: instrument matches the structural IPOP signature (verbatim v2):
        name.startswith("xyz:")
        AND 0 < venue max_leverage <= lev_cap (the throttled pre-listing leverage cap)
        AND abs(funding) <= max_funding (the throttled pre-listing funding signature)
        AND 24h notional volume >= min_day_vol (budget-relative liquidity floor)."""
    if not isinstance(name, str) or not name.lower().startswith("xyz:"):
        return False
    if not isinstance(meta, dict):
        return False
    try:
        lev = int(meta.get("max_leverage"))
    except (TypeError, ValueError):
        return False
    if lev <= 0 or lev > lev_cap:               # IPOP leverage signature
        return False
    ctx = meta.get("ctx", {}) if isinstance(meta.get("ctx"), dict) else {}
    try:
        funding_abs = abs(_f(ctx.get("funding", 0)))
    except (TypeError, ValueError):
        return False
    if funding_abs > max_funding:               # IPOP funding signature
        return False
    if day_vol(meta) < min_day_vol:             # budget-relative liquidity
        return False
    return True


# ── instrument-board accessors (ported verbatim from v2) ──

def day_vol(meta):
    """24h notional volume from an instrument's context. Verbatim v2 day_vol()."""
    ctx = (meta.get("ctx", {}) if isinstance(meta, dict) else {}) or {}
    try:
        return _f(ctx.get("dayNtlVlm", 0))
    except (TypeError, ValueError):
        return 0.0


def ret_24h(meta):
    """24h % return from markPx vs prevDayPx. None when unavailable. Verbatim v2 ret_24h()."""
    ctx = (meta.get("ctx", {}) if isinstance(meta, dict) else {}) or {}
    try:
        mark = _f(ctx.get("markPx", 0))
        prev = _f(ctx.get("prevDayPx", 0))
    except (TypeError, ValueError):
        return None
    if prev <= 0 or mark <= 0:
        return None
    return (mark - prev) / prev * 100.0
