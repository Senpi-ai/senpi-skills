"""ANT — Funding Harvester engine (pure: no I/O, no MCP, no clock).

The best Senpi-achievable version of a cash-and-carry funding trade. Senpi is
perps-only (no spot execution on HyperEVM), so ant cannot place the long-spot hedge
that makes true cash-and-carry delta-neutral. Instead it harvests funding the one
way the runtime allows — by SHORTING perps that pay positive funding (longs pay
shorts) — and it manages the resulting DIRECTIONAL price risk with an exhaustion
gate + a tight DSL stop.

⚠️ This is a DIRECTIONAL funding carry, NOT delta-neutral. You collect the funding
but you are short the perp; if the name squeezes up, the loss can dwarf the carry.
That risk is the price of the missing spot leg — see ant/NOTES.md.

The edge that makes it more than a naive funding short: ant only shorts a
high-funding name when the long crowd looks EXHAUSTED (overbought / rolling over /
not making fresh highs), never a name that is still ripping. High funding on a
still-accelerating name is a steamroller; ant skips it.

Pure halves:
  1. INDICATORS — trend_structure / calc_rsi / price_momentum (ported from bison).
  2. FUNDING + EXHAUSTION — funding_signal, exhaustion_score, and build_signal
     (short-only, funding gate ∧ persistence ∧ not-still-ripping).

FUNDING SOURCE: the funding fields (annualized_pct, funding_direction,
persistence_hours, trend) come straight from `market_get_funding_history` — the
call + parse are ported from pangolin (a live strategy), so ant uses the tool's
NATIVE annualized % and its LONG/SHORT collecting-side flag rather than recomputing
an APR from a raw rate (avoids the hourly-vs-8h ambiguity entirely). OI shape from
asset_context is still best-effort tolerant.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── candle accessors + indicators (ported from bison/scoring.py) ──

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


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0.0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    return ((new - old) / old) * 100 if old else 0.0


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ── funding (fields straight from market_get_funding_history — pangolin's proven
# parse: each row carries annualized_pct, funding_direction, persistence_hours, trend) ──

def funding_signal(funding, inputs):
    """Is this funding row a harvestable SHORT, and how rich? `funding` is one
    market_get_funding_history row (see scan._funding). Uses the tool's NATIVE fields —
    no rate→APR recompute, no hourly-vs-8h ambiguity:
      • funding_direction == "SHORT"  (the SHORT side COLLECTS ⇒ longs pay us to short)
      • annualized_pct >= targetApr
      • persistence_hours >= minPersistHours   (not a one-hour spike)
      • trend != "DECAYING"                     (funding isn't already drying up)
    Returns (apr, persistence_hours, reasons) or None."""
    if not isinstance(funding, dict):
        return None
    direction = str(funding.get("funding_direction") or "").upper()
    apr = _num(funding.get("annualized_pct"))
    if apr is None:
        return None
    apr = abs(apr)                                 # magnitude is the yield; direction gates the side
    persist = _f(funding.get("persistence_hours"), 0.0)
    trend = str(funding.get("trend") or "").upper()
    if direction != "SHORT":                       # ant harvests by SHORTING the funding-payer
        return None
    if apr < _f(inputs.get("targetApr"), 30.0):
        return None
    if persist < _f(inputs.get("minPersistHours"), 6):
        return None
    if trend == "DECAYING":
        return None
    reasons = [f"funding pays shorts {apr:.0f}% annualized",
               f"funding has held for {persist:.0f}h"]
    if trend:
        reasons.append(f"funding trend {trend.lower()}")
    return apr, persist, reasons


# ── exhaustion gate (never short a crowd that is still ripping) ──

def exhaustion_score(candles_1h, candles_4h):
    """0..~5 — how EXHAUSTED the long crowd looks (higher ⇒ safer to short).
    Overbought RSI only counts as exhaustion when the move is ALSO stalling — a
    vertical rip has RSI ~100 and is the most dangerous thing to short, so high RSI
    alone earns nothing. Rewards overbought-AND-stalling, a rolling-over 4h
    structure, and fading 1h momentum. Returns (score, reasons, still_ripping)."""
    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    trend_4h, _ = trend_structure(candles_4h)
    mom_1h = price_momentum(candles_1h, 2)
    stalling = mom_1h <= 0.0                       # not pushing up right now

    score, reasons = 0.0, []
    if rsi >= 70 and stalling:
        score += 2; reasons.append(f"RSI {rsi:.0f} overbought and stalling")
    if trend_4h == "BEARISH":
        score += 2; reasons.append("4h trend rolling over")
    elif trend_4h == "NEUTRAL":
        score += 1; reasons.append("4h trend has stalled")
    if mom_1h <= -0.3:
        score += 1; reasons.append(f"1h momentum fading {mom_1h:+.2f}%")

    # still ripping = a bullish 4h structure that is STILL pushing up right now →
    # never short an active uptrend, no matter how rich the funding or how high RSI.
    still_ripping = (trend_4h == "BULLISH" and mom_1h > 0.5)
    return score, reasons, still_ripping


# ── the signal (short-only funding harvest) ──

def build_signal(coin, funding, oi_usd, candles_1h, candles_4h, inputs):
    """Return a SHORT signal thesis dict or None. None ⟺ insufficient candles, the
    funding isn't a harvestable SHORT (direction / APR / persistence / decay), or the
    crowd is still ripping (exhaustion gate). Score blends funding APR + exhaustion + OI."""
    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None
    fs = funding_signal(funding, inputs)
    if not fs:
        return None
    apr, _persist, freasons = fs

    ex, ex_reasons, still_ripping = exhaustion_score(candles_1h, candles_4h)
    if still_ripping:
        return None                               # never short fresh strength
    if ex < _f(inputs.get("minExhaustion"), 1):
        return None                               # not exhausted enough to fade

    # score: APR headroom over target (capped) + exhaustion + OI liquidity bonus
    target = _f(inputs.get("targetApr"), 30.0)
    apr_pts = min(5.0, (apr - target) / max(1.0, target) * 3.0)
    oi_pts = 1.0 if _f(oi_usd) >= _f(inputs.get("oiBonusUsd"), 50_000_000) else 0.0
    score = round(apr_pts + ex + oi_pts, 2)
    reasons = freasons + [f"${_f(oi_usd) / 1e6:.0f}M open interest"] + ex_reasons
    return {"coin": coin, "direction": "SHORT", "score": score, "apr": round(apr, 1),
            "exhaustion": ex, "oi_usd": _f(oi_usd), "reasons": reasons}


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 7):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 5):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). Funding-carry shorts squeeze violently, so leverage is
    DELIBERATELY LOW (the edge is the carry, not directional conviction)."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 4, "good": 3, "base": 2}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 12, "good": 9, "base": 6}
    cap = int(_f(inputs.get("maxLeverage"), 4))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(int(_f(lev_tiers.get(band), 2)), cap))
    mgn = max(1.0, min(_f(mgn_tiers.get(band), 6), _f(inputs.get("maxMarginPct"), 20)))
    return lev, round(mgn, 2)
