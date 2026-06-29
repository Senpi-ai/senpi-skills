"""CHAMELEON — pure relative-value / pairs math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Chameleon producer's ratio mean-reversion
math (SKILL.md v1.0.0 / chameleon-producer.py v1.0.1). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on
the same market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port validates.

Thesis: for a correlated pair (numerator/denominator) compute the latest A/B price
RATIO's z-score vs its mean/std over `lookback` 1h bars. When |z| >= zEntryMin and
the reversion is starting, trade the high-beta LEG in the direction that profits as
the ratio reverts to its mean (single-position runtime -> directional RV bet, not a
two-leg spread). `sm` (smart-money lean on the leg) is fetched by the caller and
passed in, so this module stays pure and unit-testable on plain candle lists.

Score (max ~7):  +2 |z|>=zEntryMin (gate-confirmed) · +2 |z|>=zStrong ·
                 +1 ratio turning · +1 SM on the leg confirms direction · +1 leg vol rising.
"""

import statistics

# v2 defaults (chameleon-producer.py / chameleon-config.json)
DEFAULT_LOOKBACK_BARS = 48          # 1h bars (~2 days) for the ratio mean/std
DEFAULT_Z_ENTRY_MIN = 2.0           # |z| to consider the ratio extended
DEFAULT_Z_STRONG = 3.0
DEFAULT_SM_TILT_MIN = 55


def _f(c, primary, alt=None, default=0.0):
    """Numeric accessor over a candle dict. Verbatim from v2 _f (primary key,
    optional alt key, default). v2 read dicts only; this matches that shape."""
    if isinstance(c, dict):
        val = c.get(primary)
        if val is None and alt:
            val = c.get(alt)
    else:
        val = c
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ── ratio mean-reversion (ported verbatim from v2 chameleon-producer.py) ──

def ratio_zscore(closes_a, closes_b, lookback):
    """z-score of the latest A/B ratio vs the mean/stdev of the ratio over the
    last `lookback` bars. Returns (z, ratio, mean, std) or None if data is
    insufficient or the ratio has ~no variance. Verbatim from v2 ratio_zscore."""
    n = min(len(closes_a), len(closes_b))
    if n < lookback:
        return None
    a = closes_a[-lookback:]
    b = closes_b[-lookback:]
    ratios = [x / y for x, y in zip(a, b) if y > 0]
    if len(ratios) < lookback:
        return None
    mean = statistics.fmean(ratios)
    std = statistics.pstdev(ratios)
    if std <= 0:
        return None
    latest = ratios[-1]
    return (latest - mean) / std, latest, mean, std


def reversion_direction(z, leg, numerator, z_entry_min):
    """Direction to trade `leg` so the position profits from the ratio
    (numerator/denominator) reverting toward its mean. None if |z| too small.
    Verbatim from v2 reversion_direction.

      z high (ratio above mean -> numerator rich vs denominator):
        leg == numerator   -> SHORT   ;   leg == denominator -> LONG
      z low (numerator cheap):
        leg == numerator   -> LONG    ;   leg == denominator -> SHORT
    """
    if z is None or abs(z) < z_entry_min:
        return None
    rich = z > 0   # ratio above mean -> numerator expensive vs denominator
    if leg == numerator:
        return "SHORT" if rich else "LONG"
    return "LONG" if rich else "SHORT"   # leg == denominator


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2."""
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


# ── the thesis (one pair), ported verbatim from v2 build_pair_thesis ──

def build_pair_thesis(pair, closes_by_asset, candles_by_asset, sm, entry_cfg):
    """Port of v2 build_pair_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when:
      - either leg's closes are missing, OR
      - ratio_zscore is None (insufficient history / ~zero ratio variance), OR
      - reversion_direction is None (|z| < zEntryMin).
    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, tilt_pct) for the LEG, or (None, 0)
    — the caller fetches it (fetch_sm_direction) so this stays pure."""
    num, den, leg = pair["numerator"], pair["denominator"], pair["leg"]
    ca, cb = closes_by_asset.get(num), closes_by_asset.get(den)
    if not ca or not cb:
        return None
    lookback = int(entry_cfg.get("lookbackBars", DEFAULT_LOOKBACK_BARS))
    z_min = float(entry_cfg.get("zEntryMin", DEFAULT_Z_ENTRY_MIN))
    z_strong = float(entry_cfg.get("zStrong", DEFAULT_Z_STRONG))

    zr = ratio_zscore(ca, cb, lookback)
    if zr is None:
        return None
    z, ratio, mean, std = zr
    direction = reversion_direction(z, leg, num, z_min)
    if direction is None:
        return None

    # Reversion must be starting, not still extending: |z| one bar ago > |z| now.
    zr_prev = ratio_zscore(ca[:-1], cb[:-1], lookback)
    turning = bool(zr_prev and abs(z) < abs(zr_prev[0]))

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    leg_vol = volume_trend(candles_by_asset.get(leg, []))

    score = 0
    reasons = [f"{num}/{den}_z_{z:+.2f}"]
    score += 2  # |z| >= z_min gate-confirmed
    if abs(z) >= z_strong:
        score += 2
        reasons.append(f"z_extreme_{z:+.2f}")
    if turning:
        score += 1
        reasons.append("ratio_turning")
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
    if leg_vol > 10:
        score += 1
        reasons.append(f"vol_rising_{leg_vol:+.0f}%")

    return {
        "coin": leg,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "pair": f"{num}/{den}",
        "zscore": round(z, 3),
        "ratio": round(ratio, 6),
        "ratio_mean": round(mean, 6),
        "turning": turning,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": float(sm_tilt) if isinstance(sm_tilt, (int, float)) else 0.0,
        "leg_volume_trend_pct": round(leg_vol, 2),
    }


# ── recent-signal dedup helpers (port of v2 _prune_recent_signals semantics) ──

def prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def was_recently_signaled(signaled, coin, ttl, now):
    """True if `coin` was signaled within `ttl` seconds (verbatim semantics)."""
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl
