"""WHALEHUNTER — pure cohort-divergence math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 WhaleHunter v2.0 cohort engine
(whalehunter-producer.py). Given trader-state dicts already fetched by scan.py,
this bucketing/scoring is reproduced VERBATIM so a fidelity harness can diff it
against the v2 producer on the same snapshot. Shared verbatim by both sleeves;
the sleeve's direction is passed in (LONG for the long wallet, SHORT for the short).

The thesis: segment Hyperliquid traders into a SMART cohort (high lifetime realized
PnL) and a CROWD cohort, aggregate each cohort's NET positioning per coin, and fire
when the smart cohort is net-directional past a threshold AND adding to it daily,
with the crowd ideally on the other side. Position WITH the smart money, not the crowd."""


def _f(x, *keys, default=0.0):
    """Float from x. If keys given, x is a dict and we try each key in order."""
    if keys:
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def realized(t):
    # LIFETIME realized PnL only — never fall back to total PnL (realized+unrealized),
    # which is not monotonic with the realized-PnL sort and mis-buckets the cohorts.
    return _f(t, "realizedProfitAndLoss", "realized_profit_and_loss",
              "profit_and_loss_realized", "realizedPnl", "realized_pnl", default=0.0)


def _signed_notional(p):
    szi = _f(p, "szi", "size")
    val = _f(p, "positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * _f(p, "entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def aggregate_bias(traders):
    """Aggregate a cohort's NET positioning per coin from a list of trader-state dicts.
    bias = net/gross in [-1, +1] (+1 all long, -1 all short), plus member counts."""
    per = {}
    for t in traders:
        if not isinstance(t, dict):
            continue
        for p in (t.get("openPositions") or t.get("open_positions") or []):
            if not isinstance(p, dict):
                continue
            coin = p.get("coin") or p.get("asset")
            if not coin:
                continue
            sn = _signed_notional(p)
            if sn == 0:
                continue
            d = per.setdefault(coin, {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0})
            d["net"] += sn
            d["gross"] += abs(sn)
            d["n_long" if sn > 0 else "n_short"] += 1
    for d in per.values():
        d["bias"] = round(d["net"] / d["gross"], 3) if d["gross"] > 0 else 0.0
    return per


def update_ledger(days, smart_per, today, keep=10):
    """Append today's smart-cohort net-per-coin to the daily ledger; return
    (new_days, growth) where growth[coin] = today's net − the earliest snapshot's net
    (the 'adding daily' signal). growth is {} on day 1 — `requireGrowing` then blocks
    until ~day 2. `today` is passed in so this stays clock-free."""
    days = dict(days)
    days[today] = {c: round(d["net"], 2) for c, d in smart_per.items()}
    for stale in sorted(days)[:-keep]:           # keep ~`keep` days
        days.pop(stale, None)
    prior = [d for d in sorted(days) if d < today]
    growth = {}
    if prior:
        base = days[prior[0]]                     # earliest snapshot we still hold
        for coin, net in days[today].items():
            growth[coin] = round(net - float(base.get(coin, 0.0)), 2)
    return days, growth


def cohort_signals(smart_per, crowd_per, growth, direction, config):
    """For THIS sleeve's direction: smart cohort net-directional past biasThreshold,
    growing (adding), crowd ideally diverging. Returns scored strikes, best first."""
    bt = float(config.get("biasThreshold", 0.50))
    cdiv = float(config.get("crowdDivergenceMin", 0.2))
    req_grow = bool(config.get("requireGrowing", True))
    min_members = int(config.get("cohortMinMembers", 5))
    floor = int(config.get("cohortMinScore", 4))
    want_long = (direction == "LONG")
    out = []
    for coin, sd in smart_per.items():
        if sd["n_long"] + sd["n_short"] < min_members:
            continue
        bias = sd["bias"]
        if (want_long and bias < bt) or (not want_long and bias > -bt):
            continue                              # smart cohort not net in our direction
        g = growth.get(coin)
        growing = (g is not None) and ((g > 0) if want_long else (g < 0))
        if req_grow and not growing:
            continue                              # not "adding" — skip
        crowd_bias = crowd_per.get(coin, {}).get("bias", 0.0)
        crowd_div = (crowd_bias <= -cdiv) if want_long else (crowd_bias >= cdiv)
        score = 3 + (1 if abs(bias) >= 0.7 else 0) + (1 if growing else 0) + (1 if crowd_div else 0)
        if score < floor:
            continue
        n_confirm = sd["n_long"] if want_long else sd["n_short"]
        out.append({
            "coin": coin, "direction": direction, "score": score,
            "smart_bias": bias, "crowd_bias": round(crowd_bias, 3),
            "growth": g, "n_confirm": n_confirm,
            "reasons": [f"smart money tilt {bias:+.2f} on {coin}",
                        f"{'still adding daily' if growing else 'holding flat'} (net change {g})",
                        f"crowd tilt {crowd_bias:+.2f}{', on the other side' if crowd_div else ''}",
                        f"{n_confirm} smart money whales positioned {direction.lower()}"],
        })
    out.sort(key=lambda s: (s["score"], abs(s["smart_bias"])), reverse=True)
    return out


def margin_pct_for(score, config):
    """Conviction-scaled marginPct INTENT as a PERCENT of withdrawable in (0,100]
    (the runtime sizes (marginPct/100)*withdrawable). Scales +25% per point above
    the floor, capped at maxMarginPct."""
    base = float(config.get("marginPct", 12))
    cap = float(config.get("maxMarginPct", 25))
    smax = float(config.get("maxConvictionScale", 2.0))
    floor = int(config.get("cohortMinScore", 4))
    scale = min(smax, 1.0 + 0.25 * max(0, score - floor))
    return round(min(base * scale, cap), 4)
