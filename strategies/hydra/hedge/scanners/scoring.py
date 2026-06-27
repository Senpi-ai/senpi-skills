"""HYDRA HEDGE — pure cross-asset hedge math (no I/O, no MCP, no clock). Ported VERBATIM
from the v2 producer's hedge path (hydra-producer.py on origin/main, Hydra v2.1):
score_hedge_one, atr_pct, drawdown_pct, vol_parity_margin, and the thesis-stress
multiplier math. scan.py does the reads; this does the numbers. Unit-testable on plain
candle lists.

HEDGE = the CROSS-ASSET cushion. Per blend asset: 4h downtrend is the hard gate; a fast
drawdown over stressLookback >= stressDropPct OR a 1h breakdown ARMS the short;
capitulation-guarded. Vol-parity margin (inverse-ATR, normalized to referenceVolPct,
clamped) sized UP by a thesis-stress multiplier when the thesis coin itself is breaking.
"""


# ── candle accessors (dict OR [t,o,h,l,c,v] list rows) ─────────────────────
def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4] or 0)
    return float(c.get("close", c.get("c", 0)) or 0) if isinstance(c, dict) else 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2] or 0)
    return float(c.get("high", c.get("h", 0)) or 0) if isinstance(c, dict) else 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3] or 0)
    return float(c.get("low", c.get("l", 0)) or 0) if isinstance(c, dict) else 0.0


def trend_structure(candles, lookback=6):
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


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
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


def drawdown_pct(closes, lookback):
    window = closes[-lookback:] if len(closes) >= lookback else closes
    if not window:
        return 0.0
    peak = max(window)
    cur = window[-1]
    return (peak - cur) / peak * 100.0 if peak > 0 else 0.0


def atr_pct(candles, period=14):
    if len(candles) < 3:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, lo, pc = _high(candles[i]), _low(candles[i]), _close(candles[i - 1])
        if h <= 0 or lo <= 0 or pc <= 0:
            continue
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    if not trs:
        return None
    atr = sum(trs[-period:]) / len(trs[-period:])
    price = _close(candles[-1])
    return atr / price * 100.0 if price > 0 else None


def clamp_lev(desired, max_lev):
    return max(1, min(int(desired), int(max_lev)))


# ── thesis-stress multiplier (v2 thesis_stress_mult, candle math — VERBATIM) ──
def thesis_stress_from_candles(c4, config):
    """Size the hedge UP when the THESIS coin itself is breaking down. Returns
    (multiplier in [1.0, stressMultMax], thesis_drawdown_pct). scan.py fetches the
    thesis coin's 4h candles and passes them in."""
    if len(c4) < 3:
        return 1.0, 0.0
    closes4 = [_close(c) for c in c4]
    dd = drawdown_pct(closes4, int(config.get("stressLookback", 6)))
    full = float(config.get("thesisStressFullPct", 10.0))
    mmax = float(config.get("stressMultMax", 2.0))
    frac = min(1.0, dd / full) if full > 0 else 0.0
    return round(1.0 + frac * (mmax - 1.0), 3), round(dd, 2)


# ── per-asset hedge scoring (v2 score_hedge_one — VERBATIM) ────────────────
def score_hedge_one(asset, c1, c4, config):
    """Score ONE hedge-universe asset for a SHORT. 4h downtrend is the hard gate; a fast
    drawdown / 1h breakdown arms it; capitulation-guarded. Returns a thesis dict (with
    vol_pct) or None."""
    if len(c4) < 6:
        return None
    closes4 = [_close(c) for c in c4]
    closes1 = [_close(c) for c in c1] if c1 else closes4
    price = closes4[-1]
    if price <= 0:
        return None
    trend4, s4 = trend_structure(c4)
    if trend4 != "BEARISH":
        return None                                   # only short what's actually breaking down
    trend1, _ = trend_structure(c1) if len(c1) >= 6 else ("NEUTRAL", 0)
    rsi = calc_rsi(closes1)
    dd = drawdown_pct(closes4, int(config.get("stressLookback", 6)))
    stress = float(config.get("stressDropPct", 8.0))
    armed = (dd >= stress) or (trend1 == "BEARISH")
    if not armed:
        return None
    if rsi < float(config.get("rsiOversold", 18)):
        return None                                   # capitulation guard
    vol = atr_pct(c4) or float(config.get("referenceVolPct", 3.0))

    sc, reasons = 3, [f"4h_downtrend_{s4:.0%}"]
    if dd >= stress:
        sc += 2
        reasons.append(f"drawdown_{dd:.1f}%")
    if trend1 == "BEARISH":
        sc += 1
        reasons.append("1h_breaking_down")
    return {"coin": asset, "direction": "SHORT", "score": sc, "reasons": reasons,
            "price": price, "rsi": round(rsi, 1), "trend4h": trend4, "vol_pct": round(vol, 3)}


# ── vol-parity margin (v2 vol_parity_margin — VERBATIM) ────────────────────
def vol_parity_margin(account_value, vol_pct, config, mult=1.0):
    base = float(config.get("hedgeRiskPct", 0.06))
    ref = float(config.get("referenceVolPct", 3.0))
    lo = float(config.get("minMarginPct", 0.02))
    hi = float(config.get("maxMarginPct", 0.12))
    if vol_pct <= 0:
        vol_pct = ref
    pct = base * (ref / vol_pct) * mult
    pct = max(lo, min(hi, pct))
    return round(account_value * pct, 2)
