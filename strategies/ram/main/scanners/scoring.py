"""RAM — pure thesis math (no I/O, no MCP, no clock).

RAM is the single-asset GOLD (xyz:GOLD) specialist — the precious-metals analogue
of DIRE (BRENTOIL). Safe-haven + momentum: it goes LONG gold when trend and (in a
risk-off regime) safe-haven demand align, can SHORT a clean downtrend, and holds
ONE position. NEVER closes — the DSL owns every exit.

Two halves, both pure and unit-testable:

  1. MOMENTUM ENGINE — the indicator math (_close/_high/_low/_vol, trend_structure,
     price_momentum, calc_rsi, volume_trend) and the direction-waterfall + weighted
     score are ported VERBATIM from bison/scoring.py (a validated Runtime 3.0 scorer),
     so a fidelity harness can diff RAM's entries against bison's on the same candles.
     Behaviour-preserving quirks carry bison's `# v2-quirk` flags. RAM's engine is
     bison's momentum thesis, narrowed to one instrument (1h/4h only, no 15m) and no
     smart-money cohort source (gold has none — the `sm` rung is kept for fidelity but
     the caller passes (None, 0), leaving it inert exactly as in bison).

  2. SAFE-HAVEN TILT — the single metals-specific addition. When the caller detects a
     risk-off regime (from market_get_funding_regime), it passes risk_off=True and a
     small bounded bonus (`safeHavenBonusLong`) is added to a LONG thesis only — gold
     is bid when the market turns defensive. A SHORT is never given the tilt.

NOTE: the market_get_funding_regime payload shape was NOT live-verified when this was
written (auth token invalid). `risk_off_from_regime` derives the flag ONLY from an
explicit categorical/boolean field and defaults to False (neutral) on any unknown or
mis-shaped payload — it never infers risk-off from a numeric funding sign, so a bad
payload can never inject a wrong-way bias into a live long. Verify against a real
payload and extend the key/word lists before trusting the tilt.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None (distinguishes a real 0.0 from a missing field)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# Ported verbatim from bison/scoring.py.

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


# ── indicators (ported verbatim from bison/scoring.py) ──

def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from bison.
    v2-quirk: STRICT (>) counting, gate `>= total * 0.6`, total = lookback - 1."""
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


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


def calc_rsi(closes, period=14):
    """Trailing-window RSI. Verbatim from bison (uses gains[-period:])."""
    if len(closes) < period + 1:
        return 50
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


# ── the thesis (direction waterfall + weighted score) — bison's engine, narrowed
#    to one instrument (1h/4h, no 15m) with the metals safe-haven tilt appended ──

def build_thesis(coin, candles_1h, candles_4h, funding, sm, risk_off, inputs):
    """Port of bison build_thesis for a single metals instrument. Returns a thesis
    dict (with `score`) or None. None ⟺ insufficient history (len(c1h) < 8 or
    len(c4h) < 4) OR no direction resolves. minScore is applied by the CALLER
    (scan.py).

    Differences from bison (all behaviour-preserving except the tilt):
      • no 15m timeframe — RAM fetches 1h/4h only; price comes from the 1h close.
      • `sm` (smart-money lean) is kept for bison-fidelity but gold has no cohort
        source, so the caller passes (None, 0); the sm waterfall rung and sm score
        component are then inert exactly as in bison when sm is absent.
      • SAFE-HAVEN TILT: +`safeHavenBonusLong` added to a LONG when risk_off is set
        (gold bid in risk-off regimes). A SHORT is never given the tilt.
    """
    min_vol_trend = _f(inputs.get("minVolTrendPct", 10))
    rsi_max_long = _f(inputs.get("rsiMaxLong", 72))
    rsi_min_short = _f(inputs.get("rsiMinShort", 28))
    sh_bonus = _f(inputs.get("safeHavenBonusLong", 2))

    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None

    price = _close(candles_1h[-1]) if candles_1h else 0

    trend_4h, trend_strength = trend_structure(candles_4h)
    trend_1h, _ = trend_structure(candles_1h)
    sm_dir, sm_pct = sm if sm else (None, 0)
    mom_1h = price_momentum(candles_1h, 2)

    # Direction waterfall: 4H trend -> SM direction -> 1H momentum (verbatim from bison)
    direction = None
    if trend_4h == "BULLISH":
        direction = "LONG"
    elif trend_4h == "BEARISH":
        direction = "SHORT"
    elif sm_dir and sm_dir != "NEUTRAL":
        direction = sm_dir
    elif mom_1h > 0.5:
        direction = "LONG"
    elif mom_1h < -0.5:
        direction = "SHORT"
    if direction is None:
        return None

    score = 0
    reasons = []

    # 4H trend structure (+3 / -1)
    if trend_4h != "NEUTRAL":
        if (direction == "LONG") == (trend_4h == "BULLISH"):
            score += 3; reasons.append(f"4h trend {trend_4h.lower()}, {trend_strength:.0%} strength")
        else:
            score -= 1; reasons.append(f"4h trend against us ({trend_4h.lower()})")

    # 1H trend agreement (+2 / -1)
    if trend_1h != "NEUTRAL":
        if (direction == "LONG") == (trend_1h == "BULLISH"):
            score += 2; reasons.append(f"1h trend confirms ({trend_1h.lower()})")
        else:
            score -= 1; reasons.append(f"1h trend against us ({trend_1h.lower()})")

    # 1H momentum (+2 / +1 / -1)
    if direction == "LONG":
        if mom_1h >= 1.0:
            score += 2; reasons.append(f"strong 1h momentum, {mom_1h:+.2f}%")
        elif mom_1h >= 0.5:
            score += 1; reasons.append(f"1h momentum {mom_1h:+.2f}%")
        elif mom_1h < -0.5:
            score -= 1; reasons.append(f"1h momentum against us, {mom_1h:+.2f}%")
    else:
        if mom_1h <= -1.0:
            score += 2; reasons.append(f"strong 1h momentum, {mom_1h:+.2f}%")
        elif mom_1h <= -0.5:
            score += 1; reasons.append(f"1h momentum {mom_1h:+.2f}%")
        elif mom_1h > 0.5:
            score -= 1; reasons.append(f"1h momentum against us, {mom_1h:+.2f}%")

    # SM alignment (+-2) — inert when sm is (None, 0), as in bison
    if sm_dir == direction and sm_dir:
        score += 2; reasons.append(f"smart money aligned at {_f(sm_pct):.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        score -= 2; reasons.append(f"smart money on the other side ({sm_dir})")

    # Funding alignment (+2 / -1)
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2; reasons.append(f"funding favors this side ({funding:+.4f})")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1; reasons.append("funding crowded on this side")

    # Volume trend (+1)
    vol_1h = volume_trend(candles_1h)
    if vol_1h > min_vol_trend:
        score += 1; reasons.append(f"volume rising, {vol_1h:+.0f}%")

    # OI proxy (+1) — recent-3 vs earlier-3 1h volume delta (verbatim)
    vol_recent = sum(_vol(c) for c in candles_1h[-3:])
    vol_earlier = sum(_vol(c) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1; reasons.append(f"trading activity growing, {oi_proxy:+.0f}%")

    # RSI room (+1 / -1)
    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > rsi_max_long:
        score -= 1; reasons.append(f"RSI overbought at {rsi:.0f}")
    elif direction == "SHORT" and rsi < rsi_min_short:
        score -= 1; reasons.append(f"RSI oversold at {rsi:.0f}")
    elif (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1; reasons.append(f"RSI at {rsi:.0f}, room to run")

    # 4H momentum (+1)
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5 and ((direction == "LONG") == (mom_4h > 0)):
        score += 1; reasons.append(f"4h momentum {mom_4h:+.1f}%")

    # ── SAFE-HAVEN TILT (metals-specific; LONG only) ──
    if risk_off and direction == "LONG":
        score += sh_bonus; reasons.append(f"safe-haven bid in risk-off, +{sh_bonus:g}")

    return {"coin": coin, "direction": direction, "score": score, "reasons": reasons,
            "price": price, "trend_4h": trend_4h, "trend_1h": trend_1h,
            "rsi": round(rsi, 1), "momentum_1h": round(mom_1h, 3),
            "funding": funding, "risk_off": bool(risk_off)}


# ── conviction band + sizing (raven-shaped; single high-conviction slot) ──

def band_for(score, inputs):
    """Conviction band from the score."""
    apex = _f(inputs.get("apexScore"), 9)
    good = _f(inputs.get("goodScore"), 7)
    if score >= apex:
        return "apex"
    if score >= good:
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100], clamped to the
    fleet + venue leverage caps and to maxMarginPct. Single high-conviction slot —
    no self-calibration size_scale (RAM does not adapt its own sizing)."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 5, "good": 4, "base": 3}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 20, "good": 15, "base": 10}
    cap = int(_f(inputs.get("maxLeverage"), 5))
    lev = int(_f(lev_tiers.get(band), 3))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(lev, cap))
    mgn = _f(mgn_tiers.get(band), 10)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 25)))
    return lev, round(mgn, 2)


# ── safe-haven / risk-off derivation (pure parse of a funding-regime payload) ──

# categorical spellings a funding-regime payload might use. Shape UNVERIFIED — extend
# once a real market_get_funding_regime response is captured.
_RISK_OFF_WORDS = ("risk_off", "risk-off", "riskoff", "defensive", "bearish", "fear",
                   "flight_to_safety", "flight-to-safety", "capitulation")
_RISK_ON_WORDS = ("risk_on", "risk-on", "riskon", "bullish", "greed", "aggressive",
                  "euphoria")
_REGIME_KEYS = ("regime", "funding_regime", "fundingRegime", "label", "state",
                "classification", "sentiment", "mode", "risk_regime", "riskRegime")
_RISK_OFF_FLAGS = ("risk_off", "riskOff", "is_risk_off", "isRiskOff",
                   "safe_haven", "safeHaven", "risk_off_regime")


def risk_off_from_regime(data):
    """Tolerant, PURE parse of a market_get_funding_regime payload into a risk-off
    bool. Categorical / boolean ONLY — never inferred from a numeric funding sign
    (guessing the sign could inject a wrong-way bias into a live long). Any unknown,
    absent, or mis-shaped payload → False (neutral; the safe-haven tilt stays off).
    Shape NOT live-verified — see the module docstring."""
    d = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(d, dict):
        return False
    # explicit boolean flag
    for k in _RISK_OFF_FLAGS:
        v = d.get(k)
        if isinstance(v, bool) and v:
            return True
    # categorical label — risk-off words win; risk-on words explicitly clear it
    for k in _REGIME_KEYS:
        v = d.get(k)
        if isinstance(v, str):
            s = v.strip().lower()
            if any(w in s for w in _RISK_OFF_WORDS):
                return True
            if any(w in s for w in _RISK_ON_WORDS):
                return False
    return False
