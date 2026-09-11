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
