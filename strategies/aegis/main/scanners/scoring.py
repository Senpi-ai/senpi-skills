"""AEGIS — pure regime-scoring math (no I/O, no MCP, no clock).

Regime-adaptive dynamic hedge scorer. Where Phalanx reads the proven
cohort's headcount (a *people* signal), Aegis reads market structure
(a *tape* signal) — funding crowding, haven flows (gold and the yen), OI
velocity, and trend structure. The signals are uncorrelated by design,
which is what makes Aegis a real hedge rather than a second alpha engine.

The unit of analysis is the REGIME, not one coin. Per tick the scanner
reads market-wide funding + haven-asset trends, and this module scores:

  - regime_score: graduated -2.0 (severe risk-off) .. +2.0 (strong
    risk-on), from funding regime + gold trend + JPY trend.
  - direction_for: risk assets get SHORTED in risk-off, LONGED in
    risk-on; defensives are the mirror. Neutral regime => cash (None).
  - conviction: regime intensity * trend alignment * OI confirmation *
    funding-edge multiplier. Concentrated on the 2-3 assets with the
    strongest confirmation, not spread across the whole basket.
  - margin_pct_for: conviction-tiered margin (1.0 / 1.25 / 1.5 x base),
    capped by regime intensity so mild regimes don't deploy max size.

Pure: consumes already-normalized regime/trend/oi dicts and candle
lists; returns conviction-ranked signals. No I/O, no MCP, no clock —
unit-testable.
"""

import math

# ── numeric helpers ───────────────────────────────────────────────────────────

def _f(v, d=0.0):
    """Defensive numeric read: no-op on numbers, casts strings, d on None/garbage."""
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d

# ── asset classification ──────────────────────────────────────────────────────

# Risk assets: sold off in risk-off, bid in risk-on. xyz:JPY is quoted USD/JPY: it rises when the
# yen weakens (risk appetite) and falls when the yen is bid, so it trades as a risk asset here and
# shorting it in risk-off IS the yen-haven trade.
RISK_ASSETS = {"BTC", "ETH", "SOL", "HYPE", "XYZ:SP500", "XYZ:XYZ100", "XYZ:JPY"}

# Defensives: bid in risk-off, sold/faded in risk-on.
DEFENSIVE_ASSETS = {"XYZ:GOLD", "XYZ:BRENTOIL", "XYZ:NATGAS"}

def asset_role(asset):
    """Return 'risk', 'defensive', or None for an asset."""
    key = str(asset).upper()
    if key in RISK_ASSETS:
        return "risk"
    if key in DEFENSIVE_ASSETS:
        return "defensive"
    return None

def dex_for(asset):
    """XYZ (HIP-3) assets need dex='xyz' for market reads."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""

# ── trend structure (4h price confirmation) ───────────────────────────────────

def trend_structure(candles, lookback=6):
    """Determine the 4h trend: BULLISH (higher lows) or BEARISH (lower highs).

    Uses the last `lookback` candles. Higher lows in >= lookback-2 of them
    -> BULLISH. Lower highs in >= lookback-2 -> BEARISH. Otherwise NEUTRAL.

    Returns (label, strength) where strength is the fraction of matching bars.
    """
    if not candles or len(candles) < lookback:
        return "NEUTRAL", 0.0

    def _low(c):
        if isinstance(c, dict):
            return _f(c.get("l", c.get("low", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[3])
        return 0.0

    def _high(c):
        if isinstance(c, dict):
            return _f(c.get("h", c.get("high", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return _f(c[2])
        return 0.0

    recent = candles[-lookback:]
    lows = [_low(c) for c in recent]
    highs = [_high(c) for c in recent]

    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])

    denom = max(1, lookback - 1)
    if higher_lows >= lookback - 2:
        return "BULLISH", higher_lows / denom
    if lower_highs >= lookback - 2:
        return "BEARISH", lower_highs / denom
    return "NEUTRAL", 0.0

# ── regime scoring ────────────────────────────────────────────────────────────

def funding_regime_score(regime_label):
    """Map funding regime to a score component.

    LONG_CROWDED -> longs are paying -> contrarian short -> negative (risk-off lean)
    SHORT_CROWDED -> shorts are paying -> contrarian long -> positive (risk-on lean)
    NEUTRAL -> no edge -> 0
    """
    label = str(regime_label or "").upper()
    if "LONG_CROWDED" in label:
        return -1.0
    if "SHORT_CROWDED" in label:
        return 1.0
    return 0.0

def haven_trend_score(gold_trend, jpy_trend):
    """Score haven flows: gold or the yen bid = risk-off, sold = risk-on.

    gold_trend / jpy_trend: (label, strength) from trend_structure() on xyz:GOLD and
    xyz:JPY. xyz:JPY is quoted USD/JPY, so its price FALLS when the yen is bid.

    gold BULLISH or USD/JPY BEARISH -> risk-off (-0.5 each, scaled by strength)
    gold BEARISH or USD/JPY BULLISH -> risk-on  (+0.5 each, scaled by strength)
    NEUTRAL -> 0
    """
    score = 0.0
    for (label, strength), bid_when in ((gold_trend, "BULLISH"), (jpy_trend, "BEARISH")):
        if label == bid_when:
            score -= 0.5 * _f(strength)
        elif label in ("BULLISH", "BEARISH"):
            score += 0.5 * _f(strength)
    return score

def compute_regime_score(funding_label, gold_trend, jpy_trend):
    """Compute the graduated regime score: -2.0 (severe risk-off) .. +2.0 (strong risk-on).

    Components:
      - Funding regime: -1.0 / 0 / +1.0
      - Haven trends (gold + JPY): -1.0 .. +1.0 combined

    Clamped to [-2.0, +2.0].
    """
    score = funding_regime_score(funding_label) + haven_trend_score(gold_trend, jpy_trend)
    return max(-2.0, min(2.0, score))

def regime_to_direction(asset, regime_score):
    """Determine direction for an asset given the regime score.

    Risk assets: SHORT in risk-off, LONG in risk-on.
    Defensives:  LONG in risk-off, SHORT in risk-on.
    Neutral regime (|score| < threshold) -> None (cash).

    Returns 'LONG', 'SHORT', or None.
    """
    role = asset_role(asset)
    if role is None:
        return None

    if abs(regime_score) < 0.5:
        return None  # neutral — go to cash

    if regime_score < 0:  # risk-off
        return "SHORT" if role == "risk" else "LONG"
    else:  # risk-on
        return "LONG" if role == "risk" else "SHORT"

# ── conviction scoring ────────────────────────────────────────────────────────

def trend_alignment(trend_label, direction):
    """How well does the 4h trend align with the signal direction?

    Aligned (BULLISH+LONG or BEARISH+SHORT) -> 1.2 (confirmed)
    Counter (BULLISH+SHORT or BEARISH+LONG) -> 0.7 (fighting price)
    NEUTRAL -> 1.0 (no confirmation, no contradiction)
    """
    if direction == "LONG":
        if trend_label == "BULLISH":
            return 1.2
        if trend_label == "BEARISH":
            return 0.7
    else:  # SHORT
        if trend_label == "BEARISH":
            return 1.2
        if trend_label == "BULLISH":
            return 0.7
    return 1.0

def oi_confirmation(oi_velocity):
    """Score OI velocity: building OI confirms a move, declining weakens it.

    oi_velocity: dict with oi_trend (BUILDING/DECLINING/FLAT) and
    oi_acceleration (INCREASING/DECREASING/STABLE).

    BUILDING + INCREASING -> 1.15 (strong conviction)
    BUILDING + STABLE -> 1.10
    FLAT -> 1.0 (neutral)
    DECLINING -> 0.8 (positions closing, move fading)
    """
    if not isinstance(oi_velocity, dict):
        return 1.0

    trend = str(oi_velocity.get("oi_trend", "")).upper()
    accel = str(oi_velocity.get("oi_acceleration", "")).upper()

    if "DECLINING" in trend:
        return 0.8
    if "BUILDING" in trend:
        if "INCREASING" in accel:
            return 1.15
        return 1.10
    return 1.0  # FLAT or unknown

def funding_edge(funding_rate, direction):
    """Funding edge: being on the collecting side is a tailwind.

    funding_rate: per-period rate (e.g. 0.0000125).
    Positive funding -> longs pay shorts.
    Negative funding -> shorts pay longs.

    LONG + positive funding -> paying (0.9 penalty)
    LONG + negative funding -> collecting (1.1 bonus)
    SHORT + positive funding -> collecting (1.1 bonus)
    SHORT + negative funding -> paying (0.9 penalty)
    """
    rate = _f(funding_rate, 0.0)
    if rate == 0.0:
        return 1.0
    if direction == "LONG":
        return 1.1 if rate < 0 else 0.9
    else:  # SHORT
        return 1.1 if rate > 0 else 0.9

def conviction_for(regime_score, trend_label, direction, oi_vel, funding_rate):
    """Compute conviction score for an asset.

    conviction = |regime_score| * 40  (base intensity, 0-80 range)
               * trend_alignment       (0.7-1.2)
               * oi_confirmation       (0.8-1.15)
               * funding_edge          (0.9-1.1)

    Range: ~0 to ~122. Assets below minScore are filtered out.
    """
    base = abs(regime_score) * 40.0
    tal = trend_alignment(trend_label, direction)
    oic = oi_confirmation(oi_vel)
    fed = funding_edge(funding_rate, direction)
    return base * tal * oic * fed

def margin_pct_for(conviction, base_pct, regime_intensity, max_pct=None):
    """Conviction + regime-scaled margin PERCENT.

    Stronger conviction AND stronger regime -> bigger size.
    The regime intensity factor (|score|/2.0, 0-1) scales the base so
    mild regimes deploy less than severe ones.

    >= 70 conviction -> base * 1.5 * regime_intensity
    >= 50 conviction -> base * 1.25 * regime_intensity
    else             -> base * 1.0 * regime_intensity

    Returns a PERCENT in (0,100]; clamped to max_pct when supplied.
    """
    ri = max(0.25, min(1.0, abs(regime_intensity) / 2.0))  # 0.25 floor so mild regimes still deploy

    if conviction >= 70:
        pct = base_pct * 1.5 * ri
    elif conviction >= 50:
        pct = base_pct * 1.25 * ri
    else:
        pct = base_pct * 1.0 * ri

    if max_pct is not None and max_pct > 0:
        pct = min(pct, max_pct)
    return pct

# The engine refuses an order whose notional is under the platform minimum ($10) and bumps small orders
# to $12. A regime-scaled size below that is not a smaller hedge, it is no hedge and a skipped entry every
# tick. Size to the smallest order the venue accepts instead, or say the wallet is too small for the regime.
VENUE_MIN_NOTIONAL = 12.0


def floor_to_venue_min(pct, account_value, leverage, max_pct=None, min_notional=VENUE_MIN_NOTIONAL):
    """Raise a regime-scaled margin PERCENT to the smallest size the venue will fill.

    Returns (pct, floored). `floored` is True when the percent was raised. Returns (None, True) when
    even the floor exceeds `max_pct` — the wallet is too small for this regime's sizing and the caller
    should skip the entry and say why. Pure; percent in (0,100].
    """
    av = _f(account_value)
    lev = _f(leverage)
    if pct is None or av <= 0 or lev <= 0:
        return pct, False
    need = min_notional / (av * lev) * 100.0
    if pct >= need:
        return pct, False
    need = math.ceil(need * 100.0) / 100.0      # the emit is stored to 2 dp; round UP so it still clears
    if max_pct is not None and max_pct > 0 and need > max_pct:
        return None, True
    return need, True


# ── signal ranking ────────────────────────────────────────────────────────────

def rank_signals(candidates):
    """Sort candidates by conviction descending. Pure."""
    return sorted(candidates, key=lambda c: _f(c.get("conviction", 0)), reverse=True)
