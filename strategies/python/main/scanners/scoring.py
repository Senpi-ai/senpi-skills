"""PYTHON — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 PYTHON producer's `build_thesis` +
"Patience Hunter" multi-day-hold scoring (SKILL.md v2.0.0 / python-producer.py
v2.0.1). The indicators, hard gates, scoring table, leverage tiers, and margin
tiers are reproduced VERBATIM so a fidelity harness can diff this against the v2
producer on the same market snapshot. Behaviour-preserving v2 quirks are kept and
flagged `# v2-quirk`; fix them only as a separate, labelled change AFTER the port
is validated.

Multi-asset universe scorer, single-pass, unit-testable on plain candle lists.
`sm_info` (the smart-money tuple for this coin) is fetched by the caller and passed
in, so this module stays pure (no MCP, no clock).

The thesis is GATED (unlike Bison's all-contributors scorer): build_thesis returns
None whenever any hard gate fails (insufficient candles, 4h trend NEUTRAL, 1h !=
4h, MACRO_GATE counter-trend, 15m momentum doesn't confirm, SM opposes = HARD
BLOCK, RSI extreme). MIN_SCORE is applied by the CALLER (scan.py)."""


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v1.2 / v2.0.1 producer
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000
MIN_DAY_NTL_VLM = 1_000_000          # v2: day_ntl_vlm < 1_000_000 -> drop
MIN_TRADER_COUNT = 30                 # SM map gate (wider than Condor's 50)
MIN_SCORE = 8
STABLECOINS_BANNED = {"USDC", "USDT", "USDE", "FDUSD", "DAI"}  # v2 verbatim set

MACRO_GATE_THRESHOLD_PCT = 10.0
LONG_BIAS_BONUS = 1

# Sizing tiers — leverage by score (verbatim v2 LEVERAGE_TIERS).
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 3
LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 7},
    {"min_score": 10, "leverage": 5},
    {"min_score": 8,  "leverage": 3},
]

# Margin tiers — PERCENT of withdrawable in (0,100], the Runtime 3.0 wire
# convention. v2 stored these as FRACTIONS (0.25/0.30/0.40) and emitted
# marginUsd = account_value * fraction; the runtime now sizes
# (marginPct/100)*withdrawable, so these are ×100 (25/30/40 PERCENT). The score
# CUTOFFS (12 / 10) are preserved verbatim.
MARGIN_PCT_BASE = 25.0
MARGIN_PCT_STRONG = 30.0
MARGIN_PCT_APEX = 40.0


# ── numeric coercion (matches v2 safe_float) ──

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict
# candles, so it does not change v2 behaviour.

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


# ── indicators (ported verbatim from v2 python-producer.py) ──

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

    v2-quirk: the BULLISH/BEARISH gate is `>= total * 0.55` where total =
    lookback - 1 (Python's threshold is 0.55, distinct from Bison's 0.6 and
    Kodiak's variant). The strength returned is higher_lows/total (BULLISH) or
    lower_highs/total (BEARISH). Reproduced exactly."""
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
    """Latest bar volume vs the trailing `lookback`-bar average. Verbatim v2."""
    if len(candles) < lookback + 1:
        return 1.0
    vols = [_vol(c) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = _vol(candles[-1])
    return latest / avg if avg > 0 else 1.0


def calc_rsi(closes, period=14):
    """RSI. Verbatim from v2 calc_rsi (trailing-window, last `period`)."""
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


# ── sizing tiers (ported verbatim from v2 get_leverage_for_score / get_margin_pct) ──

def get_leverage_for_score(score):
    """Leverage by score tier. Verbatim from v2 get_leverage_for_score."""
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_margin_pct(score):
    """Conviction-scaled margin PERCENT in (0,100]. Ported from v2 get_margin_pct.

    v2 returned a FRACTION (0.40/0.30/0.25) multiplied by account_value to get
    marginUsd; this returns the equivalent PERCENT (40/30/25) and the runtime
    sizes (marginPct/100)*withdrawable. Score CUTOFFS (12 / 10) verbatim."""
    if score >= 12:
        return MARGIN_PCT_APEX
    elif score >= 10:
        return MARGIN_PCT_STRONG
    return MARGIN_PCT_BASE


# ── the thesis (multi-gate scorer), ported verbatim from v2 build_thesis ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, candles_1d,
                 funding, max_lev, sm_info):
    """Score a single coin for multi-day-hold potential. Returns a thesis dict
    (with `score`) or None when any HARD GATE fails. Ported VERBATIM from v2
    build_thesis.

    None is returned when:
      - insufficient candle history (15m<8 or 1h<6 or 4h<6), OR
      - 4h trend is NEUTRAL (no macro direction), OR
      - 1h trend disagrees with 4h, OR
      - MACRO_GATE: |mom_4h| > 10% AND the move runs counter to direction, OR
      - 15m momentum doesn't confirm direction (LONG: <0.1; SHORT: >-0.1), OR
      - SM opposes direction (HARD BLOCK), OR
      - RSI extreme (LONG: >78; SHORT: <22).

    MIN_SCORE is NOT applied here — the caller gates on thesis['score'].

    `sm_info` is the smart-money tuple (direction, pct, count, cc_15m) for this
    coin, or None. The caller fetches it (get_sm_map)."""
    macro_gate = float(MACRO_GATE_THRESHOLD_PCT)

    if len(candles_15m) < 8 or len(candles_1h) < 6 or len(candles_4h) < 6:
        return None
    price = _close(candles_15m[-1])

    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1d = price_momentum(candles_1d, 1) if len(candles_1d) >= 2 else 0

    # MACRO TREND GATE — no counter-trend against runaway moves > 10%
    if abs(mom_4h) > macro_gate:
        if (direction == "LONG" and mom_4h < 0) or (direction == "SHORT" and mom_4h > 0):
            return None

    # 15m momentum confirmation
    if direction == "LONG" and mom_15m < 0.1:
        return None
    if direction == "SHORT" and mom_15m > -0.1:
        return None

    score = 0
    reasons = []

    # 4h trend strength
    if trend_strength_4h >= 0.8:
        score += 4
        reasons.append(f"strong 4h {trend_4h.lower()} trend")
    elif trend_strength_4h >= 0.6:
        score += 3
        reasons.append(f"4h trend {trend_4h.lower()}")
    else:
        score += 2
        reasons.append(f"weak 4h {trend_4h.lower()} trend")

    # 1h momentum
    if abs(mom_1h) > 1.0:
        score += 2
        reasons.append(f"strong 1h move, {mom_1h:+.2f}%")
    elif abs(mom_1h) > 0.5:
        score += 1
        reasons.append(f"modest 1h move, {mom_1h:+.2f}%")

    # 1d candle trend
    if len(candles_1d) >= 3:
        if direction == "LONG" and mom_1d > 1.0:
            score += 2; reasons.append(f"daily trend up, {mom_1d:+.1f}%")
        elif direction == "SHORT" and mom_1d < -1.0:
            score += 2; reasons.append(f"daily trend down, {mom_1d:+.1f}%")
        elif direction == "LONG" and mom_1d > 0:
            score += 1; reasons.append("daily candle up")
        elif direction == "SHORT" and mom_1d < 0:
            score += 1; reasons.append("daily candle down")

    # LONG bias bonus (pr0br000 insight — top 5 winners were all LONG)
    if direction == "LONG":
        score += LONG_BIAS_BONUS
        reasons.append("long setups get a built-in bonus")

    # Smart-money alignment / HARD BLOCK
    if sm_info:
        sm_dir, sm_pct, sm_count, sm_cc_15m = sm_info
        if sm_dir == direction:
            score += 2
            reasons.append(f"smart money aligned, {sm_pct:.0f}% ({sm_count} traders)")
            if sm_pct > 70:
                score += 1
                reasons.append("smart money strongly tilted this way")
        elif sm_dir != "NEUTRAL" and sm_dir != direction:
            return None  # HARD BLOCK
        if sm_cc_15m > 0.3:
            score += 1
            reasons.append(f"fresh smart-money flow, +{sm_cc_15m:.2f} in 15m")

    # Funding alignment
    if direction == "LONG" and funding < 0:
        score += 1; reasons.append(f"funding pays longs ({funding:+.4f})")
    elif direction == "SHORT" and funding > 0:
        score += 1; reasons.append(f"funding pays shorts ({funding:+.4f})")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.01):
        score -= 1; reasons.append(f"funding crowded ({funding:+.4f})")

    # Volume ratio
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.3:
        score += 1; reasons.append(f"volume running {vol_1h:.1f}x normal")
    elif vol_1h < 0.6:
        score -= 1; reasons.append("volume below normal")

    # RSI filter (hard block on extremes; +1 for midrange room)
    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 78:
        return None
    if direction == "SHORT" and rsi < 22:
        return None
    if (direction == "LONG" and 50 < rsi < 68) or (direction == "SHORT" and 32 < rsi < 50):
        score += 1; reasons.append(f"RSI at {rsi:.0f}, room to run")

    # Move-exhaustion / move-tiring penalty (with-trend overextension)
    if abs(mom_4h) >= 6.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2; reasons.append(f"move may be exhausted, {mom_4h:+.1f}% in 4h")
    elif abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1; reasons.append(f"move tiring, {mom_4h:+.1f}% in 4h")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "price": price, "trend_4h": trend_4h, "trend_1h": trend_1h,
        "mom_1h": round(mom_1h, 3), "mom_4h": round(mom_4h, 3), "mom_1d": round(mom_1d, 3),
        "funding": funding, "rsi": round(rsi, 1), "vol_ratio": round(vol_1h, 2),
        "max_lev": max_lev,
    }
