"""PYTHON — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Python producer's `build_thesis` +
multi-factor scoring (SKILL.md v2.0.0 "The Patience Hunter"; thesis preserved
verbatim from v1.2). The math/indexing is reproduced VERBATIM so a fidelity
harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix them
only as a separate, labelled change AFTER the port is validated.

Python is a WIDE-UNIVERSE multi-day-hold agent (not single-asset): the caller
scores many coins and emits the strongest. The thesis carries a MACRO/REGIME
GATE (MACRO_GATE_THRESHOLD_PCT): it refuses any entry that fights a runaway 4h
move >10% against the trade direction. The LONG-bias bonus + smart-money
HARD-BLOCK are also preserved verbatim.

Pure + unit-testable on plain candle lists (dict {close|c} OR list [t,o,h,l,c,v])."""


# ── CONSTANTS — preserved verbatim from v2 (v1.2) ──
MIN_SCORE = 8
LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 7},
    {"min_score": 10, "leverage": 5},
    {"min_score": 8,  "leverage": 3},
]
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 3

# margin as a FRACTION of account value in v2 (the runtime sizes from marginUsd here).
MARGIN_PCT_BASE = 0.25
MARGIN_PCT_STRONG = 0.30
MARGIN_PCT_APEX = 0.40

MACRO_GATE_THRESHOLD_PCT = 10.0   # the macro/regime gate threshold (4h runaway-move filter)
LONG_BIAS_BONUS = 1


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


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from v2 producer) ──

def price_momentum(candles, n_bars=1):
    """% change over the last n_bars."""
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1], highs[i] < highs[i-1])
    # and a 0.55 fraction threshold; strength = higher_lows/total (NOT max()).
    # Reproduced verbatim — differs from kodiak's >= / 0.6 variant.
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.55:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.55:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [_vol(c) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = _vol(candles[-1])
    return latest / avg if avg > 0 else 1.0


def calc_rsi(closes, period=14):
    # v2-quirk: uses the MOST RECENT `period` deltas (gains[-period:]), unlike
    # kodiak's first-window RSI. Reproduced verbatim for fidelity.
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


# ── leverage + margin tiers (conviction-scaled, ported verbatim) ──

def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_margin_pct(score):
    if score >= 12:
        return MARGIN_PCT_APEX
    elif score >= 10:
        return MARGIN_PCT_STRONG
    return MARGIN_PCT_BASE


def clamp_leverage(desired, asset_max_leverage):
    """Clamp desired leverage to the per-asset HL venue max AND the family 7x cap
    (port of get_safe_leverage minus the MCP read — the caller passes the venue max)."""
    out = int(desired)
    if asset_max_leverage is not None:
        try:
            out = min(out, int(asset_max_leverage))
        except (TypeError, ValueError):
            pass
    return min(out, MAX_LEVERAGE)


# ── the thesis (gates incl. MACRO/REGIME GATE + multi-factor score),
#    ported verbatim from build_thesis ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, candles_1d, funding, sm_info):
    """Score a single coin for multi-day hold potential. Returns a thesis dict
    (with `score`) or None if any gate blocks. `sm_info` is the smart-money tuple
    (direction, pct, count, cc_15m) or None (the caller fetches it).

    Faithful port of v2 build_thesis: 4h structure gate, 1h confirmation, the
    MACRO/REGIME GATE, 15m momentum floor, the full multi-factor score (4h
    strength, 1h/1d momentum, LONG-bias bonus, smart-money alignment w/ HARD
    BLOCK on opposition, funding, volume, RSI, move-exhaustion penalty)."""
    if len(candles_15m) < 8 or len(candles_1h) < 6 or len(candles_4h) < 6:
        return None
    price = _close(candles_15m[-1])

    # ── 4h structure gate ──
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── 1h confirmation ──
    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1d = price_momentum(candles_1d, 1) if len(candles_1d) >= 2 else 0

    # ── MACRO / REGIME GATE ──
    # No counter-trend entry against a runaway 4h move > MACRO_GATE_THRESHOLD_PCT.
    # If 4h has already moved >10% AGAINST our intended direction, the regime is
    # mid-reversal/blowoff and we stand aside (blocks knife-catching a LONG into a
    # >10% 4h dump, or shorting a >10% 4h ramp).
    if abs(mom_4h) > MACRO_GATE_THRESHOLD_PCT:
        if (direction == "LONG" and mom_4h < 0) or (direction == "SHORT" and mom_4h > 0):
            return None

    # ── 15m momentum floor (direction confirmation) ──
    if direction == "LONG" and mom_15m < 0.1:
        return None
    if direction == "SHORT" and mom_15m > -0.1:
        return None

    # ── ALL HARD GATES PASSED — SCORE ──
    score = 0
    reasons = []

    if trend_strength_4h >= 0.8:
        score += 4
        reasons.append(f"4h_strong_{trend_4h}")
    elif trend_strength_4h >= 0.6:
        score += 3
        reasons.append(f"4h_{trend_4h}")
    else:
        score += 2
        reasons.append(f"4h_weak_{trend_4h}")

    if abs(mom_1h) > 1.0:
        score += 2
        reasons.append(f"1h_strong_{mom_1h:+.2f}%")
    elif abs(mom_1h) > 0.5:
        score += 1
        reasons.append(f"1h_ok_{mom_1h:+.2f}%")

    if len(candles_1d) >= 3:
        if direction == "LONG" and mom_1d > 1.0:
            score += 2
            reasons.append(f"1d_bullish_{mom_1d:+.1f}%")
        elif direction == "SHORT" and mom_1d < -1.0:
            score += 2
            reasons.append(f"1d_bearish_{mom_1d:+.1f}%")
        elif direction == "LONG" and mom_1d > 0:
            score += 1
            reasons.append("1d_up")
        elif direction == "SHORT" and mom_1d < 0:
            score += 1
            reasons.append("1d_down")

    # LONG-bias bonus (pr0br000 insight: top-5 winners were all LONG)
    if direction == "LONG":
        score += LONG_BIAS_BONUS
        reasons.append("LONG_bias")

    # smart-money alignment — opposition is a HARD BLOCK
    if sm_info:
        sm_dir, sm_pct, sm_count, sm_cc_15m = sm_info
        if sm_dir == direction:
            score += 2
            reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}t")
            if sm_pct > 70:
                score += 1
                reasons.append("sm_strongly_tilted")
        elif sm_dir != "NEUTRAL" and sm_dir != direction:
            return None  # HARD BLOCK — consensus-backed asymmetry
        if sm_cc_15m > 0.3:
            score += 1
            reasons.append(f"15m_fresh +{sm_cc_15m:.2f}")

    # funding
    if direction == "LONG" and funding < 0:
        score += 1
        reasons.append(f"funding_pays_long_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 1
        reasons.append(f"funding_pays_short_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.01):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # volume
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.3:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.6:
        score -= 1
        reasons.append("vol_weak")

    # RSI extremes gate + midrange-room bonus
    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 78:
        return None
    if direction == "SHORT" and rsi < 22:
        return None
    if (direction == "LONG" and 50 < rsi < 68) or (direction == "SHORT" and 32 < rsi < 50):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # move-exhaustion penalty (only when the big 4h move is WITH our direction)
    if abs(mom_4h) >= 6.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "trend_4h": trend_4h,
        "trend_strength_4h": round(trend_strength_4h, 3),
        "trend_1h": trend_1h,
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "mom_1d": round(mom_1d, 3),
        "mom_15m": round(mom_15m, 3),
        "funding": funding,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_1h, 3),
    }
