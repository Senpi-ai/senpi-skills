"""RAVEN — pure thesis math + self-calibration (no I/O, no MCP, no clock).

Two halves, both pure and unit-testable:

  1. MOMENTUM ENGINE — the indicator math + direction-waterfall + weighted score
     are ported VERBATIM from bison/scoring.py (a validated Runtime 3.0 scorer).
     Kept byte-faithful so a fidelity harness can diff raven's entries against
     bison's on the same candles; behaviour-preserving quirks carry bison's
     `# v2-quirk` flags. Raven's edge is NOT a new indicator — it is the layer below.

  2. SELF-CALIBRATION — raven reads its OWN realized track record (the caller
     fetches closed trades via discovery_get_trader_history and passes the list
     in) and ratchets two knobs within bounded rails:
        • current_min_score  — RAISE after a cold streak (more selective),
                                LOWER toward the floor after a hot streak.
        • size_scale         — CUT conviction sizing when the record is poor,
                                PRESS it when the record is strong.
     The DSL still owns every exit and the runtime's drawdown_halt is the equity
     backstop; self-calibration only moves ENTRY selectivity + sizing. Bounded,
     monotone-step, and a no-op below `minTrades` so it can never tune on noise.

NOTE: the discovery_get_trader_history payload shape was NOT live-verified when
this was written (auth token was invalid) — `realized_of` tries every realized-PnL
spelling the corpus uses and `track_record` returns n=0 loudly if it parses none,
which holds thresholds. Verify against a real payload before trusting the tuner.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None (distinguishes a real 0.0 from a missing field)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# Ported verbatim from bison/scoring.py.

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


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from bison/scoring.py) ──

def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from bison.
    v2-quirk: STRICT (>) counting, gate `>= total * 0.6`, total = lookback - 1."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


def calc_rsi(closes, period=14):
    """Trailing-window RSI. Verbatim from bison (uses gains[-period:])."""
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ── the thesis (direction waterfall + weighted score), ported from bison ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, funding, sm, inputs):
    """Port of bison build_thesis. Returns a thesis dict (with `score`) or None.
    None ⟺ insufficient history (len(c1h) < 8 or len(c4h) < 4) OR no direction
    resolves. minScore is applied by the CALLER (scan.py) using the ADAPTED floor."""
    min_vol_trend = _f(inputs.get("minVolTrendPct", 10))
    rsi_max_long = _f(inputs.get("rsiMaxLong", 72))
    rsi_min_short = _f(inputs.get("rsiMinShort", 28))

    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None

    price = _close(candles_15m[-1]) if candles_15m else _close(candles_1h[-1])

    trend_4h, trend_strength = trend_structure(candles_4h)
    trend_1h, _ = trend_structure(candles_1h)
    sm_dir, sm_pct = sm if sm else (None, 0)
    mom_1h = price_momentum(candles_1h, 2)

    direction = None
    if trend_4h == "BULLISH":
        direction = "LONG"
    elif trend_4h == "BEARISH":
        direction = "SHORT"
    elif sm_dir and sm_dir != "NEUTRAL":
        direction = sm_dir
    elif mom_1h > 0.5:
        direction = "LONG"
    elif mom_1h < -0.5:
        direction = "SHORT"
    if direction is None:
        return None

    score = 0
    reasons = []

    if trend_4h != "NEUTRAL":
        if (direction == "LONG") == (trend_4h == "BULLISH"):
            score += 3; reasons.append(f"4h trend {trend_4h.lower()}, {trend_strength:.0%} strength")
        else:
            score -= 1; reasons.append(f"4h trend against us ({trend_4h.lower()})")

    if trend_1h != "NEUTRAL":
        if (direction == "LONG") == (trend_1h == "BULLISH"):
            score += 2; reasons.append(f"1h trend confirms ({trend_1h.lower()})")
        else:
            score -= 1; reasons.append(f"1h trend against us ({trend_1h.lower()})")

    if direction == "LONG":
        if mom_1h >= 1.0:
            score += 2; reasons.append(f"strong 1h momentum, {mom_1h:+.2f}%")
        elif mom_1h >= 0.5:
            score += 1; reasons.append(f"1h momentum {mom_1h:+.2f}%")
        elif mom_1h < -0.5:
            score -= 1; reasons.append(f"1h momentum against us, {mom_1h:+.2f}%")
    else:
        if mom_1h <= -1.0:
            score += 2; reasons.append(f"strong 1h momentum, {mom_1h:+.2f}%")
        elif mom_1h <= -0.5:
            score += 1; reasons.append(f"1h momentum {mom_1h:+.2f}%")
        elif mom_1h > 0.5:
            score -= 1; reasons.append(f"1h momentum against us, {mom_1h:+.2f}%")

    if sm_dir == direction and sm_dir:
        score += 2; reasons.append(f"smart money aligned at {_f(sm_pct):.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        score -= 2; reasons.append(f"smart money on the other side ({sm_dir})")

    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2; reasons.append(f"funding favors this side ({funding:+.4f})")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1; reasons.append("funding crowded on this side")

    vol_1h = volume_trend(candles_1h)
    if vol_1h > min_vol_trend:
        score += 1; reasons.append(f"volume rising, {vol_1h:+.0f}%")

    vol_recent = sum(_vol(c) for c in candles_1h[-3:])
    vol_earlier = sum(_vol(c) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1; reasons.append(f"trading activity growing, {oi_proxy:+.0f}%")

    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > rsi_max_long:
        score -= 1; reasons.append(f"RSI overbought at {rsi:.0f}")
    elif direction == "SHORT" and rsi < rsi_min_short:
        score -= 1; reasons.append(f"RSI oversold at {rsi:.0f}")
    elif (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1; reasons.append(f"RSI at {rsi:.0f}, room to run")

    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5 and ((direction == "LONG") == (mom_4h > 0)):
        score += 1; reasons.append(f"4h momentum {mom_4h:+.1f}%")

    return {"coin": coin, "direction": direction, "score": score, "reasons": reasons,
            "price": price, "rsi": round(rsi, 1), "momentum_1h": round(mom_1h, 3)}


def band_for(score, inputs):
    """Conviction band from the score, relative to the CURRENT adaptive floor."""
    apex = _f(inputs.get("apexScore"), 12)
    good = _f(inputs.get("goodScore"), 10)
    if score >= apex:
        return "apex"
    if score >= good:
        return "good"
    return "base"


def sizing_for(band, size_scale, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100]; scaled by the
    self-calibrated `size_scale` and clamped to fleet + venue leverage caps."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 5, "good": 4, "base": 3}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 14, "good": 10, "base": 7}
    cap = int(_f(inputs.get("maxLeverage"), 5))
    lev = int(_f(lev_tiers.get(band), 3))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(lev, cap))
    mgn = _f(mgn_tiers.get(band), 7) * _f(size_scale, 1.0)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 25)))
    return lev, round(mgn, 2)


# ── self-calibration (the centerpiece) — pure, operates on the closed-trade list ──

# realized-PnL spellings observed across the corpus; tried in order. (Shape not
# live-verified — see module docstring.)
_REALIZED_KEYS = ("realizedPnl", "realized_pnl", "realizedProfitAndLoss",
                  "realized_profit_and_loss", "closedPnl", "closed_pnl", "netPnl", "pnl")


def realized_of(pos):
    """Realized PnL of one closed-position record, or None if no spelling matches."""
    if not isinstance(pos, dict):
        return None
    for k in _REALIZED_KEYS:
        if k in pos:
            v = _num(pos[k])
            if v is not None:
                return v
    return None


def track_record(closed, max_trades):
    """Roll up the most-recent `max_trades` closed positions into health stats.
    `closed` is assumed newest-first (discovery_get_trader_history default sort).
    Returns n=0 when nothing parses (caller then HOLDS thresholds — never tunes on noise)."""
    rows = [p for p in (closed or []) if isinstance(p, dict)][: max(1, int(max_trades))]
    pnls = [r for r in (realized_of(p) for p in rows) if r is not None]
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    gross_win = sum(wins)
    gross_loss = -sum(p for p in pnls if p < 0)   # positive magnitude
    # trailing losing streak, from the newest end
    streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
        else:
            break
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "sum_pnl": sum(pnls),
        "avg_pnl": sum(pnls) / n,
        "loss_streak": streak,
    }


def adapt(stats, state, inputs):
    """Ratchet (current_min_score, size_scale) from the track record, bounded.
    Returns (min_score, size_scale, note). A no-op (holds) below `minTrades`."""
    floor = _f(inputs.get("initialMinScore"), 8)
    ceil = _f(inputs.get("maxMinScore"), 12)
    min_n = int(_f(inputs.get("minTrades"), 8))
    hi_wr = _f(inputs.get("hotWinRate"), 0.55)
    lo_wr = _f(inputs.get("coldWinRate"), 0.40)
    hi_pf = _f(inputs.get("hotProfitFactor"), 1.5)
    lo_pf = _f(inputs.get("coldProfitFactor"), 1.0)
    step = _f(inputs.get("scoreStep"), 0.5)
    sstep = _f(inputs.get("sizeStep"), 0.15)
    smin = _f(inputs.get("minSizeScale"), 0.5)
    smax = _f(inputs.get("maxSizeScale"), 1.5)
    cold_streak = int(_f(inputs.get("coldStreak"), 4))

    cur_min = _f((state or {}).get("current_min_score"), floor)
    cur_scale = _f((state or {}).get("size_scale"), 1.0)

    n = int(stats.get("n", 0))
    if n < min_n:
        return (max(floor, min(cur_min, ceil)), max(smin, min(cur_scale, smax)),
                f"hold (only {n}/{min_n} trades — no tune)")

    wr = _f(stats.get("win_rate"))
    pf = _f(stats.get("profit_factor"))
    streak = int(stats.get("loss_streak", 0))

    hot = wr >= hi_wr and pf >= hi_pf
    cold = (wr <= lo_wr) or (pf <= lo_pf) or (streak >= cold_streak)

    if cold and not hot:
        new_min = min(ceil, cur_min + step)
        new_scale = max(smin, cur_scale - sstep)
        note = f"COLD wr={wr:.0%} pf={pf:.2f} streak={streak} → tighten min {cur_min:g}→{new_min:g}, size {cur_scale:.2f}→{new_scale:.2f}"
    elif hot:
        new_min = max(floor, cur_min - step)
        new_scale = min(smax, cur_scale + sstep)
        note = f"HOT wr={wr:.0%} pf={pf:.2f} → loosen min {cur_min:g}→{new_min:g}, size {cur_scale:.2f}→{new_scale:.2f}"
    else:
        # neutral — let size drift back toward 1.0, leave the score floor put
        new_min = cur_min
        new_scale = cur_scale + max(-sstep / 2, min(sstep / 2, 1.0 - cur_scale))
        note = f"neutral wr={wr:.0%} pf={pf:.2f} → hold min {cur_min:g}, size →{new_scale:.2f}"

    return round(max(floor, min(new_min, ceil)), 3), round(max(smin, min(new_scale, smax)), 3), note
