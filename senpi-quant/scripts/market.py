#!/usr/bin/env python3
"""Market fit from public data: per-coin trend, realized volatility, funding and open interest, and whether
each open position sits with or against them. Funding is quoted per 8h in basis points (Hyperliquid pays
hourly; hourly rate × 8 × 10,000)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import math
import statistics

H = 3_600_000.0


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def coin_regime(coin, candles, ctx):
    """Trend from hourly candles: 7-day and 30-day change and the close vs its 20-day mean; volatility as the
    24h realized vol against the 30-day. Returns None when the candle series is missing."""
    c = candles.get(coin)
    if not c:
        return None
    rows = c[1]
    if len(rows) < 48:
        return None
    close = [r[4] for r in rows]
    now = close[-1]
    def chg(hours):
        return (now / close[-hours - 1] - 1) if len(close) > hours else None
    sma20 = statistics.mean(close[-480:]) if len(close) >= 100 else statistics.mean(close)
    rets = [math.log(close[i] / close[i - 1]) for i in range(1, len(close)) if close[i - 1] > 0]
    vol24 = statistics.pstdev(rets[-24:]) * math.sqrt(24) if len(rets) >= 24 else None
    vol30 = statistics.pstdev(rets[-720:]) * math.sqrt(24) if len(rets) >= 100 else None
    c7, c30 = chg(168), chg(720)
    vs_sma = now / sma20 - 1
    if c7 is not None and c7 > 0.05 and vs_sma > 0.02:
        trend = "UP"
    elif c7 is not None and c7 < -0.05 and vs_sma < -0.02:
        trend = "DOWN"
    else:
        trend = "RANGING"
    fr = _f((ctx or {}).get("funding"))
    return dict(coin=coin, trend=trend, change_7d=c7, change_30d=c30, vs_20d_mean=vs_sma, vol_24h=vol24, vol_30d=vol30,
                vol_ratio=(vol24 / vol30) if (vol24 and vol30) else None, funding_bp_8h=fr * 8 * 1e4,
                open_interest_usd=_f((ctx or {}).get("openInterest")) * _f((ctx or {}).get("markPx")), day_volume=_f((ctx or {}).get("dayNtlVlm")))


def fit(position, regime):
    """WITH / AGAINST / NEUTRAL: a long fits an up-trend, a short a down-trend; ranging is neutral. Funding
    is reported beside it (a long paying positive funding is a cost, not a misfit)."""
    if not regime:
        return "UNKNOWN"
    if regime["trend"] == "RANGING":
        return "NEUTRAL — ranging"
    return "WITH THE MARKET" if (position["side"] == "LONG") == (regime["trend"] == "UP") else "AGAINST THE MARKET"


def book_fit(book, candles, ctxs):
    ctx_by = {u["name"]: c for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    rows, regimes = [], {}
    for p in book["positions"]:
        r = coin_regime(p["coin"], candles, ctx_by.get(p["coin"]))
        regimes[p["coin"]] = r
        rows.append(dict(coin=p["coin"], side=p["side"], leverage=p["leverage"], trend=r["trend"] if r else None,
                         funding_bp_8h=r["funding_bp_8h"] if r else None, open_interest_usd=r["open_interest_usd"] if r else None,
                         funding_per_day=p["funding_per_day"], fit=fit(p, r)))
    btc = coin_regime("BTC", candles, ctx_by.get("BTC"))
    fundings = [r["funding_bp_8h"] for r in rows if r["funding_bp_8h"] is not None]
    med_f = statistics.median(fundings) if fundings else None
    if med_f is None:
        fhead = "FUNDING UNKNOWN"
    elif med_f >= 10:
        fhead = "HIGH POSITIVE FUNDING"
    elif med_f >= 3:
        fhead = "POSITIVE FUNDING"
    elif med_f <= -10:
        fhead = "DEEPLY NEGATIVE FUNDING"
    elif med_f <= -3:
        fhead = "NEGATIVE FUNDING"
    else:
        fhead = "FUNDING NEAR FLAT"
    headline = f"{fhead} · BTC {btc['trend'] if btc else 'UNKNOWN'}"
    net = book["net_exposure"]
    stance = "net long" if net > 0 else ("net short" if net < 0 else "flat")
    fpd = book["funding_per_day"]
    return dict(headline=headline, median_funding_bp_8h=med_f, btc=btc, stance=stance, funding_per_day=fpd, rows=rows, regimes=regimes,
                with_market=sum(1 for r in rows if r["fit"].startswith("WITH")), against=sum(1 for r in rows if r["fit"].startswith("AGAINST")))


# ================================================================ v2: the market the trader is in
import collections  # noqa: E402

import taxonomy  # noqa: E402

DAY_MS = 86_400_000


def _field(d, *names, default=None):
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
    return default


def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def breadth(ctx_main, ctx_xyz=None):
    """Today's tape from the live contexts: 24h change per asset (mark vs previous day), grouped by asset
    class; risk_on / risk_off / mixed by senpi-market-pulse's rule (groups moving ≥ 0.5% either way)."""
    majors, large = taxonomy.crypto_tiers(ctx_main)
    rows = []
    for ctxs in (ctx_main, ctx_xyz):
        if not ctxs:
            continue
        for u, c in zip(ctxs[0]["universe"], ctxs[1]):
            try:
                mark, prev = float(c["markPx"]), float(c["prevDayPx"])
                if not mark or not prev:
                    continue
                rows.append(dict(coin=u["name"], cls=taxonomy.classify(u["name"], majors, large), change_pct=100 * (mark / prev - 1),
                                 funding_bp_8h=_f(c.get("funding")) * 8 * 1e4, oi_usd=_f(c.get("openInterest")) * mark, volume=_f(c.get("dayNtlVlm"))))
            except (TypeError, ValueError, KeyError):
                continue
    groups = {}
    for cls in sorted({r["cls"] for r in rows}):
        rs = [r for r in rows if r["cls"] == cls and r["volume"] > 0]
        if not rs:
            continue
        chg = [r["change_pct"] for r in rs]
        groups[cls] = dict(label=taxonomy.label(cls), n=len(rs), avg_change_pct=statistics.mean(chg), median_change_pct=statistics.median(chg),
                           up=sum(1 for x in chg if x > 0), down=sum(1 for x in chg if x < 0), median_funding_bp_8h=statistics.median(r["funding_bp_8h"] for r in rs),
                           oi_usd=sum(r["oi_usd"] for r in rs))
    avgs = [g["avg_change_pct"] for g in groups.values()]
    down = sum(1 for x in avgs if x < -0.5); up = sum(1 for x in avgs if x > 0.5)
    day = "risk_off" if (down >= up * 2 and down >= 3) else ("risk_on" if (up >= down * 2 and up >= 3) else "mixed")
    main_rows = [r for r in rows if not r["coin"].startswith("xyz:") and r["volume"] > 0]
    share_up = (sum(1 for r in main_rows if r["change_pct"] > 0) / len(main_rows)) if main_rows else None
    by = {r["coin"]: r for r in rows}
    return dict(day=day, share_up=share_up, groups=groups, btc_change_pct=by.get("BTC", {}).get("change_pct"), eth_change_pct=by.get("ETH", {}).get("change_pct"),
                memes_vs_majors=(groups.get("memes", {}).get("avg_change_pct", 0) - groups.get("majors", {}).get("avg_change_pct", 0)) if ("memes" in groups and "majors" in groups) else None,
                assets=by, majors=sorted(majors), large=sorted(large))


def daily_regimes(daily, basket):
    """One label per UTC day over the window from daily candles of a basket: risk_on when BTC is up more
    than 1% or ≥ 65% of the basket closed up; risk_off symmetric; else mixed."""
    closes = {c: {r[0]: r[4] for r in rows} for c, rows in (daily or {}).items() if rows and c in basket}
    btc = closes.get("BTC") or {}
    days = sorted(set().union(*(set(v) for v in closes.values())) if closes else set())
    out = {}
    prev = {}
    for d in days:
        ups = downs = 0
        for c, s in closes.items():
            if d in s and c in prev and prev[c]:
                ups += s[d] > prev[c]; downs += s[d] < prev[c]
            if d in s:
                prev[c] = s[d]
        n = ups + downs
        bchg = (btc[d] / btc[prev_d] - 1) if (d in btc and (prev_d := _prev_key(btc, d)) is not None) else None
        share = (ups / n) if n else None
        if bchg is not None and (bchg > 0.01 or (share is not None and share >= 0.65)):
            lab = "risk_on"
        elif bchg is not None and (bchg < -0.01 or (share is not None and share <= 0.35)):
            lab = "risk_off"
        else:
            lab = "mixed"
        out[d] = dict(label=lab, btc_change=bchg, share_up=share)
    return out


def _prev_key(series, d):
    ks = [k for k in series if k < d]
    return max(ks) if ks else None


def regime_performance(closed, regimes):
    """The trader's own record split by the regime of the day each trade was opened."""
    if not regimes:
        return None
    days = sorted(regimes)
    import bisect
    def label_for(t):
        i = bisect.bisect_right(days, t) - 1
        return regimes[days[i]]["label"] if i >= 0 and t - days[i] < 2 * DAY_MS else None
    g = collections.defaultdict(list)
    for e in closed:
        lab = label_for(e["open_time"])
        if lab:
            g[(lab, "ALL")].append(e); g[(lab, e["direction"])].append(e)
    out = {}
    for (lab, side), eps in g.items():
        w = sum(e["realized"] for e in eps if e["realized"] > 0); l = -sum(e["realized"] for e in eps if e["realized"] <= 0)
        out[f"{lab}/{side}"] = dict(regime=lab, side=side, trades=len(eps), wins=sum(1 for e in eps if e["win"]), realized=sum(e["realized"] for e in eps),
                                    pf=(w / l) if l else (float("inf") if w else None))
    counts = collections.Counter(v["label"] for v in regimes.values())
    return dict(cells=out, days=dict(counts))


def funding_regime(resp):
    d = _ok(resp)
    if isinstance(d, str):
        return dict(regime=d)
    if not isinstance(d, dict):
        return None
    return dict(regime=_field(d, "regime", "funding_regime", "label", "state"), avg_annualized_pct=_field(d, "avg_annualized_funding", "average_annualized_funding_pct", "avg_annualized_pct"),
                extreme_count=_field(d, "extreme_count", "extreme_funding_count", "num_extreme"), duration_hours=_field(d, "regime_duration_hours", "duration_hours", "duration"),
                long_funding_count=_field(d, "long_funding_count", "longs_pay_count"), short_funding_count=_field(d, "short_funding_count", "shorts_pay_count"))


def attention(markets_resp, momentum_resp, book):
    """Where the top traders' gains sit right now (leaderboard_get_markets) and who is entering momentum
    in what (momentum events, last 4h) — against the trader's own book."""
    out = dict(markets=[], overlap=[], momentum=[], with_momentum=[], against_momentum=[])
    m = _ok(markets_resp) or {}
    rows = m.get("markets") if isinstance(m, dict) else (m if isinstance(m, list) else [])
    dom = [r for r in rows or [] if isinstance(r, dict) and r.get("is_dominant_direction")]
    dom.sort(key=lambda r: -_f(r.get("pct_of_top_traders_gain")))
    mine = {p["coin"]: p["side"] for p in book["positions"]}
    for r in dom[:8]:
        coin = (("xyz:" + r["token"]) if r.get("dex") == "xyz" else r.get("token")) if r.get("token") else None
        row = dict(coin=coin, direction=(r.get("direction") or "").upper(), share_of_gains=_f(r.get("pct_of_top_traders_gain")), traders=int(_f(r.get("trader_count"))),
                   change_4h=_field(r, "token_price_change_pct_4h"), contribution_change_1h=_field(r, "contribution_pct_change_1h"))
        out["markets"].append(row)
        if coin in mine:
            out["overlap"].append(dict(coin=coin, you=mine[coin], top=row["direction"], read="WITH" if mine[coin] == row["direction"] else "AGAINST"))
    ev = _ok(momentum_resp) or []
    if isinstance(ev, dict):
        ev = _field(ev, "events", "data", "results", default=[]) or []
    seen = collections.Counter()
    for e in ev[:50]:
        if not isinstance(e, dict):
            continue
        for p in e.get("top_positions") or []:
            if not isinstance(p, dict):
                continue
            coin = _field(p, "token", "coin", "asset", "market"); side = str(_field(p, "direction", "side", default="")).upper()
            if coin and side:
                seen[(coin, side)] += 1
    for (coin, side), n in seen.most_common(8):
        out["momentum"].append(dict(coin=coin, direction=side, events=n))
        if coin in mine:
            (out["with_momentum"] if mine[coin] == side else out["against_momentum"]).append(coin)
    return out
