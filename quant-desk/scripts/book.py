#!/usr/bin/env python3
"""One desk over SEVERAL wallets — a senpi user's whole book, not one subwallet of it.

A senpi user does not have "a wallet". They have a funding wallet that never trades and N strategy
subwallets that do, opened and closed as strategies come and go. Running the desk on one of them
scores one strategy; the reader asked about their trading. `--book 0xA 0xB 0xC` reads each wallet,
then UNIONS them into a single `tr_raw` the rest of the desk cannot tell from one account's.

Union, not concatenation — four of the keys need real arithmetic:

* **episodes** are built PER WALLET and then unioned. `episodes_from_fills` tracks position per coin
  through `startPosition`; concatenating two wallets that both trade BTC interleaves two position
  tracks into one and the reconstruction is garbage. This is the reason this module exists.
* **portfolio** series are SUMMED on a union timestamp grid, step-interpolated. Before a wallet's
  first sample it contributes 0 (it did not exist); after its last it carries its final value (a
  closed strategy keeps whatever P&L it ended with). Equity and cumulative P&L are both correct
  under that rule, and both are denominators — drawdown, return-on-equity, beta.
* **ledger** transfers BETWEEN wallets in the set are dropped. Moving $10k from one of your
  strategies to another is not a deposit and not a withdrawal; left in, it lands in `flows()` twice
  with opposite signs on the same book and makes the equity curve step for no reason.
* **dailyUserVlm** is summed by date, because the coverage diagnostic divides observed volume by it.
  One wallet's denominator against three wallets' numerator reads as 300% coverage.

Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
"""
import collections

from roundtrips import episodes_from_fills


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _sum_series(per_wallet):
    """Sum N [(t, value)] series onto the union of their timestamps.

    Step-interpolated: each wallet holds its last value between its own samples. Outside its range
    it contributes 0 BEFORE it opened and its final value AFTER it closed — which is what a wallet
    that did not exist yet, and one that has been emptied, actually contributed to the book."""
    grid = sorted({t for s in per_wallet for t, _ in s})
    if not grid:
        return []
    cursors = [0] * len(per_wallet)
    held = [0.0] * len(per_wallet)
    out = []
    for t in grid:
        for i, s in enumerate(per_wallet):
            while cursors[i] < len(s) and s[cursors[i]][0] <= t:
                held[i] = s[cursors[i]][1]; cursors[i] += 1
        out.append([t, sum(held)])
    return out


def _merge_portfolio(portfolios):
    """Sum every window of HL's `portfolio` response across wallets, series by series."""
    names, by_name = [], collections.defaultdict(list)
    for p in portfolios:
        for name, win in (p or []):
            if name not in by_name:
                names.append(name)
            by_name[name].append(win or {})
    out = []
    for name in names:
        wins = by_name[name]
        win = {}
        for key in ("accountValueHistory", "pnlHistory"):
            series = [[(t, _f(v)) for t, v in (w.get(key) or [])] for w in wins]
            if any(series):
                # back to HL's own shape: [ms, "string"] — every reader of this goes through _f()
                win[key] = [[t, str(v)] for t, v in _sum_series(series)]
        vlm = sum(_f(w.get("vlm")) for w in wins)
        if vlm:
            win["vlm"] = str(vlm)
        out.append([name, win])
    return out


def _merge_fees(scheds):
    """The fee SCHEDULE of the wallet that traded most, with `dailyUserVlm` summed across all of them.

    Rates are a tier, not a quantity: they do not add. Every dollar figure on the desk is anchored on
    fees actually paid (summed from fills) and the schedule is only used to APPORTION those dollars
    between taker and maker, so the busiest wallet's tier is the right representative. The daily
    volume table is a quantity and does add — it is the denominator of the coverage diagnostic."""
    live = [s for s in scheds if s]
    if not live:
        return None
    def traded(s):
        return sum(_f(d.get("userCross")) + _f(d.get("userAdd")) for d in (s.get("dailyUserVlm") or []))
    out = dict(max(live, key=traded))
    by_date = collections.defaultdict(lambda: [0.0, 0.0])
    for s in live:
        for d in (s.get("dailyUserVlm") or []):
            row = by_date[d.get("date")]
            row[0] += _f(d.get("userCross")); row[1] += _f(d.get("userAdd"))
    if by_date:
        out["dailyUserVlm"] = [{"date": d, "userCross": str(c), "userAdd": str(m)}
                               for d, (c, m) in sorted(by_date.items()) if d]
    return out


def _merge_clearinghouse(states, addrs=None):
    """Positions concatenated, margin summed. A merged book's margin is the sum of its wallets'.

    Each position is stamped with the wallet it came from. Two strategies can hold the same coin,
    and the protection audit matches resting stops to positions — by coin alone it would credit one
    wallet's stop to the other's bare position and call a naked position protected."""
    live = [(a, s) for a, s in zip(addrs or [None] * len(states), states) if s]
    if not live:
        return None
    pos = []
    for a, s in live:
        for ap in (s.get("assetPositions") or []):
            ap = dict(ap); ap["position"] = dict(ap["position"], wallet=a)
            pos.append(ap)
    live = [s for _a, s in live]
    out = {"assetPositions": pos, "time": max((s.get("time") or 0) for s in live)}
    for key in ("marginSummary", "crossMarginSummary"):
        parts = [s.get(key) or {} for s in live]
        fields = {k for p in parts for k in p}
        if fields:
            out[key] = {k: str(sum(_f(p.get(k)) for p in parts)) for k in fields}
    for key in ("withdrawable", "crossMaintenanceMarginUsed"):
        out[key] = str(sum(_f(s.get(key)) for s in live))
    return out


def _merge_spot(states):
    live = [s for s in states if s]
    if not live:
        return None
    bal = collections.defaultdict(lambda: [0.0, 0.0])
    for s in live:
        for b in (s.get("balances") or []):
            row = bal[b.get("coin")]
            row[0] += _f(b.get("total")); row[1] += _f(b.get("hold"))
    return {"balances": [{"coin": c, "total": str(t), "hold": str(h)} for c, (t, h) in sorted(bal.items())]}


def _internal(ledger, wallets):
    """Drop sends whose BOTH ends are wallets of this book — an internal move, not a flow."""
    out = []
    for x in ledger or []:
        d = x.get("delta") or {}
        if d.get("type") == "send":
            if str(d.get("destination") or "").lower() in wallets and str(d.get("user") or "").lower() in wallets:
                continue
        out.append(x)
    return out


def read(hl, addrs, days=90, progress=None):
    """Read every wallet and return `(tr_raw, closed, opened, per_wallet)` for the union.

    Sequential by design: Hyperliquid's Info API 429s on parallel reads from one client, and a
    rate-limited wallet would silently drop out of somebody's book total."""
    addrs = [a.strip().lower() for a in addrs]
    seen, uniq = set(), []
    for a in addrs:
        if a not in seen:
            seen.add(a); uniq.append(a)
    raws, closed, opened, per_wallet = [], [], [], []
    for i, a in enumerate(uniq, 1):
        if progress:
            progress(f"[quant-desk]   · wallet {i} of {len(uniq)}: {a[:6]}…{a[-4:]} …")
        tr = hl.trader(a, days=days)
        raws.append(tr)
        c, o = episodes_from_fills(tr["fills"])
        for e in c + o:
            e["wallet"] = a
        closed.extend(c); opened.extend(o)
        per_wallet.append(dict(address=a, fills=len(tr["fills"]), closed=len(c), open=len(o)))
    first = raws[0]
    wallets = set(uniq)
    merged = {
        "address": uniq[0], "wallets": uniq,
        "now_ms": first["now_ms"], "window_start_ms": first["window_start_ms"],
        "fetch_start_ms": first["fetch_start_ms"], "days": days,
        "clearinghouseState": _merge_clearinghouse([r["clearinghouseState"] for r in raws], uniq),
        "clearinghouseState_xyz": _merge_clearinghouse([r.get("clearinghouseState_xyz") for r in raws], uniq),
        "frontendOpenOrders": [dict(o, wallet=a) for a, r in zip(uniq, raws) for o in (r["frontendOpenOrders"] or [])],
        "frontendOpenOrders_xyz": [dict(o, wallet=a) for a, r in zip(uniq, raws) for o in (r.get("frontendOpenOrders_xyz") or [])],
        "spotClearinghouseState": _merge_spot([r.get("spotClearinghouseState") for r in raws]),
        "fills": sorted((f for r in raws for f in r["fills"]), key=lambda f: f["time"]),
        "userFunding": sorted((dict(x, wallet=a) for a, r in zip(uniq, raws) for x in (r["userFunding"] or [])),
                              key=lambda x: x["time"]),
        "userFees": _merge_fees([r["userFees"] for r in raws]),
        "userFees_xyz": _merge_fees([r.get("userFees_xyz") for r in raws]),
        "portfolio": _merge_portfolio([r["portfolio"] for r in raws]),
        "ledger": sorted(_internal([x for r in raws for x in (r["ledger"] or [])], wallets), key=lambda x: x["time"]),
    }
    closed.sort(key=lambda e: e.get("close_time") or e.get("open_time") or 0)
    return merged, closed, opened, per_wallet
