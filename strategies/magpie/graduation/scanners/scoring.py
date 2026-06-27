"""MAGPIE · GRADUATION book — pure conversion-detection + momentum math.

A faithful Runtime 3.0 port of the v2 Magpie producer's GRADUATION leg
(magpie-producer.py: classify_instrument + detect_conversion + momentum_pct +
conversion_direction + volume_trend + build_thesis_graduation + clamp_leverage).
No I/O, no MCP, no clock — scan.py does the reads + conversion-window state, this
does the numbers. Math/gates reproduced VERBATIM for fidelity; v2 quirks kept and
flagged `# v2-quirk`.

The thesis: classify every live xyz instrument IPOP-vs-STANDARD each tick by the
funding signature, compare against the prior-tick classification to detect the
IPOP->STANDARD CONVERSION flip (funding jumps ~100x, leverage cap lifts, throttle
off), stamp it into a conversion-eligibility window, and within that window ride
the explosive post-conversion price-discovery momentum — the SpaceX day-1 pattern."""


def _f(c, primary, alt=None, default=0.0):
    # v2-quirk: candle accessor reads dict keys with a short-alias fallback
    # (close|c, volume|v); ported verbatim from the v2 producer.
    val = c.get(primary) if isinstance(c, dict) else None
    if val is None and alt:
        val = c.get(alt) if isinstance(c, dict) else None
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ── conversion detection (ported verbatim) ──

def classify_instrument(funding_abs, max_leverage, ipop_funding_max, ipop_lev_cap):
    """IPOP = |funding| <= ipop_funding_max AND max_leverage <= ipop_lev_cap;
    else STANDARD. The SAME funding signature the pre-listing book discovers on."""
    try:
        f = abs(float(funding_abs))
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return "STANDARD"
    if f <= ipop_funding_max and lev <= ipop_lev_cap:
        return "IPOP"
    return "STANDARD"


def detect_conversion(prev_class, curr_class):
    """A conversion = an IPOP that became STANDARD since last tick (known prior).
    The first time an instrument is seen prev_class is None -> never a false flip."""
    return prev_class == "IPOP" and curr_class == "STANDARD"


# ── momentum helpers (ported verbatim) ──

def momentum_pct(closes, lookback):
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def conversion_direction(momentum, min_momentum_pct):
    if momentum is None or abs(momentum) < min_momentum_pct:
        return None
    return "LONG" if momentum > 0 else "SHORT"


def volume_trend(candles, lookback=6):
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


def clamp_leverage(desired, cap):
    """Clamp desired leverage to the instrument's venue max (per-signal leverage)."""
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = desired
    if cap <= 0:
        cap = desired
    return max(1, min(int(desired), cap))


# ── GRADUATION thesis (post-conversion momentum + SM + volume), ported verbatim ──

def build_thesis_graduation(name, c1h, max_leverage_cap, sm_dir, sm_tilt, config):
    """Returns a scored thesis dict or None if a gate blocks. `c1h` are 1h candles;
    `sm_dir`/`sm_tilt` are the smart-money lean (scan.py fetches them). The caller
    only passes names that are inside the conversion-eligibility window."""
    if len(c1h) < 8:
        return None
    closes = [_f(c, "close", "c") for c in c1h]
    lookback = int(config.get("momentumLookbackBars", 6))
    min_mom = float(config.get("minMomentumPct", 3.0))
    strong_mom = float(config.get("strongMomentumPct", 8.0))

    mom = momentum_pct(closes, lookback)
    direction = conversion_direction(mom, min_mom)
    if direction is None:
        return None

    sm_min = float(config.get("smTiltMinPct", 55))
    sm_strong = float(config.get("smStrongTiltPct", 70))
    vt = volume_trend(c1h)

    score = 3
    reasons = ["converted_ipop_to_equity", f"mom_{mom:+.1f}%"]
    if abs(mom) >= strong_mom:
        score += 2
        reasons.append(f"mom_strong_{mom:+.1f}%")
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")
    return {"coin": name, "direction": direction, "score": score, "reasons": reasons,
            "momentum_pct": round(mom, 2), "max_leverage_cap": max_leverage_cap}
