"""PILOTFISH — pure cohort-accumulation math. No I/O, no MCP, no clock.

Users asked to "catch breakouts early, when smart money is just piling in". Every other
copy strategy we ship reads a STANDING position — who is long what, right now. That is
already priced. PILOTFISH reads the DERIVATIVE: is the proven cohort's conviction in a
name WIDENING since the last look?

Three rules this engine exists to enforce, each one learned from a live failure:

  1. DOMINANT SIDE ONLY. Evaluating long and short legs independently let a 13-vs-29
     minority long fire — and band as apex, so it sized UP on the wrong side. We read
     the NET, so the minority side is unpickable by construction.
  2. FRESHNESS ON THE NET, NOT PER LEG. A cohort standing short for weeks has a FLAT
     short count, so per-leg "is it fresh?" discarded it as stale and traded the noisy
     long leg instead. Measuring the change in the net fixes the asymmetry.
  3. A RATIO IS NOT EVIDENCE WITHOUT A SAMPLE. 4-vs-1 and 400-vs-100 are both "80%
     one-sided"; only one is a fact about the market. Conviction is shrunk toward
     neutral by how few wallets stand behind it.

`bias` here is net/gross NOTIONAL in [-1,+1] — DOLLAR-weighted and signed, the same
definition senpi-smart-money uses. It is NOT a headcount percentage; rendering it as one
("83% of the cohort is short") is a claim about a different quantity.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

SAMPLE_PRIOR_N = 8      # wallets before an observed lean counts at ~half weight
MIN_MEMBERS = 4         # below this the name is not evidence at all


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bare_upper(coin):
    return str(coin).split(":", 1)[-1].upper()


def _positions_of(state):
    for k in ("openPositions", "open_positions", "positions"):
        v = (state or {}).get(k)
        if isinstance(v, list):
            return v
    return []


def _trader_address(t):
    for k in ("address", "traderAddress", "trader_address"):
        v = (t or {}).get(k)
        if v:
            return str(v).lower()
    return ""


def _signed_notional(pos):
    """Signed USD exposure: + long, - short. Tolerant of the shapes the corpus uses."""
    szi = _num(pos.get("szi"))
    if szi is None:
        side = str(pos.get("side", "")).upper()
        sz = _f(pos.get("sz", pos.get("size")), 0.0)
        szi = -sz if side in ("SELL", "SHORT", "S", "A") else sz
    px = _num(pos.get("entryPx", pos.get("entry_price", pos.get("markPx"))))
    ntl = _num(pos.get("positionValue", pos.get("notional")))
    if ntl is not None and ntl > 0:
        return ntl if szi >= 0 else -ntl
    if px is None:
        return szi                                   # size-only fallback; still directional
    return szi * px


def cohort_bias(trader_states):
    """{ASSET: {net, gross, bias, n_long, n_short, members, name}} — one entry per wallet.

    bias = net/gross NOTIONAL in [-1,+1]: +1 all long, -1 all short. Dollar-weighted.
    """
    per, seen = {}, set()
    for st in trader_states or []:
        if not isinstance(st, dict):
            continue
        wallet = _trader_address(st)
        for pos in _positions_of(st):
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            if not coin:
                continue
            sn = _signed_notional(pos)
            if not sn:
                continue
            asset = _bare_upper(coin)
            key = (wallet or f"_anon_{id(pos)}", asset)
            if key in seen:
                continue
            seen.add(key)
            d = per.setdefault(asset, {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0,
                                       "name": str(coin)})
            d["net"] += sn
            d["gross"] += abs(sn)
            d["n_long" if sn > 0 else "n_short"] += 1
    for d in per.values():
        d["bias"] = round(d["net"] / d["gross"], 4) if d["gross"] > 0 else 0.0
        d["members"] = d["n_long"] + d["n_short"]
        d["net"] = round(d["net"], 2)
        d["gross"] = round(d["gross"], 2)
    return per


def sample_shrink(n):
    """0..1 — how much of an observed lean survives the sample behind it."""
    if not n or n <= 0:
        return 0.0
    return round(n / (n + SAMPLE_PRIOR_N), 4)


def conviction(entry):
    """|bias| shrunk by sample size. This is what may drive a score; the RAW bias is what
    gets reported, so shrinkage governs the decision, never the stated facts."""
    if not entry or entry.get("members", 0) < MIN_MEMBERS:
        return 0.0
    return round(abs(_f(entry.get("bias"))) * sample_shrink(entry.get("members")), 4)


def accumulating(cur, prev, inputs):
    """Names where the cohort's DOMINANT side is actively WIDENING since the last look.

    Returns [{asset, name, direction, bias, prev_bias, delta, members, conviction, score}]
    sorted by score desc. Pure.
    """
    min_delta = _f(inputs.get("minBiasDelta"), 0.10)
    min_bias = _f(inputs.get("minBias"), 0.25)
    min_members = int(_f(inputs.get("minMembers"), MIN_MEMBERS))
    out = []
    for asset, d in (cur or {}).items():
        members = d.get("members", 0)
        if members < min_members:
            continue                                  # not evidence — too few wallets
        bias = _f(d.get("bias"))
        if abs(bias) < min_bias:
            continue                                  # cohort is split; no dominant side
        p = (prev or {}).get(asset) or {}
        prev_bias = _f(p.get("bias"), 0.0)
        # THE DERIVATIVE, on the NET — not on either leg independently
        delta = abs(bias) - abs(prev_bias)
        if bias * prev_bias < 0:
            delta = abs(bias)                         # just flipped side: the whole lean is new
        if delta < min_delta:
            continue                                  # standing conviction, already priced
        conv = conviction(d)
        score = round(conv * 10.0 + min(delta, 1.0) * 8.0, 3)
        out.append({"asset": asset, "name": d.get("name", asset),
                    "direction": "LONG" if bias > 0 else "SHORT",
                    "bias": bias, "prev_bias": round(prev_bias, 4), "delta": round(delta, 4),
                    "members": members, "n_long": d.get("n_long", 0), "n_short": d.get("n_short", 0),
                    "conviction": conv, "score": score})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 9):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 6):
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100] — never a fraction."""
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 3)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 8)
    lev = min(lev, _f(inputs.get("maxLeverage"), 4))
    if venue_max:
        lev = min(lev, _f(venue_max, lev))
    mgn = min(mgn, _f(inputs.get("maxMarginPct"), 20))
    return int(max(1, lev)), round(max(0.1, mgn), 4)
