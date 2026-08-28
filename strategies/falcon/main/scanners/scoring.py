"""FALCON — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 FALCON producer's conversion/momentum
logic (falcon-producer.py v1.0.1 / SKILL.md v1.0.0, "Conversion-Event Momentum
— XYZ Pre-IPO → equity"). The classification heuristic, conversion detector,
momentum math, direction rule, volume trend, and the score table are reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
xyz-instrument snapshot.

`scan.py` owns the clock, the MCP reads, and the cross-tick class/conversion
caches (ctx.state); this module stays pure and unit-testable on plain dicts.
"""

# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2 falcon-producer.py v1.0.1
# ═══════════════════════════════════════════════════════════════

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70

# IPOP funding signature (the same heuristic Lemur uses to FIND IPOPs; Falcon
# uses it to detect when one STOPS being an IPOP).
DEFAULT_IPOP_FUNDING_MAX = 1e-7
DEFAULT_IPOP_LEV_CAP = 5

DEFAULT_MOMENTUM_LOOKBACK = 6       # 1h bars
DEFAULT_MIN_MOMENTUM_PCT = 3.0     # |move| to call a discovery trend
DEFAULT_STRONG_MOMENTUM_PCT = 8.0
DEFAULT_CONVERSION_WINDOW_HOURS = 72   # how long after a flip a name stays eligible

# marginPct here is a PERCENT of withdrawable in (0,100] — the Runtime 3.0 wire
# convention — NOT the v2 fraction (0.15). v2 emitted marginUsd =
# account_value * fraction; the runtime now sizes (marginPct/100)*withdrawable.
DEFAULT_MARGIN_PCT = 15


# ── numeric coercion (matches v2 _f / safe_float) ──

def _f(v, primary=None, alt=None, default=0.0):
    """Two call shapes preserved from v2:
      _f(scalar)                  -> float(scalar) or default
      _f(dict, primary, alt)      -> dict.get(primary) or dict.get(alt) (v2 candle _f)
    """
    if isinstance(v, dict):
        val = v.get(primary)
        if val is None and alt:
            val = v.get(alt)
    else:
        val = v
    try:
        return float(val) if val is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


# ═══════════════════════════════════════════════════════════════
# Conversion / momentum logic (verbatim from v2, unit-testable)
# ═══════════════════════════════════════════════════════════════

def classify_instrument(funding_abs, max_leverage, ipop_funding_max, ipop_lev_cap):
    """Classify an xyz equity instrument by its funding signature.

    IPOP     = pre-listing product: |funding| <= ipop_funding_max AND
               max_leverage <= ipop_lev_cap.
    STANDARD = a normal equity perp: funding has normalized OR the leverage
               cap has lifted.
    Returns "IPOP" or "STANDARD". Verbatim from v2."""
    try:
        f = abs(float(funding_abs))
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return "STANDARD"
    if f <= ipop_funding_max and lev <= ipop_lev_cap:
        return "IPOP"
    return "STANDARD"


def detect_conversion(prev_class, curr_class):
    """A conversion event is an IPOP that has become STANDARD since last tick.
    Requires a known prior class (None prev = first sighting, not a flip).
    Verbatim from v2."""
    return prev_class == "IPOP" and curr_class == "STANDARD"


def momentum_pct(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive.
    Verbatim from v2."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def conversion_direction(momentum, min_momentum_pct):
    """Direction to trade post-conversion price discovery: ride the momentum.
    None if momentum is missing or below the minimum magnitude. Verbatim from v2."""
    if momentum is None or abs(momentum) < min_momentum_pct:
        return None
    return "LONG" if momentum > 0 else "SHORT"


def volume_trend(candles, lookback=6):
    """Recent-vs-earlier volume % change over `lookback` 1h candles. Verbatim from v2."""
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Thesis builder — score one freshly-converted instrument (verbatim v2 table)
# ═══════════════════════════════════════════════════════════════

def build_thesis(name, scan_info, candles, sm_dir, sm_tilt, inputs=None):
    """Score a freshly-converted instrument's post-conversion momentum.

    `name`       = "xyz:NAME"
    `scan_info`  = {"class", "max_leverage", "funding", "vol_usd"} from the scan
    `candles`    = list of 1h candle dicts (caller fetched via MCP)
    `sm_dir`     = smart-money net direction ("LONG"/"SHORT"/"NEUTRAL"/None)
    `sm_tilt`    = smart-money tilt percent

    Returns a scored thesis dict, or None if there is insufficient candle data
    or no confirmed directional momentum. Score table is VERBATIM from v2
    build_thesis (max ~8). Caller owns the clock and all MCP reads."""
    inputs = inputs or {}
    if len(candles) < 8:
        return None
    closes = [_f(c, "close", "c") for c in candles]

    lookback = int(inputs.get("momentumLookbackBars", DEFAULT_MOMENTUM_LOOKBACK))
    min_mom = float(inputs.get("minMomentumPct", DEFAULT_MIN_MOMENTUM_PCT))
    strong_mom = float(inputs.get("strongMomentumPct", DEFAULT_STRONG_MOMENTUM_PCT))
    sm_min = float(inputs.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(inputs.get("smStrongTiltPct", DEFAULT_SM_STRONG))

    mom = momentum_pct(closes, lookback)
    direction = conversion_direction(mom, min_mom)
    if direction is None:
        return None

    vol_trend = volume_trend(candles)

    score = 0
    reasons = ["just converted from pre-IPO to a live equity perp", f"price momentum {mom:+.1f}%"]
    score += 3  # inside conversion window + momentum >= min (gate-confirmed)
    if abs(mom) >= strong_mom:
        score += 2
        reasons.append(f"strong momentum of {mom:+.1f}%")
    # SM is often sparse on a freshly-listed name — agreement is a bonus, not a gate.
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"smart money agrees, {sm_tilt:.0f}% tilted this way")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("smart-money tilt is strong")
    if vol_trend > 15:
        score += 1
        reasons.append(f"volume rising {vol_trend:+.0f}%")

    return {
        "coin": name,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "momentum_pct": round(mom, 2),
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": round(vol_trend, 2),
        "max_leverage_cap": scan_info.get("max_leverage", MAX_LEVERAGE),
    }


def leverage_for(config_leverage, max_leverage_cap):
    """Clamp config leverage to the instrument's post-conversion leverage cap and
    the global MAX_LEVERAGE. Verbatim from v2 main()'s clamp."""
    return min(int(config_leverage), int(max_leverage_cap), MAX_LEVERAGE)
