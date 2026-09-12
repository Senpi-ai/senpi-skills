#!/usr/bin/env python3
"""The desk's core numbers from public Hyperliquid data: track record, costs, holds, sizing, per-coin
table, the open book with its protection audit, transfer-adjusted equity and drawdown, activity."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import collections
import statistics

from hl_api import is_perp


MIN_HOLD_N = 5      # complete winners AND losers needed before a hold-time claim is made


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def in_window(ep, start_ms):
    return (ep["close_time"] or ep["last_time"]) >= start_ms


def track_record(closed, opened, funding_rows, fee_sched, window_start):
    closed = [e for e in closed if in_window(e, window_start)]
    complete = [e for e in closed if e["complete"]]
    wins = [e for e in closed if e["win"]]
    losses = [e for e in closed if not e["win"]]
    cw = [e for e in complete if e["win"]]
    cl = [e for e in complete if not e["win"]]
    gross = sum(e["realized"] for e in closed)
    fees = sum(e["fees"] for e in closed) + sum(e["fees"] for e in opened)
    fund_rows = [x for x in funding_rows if x["time"] >= window_start]
    funding = sum(_f(x["delta"]["usdc"]) for x in fund_rows)
    fund_by_coin = collections.defaultdict(float)
    for x in fund_rows:
        fund_by_coin[x["delta"]["coin"]] += _f(x["delta"]["usdc"])
    win_sum = sum(e["realized"] for e in wins)
    loss_sum = -sum(e["realized"] for e in losses)
    vol = sum(e["volume"] for e in closed + opened)
    taker_vol = sum(e["taker_volume"] for e in closed + opened)
    cross, add = _f(fee_sched.get("userCrossRate")), _f(fee_sched.get("userAddRate"))
    sizes = [e["peak_notional"] for e in closed if not e["truncated"] and e["peak_notional"] > 0]
    liq = [e for e in closed if e["liquidated"]]
    by_coin = collections.defaultdict(lambda: dict(trades=0, wins=0, realized=0.0, fees=0.0, volume=0.0, long=0, short=0, hold_h=[], sizes=[]))
    for e in closed:
        c = by_coin[e["coin"]]
        c["trades"] += 1; c["wins"] += e["win"]; c["realized"] += e["realized"]; c["fees"] += e["fees"]
        c["volume"] += e["volume"]; c[e["direction"].lower()] += 1
        if e["complete"]:
            c["hold_h"].append(e["hold_h"]); c["sizes"].append(e["peak_notional"])
    coins = {}
    for k, v in sorted(by_coin.items(), key=lambda kv: -kv[1]["volume"]):
        coins[k] = dict(trades=v["trades"], win_rate=v["wins"] / v["trades"], realized=v["realized"], fees=v["fees"],
                        funding=fund_by_coin.get(k, 0.0), volume_share=v["volume"] / vol if vol else 0.0, long=v["long"], short=v["short"],
                        hold_median_h=med(v["hold_h"]), size_median=med(v["sizes"]))
    longs = [e for e in closed if e["direction"] == "LONG"]
    shorts = [e for e in closed if e["direction"] == "SHORT"]
    pf = (win_sum / loss_sum) if loss_sum else (None if not win_sum else float("inf"))
    return dict(
        trades=len(closed), complete_trades=len(complete), truncated_trades=sum(1 for e in closed if e["truncated"]),
        wins=len(wins), losses=len(losses), win_rate=len(wins) / len(closed) if closed else None, profit_factor=pf,
        gross_realized=gross, fees=fees, funding=funding, net=gross - fees + funding,
        cost_ratio=((fees - funding) / gross) if gross > 0 else None,
        avg_win=win_sum / len(wins) if wins else None, avg_loss=-loss_sum / len(losses) if losses else None,
        payoff_ratio=((win_sum / len(wins)) / (loss_sum / len(losses))) if (wins and losses and loss_sum) else None,
        largest_win=max((e["realized"] for e in closed), default=None), largest_loss=min((e["realized"] for e in closed), default=None),
        hold_winners_h=med([e["hold_h"] for e in cw]) if len(cw) >= MIN_HOLD_N else None,
        hold_losers_h=med([e["hold_h"] for e in cl]) if len(cl) >= MIN_HOLD_N else None,
        hold_ratio=(med([e["hold_h"] for e in cl]) / med([e["hold_h"] for e in cw])) if (len(cw) >= MIN_HOLD_N and len(cl) >= MIN_HOLD_N and med([e["hold_h"] for e in cw])) else None,
        hold_n=dict(winners=len(cw), losers=len(cl)),
        taker_share=taker_vol / vol if vol else None, volume=vol, fee_rate_taker=cross, fee_rate_maker=add,
        fee_recoverable=taker_vol * max(0.0, cross - add) if vol else 0.0,
        liquidations=len(liq), liquidation_loss=sum(e["realized"] for e in liq),
        size_cv=(statistics.pstdev(sizes) / statistics.mean(sizes)) if len(sizes) > 1 and statistics.mean(sizes) else None,
        size_max_over_median=(max(sizes) / med(sizes)) if sizes and med(sizes) else None, size_median=med(sizes),
        long_share=len(longs) / len(closed) if closed else None,
        long=dict(trades=len(longs), wins=sum(1 for e in longs if e["win"]), realized=sum(e["realized"] for e in longs)),
        short=dict(trades=len(shorts), wins=sum(1 for e in shorts if e["win"]), realized=sum(e["realized"] for e in shorts)),
        adds_per_trade=statistics.mean([e["adds"] for e in complete]) if complete else None,
        size_buckets=_size_buckets([e for e in closed if not e["truncated"]]), coins=coins)


def _size_buckets(complete):
    """Winner and loser counts by peak notional, in bands scaled to the trader's own median size."""
    sizes = [e["peak_notional"] for e in complete if e["peak_notional"] > 0]
    m = med(sizes) or 1.0
    bands = [("< ½× median", 0.0, 0.5), ("½–1× median", 0.5, 1.0), ("1–2× median", 1.0, 2.0), ("> 2× median", 2.0, float("inf"))]
    out = []
    for label, lo, hi in bands:
        eps = [e for e in complete if lo * m <= e["peak_notional"] < hi * m]
        out.append(dict(band=label, winners=sum(1 for e in eps if e["win"]), losers=sum(1 for e in eps if not e["win"]),
                        realized=sum(e["realized"] for e in eps)))
    return dict(median_notional=m, bands=out)


def open_book(cs, open_orders, ctxs):
    """Every open position with liquidation distance, funding per day at the current rate, and its stop
    coverage from resting trigger orders: a stop for a long is a sell trigger below the mark, for a short
    a buy trigger above it. Coverage is the stop-covered fraction of the size."""
    marks = {u["name"]: _f(c["markPx"]) for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    rates = {u["name"]: _f(c["funding"]) for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    out = []
    for ap in cs.get("assetPositions") or []:
        p = ap["position"]; szi = _f(p["szi"]); coin = p["coin"]; side = "LONG" if szi > 0 else "SHORT"
        size = abs(szi); mark = marks.get(coin) or _f(p["entryPx"])
        liq_px = _f(p["liquidationPx"]) if p.get("liquidationPx") else None
        exit_side = "A" if side == "LONG" else "B"
        stops, tps = [], []
        for o in open_orders or []:
            if o.get("coin") != coin or not o.get("isTrigger") or o.get("side") != exit_side:
                continue
            tp_ = _f(o.get("triggerPx"))
            (stops if ((side == "LONG" and tp_ < mark) or (side == "SHORT" and tp_ > mark)) else tps).append(o)
        covered = min(size, sum(_f(o["sz"]) for o in stops))
        nearest = None
        if stops:
            nearest = max(_f(o["triggerPx"]) for o in stops) if side == "LONG" else min(_f(o["triggerPx"]) for o in stops)
        notional = size * mark; rate = rates.get(coin, 0.0)
        lev = p.get("leverage") or {}
        out.append(dict(coin=coin, side=side, size=size, entry=_f(p["entryPx"]), mark=mark, leverage=lev.get("value"), margin_mode=lev.get("type"),
                        notional=notional, margin_used=_f(p.get("marginUsed")), unrealized=_f(p.get("unrealizedPnl")), roe=_f(p.get("returnOnEquity")),
                        liq_px=liq_px, liq_distance_pct=(abs(mark - liq_px) / mark * 100) if (liq_px and mark) else None,
                        stop_covered_share=(covered / size) if size else 0.0, stop_px=nearest,
                        stop_distance_pct=(abs(mark - nearest) / mark * 100) if (nearest and mark) else None, take_profit=bool(tps),
                        funding_rate_hourly=rate, funding_per_day=-(rate * notional * 24) * (1 if side == "LONG" else -1),
                        funding_since_open=_f((p.get("cumFunding") or {}).get("sinceOpen"))))
    ms = cs.get("marginSummary") or {}
    av = _f(ms.get("accountValue")); mu = _f(ms.get("totalMarginUsed"))
    gross_exp = sum(p["notional"] for p in out)
    net_exp = sum(p["notional"] * (1 if p["side"] == "LONG" else -1) for p in out)
    return dict(positions=out, account_value=av, margin_used=mu, margin_utilization=(mu / av) if av else None, withdrawable=_f(cs.get("withdrawable")),
                unrealized=sum(p["unrealized"] for p in out), naked=[p["coin"] for p in out if p["stop_covered_share"] == 0],
                partial=[p["coin"] for p in out if 0 < p["stop_covered_share"] < 0.9], gross_exposure=gross_exp, net_exposure=net_exp,
                exposure_over_equity=(gross_exp / av) if av else None, funding_per_day=sum(p["funding_per_day"] for p in out),
                largest_share=(max(p["notional"] for p in out) / gross_exp) if gross_exp else None)


def flows(ledger, addr):
    """Signed transfers in the window: + into this account, − out. Sends carry `user` (sender) and
    `destination`; deposits and withdrawals carry their own types. Other ledger types are ignored."""
    a = addr.lower(); out = []
    for x in ledger or []:
        d = x.get("delta") or {}; t = d.get("type")
        amt = _f(d.get("amount") if d.get("amount") is not None else d.get("usdc"))
        if t == "send":
            sign = 1 if (d.get("destination") or "").lower() == a else -1
        elif t == "deposit":
            sign = 1
        elif t == "withdraw":
            sign = -1
        else:
            continue
        out.append((x["time"], sign * amt))
    return sorted(out)


def equity_curve(portfolio, flow_list, window_start):
    """Transfer-adjusted equity over the window (account value + cumulative net outflows), from the longest
    portfolio series that covers it. A withdrawal must not read as a loss."""
    windows = dict(portfolio or [])
    series = []
    for name in ("allTime", "perpAllTime", "month", "perpMonth"):
        w = windows.get(name) or {}
        pts = [(t, _f(v)) for t, v in (w.get("accountValueHistory") or []) if t >= window_start]
        if len(pts) >= 2 and len(pts) > len(series):
            series = pts
    adj, j, cum_out = [], 0, 0.0
    for t, v in series:
        while j < len(flow_list) and flow_list[j][0] <= t:
            cum_out -= flow_list[j][1]; j += 1
        adj.append((t, v + cum_out))
    return adj


def drawdown(adj):
    """Max drawdown on an adjusted equity curve: ($, fraction of the peak, (peak_ts, trough_ts))."""
    peak, peak_t, dd, dd_pct, span = -1e18, None, 0.0, 0.0, None
    for t, v in adj:
        if v > peak:
            peak, peak_t = v, t
        if peak > 0 and peak - v > dd:
            dd, dd_pct, span = peak - v, (peak - v) / peak, (peak_t, t)
    in_dd = bool(adj) and peak > 0 and (peak - adj[-1][1]) / peak > 0.05
    return dict(dd=dd, dd_pct=dd_pct, span=span, in_drawdown=in_dd, current_dd_pct=((peak - adj[-1][1]) / peak) if (adj and peak > 0) else None)


def pnl_series(portfolio, window_start):
    windows = dict(portfolio or [])
    best = []
    for name in ("allTime", "perpAllTime", "month", "perpMonth"):
        w = windows.get(name) or {}
        pts = [(t, _f(v)) for t, v in (w.get("pnlHistory") or []) if t >= window_start]
        if len(pts) >= 2 and len(pts) > len(best):
            best = pts
    if not best:
        return []
    base = best[0][1]
    return [(t, v - base) for t, v in best]


def coverage(closed, opened, fills=None, fee_sched=None):
    """Observed volume ÷ (observed + the volume implied by `startPosition` jumps), over every episode —
    1.0 when the public API returned every execution. Below 0.9 the desk says so: the usual cause is TWAP
    slices older than the retained window. `volume_ratio` (observed ÷ the wallet's `dailyUserVlm`) rides
    along as a diagnostic only — that figure is not a reliable denominator on every wallet."""
    from roundtrips import coverage as _cov
    eps = list(closed) + list(opened)
    out = dict(overall=_cov(eps), unobserved_volume=sum(e["unobserved_notional"] for e in eps), observed_volume=sum(e["volume"] for e in eps),
               episodes_incomplete=sum(1 for e in eps if e["unobserved_qty"] or not e["close_observed"]), episodes=len(eps))
    out["volume_ratio"] = _daily_ratio(fills or [], fee_sched or {})
    return out


def _daily_ratio(fills, fee_sched):
    import datetime
    days = fee_sched.get("dailyUserVlm") or []
    if not days:
        return None
    seen = collections.defaultdict(lambda: [0.0, 0.0])
    for f in fills:
        if not is_perp(f["coin"]):
            continue
        d = datetime.datetime.utcfromtimestamp(f["time"] / 1000).strftime("%Y-%m-%d")
        seen[d][1 if f.get("crossed") else 0] += _f(f["sz"]) * _f(f["px"])
    hl_t = sum(_f(d.get("userCross")) for d in days); hl_m = sum(_f(d.get("userAdd")) for d in days)
    ob_t = sum(seen[d["date"]][1] for d in days); ob_m = sum(seen[d["date"]][0] for d in days)
    tot = hl_t + hl_m
    return dict(days=len(days), overall=((ob_t + ob_m) / tot) if tot else None, taker=(ob_t / hl_t) if hl_t else None, maker=(ob_m / hl_m) if hl_m else None)


def ledger_pnl(portfolio, window_start):
    """Net P&L over the window from Hyperliquid's own portfolio series (fees, funding and unrealized
    included) — the account-level truth, independent of which fills the API returned."""
    pts = pnl_series(portfolio, window_start)
    return pts[-1][1] if pts else None


def activity(fills, window_start):
    perp = [f for f in fills if f["time"] >= window_start and is_perp(f["coin"])]
    days = {f["time"] // 86_400_000 for f in perp}
    hours = collections.Counter((f["time"] // 3_600_000) % 24 for f in perp)
    return dict(fills=len(perp), coins=len({f["coin"] for f in perp}), active_days=len(days),
                twap_share=(sum(1 for f in perp if f.get("twapId")) / len(perp)) if perp else 0.0,
                busiest_utc_hours=[h for h, _ in hours.most_common(3)], first_fill=min((f["time"] for f in perp), default=None),
                last_fill=max((f["time"] for f in perp), default=None))
