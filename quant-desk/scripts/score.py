#!/usr/bin/env python3
"""The desk's judgement layer: six 0–100 dimensions with one-line explanations, the quant score, the
archetype, flag chips, the verdict, the leaks ranked by counterfactual dollars, and where the edge is.
Every number here is a transparent function of the metrics — the formulas are in references/methodology.md."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import collections
import statistics

WEIGHTS = {"risk": 0.25, "consistency": 0.20, "timing": 0.15, "cost": 0.15, "market_fit": 0.15, "sizing": 0.10}


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _pct(x, d=0):
    return f"{100 * x:.{d}f}%"


def _usd(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


# ---------------------------------------------------------------- six dimensions
def dim_timing(tm, sm, cov=None):
    s, lines = 70.0, []
    if not tm or (tm.get("n") or 0) < 5:
        why = f" — the public API returned about {_pct(cov['overall'])} of your executed volume; connect senpi for the full history" if cov and cov.get("overall") is not None and cov["overall"] < 0.9 else ""
        return 60.0, f"Not enough fully observed trades to judge timing{why}."
    if tm and tm.get("n"):
        cs = tm.get("chased_share") or 0.0
        s -= cs * 40
        cpf, kpf = tm.get("chased_pf"), tm.get("calm_pf")
        if tm.get("chased_n", 0) >= 3 and cpf is not None and kpf is not None and cpf < 1.0 < kpf:
            s -= 15
            lines.append((3, f"{_pct(cs)} of your entries come after a ≥3% move — those run a {cpf:.1f} profit factor vs {min(kpf, 9.9):.1f} when you enter before it."))
        elif cs >= 0.5:
            lines.append((2, f"{_pct(cs)} of your entries come after a ≥3% move in their direction — you buy the move, not the turn."))
        gb = tm.get("give_back_median")
        if gb is not None:
            s -= gb * 20
            if gb >= 0.4:
                lines.append((2, f"You give back a median {_pct(gb)} of a winner's peak gain before you exit."))
    lag = (sm or {}).get("entry_lag_h")
    if lag is not None:
        if lag > 0:
            s -= min(20, lag * 2); lines.append((3, f"You enter ~{lag:.1f}h after the whale cohort's flow turns."))
        else:
            s += 10; lines.append((1, f"You enter {abs(lag):.1f}h ahead of the whale cohort on the coins you share."))
    if not lines:
        lines.append((0, "Entries are not systematically late or chased over this window." if tm and tm.get("n") else "Not enough complete trades to judge timing."))
    return clamp(s), max(lines)[1]


def dim_risk(tr, book, dd):
    s, lines = 85.0, []
    hr = tr.get("hold_ratio")
    if hr and hr > 1.2:
        s -= min(30, (hr - 1) * 15); lines.append((3 if hr >= 2 else 2, f"You hold losers {hr:.1f}× longer than winners (median {tr['hold_losers_h']:.1f}h vs {tr['hold_winners_h']:.1f}h)."))
    n = len(book["positions"])
    if n:
        naked = len(book["naked"]); s -= 25 * naked / n
        if naked:
            lines.append((3, f"{naked} of {n} open positions have no stop at all — {', '.join(book['naked'])}."))
        near = [p for p in book["positions"] if p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5]
        if near:
            s -= 15; p = min(near, key=lambda p: p["liq_distance_pct"])
            lines.append((4, f"{p['coin']} {p['side'].lower()} {p['leverage']}× sits {p['liq_distance_pct']:.1f}% from liquidation."))
    if tr.get("liquidations"):
        s -= min(30, 10 * tr["liquidations"]); lines.append((3, f"{tr['liquidations']} liquidation(s) in 90 days cost {_usd(tr['liquidation_loss'])}."))
    mu = book.get("margin_utilization")
    if mu and mu > 0.6:
        s -= min(20, (mu - 0.6) * 50); lines.append((2, f"Margin used is {_pct(mu)} of account value — little cushion for a bad hour."))
    if dd and dd.get("dd_pct"):
        s -= min(20, dd["dd_pct"] * 60)
        if dd["dd_pct"] >= 0.25:
            lines.append((2, f"Max drawdown {_pct(dd['dd_pct'])} of equity over the window."))
    if not lines:
        lines.append((0, "Stops in place, losers cut faster than winners, no liquidations."))
    return clamp(s), max(lines)[1]


def dim_cost(tr):
    cr, ts = tr.get("cost_ratio"), tr.get("taker_share")
    if cr is not None:
        s = 100 - min(70, cr * 150)
        line = f"Fees + funding ate {_pct(cr)} of gross P&L ({_usd(tr['fees'])} fees, {_usd(-tr['funding'])} funding on {_usd(tr['gross_realized'])} gross)."
    else:
        s = 90 - min(60, (ts or 0) * 50)
        line = f"Gross P&L is not positive over the window; fees {_usd(tr['fees'])} and funding {_usd(-tr['funding'])} came on top."
    if ts is not None and ts > 0.6:
        s -= 10
        line += f" {_pct(ts)} of your volume crossed the spread as a taker."
    return clamp(s), line


def dim_sizing(tr, book, closed):
    s, lines = 85.0, []
    cv = tr.get("size_cv")
    if cv:
        s -= min(30, cv * 20)
        if cv >= 0.6:
            lines.append((2, f"Position size varies {tr['size_max_over_median']:.1f}× trade-to-trade (largest vs median)."))
    comp = [e for e in closed if not e.get("truncated") and e["peak_notional"] > 0]
    lw = [e["peak_notional"] for e in comp if e["win"]]; ll = [e["peak_notional"] for e in comp if not e["win"]]
    if len(lw) >= 3 and len(ll) >= 3:
        r = statistics.median(ll) / statistics.median(lw)
        if r > 1.3:
            s -= 15; lines.append((3, f"Your losers are {r:.1f}× the size of your winners — conviction lands on the wrong trades."))
        elif r < 0.8:
            lines.append((1, f"Your winners are {1 / r:.1f}× the size of your losers — sizing is doing real work."))
    ex = book.get("exposure_over_equity")
    if ex and ex > 5:
        s -= min(20, (ex - 5) * 3); lines.append((2, f"Gross exposure is {ex:.1f}× account value right now."))
    if book.get("largest_share") and book["largest_share"] > 0.6 and len(book["positions"]) > 1:
        s -= 10; lines.append((1, f"One position is {_pct(book['largest_share'])} of the book."))
    if not lines:
        lines.append((0, "Sizes are consistent and exposure is proportionate."))
    return clamp(s), max(lines)[1]


def dim_consistency(tr, pnl_curve):
    n = tr.get("trades") or 0
    wr, pf = tr.get("win_rate"), tr.get("profit_factor")
    if pf is None and wr is None:
        return 50.0, "No closed trades in the window."
    pf_ = 3.0 if pf in (None, float("inf")) else min(pf, 3.0)
    s = 50 + (pf_ - 1) * 20 + (((wr or 0.5) - 0.5) * 40 if wr is not None else 0)
    s = 50 + (s - 50) * (n / (n + 10.0)) if n else 50.0
    pfs = "∞" if pf == float("inf") else (f"{pf:.1f}" if pf is not None else "n/a")
    line = f"Win rate {_pct(wr)}, profit factor {pfs} across {tr['trades']} trades"
    if tr.get("payoff_ratio"):
        line += f" — average winner {tr['payoff_ratio']:.1f}× the average loser"
    line += "."
    if n and n < 15:
        line += f" Only {n} trades: read this loosely."
    return clamp(s), line


def dim_market(book, mf):
    s, lines = 65.0, []
    if not mf or not book["positions"]:
        return 60.0, "No open positions to fit against the market."
    s += min(25, 10 * mf["with_market"]) - min(45, 15 * mf["against"])
    ag = [r for r in mf["rows"] if r["fit"].startswith("AGAINST")]
    if ag:
        lines.append((3, f"{', '.join(r['coin'] for r in ag)} — {ag[0]['side'].lower()} into a {ag[0]['trend'].lower()}-trend."))
    av = book.get("account_value") or 0
    fpd = mf.get("funding_per_day") or 0.0
    if fpd < 0 and av:
        yr = -fpd * 365 / av
        s -= min(20, yr * 50)
        lines.append((2, f"{mf['stance'].capitalize()} into {'positive' if book['net_exposure'] > 0 else 'negative'} funding — paying ~{_usd(-fpd)}/day to hold ({_pct(yr)} of equity a year)."))
    elif fpd > 0:
        lines.append((1, f"Your book collects ~{_usd(fpd)}/day in funding at today's rates."))
    if not lines:
        lines.append((0, f"{mf['with_market']} of {len(mf['rows'])} positions sit with the trend; funding is near flat."))
    return clamp(s), max(lines)[1]


def dimensions(tr, book, dd, tm, mf, sm, closed, pnl_curve):
    d = {}
    for key, (score, line) in {
        "timing": dim_timing(tm, sm, tr.get("coverage")), "risk": dim_risk(tr, book, dd), "cost": dim_cost(tr), "sizing": dim_sizing(tr, book, closed),
        "consistency": dim_consistency(tr, pnl_curve), "market_fit": dim_market(book, mf)}.items():
        d[key] = dict(score=round(score), line=line)
    quant = sum(WEIGHTS[k] * d[k]["score"] for k in WEIGHTS)
    return d, round(quant)


# ---------------------------------------------------------------- archetype, flags, verdict
def archetype(tr, book, tm, act):
    ex = book.get("exposure_over_equity") or 0; mu = book.get("margin_utilization") or 0
    levs = [p["leverage"] for p in book["positions"] if p.get("leverage")]
    adj = "Aggressive" if (ex > 5 or mu > 0.6 or (levs and max(levs) >= 10)) else ("Careful" if (ex < 2 and mu < 0.3) else "Balanced")
    hold = tr.get("hold_winners_h") or 0
    if tr.get("complete_trades"):
        hold = statistics.median([e for e in [tr.get("hold_winners_h"), tr.get("hold_losers_h")] if e]) if (tr.get("hold_winners_h") or tr.get("hold_losers_h")) else 0
    tpd = (tr.get("trades") or 0) / max(1, act.get("active_days") or 1)
    cs = (tm or {}).get("chased_share") or 0; pre = (tm or {}).get("pre24_median")
    if cs >= 0.5:
        noun = "momentum chaser"
    elif hold and hold < 2 and tpd >= 3:
        noun = "scalper"
    elif pre is not None and pre < -0.02:
        noun = "dip buyer" if (tr.get("long_share") or 0) >= 0.7 else "fader"
    elif hold and hold > 120:
        noun = "position trader"
    elif hold and hold > 24:
        noun = "swing trader"
    elif pre is not None and pre > 0 and (tm or {}).get("give_back_median", 1) < 0.45:
        noun = "trend rider"
    else:
        noun = "opportunist"
    bias = ""
    ls = tr.get("long_share")
    if ls is not None and tr.get("trades", 0) >= 5:
        bias = "long-only " if ls >= 0.9 else ("short-only " if ls <= 0.1 else "")
    return f"{adj} {bias}{noun}"


def flags(tr, book, dd, tm, mf, labels):
    out = []
    n = len(book["positions"])
    if n and book["naked"]:
        out.append(f"NO STOPS ({len(book['naked'])}/{n})")
    elif n and book["partial"]:
        out.append(f"PARTIAL STOPS ({len(book['partial'])}/{n})")
    near = [p for p in book["positions"] if p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5]
    if near:
        out.append(f"NEAR LIQUIDATION {min(p['liq_distance_pct'] for p in near):.1f}%")
    mu = book.get("margin_utilization")
    if mu and mu >= 0.6:
        out.append(f"HIGH MARGIN {_pct(mu)}")
    if dd and dd.get("in_drawdown"):
        out.append("IN DRAWDOWN")
    if tr.get("liquidations"):
        out.append(f"LIQUIDATED ×{tr['liquidations']}")
    if (tm or {}).get("chased_share", 0) >= 0.5:
        out.append("CHASING")
    if mf and mf.get("funding_per_day", 0) < 0 and book.get("account_value") and -mf["funding_per_day"] * 365 / book["account_value"] > 0.1:
        out.append(f"PAYING FUNDING {_usd(-mf['funding_per_day'])}/DAY")
    for k in ("consistency", "risk", "activity"):
        v = (labels or {}).get(k)
        if v and v.upper() in ("CHOPPY", "STREAKY", "SNIPER", "DEGEN"):
            out.append(v.upper())
    return out


def verdict(tr, book, dims, leaks):
    pf, pr = tr.get("profit_factor"), tr.get("payoff_ratio")
    if pf and pf != float("inf") and pf >= 1.5 and (tr.get("trades") or 0) >= 10:
        strength = f"Real edge — profit factor {pf:.1f} on {tr['trades']} trades"
    elif pr and pr >= 2 and (tr.get("trades") or 0) >= 10:
        strength = f"You let winners run ({pr:.1f}× payoff)"
    elif tr.get("win_rate") and tr["win_rate"] >= 0.55 and (tr.get("trades") or 0) >= 10:
        strength = f"You pick well — {_pct(tr['win_rate'])} win rate"
    elif (tr.get("net") or 0) > 0:
        strength = f"Net positive ({_usd(tr['net'])} over the window)"
    else:
        strength = "No edge shows up in this window"
    weakest = min(dims, key=lambda k: dims[k]["score"])
    weak_line = {
        "risk": "you're carrying unprotected risk" if book["naked"] else "the risk side is where it leaks",
        "cost": "execution and funding are eating the gains", "timing": "your entries are late or chased",
        "sizing": "sizing is working against you", "consistency": "the results are not repeatable yet",
        "market_fit": "the book is fighting the market it sits in"}[weakest]
    imperative = {"risk": "Fix the risk first.", "cost": "Cut the costs first.", "timing": "Fix the entries first.", "sizing": "Fix the sizing first.",
                  "consistency": "Build the sample before scaling.", "market_fit": "Get on the right side of the regime first."}[weakest]
    near = [p for p in book["positions"] if p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5]
    if near:
        p = min(near, key=lambda p: p["liq_distance_pct"])
        weak_line = f"{p['coin']} sits {p['liq_distance_pct']:.1f}% from liquidation with {'no' if p['stop_covered_share'] == 0 else 'a partial'} stop"
        imperative = "Protect that position today."
    return f"{strength} — but {weak_line}. {imperative}"


# ---------------------------------------------------------------- leaks
def leaks(tr, book, tm, funding_rows, closed, window_start, days):
    out = []
    yr = 365.0 / days
    # 1. costs — resting instead of crossing the spread
    if tr.get("fee_recoverable", 0) >= 50 and tr.get("taker_share"):
        out.append(dict(agent="Leak finder", title=f"{_pct(tr['taker_share'])} of your volume crossed the spread as a taker",
                        evidence=f"{_usd(tr['fees'])} in fees on {_usd(tr['volume'])} of volume at {tr['fee_rate_taker'] * 1e4:.1f} bp taker / {tr['fee_rate_maker'] * 1e4:.1f} bp maker.",
                        counterfactual=f"Resting maker orders for the same fills would have kept ~{_usd(tr['fee_recoverable'])} over {days} days (≈{_usd(tr['fee_recoverable'] * yr)}/yr).",
                        usd=tr["fee_recoverable"], window=f"{days}d", cta="A maker-first entry with a taker fallback is one line in a strategy."))
    # 2. funding — hold time on funding-paying legs
    paid_late = _funding_after(funding_rows, closed, window_start, 24.0)
    if tr.get("funding", 0) < -100 and paid_late > 50:
        worst = min(tr["coins"].items(), key=lambda kv: kv[1]["funding"])
        out.append(dict(agent="Market regime", title=f"You paid {_usd(-tr['funding'])} in funding over {days} days",
                        evidence=f"{worst[0]} alone cost {_usd(-worst[1]['funding'])}; the book pays {_usd(-book['funding_per_day'])}/day at today's rates." if book.get("funding_per_day", 0) < 0 else f"{worst[0]} alone cost {_usd(-worst[1]['funding'])}.",
                        counterfactual=f"A 24h cap on holds that pay funding would have kept ~{_usd(paid_late)} over {days} days.",
                        usd=paid_late, window=f"{days}d", cta="A funding-aware hold rule caps the cost without changing the thesis."))
    # 3. losers held too long — only when the time cut is robust
    cut = ((tm or {}).get("cut") or {}).get("robust")
    if cut and cut > 50 and tr.get("hold_ratio", 0) and tr["hold_ratio"] > 1.2:
        out.append(dict(agent="Leak finder", title=f"You hold losers {tr['hold_ratio']:.1f}× longer than winners",
                        evidence=f"Median loser {tr['hold_losers_h']:.1f}h vs winner {tr['hold_winners_h']:.1f}h across {tr['complete_trades']} complete trades.",
                        counterfactual=f"A time-cut on losers (12–48h, whichever) would have kept roughly ~{_usd(cut)} over {days} days (approximate: peak size × price move).",
                        usd=cut, window=f"{days}d", cta="A time-cut is a rule your quant can run for you."))
    # 4. giving back winners — only when the lock is robust
    lock = ((tm or {}).get("lock") or {}).get("robust")
    if lock and lock > 50:
        out.append(dict(agent="Leak finder", title=f"You give back a median {_pct(tm['give_back_median'])} of a winner's peak",
                        evidence=f"Winners reach a median +{_pct(tm['mfe_median_winners'], 1)} before exit; {_pct(tm['losers_that_were_green'])} of losers were green first." if tm.get("losers_that_were_green") is not None else "",
                        counterfactual=f"A trailing lock on peak gains would have kept roughly ~{_usd(lock)} over {days} days (approximate: peak size × price move).",
                        usd=lock, window=f"{days}d", cta="A ratcheting stop locks the peak without capping the run."))
    # 5. chasing
    if tm and tm.get("chased_n", 0) >= 3 and tm.get("chased_realized", 0) < -50 and (tm.get("calm_pf") or 0) > (tm.get("chased_pf") or 0):
        out.append(dict(agent="Smart money", title=f"Entries after a ≥3% move lose money — {tm['chased_n']} of {tm['n']} trades",
                        evidence=f"Those trades realized {_usd(tm['chased_realized'])} (profit factor {tm['chased_pf']:.1f}) vs {min(tm['calm_pf'], 9.9):.1f} when you entered before the move.",
                        counterfactual=f"Skipping entries that had already run ≥3% would have kept ~{_usd(-tm['chased_realized'])} over {days} days.",
                        usd=-tm["chased_realized"], window=f"{days}d", cta="Enter with the flow, not after it — a signal-driven entry does this."))
    # 6. liquidations
    if tr.get("liquidations"):
        half = -tr["liquidation_loss"] / 2
        out.append(dict(agent="Risk guard", title=f"{tr['liquidations']} liquidation(s) cost {_usd(-tr['liquidation_loss'])}",
                        evidence="A liquidation surrenders the whole margin plus the liquidation fee.",
                        counterfactual=f"A stop halfway to liquidation would have kept roughly ~{_usd(half)} of it.",
                        usd=half, window=f"{days}d", cta="A hard stop is the cheapest insurance there is."))
    # 7. sizing — oversized losers
    comp = [e for e in closed if not e.get("truncated") and e["peak_notional"] > 0]
    lw = [e["peak_notional"] for e in comp if e["win"]]
    if len(lw) >= 3:
        m = statistics.median(lw); over = [e for e in comp if not e["win"] and e["peak_notional"] > 1.5 * m]
        saved = sum(-e["realized"] * (1 - m / e["peak_notional"]) for e in over if e["realized"] < 0)
        if saved > 50 and len(over) >= 2:
            out.append(dict(agent="Leak finder", title=f"{len(over)} losers were sized >1.5× your median winner",
                            evidence=f"Median winner {_usd(m)} notional; those losers averaged {_usd(statistics.mean(e['peak_notional'] for e in over))}.",
                            counterfactual=f"Sizing them at your median would have kept ~{_usd(saved)} over {days} days.",
                            usd=saved, window=f"{days}d", cta="Size by conviction, not by frustration — a fixed-fraction rule does this."))
    out.sort(key=lambda l: -l["usd"])
    return out


def _funding_after(rows, closed, window_start, hours):
    """Funding paid (negative usdc) on payments that landed more than `hours` after the episode holding
    that coin opened — the part of the funding bill that hold time alone would have avoided."""
    opens = collections.defaultdict(list)
    for e in closed:
        opens[e["coin"]].append((e["open_time"], e["close_time"] or 4e12))
    paid = 0.0
    for x in rows or []:
        if x["time"] < window_start:
            continue
        d = x["delta"]; u = float(d["usdc"])
        if u >= 0:
            continue
        for o, c in opens.get(d["coin"], []):
            if o + hours * 3.6e6 <= x["time"] <= c:
                paid += -u; break
    return paid


# ---------------------------------------------------------------- where the edge is
HOLD_BUCKETS = (("< 4h", 0, 4), ("4–24h", 4, 24), ("1–3d", 24, 72), ("> 3d", 72, 1e9))


def best_setups(closed, tm_rows):
    """Coin × side on every closed trade (realized is realized); hold buckets and entry style only on
    fully observed ones (they need the true open and close)."""
    comp = [e for e in closed if e.get("complete")]
    groups = collections.defaultdict(list)
    for e in closed:
        groups[("coin", f"{e['coin']} {e['direction'].lower()}s")].append(e)
    for e in comp:
        for label, lo, hi in HOLD_BUCKETS:
            if lo <= e["hold_h"] < hi:
                groups[("hold", f"{e['direction'].lower()}s held {label}")].append(e)
    chased = {(r["coin"], r["open_time"]) for r in (tm_rows or []) if r["chased"]}
    for e in comp:
        groups[("entry", "entries after a ≥3% move" if (e["coin"], e["open_time"]) in chased else "entries before the move")].append(e)
    rows = []
    for (kind, label), eps in groups.items():
        if len(eps) < 3:
            continue
        w = sum(e["realized"] for e in eps if e["win"]); l = -sum(e["realized"] for e in eps if not e["win"])
        pf = (w / l) if l else (float("inf") if w else None)
        rows.append(dict(kind=kind, label=label, n=len(eps), wins=sum(1 for e in eps if e["win"]), realized=sum(e["realized"] for e in eps), profit_factor=pf))
    best = sorted([r for r in rows if r["profit_factor"] and r["profit_factor"] >= 1.5 and r["realized"] > 0], key=lambda r: -r["realized"])
    worst = sorted([r for r in rows if r["profit_factor"] is not None and r["profit_factor"] < 1.0], key=lambda r: r["realized"])
    return dict(best=best[:3], worst=worst[:2], all=rows)


def families(closed, tm, tr):
    """The catalog vocabulary the edge maps to, for the discover handoff."""
    out = []
    pre = (tm or {}).get("pre24_median")
    if pre is not None and pre > 0.01:
        out.append("trend_following" if (tm.get("give_back_median") or 1) < 0.5 else "breakout_momentum")
    if pre is not None and pre < -0.01:
        out.append("contrarian_fade")
    top = next(iter(tr.get("coins") or {}), None)
    if top and tr["coins"][top]["volume_share"] >= 0.5:
        out.append("single_market")
    return out or ["trend_following"]
