"""SHADOW — pure fresh-entry mirror math (no I/O, no MCP, no clock).

Given trader-state dicts already fetched by scan.py, decide which positions are FRESHLY
opened (never seen before for that trader), aggregate multi-trader confirmation per
(coin, side), gate on entry slippage vs the trader's fill, and size budget-relative with
a min-notional floor. scan.py owns the MCP reads + state; this module is the numbers,
unit-testable in isolation."""


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


def _side(szi):
    return "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)


def pos_key(coin, side):
    return f"{coin}|{side}"


def extract_positions(state):
    """Flatten one discovery_get_trader_state dict -> [{coin, side, szi, entry, mark,
    notional, leverage}]. Reads the openPositions list (coin keeps its dex prefix; szi
    sign = side; entryPx = the trader's fill; markPx = current mark for slippage)."""
    if not isinstance(state, dict):
        return []
    out = []
    for p in (state.get("openPositions") or state.get("open_positions") or []):
        if not isinstance(p, dict):
            continue
        coin = p.get("coin") or p.get("asset")
        szi = _f(p, "szi", "size")
        side = _side(szi)
        if not coin or side is None:
            continue
        entry = _f(p, "entryPx", "entry_price", "entryPrice")
        mark = _f(p, "markPx", "mark_price", "oraclePx")
        notional = _f(p, "positionValue", "notional", "position_value")
        if notional <= 0:
            notional = abs(szi) * (mark or entry)
        lev = _f(p.get("leverage"), "value") or _f(p, "leverage", default=0.0)
        out.append({"coin": coin, "side": side, "szi": szi, "entry": entry,
                    "mark": mark, "notional": notional, "leverage": lev})
    return out


def diff_fresh(addr, positions, seen_map):
    """FRESH = a (coin, side) this trader holds now but that was NOT in their prior
    seen-set. First sight of a trader (addr absent from seen_map) SEEDS their whole book
    as already-seen and returns NO fresh entries — Shadow never inherits an existing book.
    Returns (fresh_positions, sorted_current_keys)."""
    now_keys = {pos_key(p["coin"], p["side"]) for p in positions}
    if addr not in seen_map:                       # first sight -> seed, emit nothing
        return [], sorted(now_keys)
    prior = set(seen_map.get(addr) or [])
    fresh = [p for p in positions if pos_key(p["coin"], p["side"]) not in prior]
    return fresh, sorted(now_keys)


def chase_pct(entry, mark, side):
    """How far price has ALREADY moved in the trade's direction since the trader's entry
    (adverse for a late mirror). LONG: (mark-entry)/entry; SHORT: (entry-mark)/entry.
    Positive => we'd chase a worse fill than the trader got; negative => we'd get in
    better. Guarded against a zero/absent entry or mark."""
    if entry <= 0 or mark <= 0:
        return 0.0
    raw = (mark - entry) / entry if side == "LONG" else (entry - mark) / entry
    return round(raw * 100.0, 4)


def aggregate_fresh(fresh_by_trader):
    """Collapse fresh entries across traders into per-(coin,side) candidates with a
    confirmation count. fresh_by_trader: {addr: [pos, ...]}. entry_avg = mean of confirming
    traders' entries (the reference fill to slippage-check); mark is the shared coin mark."""
    agg = {}
    for addr, positions in fresh_by_trader.items():
        for p in positions:
            k = pos_key(p["coin"], p["side"])
            a = agg.setdefault(k, {"coin": p["coin"], "side": p["side"], "confirm": 0,
                                   "traders": [], "entry_sum": 0.0, "lev_sum": 0.0, "mark": 0.0})
            a["confirm"] += 1
            a["traders"].append(addr[:10])
            a["entry_sum"] += p["entry"]
            a["lev_sum"] += p["leverage"]
            if p["mark"] > 0:
                a["mark"] = p["mark"]              # same coin across confirmers -> same mark
    for a in agg.values():
        n = max(1, a["confirm"])
        a["entry_avg"] = a["entry_sum"] / n
        a["lev_avg"] = a["lev_sum"] / n
    return agg


def margin_pct_for(confirm, inputs):
    """Budget-relative sizing as a PERCENT of withdrawable in (0,100]. Base marginPct,
    scaled per extra confirming trader, capped, and FLOORED at minMarginPct so a mirror is
    never 'too small to enter'."""
    base = float(inputs.get("marginPct", 15))
    floor = float(inputs.get("minMarginPct", 8))
    cap = float(inputs.get("maxMarginPct", 30))
    step = float(inputs.get("convictionStepPct", 0.5))    # +50% of base per extra confirmer
    scaled = base * (1.0 + step * max(0, confirm - 1))
    return round(min(max(scaled, floor), cap), 4)


def leverage_for(lev_avg, inputs):
    """Follow the traders' leaning leverage, clamped to [1, maxLeverage]; fall back to
    stdLeverage when unknown. Shadow never blindly mirrors a 40x book — the cap is the
    guardrail."""
    std = float(inputs.get("stdLeverage", 5))
    mx = float(inputs.get("maxLeverage", 10))
    lev = lev_avg if lev_avg and lev_avg > 0 else std
    return int(round(min(max(lev, 1.0), mx)))


def score_candidate(cand, slip):
    """Conviction score for observability: base 3, +1 per extra confirmer (capped 2),
    +1 for a favorable (negative) chase. Not a gate — the gates are confirm count and
    slippage in scan.py."""
    return int(3 + min(2, max(0, cand["confirm"] - 1)) + (1 if slip < 0 else 0))
