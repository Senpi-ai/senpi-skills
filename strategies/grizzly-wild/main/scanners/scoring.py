"""GRIZZLY — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Grizzly producer's `build_btc_thesis` +
multi-factor scoring (SKILL.md v7.0.0). The math/indexing is reproduced VERBATIM
so a fidelity harness can diff this against the v2 producer on the same market
snapshot. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`;
fix them only as a separate, labelled change AFTER the port is validated.

Single-asset (BTC), single-pass, unit-testable on plain candle lists. Time-of-day
is NOT a scoring factor for Grizzly (unlike Kodiak) — the only clock dependency is
FP-001 quiet hours, which `scan.py` owns (the caller passes the hour). This module
stays pure.

THE MACRO / REGIME GATE (v5.5 V-recovery) lives in `build_thesis` as GATE 6,
ported verbatim: BTC's 4h structure metric lags V-recoveries by 8-12h, so it can
still read "BEARISH 100%" while price has already rallied 1-2% off the 24h low.
The gate blocks SHORTs whose price is > macroVrecoveryDistancePct above the 24h
low, and mirrors for LONGs below the 24h high. Its inputs are the last 24 1h
candle highs/lows + the current price (all derived from the 1h candle list that
`scan.py` already fetches)."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

def _f(v, d=0.0):
    if v is None:
        return d
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


def _volume(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from v2) ──

def mom(candles, n_bars=1):
    """% change over the last n_bars (price_momentum)."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """(label, strength): fraction of higher-lows (BULLISH) / lower-highs (BEARISH)
    over the last `lookback` bars. Entry requires strength >= 0.75.

    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1], highs[i] < highs[i-1]) —
    # equal bars do NOT count. NEUTRAL returns strength 0.0 (not max(bull,bear)).
    # Reproduced verbatim from v2 trend_structure; differs from Kodiak's >= variant."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def rsi(closes, period=14):
    # v2-quirk: builds gain/loss arrays over ALL closes, then averages the LAST
    # `period` of each (gains[-period:]). This is the trailing-window RSI of the v2
    # Grizzly producer — reproduced verbatim. (Differs from Kodiak's first-window
    # quirk; do not "fix" or align inside the port.)
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [_volume(c) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = _volume(candles[-1])
    return latest / avg if avg > 0 else 1.0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0.0
    vols = [_volume(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


def get_leverage(score, tiers, default_leverage=7):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score.
    Below the lowest tier, returns `default_leverage` (v2 DEFAULT_LEVERAGE=7)."""
    for t in tiers:
        if score >= t[0]:
            return int(t[1])
    return int(default_leverage)


# ── the thesis (gates + multi-factor score), ported verbatim from build_btc_thesis ──

def build_thesis(c5, c15, c1h, c4h, funding, oi_velocity, sm,
                 funding_regime, funding_persistence_h, inputs):
    """Returns a thesis dict (with `score`) or None if any gate blocks.

    `sm` is the smart-money dict {direction, pct, traders, cc_15m} or None (the caller
    fetches it via leaderboard_get_markets). SM_OPPOSES is a HARD BLOCK (returns None).
    `oi_velocity` is the raw market_get_asset_data `oi_velocity` block (dict or None).
    `funding_regime` is the market_get_funding_regime regime string (or None).
    `funding_persistence_h` is the funding-history persistence_hours float (or None).
    All four are optional; a missing input degrades that factor, never crashes."""
    min_ts_4h = float(inputs.get("minTrendStrength4h", 0.75))
    min_mom_15m = float(inputs.get("minMom15mPct", 0.05))
    rsi_long_max = float(inputs.get("rsiMaxLong", 70))
    rsi_short_min = float(inputs.get("rsiMinShort", 30))
    strong_4h_pct = float(inputs.get("strong4hPct", 2.0))
    move_exhaustion_pct = float(inputs.get("moveExhaustionPct", 2.5))
    move_tiring_pct = float(inputs.get("moveTiringPct", 1.5))
    min_vol_ratio = float(inputs.get("minVolRatio", 1.1))
    funding_crowded = float(inputs.get("fundingCrowded", 0.003))
    vrecovery_distance_pct = float(inputs.get("macroVrecoveryDistancePct", 1.25))

    if len(c5) < 12 or len(c15) < 8 or len(c1h) < 8 or len(c4h) < 6:
        return None

    price = _close(c5[-1])

    # ── GATE 1: 4h trend structure != NEUTRAL ──
    trend_4h, ts_4h = trend_structure(c4h)
    if trend_4h == "NEUTRAL":
        return None

    # ── GATE 2: strong 4h structural alignment (v5.3 fix) ──
    if ts_4h < min_ts_4h:
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── GATE 3: 1h matches 4h ──
    trend_1h, _ = trend_structure(c1h)
    if trend_1h != trend_4h:
        return None

    # ── GATE 4: 15m momentum confirms direction ──
    mom_5m = mom(c5, 1)
    mom_15m = mom(c15, 1)
    mom_1h = mom(c1h, 2)
    mom_4h = mom(c4h, 1)
    if direction == "LONG" and mom_15m < min_mom_15m:
        return None
    if direction == "SHORT" and mom_15m > -min_mom_15m:
        return None

    # ── GATE 5: base-tech floor (strong_15m OR aligned_5m) ──
    strong_15m = abs(mom_15m) > min_mom_15m * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return None

    # ── GATE 6: v5.5 MACRO V-RECOVERY GATE — ported VERBATIM ──
    # BTC's 4h structure metric lags V-recoveries by 8-12h. During a V-recovery the
    # 4h gate still reads "BEARISH 100%" while price has rallied 1-2% off the 24h low.
    # Block SHORTs whose price is > vrecovery_distance_pct above the 24h low; mirror
    # for LONGs below the 24h high. Inputs: last 24 1h-candle highs/lows + price.
    # Live failure 2026-04-23: BTC V-bottomed $76.4k @ 04:00 UTC, rallied to $77,495
    # by 10:38 (+1.43% off low) with trend_strength_4h 0.80 — fired SHORT, lost.
    if len(c1h) >= 24:
        highs_1h_24 = [_high(c) for c in c1h[-24:]]
        lows_1h_24 = [_low(c) for c in c1h[-24:]]
        high_24h = max([h for h in highs_1h_24 if h > 0], default=0)
        low_24h = min([lo for lo in lows_1h_24 if lo > 0], default=0)
        if direction == "SHORT" and low_24h > 0:
            distance_from_low_pct = (price - low_24h) / low_24h * 100
            if distance_from_low_pct > vrecovery_distance_pct:
                return None  # V_RECOVERY_BLOCK_SHORT
        if direction == "LONG" and high_24h > 0:
            distance_from_high_pct = (high_24h - price) / high_24h * 100
            if distance_from_high_pct > vrecovery_distance_pct:
                return None  # V_RECOVERY_BLOCK_LONG

    # ── ALL HARD GATES PASSED (except SM-opposes + RSI, checked inline below) — SCORE ──
    score = 0
    reasons = []

    # 4h trend (3 pts — the foundation)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{ts_4h:.0%}")

    # 1h trend agreement (2 pts)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # 15m momentum strength (1 pt if strong)
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    else:
        reasons.append(f"15m_{mom_15m:+.2f}%")

    # 5m alignment (1 pt — all 4 timeframes agree)
    if aligned_5m:
        score += 1
        reasons.append("4TF_aligned")

    # SM positioning — HARD BLOCK if opposes (BTC has strongest SM signal)
    sm_dir = sm_pct = sm_count = sm_cc_15m = None
    if sm:
        sm_dir = sm.get("direction")
        sm_pct = _f(sm.get("pct", 0))
        sm_count = int(sm.get("traders", 0) or 0)
        sm_cc_15m = _f(sm.get("cc_15m", 0))
    else:
        # v2: get_btc_sm_direction returns (None,0,0,0) on read failure → no align bonus,
        # no opposes block, and the 15m-stale penalty fires (sm_cc_15m=0 <= 0).
        sm_dir, sm_pct, sm_count, sm_cc_15m = None, 0.0, 0, 0.0
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return None  # sm_opposes — HARD BLOCK

    # 15m velocity freshness
    if sm_cc_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({sm_cc_15m:.2f})")
    elif sm_cc_15m > 0.5:
        score += 1
        reasons.append(f"15M_FRESH +{sm_cc_15m:.2f}")

    # Funding alignment
    if direction == "LONG" and funding < 0:
        score += 2
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 2
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")
    elif (direction == "LONG" and funding > funding_crowded) or \
         (direction == "SHORT" and funding < -funding_crowded):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # Funding regime
    regime = funding_regime
    if regime == "LONG_CROWDED" and direction == "LONG":
        score += 1
        reasons.append("REGIME_LONG_CROWDED_aligned")
    elif regime == "SHORT_CROWDED" and direction == "SHORT":
        score += 1
        reasons.append("REGIME_SHORT_CROWDED_aligned")
    elif regime == "LONG_CROWDED" and direction == "SHORT":
        score -= 1
        reasons.append("REGIME_LONG_CROWDED_fighting")
    elif regime == "SHORT_CROWDED" and direction == "LONG":
        score -= 1
        reasons.append("REGIME_SHORT_CROWDED_fighting")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # Funding persistence
    persistence_h = None
    if funding_persistence_h is not None:
        try:
            persistence_h = float(funding_persistence_h)
        except (TypeError, ValueError):
            persistence_h = None
        if persistence_h is not None and persistence_h >= 6:
            score += 1
            reasons.append(f"FUNDING_PERSISTENT_{persistence_h:.0f}h")

    # Volume confirmation
    vol_1h = volume_ratio(c1h)
    if vol_1h >= min_vol_ratio:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vt = volume_trend(c1h)
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")

    # OI growth proxy (legacy fallback if OI velocity missing)
    vol_recent = sum(_volume(c) for c in c1h[-3:])
    vol_earlier = sum(_volume(c) for c in c1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # OI velocity (real OI data when available)
    oi_vel = oi_velocity if isinstance(oi_velocity, dict) else {}
    oi_change = None
    oi_vel_1h = oi_vel.get("1h", {}) if isinstance(oi_vel.get("1h"), dict) else {}
    oi_vel_change = oi_vel_1h.get("change_pct")
    if oi_vel_change is not None:
        try:
            oi_change = float(oi_vel_change)
            if oi_change > 5:
                score += 2
                reasons.append(f"OI_ACCELERATING_{oi_change:+.1f}%")
            elif oi_change > 2:
                score += 1
                reasons.append(f"OI_rising_{oi_change:+.1f}%")
            elif oi_change < -3:
                score -= 1
                reasons.append(f"OI_draining_{oi_change:+.1f}%")
        except (TypeError, ValueError):
            oi_change = None

    # RSI hard gates + bonus
    closes_1h = [_close(c) for c in c1h]
    r = rsi(closes_1h)
    if direction == "LONG" and r > rsi_long_max:
        return None  # rsi_overbought — HARD BLOCK
    if direction == "SHORT" and r < rsi_short_min:
        return None  # rsi_oversold — HARD BLOCK
    if (direction == "LONG" and r < 55) or (direction == "SHORT" and r > 45):
        score += 1
        reasons.append(f"rsi_room_{r:.0f}")

    # 4h momentum bonus
    if abs(mom_4h) > strong_4h_pct:
        score += 1
        reasons.append(f"4h_strong_{mom_4h:+.1f}%")

    # Move-exhaustion / tiring penalty
    if abs(mom_4h) >= move_exhaustion_pct:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= move_tiring_pct:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": round(price, 2),
        "rsi": round(r, 1),
        "sm_pct": round(_f(sm_pct), 2),
        "sm_traders": int(sm_count),
        "sm_cc_15m": round(_f(sm_cc_15m), 3),
        "funding": funding,
        "regime": regime,
        "persistence_h": persistence_h,
        "oi_change_1h": oi_change,
        "vol_1h": round(vol_1h, 3),
        "mom_5m": round(mom_5m, 3),
        "mom_15m": round(mom_15m, 3),
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "trend_4h": trend_4h,
        "trend_strength_4h": round(ts_4h, 3),
        "trend_1h": trend_1h,
    }


def all_confirmations_present(reasons):
    """FP-003: require each soft confirmation to contribute, not just score-summed.
    Five Kodiak-family confirmations (4TF + SM + Funding + Volume + OI) must all fire.
    Ported verbatim from v2 all_confirmations_present. Returns (ok, missing)."""
    reasons = reasons or []
    needed = {
        "4TF_ALIGNED": ("4TF_aligned",),
        "SM_ALIGNED": ("sm_aligned_",),
        "FUNDING_OK": ("funding_pays_",),
        "VOLUME": ("vol_",),
        "OI_ACCELERATING": ("OI_ACCELERATING_", "OI_rising_", "oi_growing_"),
    }
    missing = []
    for label, prefixes in needed.items():
        if not any(any(rsn.startswith(p) for p in prefixes) for rsn in reasons):
            missing.append(label)
    return (not missing), missing


def in_quiet_hours(hour, start_utc, end_utc):
    """FP-001: is `hour` (UTC) inside the quiet window? start==end disables.
    Ported verbatim from v2 in_quiet_hours (the caller owns the clock)."""
    start = int(start_utc)
    end = int(end_utc)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end
