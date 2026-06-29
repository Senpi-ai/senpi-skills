"""BALD EAGLE — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Bald Eagle producer's candidate scorer
(eagle-producer.py v5.0.1, thesis preserved verbatim from v4.1). All math/indexing
is reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on
the same market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`.

THESIS: XYZ macro CONTRARIAN FADER. The producer scores a smart-money (SM) candidate
in the SM CONSENSUS direction (all scoring is "does the SM move look strong/exhausting
in the SM direction?"). The FADE flip (emit the OPPOSITE direction) happens in the
CALLER (scan.py), NOT here — `score_candidate` returns the score in the SM direction
exactly as v2 `score_candidate(cand, candle_data)` did.

Single-pass, unit-testable on plain candle lists + a normalized SM candidate dict.
`candle_data` (1h/4h candles + funding) is fetched by the caller and passed in, so this
module stays pure.
"""


# ── safe numeric coercion (v2 safe_float) ──

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# back-compat alias for callers that prefer the v2 name
safe_float = _f


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

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


# ── indicators (ported verbatim from v2 eagle-producer.py) ──

def price_momentum(candles, n_bars=1):
    """% change over the last n_bars. Verbatim from v2 price_momentum."""
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from v2.

    v2-quirk: BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1,
    with STRICT (>) higher-lows / lower-highs counting. Reproduced exactly."""
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
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2."""
    if len(candles) < lookback + 2:
        return 0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


# ── conviction-scaled leverage (ported verbatim from v2 get_leverage_for_score) ──

# Conviction-scaled leverage (XYZ macro caps lower than crypto agents)
DEFAULT_LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 7},
    {"min_score": 8,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 7
MIN_LEVERAGE = 3


def get_leverage_for_score(score, tiers=None, max_leverage=MAX_LEVERAGE):
    """Conviction-scaled leverage. Higher score = higher leverage. Verbatim from v2:
      score >= 10 -> 7x ; score >= 8 -> 5x ; else -> DEFAULT_LEVERAGE (5x).
    Each tier is clamped to MAX_LEVERAGE, exactly as v2."""
    if tiers is None:
        tiers = DEFAULT_LEVERAGE_TIERS
    for tier in tiers:
        if score >= tier["min_score"]:
            return min(tier["leverage"], max_leverage)
    return DEFAULT_LEVERAGE


# ── candidate scoring (ported VERBATIM from v2 score_candidate) ──

def score_candidate(cand, candle_data):
    """Score an XYZ SM candidate in the SM CONSENSUS direction. Preserved verbatim
    from v2 score_candidate(cand, candle_data) (v4.1 thesis).

    `cand` is a normalized leaderboard_get_markets row:
      {direction, pct, traders, price_chg_4h, price_chg_1h, contrib_chg_1h,
       contrib_chg_4h}.  `direction` is the SM CONSENSUS direction (LONG/SHORT).
    `candle_data` is {candles:{"1h":[],"4h":[]}, asset_context|funding} or None.

    Returns (score:int, reasons:list[str]). The contrarian FADE flip is the
    caller's job — this scores in the SM direction exactly as v2 did."""
    score = 0
    reasons = []
    direction = cand["direction"]

    # ── SM concentration (0-4 pts) ──
    pct = cand["pct"]
    if pct >= 20:
        score += 4
        reasons.append(f"DOMINANT_SM {pct:.1f}%")
    elif pct >= 10:
        score += 3
        reasons.append(f"HIGH_SM {pct:.1f}%")
    elif pct >= 5:
        score += 2
        reasons.append(f"SOLID_SM {pct:.1f}%")
    elif pct >= 3:
        score += 1
        reasons.append(f"BASE_SM {pct:.1f}%")

    # ── Trader depth (0-2 pts) ──
    traders = cand["traders"]
    if traders >= 100:
        score += 2
        reasons.append(f"DEEP_SM ({traders}t)")
    elif traders >= 30:
        score += 1
        reasons.append(f"SM_ACTIVE ({traders}t)")

    # ── SM contribution velocity (0-3 pts) ──
    c1h = cand["contrib_chg_1h"]
    c4h = cand["contrib_chg_4h"]
    if c1h > 5:
        score += 2
        reasons.append(f"CONTRIB_SURGE_1H +{c1h:.1f}%")
    elif c1h > 2:
        score += 1
        reasons.append(f"CONTRIB_RISING_1H +{c1h:.1f}%")
    if c4h > 3 and c1h > 0:
        score += 1
        reasons.append(f"CONTRIB_SUSTAINED_4H +{c4h:.1f}%")

    # ── 4H price alignment (±2 pts) ──
    p4h = cand["price_chg_4h"]
    if direction == "LONG" and p4h > 0.5:
        score += 2
        reasons.append(f"4H_ALIGNED +{p4h:.1f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 2
        reasons.append(f"4H_ALIGNED {p4h:.1f}%")
    elif direction == "LONG" and p4h > 0:
        score += 1
        reasons.append(f"4H_POSITIVE +{p4h:.2f}%")
    elif direction == "SHORT" and p4h < 0:
        score += 1
        reasons.append(f"4H_POSITIVE {p4h:.2f}%")
    elif direction == "LONG" and p4h < -1:
        score -= 1
        reasons.append(f"4H_OPPOSING {p4h:.1f}%")
    elif direction == "SHORT" and p4h > 1:
        score -= 1
        reasons.append(f"4H_OPPOSING +{p4h:.1f}%")

    # ── MOVE EXHAUSTION BONUS (XYZ-tuned: 1-2% vs crypto 3-5%) ──
    if abs(p4h) >= 2.0:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}%")
    elif abs(p4h) >= 1.0:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"EXHAUSTION {p4h:+.1f}%")

    # ── Technical scoring from candles ──
    if candle_data:
        candles_1h = candle_data.get("candles", {}).get("1h", [])
        candles_4h = candle_data.get("candles", {}).get("4h", [])

        # 4H trend structure (0-2 pts)
        if len(candles_4h) >= 6:
            trend_4h, strength = trend_structure(candles_4h)
            if (direction == "LONG" and trend_4h == "BULLISH") or \
               (direction == "SHORT" and trend_4h == "BEARISH"):
                score += 2
                reasons.append(f"4H_TREND_{trend_4h} {strength:.0%}")
            elif trend_4h != "NEUTRAL" and \
                 ((direction == "LONG" and trend_4h == "BEARISH") or
                  (direction == "SHORT" and trend_4h == "BULLISH")):
                score -= 1
                reasons.append("4H_TREND_OPPOSING")

        # 1H momentum (0-1 pts)
        if len(candles_1h) >= 4:
            mom_1h = price_momentum(candles_1h, 2)
            if (direction == "LONG" and mom_1h > 0.3) or \
               (direction == "SHORT" and mom_1h < -0.3):
                score += 1
                reasons.append(f"1H_MOMENTUM {mom_1h:+.2f}%")

        # Volume trend (0-1 pts)
        if len(candles_1h) >= 8:
            vol = volume_trend(candles_1h)
            if vol > 15:
                score += 1
                reasons.append(f"VOL_RISING +{vol:.0f}%")

        # Funding alignment (0-1 pts)
        asset_ctx = candle_data.get("asset_context", candle_data)
        funding = _f(asset_ctx.get("funding", 0)) if isinstance(asset_ctx, dict) else 0
        if (direction == "LONG" and funding < -0.005) or \
           (direction == "SHORT" and funding > 0.005):
            score += 1
            reasons.append(f"FUNDING_ALIGNED {funding:+.4f}")

    return score, reasons


def fade_direction(sm_direction):
    """Contrarian flip — fade SM consensus. Verbatim from v2 main():
      fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"."""
    return "SHORT" if sm_direction == "LONG" else "LONG"
