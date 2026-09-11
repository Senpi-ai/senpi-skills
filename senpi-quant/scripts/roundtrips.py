#!/usr/bin/env python3
"""Round trips from Hyperliquid fills (`userFillsByTime` + `userTwapSliceFillsByTime`, merged).

A fill carries `startPosition` — the signed position BEFORE the fill — so the position after it is
startPosition ± sz (side B buys, A sells). An episode opens when the position leaves 0 and closes when it
returns to 0; a flip (`Long > Short`) closes one episode at that fill and opens the next.

Two kinds of incompleteness are handled explicitly, never silently:
* an episode whose first observed fill starts from a non-zero position began before the fetched window
  (`truncated`);
* a fill whose `startPosition` disagrees with the tracked position means fills between were not returned
  by the API (the public endpoints keep only the most recent fills). The tracker RESYNCS to the fill's own
  `startPosition`, records the unobserved size on the episode (`unobserved_qty`), and, when the resync
  crosses zero, closes the episode at the last observed fill with `close_observed=False`.
Only `complete` episodes (not truncated, close observed, no unobserved size) feed entry- and hold-based
statistics; P&L, fee and volume totals sum every observed fill."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
EPS = 1e-9


def is_perp(coin):
    """Spot fills name the pair (`@107`, `PURR/USDC`); perps are bare names or `xyz:`-prefixed."""
    return not (coin.startswith("@") or "/" in coin)


def _new(coin, f, signed, truncated=False):
    return dict(coin=coin, signed=signed, open_time=f["time"], close_time=None, last_time=f["time"], realized=0.0, fees=0.0,
                volume=0.0, taker_volume=0.0, twap_volume=0.0, adds=0, partial_closes=0, entry_qty=0.0, entry_val=0.0, exit_qty=0.0,
                exit_val=0.0, peak_size=0.0, peak_notional=0.0, liquidated=False, truncated=truncated, n_fills=0, unobserved_qty=0.0, unobserved_notional=0.0,
                close_observed=True)


def _sign(x):
    return 1 if x > EPS else (-1 if x < -EPS else 0)


def episodes_from_fills(fills):
    fills = sorted((f for f in fills if is_perp(f["coin"])), key=lambda f: (f["time"], f["tid"]))
    open_ep, done, tracked = {}, [], {}
    for f in fills:
        coin, sz, px = f["coin"], float(f["sz"]), float(f["px"])
        before = float(f["startPosition"])
        after = before + (sz if f["side"] == "B" else -sz)
        if abs(after) < EPS:
            after = 0.0
        ep = open_ep.get(coin)
        exp = tracked.get(coin)
        if ep is not None and exp is not None and abs(exp - before) > 1e-6:
            # fills between the last observed one and this one were not returned: resync
            if _sign(exp) != _sign(before) or before == 0.0:
                ep["close_time"] = ep["last_time"]; ep["close_observed"] = False
                ep["unobserved_qty"] += abs(exp) + abs(before); ep["unobserved_notional"] += (abs(exp) + abs(before)) * px
                done.append(ep); ep = None; del open_ep[coin]
            else:
                ep["unobserved_qty"] += abs(before - exp); ep["unobserved_notional"] += abs(before - exp) * px
                ep["peak_size"] = max(ep["peak_size"], abs(before)); ep["peak_notional"] = max(ep["peak_notional"], abs(before) * px)
        if ep is None:
            ep = open_ep[coin] = _new(coin, f, before if before else after, truncated=(before != 0.0))
        elif before == 0.0:
            ep = open_ep[coin] = _new(coin, f, after)
        closing = (before > 0 and f["side"] == "A") or (before < 0 and f["side"] == "B")
        ep["n_fills"] += 1; ep["realized"] += float(f["closedPnl"]); ep["fees"] += float(f["fee"])
        ep["volume"] += sz * px
        if f.get("crossed"):
            ep["taker_volume"] += sz * px
        if f.get("twapId") is not None:
            ep["twap_volume"] += sz * px
        if "Liquidated" in f.get("dir", ""):
            ep["liquidated"] = True
        flipped = (before > 0 > after) or (before < 0 < after)
        if closing:
            q = min(sz, abs(before)); ep["exit_qty"] += q; ep["exit_val"] += q * px
            if after != 0.0 and not flipped:
                ep["partial_closes"] += 1
        else:
            if ep["n_fills"] > 1:
                ep["adds"] += 1
            ep["entry_qty"] += sz; ep["entry_val"] += sz * px
        ep["peak_size"] = max(ep["peak_size"], abs(after), abs(before))
        ep["peak_notional"] = max(ep["peak_notional"], max(abs(after), abs(before)) * px)
        ep["last_time"] = f["time"]
        tracked[coin] = after
        if after == 0.0 or flipped:
            ep["close_time"] = f["time"]; done.append(ep); del open_ep[coin]
            if flipped:
                ne = _new(coin, f, after); ne["entry_qty"], ne["entry_val"] = abs(after), abs(after) * px
                ne["peak_size"], ne["peak_notional"] = abs(after), abs(after) * px; ne["n_fills"] = 1
                open_ep[coin] = ne
    for ep in done + list(open_ep.values()):
        _finish(ep)
    return done, list(open_ep.values())


def _finish(ep):
    ep["direction"] = "LONG" if ep["signed"] > 0 else "SHORT"
    ep["entry_vwap"] = ep["entry_val"] / ep["entry_qty"] if ep["entry_qty"] else None
    ep["exit_vwap"] = ep["exit_val"] / ep["exit_qty"] if ep["exit_qty"] else None
    ep["net"] = ep["realized"] - ep["fees"]
    end = ep["close_time"] or ep["last_time"]
    ep["hold_h"] = (end - ep["open_time"]) / 3.6e6
    ep["taker_share"] = ep["taker_volume"] / ep["volume"] if ep["volume"] else 0.0
    ep["complete"] = (not ep["truncated"]) and ep["close_time"] is not None and ep["close_observed"] and ep["unobserved_qty"] == 0.0
    ep["win"] = ep["realized"] > 0


def coverage(episodes):
    """Observed fill volume ÷ (observed + the volume implied by position jumps) — 1.0 when the API returned
    every execution. Self-contained: needs nothing but the fills themselves."""
    seen = sum(e["volume"] for e in episodes)
    missed = sum(e["unobserved_notional"] for e in episodes)
    return (seen / (seen + missed)) if (seen + missed) else None
