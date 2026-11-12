"""DUGONG — pure scoring, manatee's apart from this docstring and the fee ceiling (no builder rate assumed).
No I/O, no MCP. Unit-test this file.

The thesis: a large cap stretched away from its one-week mean tends to come back. Below the mean
the signal is LONG, above it SHORT; the direction comes from the signal, never from a bias. The
engine works on CLOSED daily bars (the scanner drops the in-progress bar before calling it), so a
name qualifies once per day, at the close that confirmed the stretch. Everything here is arithmetic
on candles and the recipe's inputs.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    return _f(c.get("c") if isinstance(c, dict) else None)


def roundtrip_fee_pct(inputs, maker_entry=True):
    """Fees as a PERCENT of notional for one round trip (entry + exit).

    A CEILING, not a quote. The venue's BASE rates: the account's HL volume tier only lowers them.
    Senpi adds no fee on a paid plan, so none is assumed — `builderFeeBps` counts another fee on
    BOTH legs if an account sets it. It exists for one job — refusing a harvest target too small to
    clear a round trip. In dugong both sides of the gate are
    in PRICE percent: the 1.4% harvest against a 0.18% hurdle (a 0.06% venue round trip, x3). It is
    inert at the shipped harvest and only bites if someone tunes the harvest down.
    """
    taker = _f(inputs.get("takerFeeBps"), 4.5)
    maker = _f(inputs.get("makerFeeBps"), 1.5)
    builder = _f(inputs.get("builderFeeBps"), 0.0)   # per account: never assumed — see runtime.yaml
    entry = maker if maker_entry else taker
    return (entry + builder + taker + builder) / 100.0


def deviation_from_mean(closes, bars):
    """Percent distance of the last close from the simple mean of the previous `bars` closes."""
    if len(closes) < bars + 1:
        return None
    window = closes[-(bars + 1):-1]
    mean = sum(window) / len(window)
    if mean <= 0:
        return None
    return (closes[-1] / mean - 1.0) * 100.0


def build_thesis(asset, candles, inputs):
    """Return a thesis dict (LONG below the mean, SHORT above it), or None when the name does not
    qualify. `candles` are CLOSED bars, oldest first; the last one is the bar that just closed."""
    closes = [_close(c) for c in (candles or []) if _close(c) > 0]
    bars = int(_f(inputs.get("maBars"), 7))
    ret_bars = int(_f(inputs.get("retBars"), 1))
    if len(closes) < bars + max(ret_bars, 1) + 1:
        return None
    dev = deviation_from_mean(closes, bars)
    if dev is None:
        return None
    dev_req = _f(inputs.get("devPct"), 5.0)
    if abs(dev) < dev_req:
        return None
    direction = "LONG" if dev < 0 else "SHORT"
    ret = (closes[-1] / closes[-(ret_bars + 1)] - 1.0) * 100.0
    capture = _f(inputs.get("expectedCapturePct"), 3.0)
    fees = roundtrip_fee_pct(inputs, maker_entry=True)
    if capture < fees * _f(inputs.get("feeCoverMultiple"), 3.0):
        return None
    depth = abs(dev)
    band = "extreme" if depth >= _f(inputs.get("extremeDevPct"), 10.0) else \
           "deep" if depth >= _f(inputs.get("deepDevPct"), 7.0) else "base"
    return {
        "asset": asset, "direction": direction, "score": round(min(10.0, depth), 2), "band": band,
        "dev_pct": round(dev, 3), "ret_pct": round(ret, 3),
        "reasons": [f"{dev:+.2f}% vs {bars}d mean at the close", f"{ret_bars}d {ret:+.2f}%", f"band {band}", direction.lower()],
    }


def sizing_for(inputs, add=False):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100] — never a fraction. Every clip is the
    same size, entry or add: the ladder is the sizing."""
    key = "addMarginPct" if add else "marginPct"
    mgn = _f(inputs.get(key), _f(inputs.get("marginPct"), 2.0))
    lev = max(1, int(_f(inputs.get("leverage"), 1)))
    return lev, mgn


def signal_data(th, lev, kind, add_index):
    """The `data{}` map for a signal: only keys with a value, so an optional key is absent, never null."""
    data = {"score": th["score"], "direction": th["direction"], "leverage": lev, "band": th["band"],
            "kind": kind, "addIndex": add_index, "devPct": th["dev_pct"], "retPct": th["ret_pct"],
            "reasons": th["reasons"]}
    return {k: v for k, v in data.items() if v is not None}
