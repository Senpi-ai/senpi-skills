"""OWL — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 OWL producer's contrarian crowding-unwind
scoring (SKILL.md / producer v8.0.1, thesis frozen at v7.1). The crowding score,
exhaustion detection, funding annualization, and leverage tiers are reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
market snapshot. Behaviour-preserving v2 quirks are kept and flagged `# v2-quirk`.

Multi-asset universe, single-pass, unit-testable on plain dicts/candle lists. The
caller (scan.py) owns the clock, the MCP reads, and the cross-tick persistence
ledger; this module is pure functions over numbers."""

# ── numeric coercion (matches v2 safe_float) ──


def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from the v2 v7.1 producer
# ═══════════════════════════════════════════════════════════════

MIN_OI_USD = 3_000_000               # v2 MIN_OI_USD — $3M liquidity floor (no top-N truncation)
MIN_CROWDING_SCORE = 6               # v2 MIN_CROWDING_SCORE (v6.2: lowered 8->6)
MIN_PERSIST_HOURS = 1                # v2 MIN_PERSIST_HOURS (v6.0: lowered 4->1)
BELOW_THRESHOLD_TOLERANCE = 2        # v2 BELOW_THRESHOLD_TOLERANCE (v5.3 noise-tick guard)
MIN_EXHAUSTION_SCORE = 5             # v2 MIN_EXHAUSTION_SCORE
MIN_EXHAUSTION_SIGNALS = 2           # v2 MIN_EXHAUSTION_SIGNALS
MIN_COMBINED_SCORE = 12              # v2 MIN_COMBINED_SCORE (v6.0: lowered 14->12)
MIN_FUNDING_ANNUALIZED_PCT = 12      # v2 MIN_FUNDING_ANNUALIZED_PCT (v5.2: was 20)
MACRO_GATE_BTC_4H_PCT = 3.0          # v2 MACRO_GATE_BTC_4H_PCT (v7.1)
ASSET_COOLDOWN_MINUTES = 360         # v2 ASSET_COOLDOWN_MINUTES — 6h post-emit per-asset cooldown

# Conviction-scaled leverage (v2 LEVERAGE_TIERS; Polar v2.4 / Bald Eagle v3.0 pattern)
DEFAULT_LEVERAGE = 7                  # v2 DEFAULT_LEVERAGE
LEVERAGE_TIERS = [
    {"min_score": 16, "leverage": 10},
    {"min_score": 14, "leverage": 8},
    {"min_score": 12, "leverage": 7},
]


def get_leverage_for_score(score, tiers=None):
    """Returns the leverage for the score tier. Ported verbatim from v2
    get_leverage_for_score; the fallback (7) matches v2 DEFAULT_LEVERAGE."""
    for tier in (tiers or LEVERAGE_TIERS):
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def funding_annualized_pct(funding):
    """v2: abs(hourly funding) * 8760 (= * 24 * 365) -> annualized percent."""
    return abs(_f(funding)) * 8760


# ═══════════════════════════════════════════════════════════════
# CROWDING SCORING — preserved verbatim from v2 score_crowding (v6.2)
# ═══════════════════════════════════════════════════════════════

def score_crowding(asset, sm_long_pct, sm_count, min_funding_ann=MIN_FUNDING_ANNUALIZED_PCT):
    """Score how crowded an asset is. Higher = more one-sided.
    Returns (score, crowd_direction, details).

    `asset` = {coin, oi_usd, funding, ...}
    `sm_long_pct` = smart-money long percentage for this coin (50 = neutral)
    `sm_count` = trader count (carried for parity; not used in the v2 score)

    Scoring tables/thresholds are VERBATIM from the v2 producer."""
    funding = _f(asset.get("funding", 0))
    funding_ann = abs(funding) * 8760  # hourly funding x 24 x 365 = annualized %

    score = 0
    details = []
    crowd_direction = None

    # Funding extremity (biggest signal)
    if funding_ann >= 60:
        score += 4
        details.append(f"funding rate extreme at {funding_ann:.0f}% a year")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= 40:
        score += 3
        details.append(f"funding rate high at {funding_ann:.0f}% a year")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= min_funding_ann:
        score += 2
        details.append(f"funding rate elevated at {funding_ann:.0f}% a year")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    else:
        details.append(f"funding rate mild at {funding_ann:.0f}% a year")
        if funding != 0:
            crowd_direction = "LONG" if funding > 0 else "SHORT"

    # SM concentration (top traders tilted one way)
    sm_tilt = abs(sm_long_pct - 50)
    if sm_tilt > 20:
        score += 3
        sm_dir = "LONG" if sm_long_pct > 50 else "SHORT"
        details.append(f"smart money piled {sm_dir} ({sm_long_pct:.0f}% long)")
        if (funding > 0 and sm_long_pct > 50) or (funding < 0 and sm_long_pct < 50):
            score += 1
            details.append("smart money and funding point the same way")
    elif sm_tilt > 12:
        score += 1
        details.append(f"smart money leaning one way ({sm_long_pct:.0f}% long)")

    # OI concentration (positions building, not churning)
    oi_usd = _f(asset.get("oi_usd", 0))
    if oi_usd > 20_000_000:
        score += 2
        details.append(f"open interest heavy at ${oi_usd/1e6:.0f}M")
    elif oi_usd > 10_000_000:
        score += 1
        details.append(f"open interest building at ${oi_usd/1e6:.0f}M")

    return score, crowd_direction, details


# ═══════════════════════════════════════════════════════════════
# EXHAUSTION DETECTION — preserved verbatim from v2 detect_exhaustion (v6.2)
# ═══════════════════════════════════════════════════════════════

def detect_exhaustion(candles_1h, candles_4h, crowd_direction):
    """Check whether the crowded trade is showing exhaustion signals.

    Pure: takes the already-fetched 1h/4h candle lists (caller does the MCP read)
    and the crowd direction. Returns (score, signals_list, price_chg_4h, rsi).

    Candle field access mirrors v2 (volume/v/vlm, close/c). Thresholds/weights are
    VERBATIM from the v2 producer."""
    candles_1h = candles_1h or []
    candles_4h = candles_4h or []

    if len(candles_1h) < 12 or len(candles_4h) < 6:
        return 0, [], 0, None

    score = 0
    signals = []

    def _vol(c):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))

    def _close(c):
        return _f(c.get("close", c.get("c", 0)))

    # SIGNAL 1: Volume declining while funding stays extreme = exhaustion building
    if len(candles_1h) >= 8:
        recent_vol = sum(_vol(c) for c in candles_1h[-3:]) / 3
        earlier_vol = sum(_vol(c) for c in candles_1h[-8:-3]) / 5
        if earlier_vol > 0 and recent_vol < earlier_vol * 0.7:
            score += 3
            signals.append(f"volume faded to {recent_vol/earlier_vol:.0%} of recent pace")

    # SIGNAL 2: Price stalling despite extreme positioning
    closes_4h = [_close(c) for c in candles_4h[-4:]]
    price_change_4h = 0
    if len(closes_4h) >= 4 and closes_4h[-4] > 0:
        price_change_4h = ((closes_4h[-1] - closes_4h[-4]) / closes_4h[-4]) * 100
        if crowd_direction == "LONG" and price_change_4h < 0.5:
            score += 3
            signals.append(f"crowd is long but price stalled ({price_change_4h:+.1f}% in 4h)")
        elif crowd_direction == "SHORT" and price_change_4h > -0.5:
            score += 3
            signals.append(f"crowd is short but price held ({price_change_4h:+.1f}% in 4h)")

    # SIGNAL 3: Volume spike without price follow-through (capitulation wick)
    if len(candles_1h) >= 6:
        latest_vol = _vol(candles_1h[-1])
        avg_vol = sum(_vol(c) for c in candles_1h[-6:-1]) / 5
        latest_close = _close(candles_1h[-1])
        prev_close = _close(candles_1h[-2])
        price_move = ((latest_close - prev_close) / prev_close * 100) if prev_close > 0 else 0
        if avg_vol > 0 and latest_vol > avg_vol * 2.0 and abs(price_move) < 1.0:
            score += 2
            signals.append(f"volume spiked {latest_vol/avg_vol:.1f}x but price didn't follow")

    # SIGNAL 4: 4h RSI divergence (price flat/up but RSI declining = momentum dying)
    closes_4h_full = [_close(c) for c in candles_4h]
    rsi = None
    if len(closes_4h_full) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes_4h_full)):
            d = closes_4h_full[i] - closes_4h_full[i - 1]
            gains.append(max(0, d))
            losses.append(max(0, -d))
        period = 14
        if len(gains) >= period:
            avg_g = sum(gains[-period:]) / period
            avg_l = sum(losses[-period:]) / period
            rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100

            if crowd_direction == "LONG" and rsi < 55:
                score += 2
                signals.append(f"crowd long yet RSI slipped to {rsi:.0f}")
            elif crowd_direction == "SHORT" and rsi > 45:
                score += 2
                signals.append(f"crowd short yet RSI rose to {rsi:.0f}")

    return score, signals, price_change_4h, rsi


def fade_direction(crowd_direction):
    """Owl is contrarian — the entry direction is the OPPOSITE of the crowd."""
    return "SHORT" if crowd_direction == "LONG" else "LONG"
