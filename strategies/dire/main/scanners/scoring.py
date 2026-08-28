"""DIRE — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 DIRE producer's `build_brentoil_thesis`
(SKILL.md v2.0.0 / scoring preserved verbatim from v1.7.0). DIRE is the
single-asset BRENTOIL XYZ specialist (first non-crypto Kodiak-family port).
News-driven oil: 4TF alignment, smart-money via mark/oracle premium proxy,
OI velocity, volume-spike, price-cleanliness — summed into a 0..13 composite.

The math/indexing is reproduced VERBATIM from `dire-producer.py` so a fidelity
harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix them
only as a separate, labelled change AFTER the port is validated.

Single-asset, single-pass, unit-testable on plain candle lists. `in_quiet_hours`
takes the UTC hour as an argument (the caller owns the clock) so this stays pure.

OIL / 24-7-XYZ TUNING PRESERVED (do NOT redesign):
  - SM via mark/oracle premium proxy (crypto SM tracker not applicable to HIP-3).
  - smPremium thresholds are XYZ-small (HIP-3 oracle tracks mark closely by
    design, so premiums are structurally tiny vs crypto perps).
  - FP-001 quiet-hours = a LIQUIDITY filter (00-04 UTC Asia overnight / EU
    pre-open), NOT a market-hours gate. Oil XYZ trades 24/7 incl weekends —
    there is deliberately no weekday/session gating anywhere here.
  - Price-cleanliness adverse-wick filter (oil news overshoots leave wicks).
"""


# ── value coercion (v2: safe_float) ──

def _f(v, d=0.0):
    if v is None:
        return d
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


def _open(c):
    if isinstance(c, dict):
        return _f(c.get("open", c.get("o", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[1])
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


def _volume(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── trend / 4TF (ported verbatim from v2 trend_direction + check_4tf_alignment) ──

def trend_direction(candles, n=5):
    """Direction from the last n candles. Returns ("BULLISH"|"BEARISH"|"FLAT", pct).

    v2-quirk: % change is measured first-close → last-close over the window (NOT
    a structure / higher-low count like Kodiak's trend_structure). Threshold
    +/-0.15% — deliberately small for oil's news micro-moves."""
    if not candles or len(candles) < n:
        return "FLAT", 0.0
    recent = candles[-n:]
    first = _close(recent[0])
    last = _close(recent[-1])
    if first <= 0:
        return "FLAT", 0.0
    pct = (last - first) / first * 100
    if pct > 0.15:
        return "BULLISH", pct
    if pct < -0.15:
        return "BEARISH", pct
    return "FLAT", pct


def check_4tf_alignment(candles_by_tf):
    """4TF alignment HARD GATE. Returns (direction, aligned, trends, detail).

    aligned=True only if 5m/15m/1h/4h ALL agree and NONE is FLAT.
    v2-quirk: lookback n is 6 for 5m/15m, 4 for 1h/4h."""
    required_tfs = ["5m", "15m", "1h", "4h"]
    trends = {}
    for tf in required_tfs:
        candles = candles_by_tf.get(tf, [])
        n = 6 if tf in ("5m", "15m") else 4
        trend, pct = trend_direction(candles, n=n)
        trends[tf] = {"trend": trend, "pct": pct}
    directions = {trends[tf]["trend"] for tf in required_tfs}
    if "BULLISH" in directions and "BEARISH" not in directions and "FLAT" not in directions:
        return "LONG", True, trends, "all_bullish"
    if "BEARISH" in directions and "BULLISH" not in directions and "FLAT" not in directions:
        return "SHORT", True, trends, "all_bearish"
    return None, False, trends, f"mixed:{directions}"


# ── OI velocity (ported verbatim) ──

def extract_oi_velocity_1h(asset_data):
    """Flat-path extraction of oi_velocity.oi_change_pct_1h.

    v2-quirk / silent-None bug guard: do NOT use nested
    oi_velocity["1h"]["change_pct"] — that path does not exist in the MCP
    response (reference_cobra_antipattern.md). Flat key only."""
    oi_vel = asset_data.get("oi_velocity")
    if not isinstance(oi_vel, dict):
        return None
    val = oi_vel.get("oi_change_pct_1h")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_oi_velocity(oi_vel_change):
    """> +5% -> +2 (accelerating); > +2% -> +1 (rising); < -3% -> -1 (draining);
    null -> 0 (pass)."""
    if oi_vel_change is None:
        return 0, None
    if oi_vel_change > 5:
        return 2, f"open interest accelerating, {oi_vel_change:+.1f}% in 1h"
    if oi_vel_change > 2:
        return 1, f"open interest rising {oi_vel_change:+.1f}% in 1h"
    if oi_vel_change < -3:
        return -1, f"open interest draining {oi_vel_change:+.1f}% in 1h"
    return 0, None


# ── volume spike (news-impact proxy, ported verbatim) ──

def volume_spike_score(candles_15m, candles_1h, threshold=2.5, strong_threshold=5.0):
    """Latest 15m volume vs the 1h average (per-15m-equivalent). Tiered:
    > strong (5x) -> +2 (extreme news spike); > threshold (2.5x) -> +1; else 0."""
    if not candles_15m or not candles_1h:
        return 0, None
    last_15m_vol = _volume(candles_15m[-1])
    recent_1h = candles_1h[-4:] if len(candles_1h) >= 4 else candles_1h
    vols = [_volume(c) for c in recent_1h if _volume(c) > 0]
    if not vols:
        return 0, None
    avg_15m_equiv = (sum(vols) / len(vols)) / 4
    if avg_15m_equiv <= 0:
        return 0, None
    ratio = last_15m_vol / avg_15m_equiv
    if ratio > strong_threshold:
        return 2, f"volume surged {ratio:.1f}x above average"
    if ratio > threshold:
        return 1, f"volume spiked {ratio:.1f}x above average"
    return 0, None


# ── smart-money premium (mark/oracle proxy — XYZ HIP-3 has no crypto SM tracker) ──

def sm_conviction_score(premium_pct_abs, moderate_threshold=0.001, strong_threshold=0.003):
    """Score SM conviction by absolute mark/oracle premium magnitude.
    |premium| > strong (0.3%) -> +2; > moderate (0.1%) -> +1; else 0.

    NOTE: thresholds here are the PRODUCER code defaults (0.001 / 0.003). The v2
    config.json overrode them to 0.0003 / 0.001; the runtime.yaml inputs carry
    the producer defaults per the port directive (prefer producer over config)."""
    if premium_pct_abs is None:
        return 0, None
    if premium_pct_abs > strong_threshold:
        return 2, f"smart-money premium extreme at {premium_pct_abs * 100:.3f}%"
    if premium_pct_abs > moderate_threshold:
        return 1, f"smart-money premium strong at {premium_pct_abs * 100:.3f}%"
    return 0, None


def get_sm_direction(asset_data, config=None):
    """Derive SM direction from markPx vs oraclePx premium in asset_context.
    Positive premium -> mark > oracle -> longs aggressive -> SM LONG (and vice
    versa). Returns (direction|None, premium_abs, reason). None => HARD BLOCK.

    v2-quirk: ambiguous-premium threshold default 0.0001 (0.01%) — XYZ (HIP-3)
    markets structurally have small mark-oracle premiums (oracle tracks mark by
    design), so the band that counts as 'no conviction' is tiny."""
    ctx = asset_data.get("asset_context") or {}
    try:
        premium = float(ctx.get("premium", 0) or 0)
        mark_px = float(ctx.get("markPx", 0) or 0)
    except (TypeError, ValueError):
        return None, 0.0, "parse_error"

    if mark_px <= 0:
        return None, 0.0, "no_mark_px"

    ambiguous_threshold = 0.0001
    if config is not None:
        try:
            ambiguous_threshold = float(config.get("smAmbiguousPremiumAbsPct", 0.0001))
        except (TypeError, ValueError):
            pass

    if abs(premium) < ambiguous_threshold:
        return None, abs(premium), (
            f"sm_ambiguous_premium_{premium * 100:+.4f}%_threshold_{ambiguous_threshold * 100:.4f}%"
        )

    direction = "LONG" if premium > 0 else "SHORT"
    return direction, abs(premium), f"mark price {premium * 100:+.4f}% vs oracle"


# ── price cleanliness (adverse-wick filter — oil news overshoots leave wicks) ──

def price_cleanliness_score(candles_5m, direction, max_wick_pct=1.5, lookback_minutes=30):
    """Scan the last ~30 min of 5m candles for adverse wicks > max_wick_pct.
    Any such wick -> 0 (DIRTY). A clean approach -> +1. Ported verbatim."""
    if not candles_5m or not direction:
        return 0, None
    n_candles = max(6, lookback_minutes // 5)
    recent = candles_5m[-n_candles:]
    if not recent:
        return 0, None
    for c in recent:
        o = _open(c)
        h = _high(c)
        l = _low(c)
        cl = _close(c)
        if o <= 0:
            continue
        if direction == "LONG":
            wick = (min(o, cl) - l) / o * 100
        else:  # SHORT
            wick = (h - max(o, cl)) / o * 100
        if wick > max_wick_pct:
            return 0, f"adverse wick of {wick:.2f}% in recent bars"
    return 1, "clean price action, no adverse wicks"


# ── FP-003 require-all-confirmations (ported verbatim) ──

def all_confirmations_present(reasons):
    """Every soft confirmation (Volume, OI velocity, SM premium, Price clean)
    must have contributed >= +1 — pattern completeness, not just summed score.
    Returns (ok: bool, missing: list[str]). `reasons` is the thesis reason list."""
    reasons = reasons or []
    needed = {
        "VOLUME": ("volume surged", "volume spiked"),
        "OI_VELOCITY": ("open interest accelerating", "open interest rising"),
        "SM_PREMIUM": ("smart-money premium",),
        "PRICE_CLEAN": ("clean price action",),
    }
    missing = []
    for label, prefixes in needed.items():
        if not any(any(r.startswith(p) for p in prefixes) for r in reasons):
            missing.append(label)
    return (not missing), missing


# ── FP-001 quiet hours (LIQUIDITY filter, not a market-hours gate) ──

def in_quiet_hours(hour, start=0, end=4):
    """FP-001: 00:00-04:00 UTC default low-liquidity window (Asia overnight /
    EU pre-open). Pure: caller passes the UTC hour. start == end disables.

    This is a LIQUIDITY filter, not a market-hours gate. xyz:BRENTOIL trades
    24/7 incl weekends — apex setups (score >= apexBypass) bypass this."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ── sizing tiers (conviction-scaled: leverage AND margin scale with score) ──

_DEFAULT_SIZING_TIERS = [
    {"minScore": 9,  "leverage": 3,  "marginPct": 0.20, "label": "cautious"},
    {"minScore": 10, "leverage": 5,  "marginPct": 0.25, "label": "standard"},
    {"minScore": 11, "leverage": 7,  "marginPct": 0.30, "label": "conviction"},
    {"minScore": 12, "leverage": 10, "marginPct": 0.30, "label": "apex"},
]


def resolve_sizing_tier(score, tiers=None):
    """Highest-applicable sizing tier for a score, or None below the lowest floor."""
    tiers = tiers or _DEFAULT_SIZING_TIERS
    applicable = [t for t in tiers if score >= int(t.get("minScore", 0))]
    if not applicable:
        return None
    return max(applicable, key=lambda t: int(t.get("minScore", 0)))


def compute_leverage(score, tiers=None, max_leverage=10):
    """Tier leverage, hard-capped at max_leverage (10x = 50% of HL's 20x BRENTOIL)."""
    tier = resolve_sizing_tier(score, tiers)
    if not tier:
        return 0
    return min(int(tier.get("leverage", 3)), int(max_leverage))


# ── the thesis (gates + 0..13 composite), ported verbatim from build_brentoil_thesis ──

def build_thesis(candles_by_tf, asset_context, oi_velocity_1h, config):
    """Returns a thesis dict (with `score`) or None if any HARD gate blocks.

    Args (all already fetched by the caller — this stays pure):
      candles_by_tf : {"5m":[...], "15m":[...], "1h":[...], "4h":[...]}
      asset_context : the market_get_asset_data asset_context dict (premium/markPx)
      oi_velocity_1h: float | None (flat-path extracted by the caller)
      config        : the inputs dict (thresholds)

    Max attainable score: 6 (base) + 2 (SM) + 2 (OI) + 2 (vol) + 1 (clean) = 13."""
    reasons = []

    # Gate 1: 4TF alignment HARD GATE
    direction, aligned, trends, align_detail = check_4tf_alignment(candles_by_tf)
    if not aligned:
        return None
    reasons.append(f"all four timeframes aligned {direction.lower()}")

    # Gate 2: SM HARD BLOCK (premium direction must match the 4TF direction)
    asset_data_for_sm = {"asset_context": asset_context or {}}
    sm_dir, sm_premium_abs, sm_detail = get_sm_direction(asset_data_for_sm, config)
    if sm_dir is None:
        return None
    if sm_dir != direction:
        return None
    reasons.append(f"smart money positioned {sm_dir.lower()}, {sm_detail}")

    # Base score for any aligned setup
    score = 6  # v2-quirk: baseline 6 for 4TF + SM alignment

    # Gate 3: SM conviction strength
    sm_mod = float(config.get("smPremiumModerateAbsPct", 0.001))
    sm_str = float(config.get("smPremiumStrongAbsPct", 0.003))
    sm_score, sm_reason = sm_conviction_score(sm_premium_abs, moderate_threshold=sm_mod, strong_threshold=sm_str)
    score += sm_score
    if sm_reason:
        reasons.append(sm_reason)

    # Gate 4: OI velocity
    oi_score, oi_reason = score_oi_velocity(oi_velocity_1h)
    score += oi_score
    if oi_reason:
        reasons.append(oi_reason)

    # Gate 5: Volume spike (15m vs 1h)
    vol_threshold = float(config.get("volumeSpikeThreshold", 2.5))
    vol_strong = float(config.get("volumeSpikeStrongThreshold", 5.0))
    vol_score, vol_reason = volume_spike_score(
        candles_by_tf.get("15m", []), candles_by_tf.get("1h", []),
        threshold=vol_threshold, strong_threshold=vol_strong,
    )
    score += vol_score
    if vol_reason:
        reasons.append(vol_reason)

    # Gate 6: Price cleanliness (adverse-wick filter)
    clean_score, clean_reason = price_cleanliness_score(
        candles_by_tf.get("5m", []),
        direction,
        max_wick_pct=float(config.get("priceCleanlinessMaxWickPct", 1.5)),
        lookback_minutes=int(config.get("priceCleanlinessLookbackMinutes", 30)),
    )
    score += clean_score
    if clean_reason:
        reasons.append(clean_reason)

    # telemetry: mark price + per-TF momentum
    mark_px = _f((asset_context or {}).get("markPx"))

    def _mom(tf_candles, n=1):
        if len(tf_candles) < n + 1:
            return 0.0
        old = _close(tf_candles[-(n + 1)])
        new = _close(tf_candles[-1])
        if old == 0:
            return 0.0
        return (new - old) / old * 100

    return {
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trends": trends,
        "trend_5m": trends.get("5m", {}).get("trend"),
        "trend_15m": trends.get("15m", {}).get("trend"),
        "trend_1h": trends.get("1h", {}).get("trend"),
        "trend_4h": trends.get("4h", {}).get("trend"),
        "sm_premium_abs": sm_premium_abs,
        "sm_detail": sm_detail,
        "oi_vel": oi_velocity_1h,
        "mark_px": mark_px,
        "mom_5m": round(_mom(candles_by_tf.get("5m", []), 1), 4),
        "mom_15m": round(_mom(candles_by_tf.get("15m", []), 1), 4),
        "mom_1h": round(_mom(candles_by_tf.get("1h", []), 1), 4),
        "mom_4h": round(_mom(candles_by_tf.get("4h", []), 1), 4),
    }
