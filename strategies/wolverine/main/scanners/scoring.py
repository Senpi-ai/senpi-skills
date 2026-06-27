"""WOLVERINE — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Wolverine producer's `build_hype_thesis`
(SKILL.md v6.1.0). The math/indexing/gating is reproduced VERBATIM so a fidelity
harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix them
only as a separate, labelled change AFTER the port is validated.

Single-asset (HYPE), single-pass, unit-testable on plain candle lists. The MACRO /
REGIME GATE — market-wide funding regime + HYPE funding persistence — is ported
faithfully here as a scoring layer; the SMART-MONEY HARD BLOCK (opposing direction)
and the six structural gates are ported as hard returns of `None`. scan.py fetches
the regime / persistence / OI / BTC inputs and hands them in (the caller owns I/O).
"""


# ── safe accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
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


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from the v2 producer (HYPE-tuned, v6.1.0).
# Producer values take precedence over config.json; differences noted below.
# ═══════════════════════════════════════════════════════════════

MIN_SCORE_DEFAULT = 9             # v2-quirk: producer floor is 9; config.json sets
                                  # minScore=10 ("patient-conviction"). runtime.yaml
                                  # inputs.minScore=10 (config wins for the GATE), but
                                  # the producer floor constant stayed 9. We honour the
                                  # config value 10 via inputs.minScore in scan.py.
MIN_MOM_15M = 0.15
MIN_4H_STRUCTURE = 0.65           # v4.2: 0.75 -> 0.65 (3 of 5 4h candles; captures
                                  # clean trends with normal pullback structure).
MIN_4H_MAGNITUDE_PCT = 1.0        # v4.2: 1.5 -> 1.0, paired with trailing-window mom_4h.
RSI_MAX_LONG = 72                 # v2-quirk: SKILL.md prose says "74/26" but the
RSI_MIN_SHORT = 28                # producer constants are 72/28. Ported the CODE values.
FUNDING_CROWDED = 0.005

# Move-exhaustion
STRONG_4H_PCT = 2.5
MOVE_EXHAUSTION_PCT = 3.5
MOVE_TIRING_PCT = 2.0


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
    # v2-quirk: STRICT inequalities (lows[i] > lows[i-1]), unlike kodiak's >=.
    # Ported verbatim — do not relax inside the port.
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


def calc_rsi(closes, period=14):
    # v2-quirk: uses the LAST `period` gains/losses (closes[-period:]), unlike
    # kodiak's first-window quirk. Ported verbatim from the v2 producer.
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
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
    """% change avg(latest 3) vs avg(prior 3)."""
    if len(candles) < lookback:
        return 0.0
    recent = [_volume(c) for c in candles[-3:]]
    prior = [_volume(c) for c in candles[-6:-3]]
    avg_r = sum(recent) / max(1, len(recent))
    avg_p = sum(prior) / max(1, len(prior))
    if avg_p == 0:
        return 0.0
    return ((avg_r - avg_p) / avg_p) * 100


def trailing_mom_4h(candles_1h, candles_4h):
    """v4.2: mom_4h via TRAILING WINDOW using 1H candles, not the grid-based 4h
    candle. The grid approach sees ONE 4h candle's change (0.5-1.5% on HYPE grinds);
    the trailing window captures cumulative momentum across the past 4 hours."""
    if len(candles_1h) >= 5:
        close_now = _close(candles_1h[-1])
        close_4h_ago = _close(candles_1h[-5])
        if close_4h_ago > 0:
            return ((close_now - close_4h_ago) / close_4h_ago) * 100
        return mom(candles_4h, 1)   # fallback to grid-based
    return mom(candles_4h, 1)       # not enough 1h candles


def get_leverage_tier(score, tiers):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score.
    Returns (leverage, label)."""
    for t in tiers:
        if score >= t[0]:
            label = t[2] if len(t) > 2 else "tier"
            return int(t[1]), label
    if tiers:
        return int(tiers[-1][1]), (tiers[-1][2] if len(tiers[-1]) > 2 else "default")
    return 3, "default"


# ═══════════════════════════════════════════════════════════════
# THE THESIS (six structural gates + SM hard block + macro/regime layer + score),
# ported VERBATIM from the v2 producer's build_hype_thesis.
# ═══════════════════════════════════════════════════════════════

def build_thesis(c5, c15, c1h, c4h, funding, oi_velocity, sm, regime,
                 persistence_h, btc_mom_15m, btc_mom_1h, inputs):
    """Returns a thesis dict (with `score`) or None if any gate / hard-block fires.

    Inputs the CALLER fetches and passes in (the macro/regime gate's extra data):
      - sm:            {direction, pct, traders, cc_15m} | None   (leaderboard_get_markets)
      - regime:        "LONG_CROWDED" | "SHORT_CROWDED" | <other> | None  (market_get_funding_regime)
      - persistence_h: float | None                               (market_get_funding_history)
      - btc_mom_15m / btc_mom_1h: float | None                    (market_get_asset_data BTC)
      - oi_velocity:   dict | {}                                  (asset_data.oi_velocity)
    """
    min_4h_structure = float(inputs.get("min4hStructure", MIN_4H_STRUCTURE))
    min_4h_magnitude = float(inputs.get("min4hMagnitudePct", MIN_4H_MAGNITUDE_PCT))
    min_mom_15m = float(inputs.get("minMom15mPct", MIN_MOM_15M))
    rsi_long_max = float(inputs.get("rsiMaxLong", RSI_MAX_LONG))
    rsi_short_min = float(inputs.get("rsiMinShort", RSI_MIN_SHORT))
    funding_crowded = float(inputs.get("fundingCrowded", FUNDING_CROWDED))

    if len(c5) < 12 or len(c15) < 8 or len(c1h) < 8 or len(c4h) < 6:
        return None  # insufficient_candles

    price = _close(c5[-1])

    # ── GATE 1: 4h trend structure != NEUTRAL ──
    trend_4h, ts_4h = trend_structure(c4h)
    if trend_4h == "NEUTRAL":
        return None  # 4h_NEUTRAL

    # ── GATE 2: strong 4h structural alignment ──
    if ts_4h < min_4h_structure:
        return None  # 4h_weak

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── GATE 3: 1h must NOT actively oppose 4h (relaxed 2026-05-14) ──
    # v2-quirk: NOT strict equality. NEUTRAL 1h passes (pullback-within-trend);
    # only an actively OPPOSITE 1h blocks.
    trend_1h, _ = trend_structure(c1h)
    opposite = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}
    if trend_1h == opposite.get(trend_4h):
        return None  # 1h opposes 4h

    # ── momentum reads ──
    mom_5m = mom(c5, 1)
    mom_15m = mom(c15, 1)
    mom_1h = mom(c1h, 2)
    mom_4h = trailing_mom_4h(c1h, c4h)

    # ── GATE 4: 15m momentum confirms ──
    if direction == "LONG" and mom_15m < min_mom_15m:
        return None  # 15m_too_weak
    if direction == "SHORT" and mom_15m > -min_mom_15m:
        return None  # 15m_too_weak

    # ── GATE 5: base-tech floor ──
    strong_15m = abs(mom_15m) > min_mom_15m * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return None  # base_tech_weak

    # ── GATE 6: 4h MAGNITUDE floor — reject dead-flat chop ──
    if abs(mom_4h) < min_4h_magnitude:
        return None  # 4h_magnitude_too_flat

    # ═══════ ALL STRUCTURAL GATES PASSED — SCORE ═══════
    score = 0
    reasons = []

    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{ts_4h:.0%}")
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")
    if strong_15m:
        score += 1
        reasons.append(f"15m_strong_{mom_15m:+.2f}%")
    if aligned_5m:
        score += 1
        reasons.append("4TF_aligned")

    # ── SM positioning — HARD BLOCK if opposes (the macro-conviction gate) ──
    sm_dir = sm.get("direction") if sm else None
    sm_pct = _f(sm.get("pct", 0)) if sm else 0.0
    sm_count = int(sm.get("traders", 0)) if sm else 0
    sm_cc_15m = _f(sm.get("cc_15m", 0)) if sm else 0.0
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct > 65:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return None  # sm_opposes — HARD BLOCK

    # ── SM 15m freshness ──
    if sm_cc_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({sm_cc_15m:.2f})")
    elif sm_cc_15m > 0.5:
        score += 1
        reasons.append(f"15M_FRESH +{sm_cc_15m:.2f}")

    # ── Funding alignment ──
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

    # ── MACRO / REGIME GATE (scoring layer): market-wide funding regime ──
    # regime is the market-wide crowding signal (market_get_funding_regime):
    # LONG_CROWDED = longs are paying/crowded, SHORT_CROWDED = shorts paying/crowded.
    # Trading WITH the crowd adds conviction; FIGHTING it is penalised. A missing
    # regime (None) is neutral — degrade, never block.
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

    # ── Funding persistence (macro/regime: how long the regime has held) ──
    if persistence_h is not None and persistence_h >= 6:
        score += 1
        reasons.append(f"FUNDING_PERSISTENT_{persistence_h:.0f}h")

    # ── Volume ──
    vol_1h = volume_ratio(c1h)
    if vol_1h >= 1.2:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.7:
        score -= 1
        reasons.append("vol_weak")

    vt = volume_trend(c1h)
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")

    # ── OI velocity ──
    oi_change = None
    if isinstance(oi_velocity, dict):
        oi_raw = oi_velocity.get("oi_change_pct_1h")
        if oi_raw is not None:
            try:
                oi_change = float(oi_raw)
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

    # ── BTC correlation (v2-quirk: requires BOTH 15m AND 1h to agree) ──
    if btc_mom_15m is not None and btc_mom_1h is not None:
        btc_agrees = (direction == "LONG" and btc_mom_15m > 0 and btc_mom_1h > 0) or \
                     (direction == "SHORT" and btc_mom_15m < 0 and btc_mom_1h < 0)
        if btc_agrees:
            score += 1
            reasons.append(f"btc_confirms_{btc_mom_1h:+.2f}%")

    # ── RSI hard gates + room bonus ──
    closes_1h = [_close(c) for c in c1h]
    r = calc_rsi(closes_1h)
    if direction == "LONG" and r > rsi_long_max:
        return None  # rsi_overbought — HARD GATE
    if direction == "SHORT" and r < rsi_short_min:
        return None  # rsi_oversold — HARD GATE
    if (direction == "LONG" and r < 55) or (direction == "SHORT" and r > 45):
        score += 1
        reasons.append(f"rsi_room_{r:.0f}")

    # ── 4h momentum bonus ──
    if abs(mom_4h) > STRONG_4H_PCT:
        score += 1
        reasons.append(f"4h_strong_{mom_4h:+.1f}%")

    # ── Move-exhaustion penalty ──
    if abs(mom_4h) >= MOVE_EXHAUSTION_PCT:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= MOVE_TIRING_PCT:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "direction": direction,
        "score": round(score, 2),
        "reasons": reasons,
        "price": price,
        "rsi": round(r, 1),
        "trend_4h": trend_4h,
        "trend_strength_4h": round(ts_4h, 3),
        "trend_1h": trend_1h,
        "mom_5m": round(mom_5m, 3),
        "mom_15m": round(mom_15m, 3),
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "funding": funding,
        "regime": regime,
        "persistence_h": persistence_h,
        "oi_change_1h": oi_change,
        "vol_1h": round(vol_1h, 3),
        "btc_mom_15m": btc_mom_15m if btc_mom_15m is None else round(btc_mom_15m, 3),
        "btc_mom_1h": btc_mom_1h if btc_mom_1h is None else round(btc_mom_1h, 3),
        "sm_pct": round(_f(sm_pct), 2),
        "sm_traders": int(sm_count),
        "sm_cc_15m": round(_f(sm_cc_15m), 3),
        "sm_aligned": sm_dir == direction,
    }
