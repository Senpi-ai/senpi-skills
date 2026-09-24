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


def track_record(closed, opened, funding_rows, fee_sched, window_start, fee_sched_xyz=None):
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
    # Fees are billed per dex. `userFees` returns the MAIN-dex schedule; applying it to HIP-3
    # (`xyz:`) volume overstated one book's fee leak 4.01x, and on that wallet 98.9% of taker volume
    # was xyz:. Split the volume and price each side on its own schedule (B2, @0xsarvesh #718).
    _rate = lambda sch, k: _f((sch or {}).get(k))
    cross, add = _rate(fee_sched, "userCrossRate"), _rate(fee_sched, "userAddRate")
    x_cross = _rate(fee_sched_xyz, "userCrossRate") or cross
    x_add = _rate(fee_sched_xyz, "userAddRate") or add
    _xyz = lambda e: str(e.get("coin", "")).startswith("xyz:")
    tv_main = sum(e["taker_volume"] for e in closed + opened if not _xyz(e))
    tv_xyz = taker_vol - tv_main
    # Anchor the saving on the fees ACTUALLY PAID (summed from fills), not on volume x schedule.
    # `userFees` returns the main-dex schedule and HIP-3 volume does not bill at it: on a book with
    # $91M of xyz: taker volume, volume x schedule implied $49,434 against $12,863 really paid —
    # 3.84x, and the fee leak inherited all of it. The rates are still used, but only to APPORTION
    # real fees between taker and maker fills, which is schedule-independent as long as the ratio
    # holds. (B2, @0xsarvesh #718.)
    _t_est = tv_main * cross + tv_xyz * x_cross
    # The taker side was split by dex and the maker side was not: every resting fill, xyz: included,
    # was priced at the main-dex maker rate. On the book where 98.9% of taker volume is xyz: that
    # skews the very ratio this exists to compute. Split both sides. (@danielmbirochi, #718, item 6.)
    v_main = sum(e["volume"] for e in closed + opened if not _xyz(e))
    mv_main, mv_xyz = max(0.0, v_main - tv_main), max(0.0, (vol - v_main) - tv_xyz)
    _m_est = mv_main * add + mv_xyz * x_add
    _taker_fee_share = (_t_est / (_t_est + _m_est)) if (_t_est + _m_est) > 0 else 0.0
    # Same for the saving: resting instead of crossing saves the spread on the dex the volume is
    # ACTUALLY on, at that dex's own schedule. Weight by where the taker volume sits.
    _sv = lambda a, c: max(0.0, 1.0 - (a / c)) if c else 0.0
    _save_rate = ((tv_main * _sv(add, cross) + tv_xyz * _sv(x_add, x_cross)) / taker_vol) if taker_vol else 0.0
    # The rates the reader actually faces, measured from the fills. Deriving them from the schedule
    # did not survive contact: `userFees` with dex:"xyz" returns the IDENTICAL schedule to main
    # (0.000405 / 0.000135 both ways), so the per-dex split always fell back and the evidence line
    # still read "4.0 bp taker / 1.4 bp maker" beside a fee figure those rates multiply out to 4.03x.
    # Same principle as the dollars: take what was actually paid. (item 12, @0xsarvesh #718.)
    _tk_fees = sum(e.get("taker_fees") or 0.0 for e in closed + opened)
    _mk_vol = max(0.0, vol - taker_vol)
    eff_taker = (_tk_fees / taker_vol) if (taker_vol and _tk_fees) else (_t_est / taker_vol if taker_vol else cross)
    eff_maker = ((fees - _tk_fees) / _mk_vol) if (_mk_vol > 0 and _tk_fees) else (
        (_m_est / (mv_main + mv_xyz)) if (mv_main + mv_xyz) > 0 else add)
    # NOT abs(): a maker REBATE is negative fees, money EARNED. dim_cost was fixed for exactly this
    # in #733 and this site was missed — it turned a rebate into recoverable dollars, which
    # recoverable() then added on top of the lever. (@danielmbirochi, #718.)
    fee_recoverable = max(0.0, fees) * _taker_fee_share * _save_rate if vol else 0.0
    sizes = [e["peak_notional"] for e in closed if not e["truncated"] and e["peak_notional"] > 0]
    liq = [e for e in closed if e["liquidated"]]
    # The per-coin shares divided a CLOSED-trade numerator by a closed+opened denominator, so on a
    # book carrying large open positions every coin's share read low and none of them summed to 1.
    # (@danielmbirochi, #718, round 2.)
    vol_closed = sum(e["volume"] for e in closed)
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
                        funding=fund_by_coin.get(k, 0.0), volume_share=v["volume"] / vol_closed if vol_closed else 0.0, long=v["long"], short=v["short"],
                        hold_median_h=med(v["hold_h"]), size_median=med(v["sizes"]))
    longs = [e for e in closed if e["direction"] == "LONG"]
    shorts = [e for e in closed if e["direction"] == "SHORT"]
    pf = (win_sum / loss_sum) if loss_sum else (None if not win_sum else float("inf"))
    return dict(
        trades=len(closed), complete_trades=len(complete), truncated_trades=sum(1 for e in closed if e["truncated"]),
        wins=len(wins), losses=len(losses), win_rate=len(wins) / len(closed) if closed else None, profit_factor=pf,
        gross_realized=gross, fees=fees, funding=funding, net=gross - fees + funding,
        gross_income=gross + max(0.0, funding),
        cost_ratio=((fees + max(0.0, -funding)) / (gross + max(0.0, funding))) if (gross + max(0.0, funding)) > 0 else None,     # costs over what was made: trade P&L plus funding collected
        avg_win=win_sum / len(wins) if wins else None, avg_loss=-loss_sum / len(losses) if losses else None,
        payoff_ratio=((win_sum / len(wins)) / (loss_sum / len(losses))) if (wins and losses and loss_sum) else None,
        largest_win=max((e["realized"] for e in closed), default=None), largest_loss=min((e["realized"] for e in closed), default=None),
        hold_winners_h=med([e["hold_h"] for e in cw]) if len(cw) >= MIN_HOLD_N else None,
        hold_losers_h=med([e["hold_h"] for e in cl]) if len(cl) >= MIN_HOLD_N else None,
        hold_ratio=(med([e["hold_h"] for e in cl]) / med([e["hold_h"] for e in cw])) if (len(cw) >= MIN_HOLD_N and len(cl) >= MIN_HOLD_N and med([e["hold_h"] for e in cw])) else None,
        hold_n=dict(winners=len(cw), losers=len(cl)),
        taker_share=taker_vol / vol if vol else None, volume=vol, fee_rate_taker=eff_taker, fee_rate_maker=eff_maker,
        fee_rate_taker_main=cross, fee_rate_maker_main=add,
        fee_recoverable=fee_recoverable, fee_rate_taker_xyz=x_cross, fee_rate_maker_xyz=x_add,
        taker_volume_xyz=tv_xyz,
        liquidations=len(liq), liquidation_loss=sum(e["realized"] for e in liq),
        size_cv=(statistics.pstdev(sizes) / statistics.mean(sizes)) if len(sizes) > 1 and statistics.mean(sizes) else None,
        size_max_over_median=(max(sizes) / med(sizes)) if sizes and med(sizes) else None, size_median=med(sizes),
        long_share=len(longs) / len(closed) if closed else None,
        long=dict(trades=len(longs), wins=sum(1 for e in longs if e["win"]), realized=sum(e["realized"] for e in longs)),
        short=dict(trades=len(shorts), wins=sum(1 for e in shorts if e["win"]), realized=sum(e["realized"] for e in shorts)),
        adds_per_trade=statistics.mean([e["adds"] for e in complete if e.get("adds") is not None]) if any(e.get("adds") is not None for e in complete) else None,
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


DUST_USD = 10.0


def spot_free_usdc(spot):
    """USDC in spot not on hold as perps margin — withdrawable on a unified account."""
    for b in (spot or {}).get("balances") or []:
        if b.get("coin") == "USDC":
            return max(0.0, _f(b.get("total")) - _f(b.get("hold")))
    return 0.0


def open_book(cs, open_orders, ctxs, ages=None, cs_xyz=None, open_orders_xyz=None, ctxs_xyz=None, total_account_value=None, spot_free=0.0):
    """Every open position with liquidation distance, funding per day at the current rate, and its stop
    coverage from resting trigger orders: a stop for a long is a sell trigger below the mark, for a short
    a buy trigger above it. Coverage is the stop-covered fraction of the size. The xyz dex is its own
    collateral pool on the public API: its positions, orders, margin and account value are added in."""
    marks = {u["name"]: _f(c["markPx"]) for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    rates = {u["name"]: _f(c["funding"]) for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    if ctxs_xyz:
        marks.update({u["name"]: _f(c["markPx"]) for u, c in zip(ctxs_xyz[0]["universe"], ctxs_xyz[1])})
        rates.update({u["name"]: _f(c["funding"]) for u, c in zip(ctxs_xyz[0]["universe"], ctxs_xyz[1])})
    out = []
    open_orders = list(open_orders or []) + list(open_orders_xyz or [])
    for ap in (cs.get("assetPositions") or []) + ((cs_xyz or {}).get("assetPositions") or []):
        p = ap["position"]; szi = _f(p["szi"]); coin = p["coin"]; side = "LONG" if szi > 0 else "SHORT"
        size = abs(szi); mark = marks.get(coin) or _f(p["entryPx"])
        if size * mark < DUST_USD:
            continue                                            # dust left behind by a partial close: not a position
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
                        funding_since_open=_f((p.get("cumFunding") or {}).get("sinceOpen")),
                        opened_ms=(ages or {}).get(coin)))
    ms = cs.get("marginSummary") or {}; mx = (cs_xyz or {}).get("marginSummary") or {}
    perps_av = _f(ms.get("accountValue")) + _f(mx.get("accountValue")); mu = _f(ms.get("totalMarginUsed")) + _f(mx.get("totalMarginUsed"))
    av = total_account_value if (total_account_value and total_account_value > 0) else perps_av
    gross_exp = sum(p["notional"] for p in out)
    net_exp = sum(p["notional"] * (1 if p["side"] == "LONG" else -1) for p in out)
    return dict(positions=out, account_value=av, margin_used=mu, margin_utilization=(mu / av) if av else None,
                withdrawable=_f(cs.get("withdrawable")) + _f((cs_xyz or {}).get("withdrawable")) + (spot_free or 0.0),
                account_value_main=_f(ms.get("accountValue")), account_value_xyz=_f(mx.get("accountValue")), account_value_perps=perps_av,
                unrealized=sum(p["unrealized"] for p in out), naked=[p["coin"] for p in out if p["stop_covered_share"] == 0],
                partial=[p["coin"] for p in out if 0 < p["stop_covered_share"] < 0.9], gross_exposure=gross_exp, net_exposure=net_exp,
                exposure_over_equity=(gross_exp / av) if av else None, funding_per_day=sum(p["funding_per_day"] for p in out),
                largest_share=(max(p["notional"] for p in out) / gross_exp) if gross_exp else None)


def whole_account_value(portfolio, spot):
    """Hyperliquid's own account value: the last point of the portfolio series (spot balances plus the perps
    equity — on a unified account the perps margin is USDC on hold in spot). Falls back to the spot USDC
    total when the series is missing. None when neither is readable, so the caller uses the perps view."""
    for name in ("day", "week", "month", "allTime"):
        w = dict(portfolio or []).get(name) or {}
        pts = w.get("accountValueHistory") or []
        if pts:
            v = _f(pts[-1][1])
            if v > 0:
                return v
    for b in (spot or {}).get("balances") or []:
        if b.get("coin") == "USDC":
            v = _f(b.get("total"))
            return v if v > 0 else None
    return None


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


# The portfolio series to read. Ranked by the SPAN a series covers, never by how many POINTS it
# has: HL samples `month` far more densely than `allTime`, so `len(pts) > len(best)` picked a 31-day
# series and every caller labelled it 90 days. The ledger verdict, the drawdown search and
# return-on-avg-equity all inherited it — one book read "Down $4,614 over the window" to a trader up
# $29,003 over the 90 days it claimed to cover. `methodology.md` has documented the span rule since
# 1.0; the code did not implement it.
#
# Ties go to the order below, which puts the perps-only series first: every other number on this
# desk is perps-only, so a whole-account P&L must not sit beside a perps-only anything.
_SERIES_PREF = ("perpAllTime", "allTime", "perpMonth", "month")


def _longest(portfolio, key, window_start):
    windows = dict(portfolio or [])
    best, best_span = [], -1
    for name in _SERIES_PREF:
        pts = [(t, _f(v)) for t, v in ((windows.get(name) or {}).get(key) or []) if t >= window_start]
        if len(pts) >= 2 and (pts[-1][0] - pts[0][0]) > best_span:
            best, best_span = pts, pts[-1][0] - pts[0][0]
    return best


def equity_curve(portfolio, flow_list, window_start):
    """Transfer-adjusted equity over the window (account value + cumulative net outflows), from the longest
    portfolio series that covers it. A withdrawal must not read as a loss."""
    series = _longest(portfolio, "accountValueHistory", window_start)
    adj, j, cum_out = [], 0, 0.0
    for t, v in series:
        while j < len(flow_list) and flow_list[j][0] <= t:
            cum_out -= flow_list[j][1]; j += 1
        adj.append((t, v + cum_out))
    return adj


def _dd_basis(av_at, peak_t, trough_t, fall):
    """The equity a fall came out of, or None when the series cannot say.

    Equity AT THE PEAK is the direct answer: that is the capital the drawdown ate into. The older
    form, `equity at the trough + the fall`, only equals it when nothing was deposited or withdrawn
    in between — and that is precisely the assumption a senpi strategy wallet breaks. Such a wallet
    is funded, traded, then SWEPT back to the funding wallet when the strategy closes, so its
    account-value history reads 0.0 long after real money passed through it.

    When the chosen series carries 0.0 at every point (a swept wallet's sparse perpAllTime history),
    the old form degenerated to `0 + fall = fall` and every closed wallet scored a 100% drawdown —
    "the account went to zero, a full loss of the equity at risk" printed over books that fell 12%
    and one that ended the window UP $57. There is no basis for a percentage there, so say None and
    let callers render "—" rather than invent the most alarming number in the range.
    """
    at_peak = av_at(peak_t) or 0.0
    if at_peak > 0:
        return at_peak
    at_trough = av_at(trough_t) or 0.0
    if at_trough > 0:
        return at_trough + fall
    return None


def drawdown(pnl_pts, av_pts):
    """Max drawdown from Hyperliquid's own P&L series (transfer-immune by construction): the deepest
    peak-to-trough fall in cumulative P&L, as a share of the equity that fall came out of. Capped at
    100%. `dd` (the dollar fall) is always meaningful; `dd_pct` is None when the account-value series
    has no positive reading to divide by — see _dd_basis."""
    if not pnl_pts:
        return dict(dd=0.0, dd_pct=0.0, span=None, in_drawdown=False, current_dd_pct=None)
    av = dict(av_pts or [])
    def av_at(t):
        ks = [k for k in av if k <= t]
        return av[max(ks)] if ks else (av[min(av)] if av else 0.0)
    peak, peak_t, dd, dd_pct, span = -1e18, None, 0.0, None, None
    for t, v in pnl_pts:
        if v > peak:
            peak, peak_t = v, t
        fall = peak - v
        if fall > dd:
            base = _dd_basis(av_at, peak_t, t, fall)
            dd, span = fall, (peak_t, t)
            dd_pct = min(1.0, fall / base) if base and base > 0 else None
    last_t, last = pnl_pts[-1]
    base_now = _dd_basis(av_at, peak_t, last_t, peak - last) if peak > last else None
    cur = min(1.0, (peak - last) / base_now) if base_now and base_now > 0 and peak > last else None
    if dd == 0.0 and dd_pct is None:
        dd_pct = 0.0          # no fall at all is a real 0%, not an unknown
    return dict(dd=dd, dd_pct=dd_pct, span=span, in_drawdown=bool(cur and cur > 0.05), current_dd_pct=cur)


def pnl_series(portfolio, window_start):
    best = _longest(portfolio, "pnlHistory", window_start)
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
        # main-dex only: `dailyUserVlm` is the main-dex figure, so counting xyz: fills in the
        # numerator made the diagnostic read 2.26x — "we saw 226% of the volume" (B2, #718)
        if not is_perp(f["coin"]) or str(f["coin"]).startswith("xyz:"):
            continue
        d = datetime.datetime.fromtimestamp(f["time"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
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
