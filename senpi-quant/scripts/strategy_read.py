#!/usr/bin/env python3
"""What the trader has actually been doing — a strategy fingerprint with receipts, plus a rule-based
critique. Everything here is computed; the agent turns it into the two-paragraph read."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import collections
import math
import statistics

import taxonomy

H = 3_600_000.0


def _pf(eps):
    w = sum(e["realized"] for e in eps if e["realized"] > 0); l = -sum(e["realized"] for e in eps if e["realized"] <= 0)
    return (w / l) if l else (float("inf") if w else None)


def by_class_side(closed, majors, large):
    g = collections.defaultdict(list)
    for e in closed:
        g[(taxonomy.classify(e["coin"], majors, large), e["direction"])].append(e)
    tot = sum(len(v) for v in g.values()) or 1
    total_real = sum(e["realized"] for v in g.values() for e in v)
    rows = []
    for (cls, side), eps in g.items():
        real = sum(e["realized"] for e in eps)
        rows.append(dict(cls=cls, label=taxonomy.label(cls), side=side, trades=len(eps), share=len(eps) / tot, realized=real,
                         pnl_share=(real / total_real) if total_real else None, wins=sum(1 for e in eps if e["win"]), pf=_pf(eps),
                         coins=sorted({e["coin"] for e in eps}, key=lambda c: -sum(x["realized"] for x in eps if x["coin"] == c))[:4]))
    rows.sort(key=lambda r: -r["trades"])
    return rows


def simultaneity(closed, opened, window_start, now):
    """Share of the window with a long AND a short open at once, and the class pairs that overlapped."""
    intervals = [(e["open_time"], e["close_time"] or now, e["direction"], e["coin"]) for e in closed + opened]
    if not intervals:
        return dict(both_share=0.0, pairs=[])
    pts = sorted({max(window_start, o) for o, _, _, _ in intervals} | {min(now, c) for _, c, _, _ in intervals} | {window_start, now})
    both = 0.0; pairs = collections.Counter()
    for a, b in zip(pts, pts[1:]):
        mid = (a + b) / 2
        live = [(d, c) for o, cl, d, c in intervals if o <= mid < cl]
        longs = {c for d, c in live if d == "LONG"}; shorts = {c for d, c in live if d == "SHORT"}
        if longs and shorts:
            both += b - a
            pairs[(tuple(sorted(longs))[:3], tuple(sorted(shorts))[:3])] += b - a
    span = max(1.0, now - window_start)
    top = [dict(longs=list(l), shorts=list(s), share=v / span) for (l, s), v in pairs.most_common(2)]
    return dict(both_share=both / span, pairs=top)


def leg_correlation(candles, long_coins, short_coins):
    """Correlation of hourly returns between the long-leg basket and the short-leg basket the trader
    actually used. A hedge that moves with its own book is not a hedge."""
    def basket(coins):
        series = []
        for c in coins:
            cd = candles.get(c)
            if cd and len(cd[1]) > 200:
                rows = cd[1]
                series.append({r[0]: r[4] for r in rows})
        if not series:
            return None
        times = sorted(set.intersection(*(set(s) for s in series)))
        if len(times) < 100:
            return None
        rets = []
        for a, b in zip(times, times[1:]):
            rets.append(statistics.mean(math.log(s[b] / s[a]) for s in series if s[a] > 0 and s[b] > 0))
        return dict(zip(times[1:], rets))
    L, S = basket(long_coins), basket(short_coins)
    if not L or not S:
        return None
    common = sorted(set(L) & set(S))
    if len(common) < 100:
        return None
    x = [L[t] for t in common]; y = [S[t] for t in common]
    mx, my = statistics.mean(x), statistics.mean(y)
    sx, sy = statistics.pstdev(x), statistics.pstdev(y)
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) * sx * sy)


def pnl_beta(pnl_curve, btc_candles, account_value):
    """How much of the P&L is just BTC: correlation and beta of P&L changes (as % of equity) to BTC returns
    over matching intervals."""
    if not pnl_curve or not btc_candles or len(pnl_curve) < 12 or not account_value:
        return None
    times, rows = btc_candles
    import bisect
    def px(t):
        i = bisect.bisect_right(times, t) - 1
        return rows[i][4] if i >= 0 else None
    xs, ys = [], []
    for (t0, v0), (t1, v1) in zip(pnl_curve, pnl_curve[1:]):
        p0, p1 = px(t0), px(t1)
        if p0 and p1 and t1 > t0:
            xs.append(p1 / p0 - 1); ys.append((v1 - v0) / account_value)
    if len(xs) < 10:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys); sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if not sx or not sy:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs)
    return dict(corr=cov / (sx * sy), beta=cov / (sx * sx), n=len(xs))


def fingerprint(closed, opened, book, tr, act, tm, candles, ctxs, pnl_curve, window_start, now):
    majors, large = taxonomy.crypto_tiers(ctxs)
    rows = by_class_side(closed, majors, large)
    live = [dict(coin=p["coin"], side=p["side"], cls=taxonomy.classify(p["coin"], majors, large), notional=p["notional"]) for p in book["positions"]]
    gross = sum(p["notional"] for p in live) or 1.0
    live_cls = collections.defaultdict(float)
    for p in live:
        live_cls[(taxonomy.label(p["cls"]), p["side"])] += p["notional"] / gross
    net_ratio = abs(book["net_exposure"]) / gross if book["positions"] else None
    sim = simultaneity(closed, opened, window_start, now)
    long_coins = {e["coin"] for e in closed if e["direction"] == "LONG"}; short_coins = {e["coin"] for e in closed if e["direction"] == "SHORT"}
    corr = leg_correlation(candles, long_coins, short_coins) if (long_coins and short_coins) else None
    beta = pnl_beta(pnl_curve, candles.get("BTC"), book.get("account_value"))
    # outcome concentration
    reals = sorted((e["realized"] for e in closed), reverse=True); total = sum(reals)
    top3 = sum(reals[:3]) if reals else 0.0
    conc = (top3 / total) if total > 0 else None
    # sides that never worked
    dead = [r for r in rows if r["trades"] >= 3 and r["wins"] == 0]
    coins_traded = len({e["coin"] for e in closed})
    top_coins = sorted(collections.Counter(e["coin"] for e in closed).items(), key=lambda kv: -kv[1])[:3]
    hold = tr.get("hold_winners_h") if tr.get("hold_winners_h") is not None else None
    style = []
    if tm and (tm.get("chased_share") or 0) >= 0.5:
        style.append("buys strength — half or more of your entries come after a ≥3% move")
    elif tm and tm.get("pre24_median") is not None and tm["pre24_median"] < -0.02:
        style.append("buys weakness — you enter after the move against you, a fader")
    if tr.get("adds_per_trade") and tr["adds_per_trade"] >= 1:
        style.append(f"pyramids — {tr['adds_per_trade']:.1f} adds per trade on average")
    if act.get("twap_share", 0) > 0.2:
        style.append(f"works orders — {100 * act['twap_share']:.0f}% of fills are TWAP slices")
    tpd = (tr.get("trades") or 0) / max(1, act.get("active_days") or 1)
    return dict(class_side=rows, live=live, live_mix=[dict(cls=k[0], side=k[1], share=v) for k, v in sorted(live_cls.items(), key=lambda kv: -kv[1])],
                net_over_gross=net_ratio, simultaneity=sim, leg_correlation=corr, pnl_beta=beta, outcome_concentration=conc,
                dead_sides=dead, coins_traded=coins_traded, top_coins=top_coins, trades_per_active_day=tpd, style=style,
                long_share=tr.get("long_share"))


def statements(fp, tr, book):
    """The receipts, as sentences the agent can quote. Ordered: what you do → where the money is → what's off."""
    s = []
    rows = fp["class_side"]
    if rows:
        head = rows[0]
        n_short = sum(r["trades"] for r in rows if r["side"] == "SHORT"); n_all = sum(r["trades"] for r in rows)
        if n_short == 0:
            tail = "you never short"
        elif n_short / n_all < 0.1:
            tail = f"you almost never short ({n_short} of {n_all})"
        else:
            tail = "your shorts are " + ", ".join(dict.fromkeys(r["label"] for r in rows if r["side"] == "SHORT"))
        s.append(f"{100 * head['share']:.0f}% of your trades are {head['label']} {head['side'].lower()}s ({', '.join(head['coins'][:3])}); {tail}")
    sim = fp["simultaneity"]
    if sim["both_share"] >= 0.3 and sim["pairs"]:
        p = sim["pairs"][0]
        s.append(f"{100 * sim['both_share']:.0f}% of the window you held longs and shorts at once — typically long {', '.join(p['longs'][:3])} against short {', '.join(p['shorts'][:3])}")
    if fp["net_over_gross"] is not None and book["positions"]:
        s.append("the book right now is " + ("directional" if fp["net_over_gross"] > 0.7 else ("hedged" if fp["net_over_gross"] < 0.3 else "tilted"))
                 + f" — net {100 * fp['net_over_gross']:.0f}% of gross; " + ", ".join(f"{100 * m['share']:.0f}% {m['side'].lower()} {m['cls']}" for m in fp["live_mix"][:3]))
    if fp["leg_correlation"] is not None and sim["both_share"] >= 0.2:
        c = fp["leg_correlation"]
        s.append(f"your long leg and your short leg move together (ρ = {c:+.2f}) — " + ("the hedge is mostly a fee" if c > 0.6 else ("the legs are genuinely different bets" if c < 0.3 else "partly a hedge")))
    if fp["pnl_beta"]:
        b = fp["pnl_beta"]
        if abs(b["corr"]) >= 0.5:
            s.append(f"your P&L tracks BTC (correlation {b['corr']:+.2f}): a 1% BTC move swings your equity by about {abs(b['beta']):.1f}% — a lot of the result is the market and the leverage, not the picks")
        else:
            s.append(f"your P&L is largely independent of BTC (correlation {b['corr']:+.2f}) — the picks, not the tape, drive it")
    if fp["outcome_concentration"] is not None and fp["outcome_concentration"] >= 0.6 and (tr.get("trades") or 0) >= 10:
        c = fp["outcome_concentration"]
        s.append((f"your three best trades are more than all of your realized P&L ({100 * c:.0f}%) — everything else nets negative" if c > 1
                  else f"your three best trades are {100 * c:.0f}% of your realized P&L") + " — the edge is concentrated, and fragile")
    for d in fp["dead_sides"][:2]:
        s.append(f"{d['label']} {d['side'].lower()}s have never paid: 0 of {d['trades']} ({'-' if d['realized'] < 0 else ''}${abs(d['realized']):,.0f})")
    if fp["coins_traded"] >= 20 and rows:
        top = fp["top_coins"]
        s.append(f"you touched {fp['coins_traded']} coins, but " + ", ".join(f"{c} ({n})" for c, n in top) + " are most of the activity")
    s += fp["style"]
    return s


def critique(fp, tr, book, mf, sm):
    """Rule-based: what this way of trading needs to work, and where the data says it isn't getting it."""
    out = []
    sim = fp["simultaneity"]; corr = fp["leg_correlation"]
    if sim["both_share"] >= 0.3 and corr is not None and corr > 0.6:
        out.append("A long/short book only earns its funding and fees if the legs diverge. Yours are correlated — you are paying two spreads for one bet. Either pick the side or hedge with something that actually moves differently.")
    if fp["net_over_gross"] is not None and fp["net_over_gross"] > 0.7 and mf and mf.get("against", 0) >= max(1, len(book["positions"]) // 2):
        out.append("A directional book lives or dies with the tape, and most of it is against the trend right now. Directional needs a regime filter; you are running it without one.")
    if fp["pnl_beta"] and fp["pnl_beta"]["corr"] >= 0.6:
        out.append("If the P&L is mostly BTC, the leverage and the fees are the only things you are adding. Either the picks need to diverge from BTC, or the same exposure is cheaper with one position and a stop.")
    if fp["outcome_concentration"] is not None and fp["outcome_concentration"] >= 0.6:
        out.append("An edge that lives in three trades is a story, not a system. The question your record asks is whether the process that found those three is repeatable — the size-vs-outcome and hold-time tables say where.")
    for d in fp["dead_sides"][:1]:
        out.append(f"Stop trading {d['label']} {d['side'].lower()}s until you can say what would make one work; the sample says nothing has.")
    if tr.get("hold_ratio") and tr["hold_ratio"] > 2:
        out.append("The strategy's exits are asymmetric the wrong way: losers get time, winners don't. That is the single most expensive habit in the record.")
    if sm and sm.get("against"):
        out.append(f"On {', '.join(sm['against'])} you are on the other side of the proven cohort. Being contrarian is a strategy only if it is deliberate — is it?")
    if not out:
        out.append("The structure is coherent: the book does what the record says it does. The improvements are in execution and risk, not in the thesis.")
    return out
