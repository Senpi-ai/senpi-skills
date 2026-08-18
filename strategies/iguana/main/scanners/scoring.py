"""IGUANA — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 IGUANA producer's pure index-trend logic
(iguana-producer.py v1.0.1 / SKILL.md v1.0.0). IGUANA is the two-asset broad-index
trend-follower on the Hyperliquid XYZ (HIP-3) DEX: xyz:SP500 + xyz:XYZ100. One
decision per tick — compute each index's 4-day trend strength, pick the stronger
move past the floor, and trade in its direction.

The math/indexing is reproduced VERBATIM from iguana-producer.py so a fidelity
harness can diff this against the v2 producer on the same market snapshot. The four
pure functions (trend_strength, trend_direction, pick_strongest_trend, volume_trend)
were already unit-tested in the v2 tests/test_signal.py.

Single-asset-flavoured but two-instrument: stays pure, unit-testable on plain candle
lists. No clock, no MCP — the caller (scan.py) owns all I/O.

INDEX / 24-7-XYZ TUNING PRESERVED (do NOT redesign):
  - Trend strength = % change of the latest close vs the close `lookback` bars ago
    (NOT a structure / higher-low count). Deliberately a slow, broad index-drift read.
  - Volume trend = recent-half vs earlier-half mean over the last 6 4h candles.
  - xyz:SP500 / xyz:XYZ100 trade 24/7 incl weekends — no weekday/session gating.
"""


# ── value coercion (v2: _f, with primary/alt key fallback) ──

def _f(c, primary, alt=None, default=0.0):
    """Coerce c[primary] (or c[alt]) to float, default on missing/bad.

    Ported VERBATIM from iguana-producer._f. `c` is a candle dict; supports the
    dual-shape {close|c} / {volume|v} keys the v2 producer read."""
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ── pure index-trend logic (ported VERBATIM from iguana-producer.py) ──

def trend_strength(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def trend_direction(strength, min_pct):
    """Direction implied by trend strength. None if magnitude below threshold."""
    if strength is None or abs(strength) < min_pct:
        return None
    return "LONG" if strength > 0 else "SHORT"


def pick_strongest_trend(per_asset_strength, min_pct):
    """Among {asset: strength}, return the asset with the highest |strength|
    above min_pct. Returns (asset, strength) or None."""
    best, best_mag = None, -1.0
    for asset, strength in per_asset_strength.items():
        if strength is None:
            continue
        mag = abs(strength)
        if mag < min_pct:
            continue
        if mag > best_mag:
            best_mag, best = mag, (asset, strength)
    return best


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half mean 4h volume over the last `lookback` candles,
    as a % change. 0.0 on insufficient data or non-positive earlier mean.

    Ported VERBATIM from iguana-producer.volume_trend."""
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


# ── thesis builder (ported VERBATIM from iguana-producer.build_thesis) ──

def build_thesis(asset, strength, candles, config):
    """Build the per-asset trend thesis. Returns a thesis dict or None if the
    move does not clear the trend floor.

    Score: 3 (base — trend strength above min) + 2 if |strength| >= strongTrendPct
    + 1 if 4h volume_trend > 15%. Max attainable score = 6.

    Args (all already fetched by the caller — this stays pure):
      asset    : ticker string (e.g. "xyz:SP500")
      strength : float | None — the 4-day trend strength (from trend_strength)
      candles  : the asset's 4h candle list (for the volume factor)
      config   : the inputs dict (thresholds)"""
    min_pct = float(config.get("minTrendPct", 1.5))
    strong_pct = float(config.get("strongTrendPct", 4.0))

    direction = trend_direction(strength, min_pct)
    if direction is None:
        return None

    vol = volume_trend(candles)

    score = 3   # base — trend strength above min
    reasons = [f"{asset} moved {strength:+.1f}% over 4 days"]
    if abs(strength) >= strong_pct:
        score += 2
        reasons.append(f"a strong trend at {strength:+.1f}%")
    if vol > 15:
        score += 1
        reasons.append(f"volume rising {vol:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_pct": round(strength, 2),
        "volume_trend_pct": round(vol, 2),
    }
