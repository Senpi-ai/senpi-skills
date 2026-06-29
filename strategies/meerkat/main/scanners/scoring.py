"""MEERKAT — pure momentum-event thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Meerkat producer's pure functions
(event_age_minutes, event_direction, momentum_tier, event_score) plus the
defensive event-shape accessors. The math is reproduced VERBATIM from
meerkat-producer.py v1.0.1 so a fidelity harness can diff this against the v2
producer on the same momentum-event feed snapshot.

All functions are pure: they take plain dicts / numbers (the caller in scan.py
does the MCP reads + clock) so they remain unit-testable exactly as the v2
tests/test_signal.py exercised them. The `now` timestamp and the smart-money
tuple are passed IN, never read here.
"""


def safe_float(v, default=0.0):
    """Verbatim v2 safe_float."""
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


# ── defensive event-shape accessors (verbatim v2) ──

def event_asset(event):
    if not isinstance(event, dict):
        return ""
    return str(event.get("token", event.get("coin", event.get("asset", event.get("symbol", ""))))).upper()


def event_magnitude(event):
    if not isinstance(event, dict):
        return 0.0
    return safe_float(
        event.get("momentum", event.get("change_pct", event.get("changePct", event.get("delta", 0))))
    )


def event_timestamp(event):
    if not isinstance(event, dict):
        return None
    return event.get("ts", event.get("timestamp", event.get("time", event.get("created_at"))))


# ── pure momentum-event logic (unit-tested in v2 tests/test_signal.py) ──

def event_age_minutes(event_ts, now_ts):
    """Minutes since an event fired. event_ts may be epoch seconds or
    milliseconds. None if missing/unparseable. Verbatim v2."""
    if event_ts is None:
        return None
    try:
        ts = float(event_ts)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts > 1e12:        # milliseconds → seconds
        ts /= 1000.0
    return (now_ts - ts) / 60.0


def event_direction(event):
    """LONG / SHORT for a momentum event: explicit direction/side, else the
    sign of the momentum/change magnitude. None if undeterminable. Verbatim v2."""
    if not isinstance(event, dict):
        return None
    d = str(event.get("direction", event.get("side", ""))).upper()
    if d in ("LONG", "SHORT"):
        return d
    mag = safe_float(
        event.get("momentum", event.get("change_pct", event.get("changePct", event.get("delta", 0))))
    )
    if mag > 0:
        return "LONG"
    if mag < 0:
        return "SHORT"
    return None


def momentum_tier(magnitude_pct, tier2_min, tier3_min):
    """Classify |momentum| into a tier: 3 (strongest) >= tier3_min,
    2 >= tier2_min, else 1. Verbatim v2."""
    m = abs(safe_float(magnitude_pct))
    if m >= tier3_min:
        return 3
    if m >= tier2_min:
        return 2
    return 1


def event_score(tier, fresh, sm_aligned, vol_rising):
    """Score a momentum event (max ~7). Tier is the backbone; freshness is the
    sniper edge; SM + volume are confirmation bonuses. Verbatim v2."""
    score = {1: 1, 2: 2, 3: 3}.get(tier, 0)
    if fresh:
        score += 2
    if sm_aligned:
        score += 1
    if vol_rising:
        score += 1
    return score


def volume_rising(candles_1h, threshold=0.15):
    """True if the asset's recent 1h volume is rising vs the prior window.
    Verbatim port of v2 fetch_volume_rising's pure tail (the MCP fetch lives in
    scan.py; this consumes the parsed 1h candle list).

    Needs >= 6 candles; compares mean of last-3 vs first-3 of the trailing-6
    window, returns True iff the rise exceeds `threshold` (v2: 0.15 = 15%)."""
    if not isinstance(candles_1h, list) or len(candles_1h) < 6:
        return False
    vols = [safe_float(c.get("volume", c.get("v", 0)) if isinstance(c, dict) else 0)
            for c in candles_1h[-6:]]
    recent = sum(vols[-3:]) / 3
    earlier = sum(vols[:3]) / 3
    return earlier > 0 and (recent - earlier) / earlier > threshold


# ── thesis builder (verbatim port of v2 build_thesis; SM tuple passed IN) ──

def build_thesis(event, config, now, sm, vol_rising):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    Differs from v2 ONLY in that the smart-money tuple (sm_dir, sm_tilt) and the
    vol_rising boolean are passed IN by the caller (which does the MCP reads),
    keeping this module pure. The gate logic, tier classification, freshness
    gate, scoring, and reason strings are reproduced VERBATIM.

    Returns None when: no asset, no resolvable direction, tier < minTier, or the
    event is stale (age > maxEventAgeMinutes). minScore is applied by the CALLER.
    """
    asset = event_asset(event)
    if not asset:
        return None
    direction = event_direction(event)
    if direction is None:
        return None

    mag = event_magnitude(event)
    tier2 = float(config.get("tier2MinPct", 5.0))
    tier3 = float(config.get("tier3MinPct", 10.0))
    tier = momentum_tier(mag, tier2, tier3)
    if tier < int(config.get("minTier", 2)):
        return None

    age = event_age_minutes(event_timestamp(event), now)
    max_age = float(config.get("maxEventAgeMinutes", 30.0))
    # No timestamp → treat as fresh (feed only returns current-window events).
    fresh = age is None or age <= max_age
    if not fresh:
        return None

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    sm_min = float(config.get("smTiltMinPct", 55))
    sm_strong = float(config.get("smStrongTiltPct", 70))
    sm_aligned = (sm_dir == direction and sm_tilt >= sm_min)

    score = event_score(tier, fresh, sm_aligned, vol_rising)
    reasons = [f"momentum_event_{direction}", f"tier_{tier}", f"mag_{mag:+.1f}%"]
    if fresh:
        reasons.append("fresh" if age is None else f"fresh_{age:.0f}min")
    if sm_aligned:
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vol_rising:
        reasons.append("vol_rising")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "tier": tier,
        "magnitude_pct": round(mag, 2),
        "age_min": round(age, 1) if age is not None else None,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": safe_float(sm_tilt),
        "vol_rising": bool(vol_rising),
    }
