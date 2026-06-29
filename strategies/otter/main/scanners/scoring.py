"""OTTER — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 OTTER producer's OI-velocity thesis
(otter-producer.py v2.0.0, "Open Interest Velocity Hunter"). The rolling-history
delta math, the 4-quadrant hard gates, the multi-factor scoring table, and the
conviction-scaled leverage tiers are reproduced VERBATIM so a fidelity harness
can diff this against the v2 producer on the same OI-history snapshot.

Multi-asset universe, single-pass, unit-testable on plain dicts. The caller
(scan.py) owns the clock and the MCP reads; the time-of-day modifier and the
delta computation take their `hour` / `samples` as arguments so this module
stays pure.

Behaviour-preserving v2 quirks are kept and flagged `# v2-quirk`."""

# ── numeric coercion (matches v2 safe_float) ──


def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2 otter-producer.py v2.0.0
# ═══════════════════════════════════════════════════════════════

MIN_SCORE = 9                      # high bar (Polar v2.4 / Cheetah v5.2 / Roach v1.1 pattern)
MIN_OI_DELTA_1H_PCT = 5.0          # 1h OI delta floor — fresh leveraged flow
MIN_PRICE_ALIGN_PCT = 0.5          # 1h price must move >= 0.5% in same direction as OI
MIN_OI_USD = 1_000_000            # liquidity floor
MAX_SPREAD_BPS = 5                 # entry quality gate
ASSET_COOLDOWN_MINUTES = 240       # per-asset cooldown (defense-in-depth alongside runtime)

# Rolling history config — 5min cadence, 60 samples = 5h window
HISTORY_MAX_SAMPLES = 60
SAMPLES_FOR_1H = 12                # 12 samples × 5 min = 60 min
SAMPLES_FOR_4H = 48               # 48 samples × 5 min = 240 min
MIN_SAMPLES_TO_FIRE = SAMPLES_FOR_1H   # need >= 1h of history to compute the 1h delta

# Conviction-scaled leverage (Polar v2.4 / Bald Eagle v3.0 pattern)
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 10},
    {"min_score": 11, "leverage": 7},
    {"min_score": 9,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5


def get_leverage_for_score(score, tiers=None):
    """Returns leverage for the score tier. Ported verbatim from v2
    get_leverage_for_score; the fallback (5x) matches v2 DEFAULT_LEVERAGE."""
    for tier in (tiers or LEVERAGE_TIERS):
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def time_of_day_modifier(hour):
    """UTC time-of-day adjustment (caller owns the clock — keeps this pure).
    Verbatim from v2 time_of_day_modifier (same logic as Roach/Bloodhound)."""
    if 4 <= hour < 14:
        return 1, "tod_active_window"
    elif hour >= 18 or hour < 2:
        return -2, "tod_chop_zone"
    return 0, None


# ═══════════════════════════════════════════════════════════════
# DELTA COMPUTATION — preserved verbatim from v2 compute_deltas
# ═══════════════════════════════════════════════════════════════

def compute_deltas(samples):
    """Compute 1h and 4h OI delta % + price delta % from a list of samples
    (oldest first). Each sample = {ts, oi, mark_px}. Returns dict with computed
    deltas or None if insufficient history. Verbatim from v2 compute_deltas."""
    n = len(samples)
    if n < SAMPLES_FOR_1H + 1:
        return None  # need at least 1h + current

    current = samples[-1]
    sample_1h_ago = samples[-(SAMPLES_FOR_1H + 1)] if n >= SAMPLES_FOR_1H + 1 else None
    sample_4h_ago = samples[-(SAMPLES_FOR_4H + 1)] if n >= SAMPLES_FOR_4H + 1 else None

    out = {
        "samples": n,
        "current_oi": current["oi"],
        "current_px": current["mark_px"],
        "oi_delta_1h_pct": None,
        "price_delta_1h_pct": None,
        "oi_delta_4h_pct": None,
        "price_delta_4h_pct": None,
    }

    if sample_1h_ago and sample_1h_ago["oi"] > 0 and sample_1h_ago["mark_px"] > 0:
        out["oi_delta_1h_pct"] = (current["oi"] - sample_1h_ago["oi"]) / sample_1h_ago["oi"] * 100
        out["price_delta_1h_pct"] = (current["mark_px"] - sample_1h_ago["mark_px"]) / sample_1h_ago["mark_px"] * 100

    if sample_4h_ago and sample_4h_ago["oi"] > 0 and sample_4h_ago["mark_px"] > 0:
        out["oi_delta_4h_pct"] = (current["oi"] - sample_4h_ago["oi"]) / sample_4h_ago["oi"] * 100
        out["price_delta_4h_pct"] = (current["mark_px"] - sample_4h_ago["mark_px"]) / sample_4h_ago["mark_px"] * 100

    return out


# ═══════════════════════════════════════════════════════════════
# 4-QUADRANT FILTER + SCORING — preserved verbatim from v2
# build_candidates' per-asset body
# ═══════════════════════════════════════════════════════════════

def evaluate_oi_velocity(asset_info, samples, sm, hour, inputs=None):
    """Score one asset for an OI-velocity TOP-quadrant setup.

    `asset_info` = {asset, oi, mark_px, oi_usd}
    `samples`    = rolling history list [{ts, oi, mark_px}, ...] (oldest first)
    `sm`         = {direction, pct, traders} or None (SM concentration bonus)
    `hour`       = current UTC hour (caller owns the clock — keeps this pure)

    Returns scored candidate dict (spread_bps still None) or None if any hard
    gate fails / insufficient history. Hard gates, scoring tables, and bonus
    magnitudes are VERBATIM from v2 build_candidates. The `spread` bonus and the
    spread REJECT gate live in scan.py (they need a per-candidate MCP read), as
    in v2 main()."""
    inputs = inputs or {}
    min_oi_usd = float(inputs.get("minOiUsd", MIN_OI_USD))
    min_oi_delta = float(inputs.get("minOiDelta1hPct", MIN_OI_DELTA_1H_PCT))
    min_price_align = float(inputs.get("minPriceAlignPct", MIN_PRICE_ALIGN_PCT))
    min_samples = int(inputs.get("minSamplesToFire", MIN_SAMPLES_TO_FIRE))

    asset = asset_info["asset"]
    oi_usd = asset_info["oi_usd"]

    # Liquidity floor
    if oi_usd < min_oi_usd:
        return None

    # Need at least 1h of history + current sample.
    if len(samples) < min_samples + 1:
        return "BOOTSTRAP"

    deltas = compute_deltas(samples)
    if not deltas or deltas["oi_delta_1h_pct"] is None:
        return "BOOTSTRAP"

    oi_d_1h = deltas["oi_delta_1h_pct"]
    px_d_1h = deltas["price_delta_1h_pct"]
    oi_d_4h = deltas.get("oi_delta_4h_pct")
    px_d_4h = deltas.get("price_delta_4h_pct")

    # ── HARD GATE 1: 1h OI delta floor ──
    if abs(oi_d_1h) < min_oi_delta:
        return None

    # ── HARD GATE 2: top-quadrant only (OI ↑) ──
    # Bottom quadrants (OI ↓) are unwinding signals → Pangolin/Owl territory.
    if oi_d_1h <= 0:
        return None

    # ── HARD GATE 3: 1h price aligned with conviction direction ──
    # OI growing but price flat / ambiguous (could be hedging) — skip.
    if px_d_1h is None:
        return None
    if abs(px_d_1h) < min_price_align:
        return None
    # Flow direction = price direction (OI is growing, so flow adds liquidity to
    # whichever side price is going).
    flow_direction = "LONG" if px_d_1h > 0 else "SHORT"

    # ── HARD GATE 4: 4h OI not net unwinding ──
    # If 1h OI is up but 4h OI is down, the 1h is an inversion of a longer unwind.
    if oi_d_4h is not None and oi_d_4h < 0:
        return None

    # ─── SCORING (verbatim v2 tables) ───
    score = 0.0
    reasons = [
        f"OI_DELTA_1H {oi_d_1h:+.1f}%",
        f"PRICE_DELTA_1H {px_d_1h:+.2f}%",
    ]

    # 1h OI delta tier (4-6 points)
    abs_oi_d = abs(oi_d_1h)
    if abs_oi_d > 20:
        score += 6
        reasons.append("OI_TIER_EXTREME")
    elif abs_oi_d > 10:
        score += 5
        reasons.append("OI_TIER_HIGH")
    else:
        score += 4
        reasons.append("OI_TIER_BASE")

    # 4h confirmation (+2) or contradiction (-2)
    if oi_d_4h is not None:
        if oi_d_4h >= 10:
            score += 2
            reasons.append(f"OI_4H_CONFIRMS {oi_d_4h:+.1f}%")
        elif oi_d_4h < 0:
            score -= 2
            reasons.append(f"OI_4H_CONTRADICTS {oi_d_4h:+.1f}%")

    # 1h price magnitude
    abs_px_d = abs(px_d_1h)
    if abs_px_d > 2:
        score += 2
        reasons.append("PRICE_STRONG")
    elif abs_px_d > 1:
        score += 1
        reasons.append("PRICE_MODERATE")

    # SM concentration alignment
    sm_aligned = False
    sm_pct = 0.0
    if sm:
        sm_dir = sm.get("direction", "")
        sm_pct = sm.get("pct", 0)
        if sm_dir == flow_direction and sm_pct >= 5:
            score += 2
            sm_aligned = True
            reasons.append(f"SM_ALIGNED {sm_pct:.1f}%")

    # Time-of-day modifier
    tod_mod, tod_reason = time_of_day_modifier(hour)
    score += tod_mod
    if tod_reason:
        reasons.append(tod_reason)

    return {
        "asset": asset,
        "direction": flow_direction,
        "score": round(score, 2),
        "oi_delta_1h_pct": oi_d_1h,
        "oi_delta_4h_pct": oi_d_4h,
        "price_delta_1h_pct": px_d_1h,
        "price_delta_4h_pct": px_d_4h,
        "oi_usd": oi_usd,
        "mark_px": deltas["current_px"],
        "samples": deltas["samples"],
        "sm_aligned": sm_aligned,
        "sm_pct": sm_pct,
        "reasons": reasons,
        "spread_bps": None,        # filled in by scan.py for the top scorer(s)
    }


def apply_spread_bonus(candidate, spread_bps, max_spread_bps=MAX_SPREAD_BPS):
    """Verbatim port of the v2 main() spread gate + bonus. Mutates `candidate`
    (score + reasons + spread_bps) and returns True if it PASSES the spread gate
    (<= max_spread_bps), False if it should be skipped. `spread_bps` is the
    measured orderbook spread or None (None -> skip, as in v2)."""
    candidate["spread_bps"] = spread_bps
    if spread_bps is None or spread_bps > max_spread_bps:
        candidate["reasons"].append(f"SKIP_SPREAD {spread_bps}")
        return False
    if spread_bps <= 2:
        candidate["score"] += 2
        candidate["reasons"].append(f"SPREAD_TIGHT {spread_bps:.1f}bps")
    else:
        candidate["score"] += 1
        candidate["reasons"].append(f"SPREAD_OK {spread_bps:.1f}bps")
    return True
