"""ASIA-AI — pure thesis math (no I/O, no MCP, no clock). Shared verbatim by both
instances; direction is passed in (`want` = "UP" for the long book, "DOWN" for the
hedge). Unit-testable on plain candle lists."""


def _closes(candles):
    out = []
    for x in candles:
        if isinstance(x, (list, tuple)) and len(x) >= 5:
            out.append(float(x[4]))                      # [t,o,h,l,c,v] -> close
        elif isinstance(x, dict):
            out.append(float(x.get("close", x.get("c", 0)) or 0))
    return out


def _ret(candles, n):
    c = _closes(candles)
    if len(c) < n + 1 or c[-(n + 1)] == 0:
        return 0.0
    return (c[-1] - c[-(n + 1)]) / c[-(n + 1)]


def _trend(candles, look=6):
    r = _ret(candles, min(look, max(1, len(_closes(candles)) - 1)))
    if r > 0.01:
        return "UP"
    if r < -0.01:
        return "DOWN"
    return "FLAT"


def _rsi(candles, period=14):
    c = _closes(candles)
    if len(c) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-period, 0):
        d = c[i] - c[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def confirm_trend(c4, c1d, want, inputs):
    """Confirm a directional trend (want = UP for long / DOWN for short) on BOTH the
    4h and 1d timeframes, with RSI room so we accumulate rather than chase. Returns a
    thesis dict or None."""
    if len(c4) < 6 or len(c1d) < 6:
        return None
    if _trend(c4) != want or _trend(c1d) != want:
        return None                                       # both timeframes must agree
    rsi = _rsi(c4)
    if want == "UP" and rsi > float(inputs.get("rsiMax", 75)):
        return None                                       # don't chase an overbought long
    if want == "DOWN" and rsi < float(inputs.get("rsiMin", 25)):
        return None                                       # don't chase an oversold short
    strength = abs(_ret(c4, 6))
    room = (rsi < 60) if want == "UP" else (rsi > 40)
    score = 3 + (1 if strength > 0.06 else 0) + (1 if room else 0)
    return {"score": score,
            "direction": "LONG" if want == "UP" else "SHORT",
            "reasons": [f"4h trend {want.lower()} {strength:.0%}",
                        f"daily trend also {want.lower()}", f"RSI at {rsi:.0f}"]}
