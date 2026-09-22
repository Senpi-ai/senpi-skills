#!/usr/bin/env python3
"""The desk's judgement layer: six 0–100 dimensions with one-line explanations, the quant score, the
archetype, flag chips, the verdict, the leaks ranked by counterfactual dollars, and where the edge is.
Every number here is a transparent function of the metrics — the formulas are in references/methodology.md."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import collections
import statistics

import timing

WEIGHTS = {"risk": 0.25, "consistency": 0.20, "timing": 0.15, "cost": 0.15, "market_fit": 0.15, "sizing": 0.10}


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _pct(x, d=0):
    return f"{100 * x:.{d}f}%"


def _bp(rate):
    """A fee rate in basis points. `-0.0 bp` is what `f"{x*1e4:.1f}"` prints for a rate that is zero
    or a hair negative, and a book on a zero-maker-fee tier hits that every time — it read as a typo
    in a sentence whose whole job is to reproduce the dollars beside it. A real rebate says so."""
    bp = (rate or 0.0) * 1e4
    if bp <= -0.05:
        return f"{abs(bp):.1f} bp rebate"
    return f"{max(0.0, bp):.1f} bp"


def _lower_first(s):
    """Lower the first letter so a sentence reads on after "and …" — unless the first word is a
    TICKER. `s[0].lower()` turned the market-fit line "ETH, AVAX, TAO, xyz:MU — short into an
    up-trend" into "eTH, AVAX, …" on the headline, which is the most-read sentence on the page.
    Second character lower-case means an ordinary capitalised word; upper means an acronym.
    (Seen on a live 1.25.1 run, M408027.)"""
    return (s[0].lower() + s[1:]) if (s and s[1:2].islower()) else s


def _usd(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _pct_cost(x):
    """Cost shares under 1% keep a decimal — "0%" is a claim, "0.4%" is a measurement."""
    if 0 < x < 0.01:
        return f"{100 * x:.1f}%" if x >= 0.0005 else "<0.1%"
    return _pct(x)


# ---------------------------------------------------------------- six dimensions
def _n(d, key, default=0.0):
    """`dict.get(k, default)` only defaults when the key is ABSENT. Several timing and market fields
    are present-and-NULL — a median of nothing, a share with no sample, a funding rate with no book —
    so `tm.get("chased_share", 0) >= 0.5` raised TypeError on a real wallet (0x767a…0ace, one closed
    trade, no entry had a 24h prior). This defaults for missing AND null."""
    v = (d or {}).get(key)
    return default if v is None else v


def dim_timing(tm, sm, cov=None):
    s, lines = 70.0, []
    if not tm or (tm.get("n") or 0) < 5:
        # abstain, do not score: a dimension that measured nothing must not vote. Scoring 60 here
        # propped a book with 0 closed trades to 56/100 while it sat $1.27M underwater on a naked
        # position. dimensions() re-normalises over the dimensions that could measure.
        return None, "Not enough fully observed trades to judge timing."
    if tm and tm.get("n"):
        cs = tm.get("chased_share") or 0.0
        s -= cs * 40
        cpf, kpf = tm.get("chased_pf"), tm.get("calm_pf")
        if _n(tm, "chased_n") >= 3 and cpf is not None and kpf is not None and cpf < 1.0 < kpf:
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
    return clamp(s), max(lines, key=lambda x: x[0])[1]


def dim_risk(tr, book, dd):
    s, lines = 85.0, []
    hr = tr.get("hold_ratio")
    if hr and hr > 1.2:
        s -= min(30, (hr - 1) * 15); lines.append((3 if hr >= 2 else 2, f"You hold losers {hr:.1f}× longer than winners (median {tr['hold_losers_h']:.1f}h vs {tr['hold_winners_h']:.1f}h)."))
    n = len(book["positions"])
    if n:
        naked = len(book["naked"]); s -= 25 * naked / n
        if naked:
            lines.append((4, f"{naked} of {n} open positions {'has' if naked == 1 else 'have'} no stop at all — {', '.join(book['naked'])}."))
        near = [p for p in book["positions"] if p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5]
        if near:
            s -= 15; p = min(near, key=lambda p: p["liq_distance_pct"])
            lines.append((5, f"{p['coin']} {p['side'].lower()} {p['leverage']}× sits {p['liq_distance_pct']:.1f}% from liquidation."))
    if tr.get("liquidations"):
        s -= min(30, 10 * tr["liquidations"]); lines.append((4, f"{tr['liquidations']} liquidation(s) in 90 days cost {_usd(tr['liquidation_loss'])}."))
    mu = book.get("margin_utilization")
    if mu and mu > 0.6:
        s -= min(20, (mu - 0.6) * 50); lines.append((2, f"Margin used is {_pct(mu)} of account value — little cushion for a bad hour."))
    if dd and dd.get("dd_pct"):
        # The old cap was min(20, dd_pct * 60), which flattened at 33%: a book that gave back a
        # third and a book that went to ZERO were penalised identically, and a wiped-out account
        # scored 65/100 on "Risk management". Drawdown here is built from cumulative P&L and is
        # transfer-immune, so dd_pct = 1.0 really does mean the equity at risk was lost — the one
        # outcome the dimension exists to catch.
        s -= min(75, dd["dd_pct"] * 75)
        if dd["dd_pct"] >= 0.9:
            # dd_pct is scale-relative: giving back $5k of a $5.4k account is 93%. Saying "the
            # account went to zero — a full loss of the equity at risk" about a book that ENDED the
            # window up $25,008 and holds $28,420 is false, and 1.16.1 promoted this sentence into
            # the headline, so it sat directly beside "Net $25,008 on the ledger".
            # The penalty stands either way — they really did nearly lose it — but a drawdown that
            # was recovered is a different sentence from one that was not.
            if (tr.get("ledger_net") or 0) > 0:
                lines.append((4, f"A {_pct(dd['dd_pct'])} drawdown inside the window — nearly the whole "
                                 f"book at the trough — though it ended up {_usd(tr['ledger_net'])}."))
            else:
                lines.append((6, "The account went to zero inside the window — a full loss of the equity at risk."))
        elif dd["dd_pct"] >= 0.25:
            lines.append((2, f"Max drawdown {_pct(dd['dd_pct'])} of equity over the window."))
    if not lines:
        # "Stops in place, losers cut faster than winners" asserts three findings. On a book with no
        # open positions and one closed trade there is nothing to have found — and it scored 76.
        if not n and (tr.get("trades") or 0) < MIN_PATTERN_TRADES:
            return None, "No open positions and too few closed trades to judge risk."
        lines.append((0, "Stops in place, losers cut faster than winners, no liquidations."))
    return clamp(s), max(lines, key=lambda x: x[0])[1]


def dim_cost(tr):
    cr, ts = tr.get("cost_ratio"), tr.get("taker_share")
    if cr is not None:
        s = 100 - min(70, cr * 150)
        if cr > 1:
            costs = abs(tr.get("fees") or 0) + max(0.0, -(tr.get("funding") or 0))
            line = f"Costs exceeded what you made: {_usd(costs)} against {_usd(tr.get('gross_income') or tr['gross_realized'])} of trade P&L{' and funding' if (tr.get('funding') or 0) > 0 else ''}."
        elif (tr.get("funding") or 0) > 0:
            line = f"Fees took {_pct_cost(cr)} of what you made ({_usd(tr['fees'])} on {_usd(tr['gross_realized'])} of trade P&L plus {_usd(tr['funding'])} of funding collected)."
        else:
            line = f"Fees + funding ate {_pct_cost(cr)} of gross P&L ({_usd(tr['fees'])} fees, {_usd(-tr['funding'])} funding on {_usd(tr['gross_realized'])} gross)."
    else:
        # cost_ratio is None because gross P&L was not positive. That does NOT make costs
        # unmeasurable — it makes them the worst case: a book that lost money and paid to do it.
        # "—" hid the clearest cost problem on the desk (a book paying $7,068 of fees while losing
        # $16,969 read as a blank), and an abstaining dimension let the other five re-normalise the
        # headline UP.
        #
        # The base is |net| — the money actually lost — NOT |gross|. Against |gross| a book that
        # lost $180,996 on $7,143 of fees scores 94/100, which is not cost efficiency, just a large
        # loss. Against |net| the question is the one that matters: how much of what you lost went
        # to cost rather than to bad trades.
        # The ledger is what the user lost, so it is the denominator they would use. Realized-only
        # net puts a book whose result lives in unrealized P&L against the wrong base (#718 Q2);
        # with B1 fixed, ledger_net is finally a 90-day number and safe to prefer.
        net = float(tr.get("ledger_net") if tr.get("ledger_net") is not None else (tr.get("net") or 0.0))
        # NOT abs(): a maker REBATE is negative fees, and abs() turned money earned into an
        # equal-sized cost. 0.51% of books with 5+ trades run a net rebate, and they are exactly the
        # sophisticated books worth reading correctly.
        costs = max(0.0, float(tr.get("fees") or 0)) + max(0.0, -(tr.get("funding") or 0))
        if abs(net) < 1 and costs < 1:
            return None, "Not measurable this window: no trading result, and no costs to price against it."
        drag = costs / max(abs(net), 1.0)
        s = 100 - min(70, drag * 150)
        # a rebate is money EARNED — say so, rather than printing it as a bill
        _fee = float(tr.get("fees") or 0)
        _fee_txt = f"{_usd(-_fee)} EARNED in maker rebates" if _fee < 0 else f"{_usd(_fee)} of fees"
        # Above 100% the share framing stops parsing: "513% of what you lost was cost, not bad
        # trades" is arithmetically true and reads as a broken number. What it actually means is
        # stronger and simpler — costs are bigger than the entire loss, so the trading was ahead
        # before them. The `cost_ratio > 1` branch above already says it that way; say it here too.
        # (Live 1.24.1 run, 0xea66…61ee: $46,786 fees + $125,909 funding on a -$33,647 result.)
        _costs_txt = _fee_txt + (f" and {_usd(-tr['funding'])} of funding" if (tr.get("funding") or 0) < 0 else "")
        if drag > 1.0 and net < 0:
            line = (f"Costs are bigger than the loss itself: {_costs_txt} against a {_usd(net)} net "
                    f"result — the trading was {_usd(costs + net)} ahead before costs.")
        else:
            line = (f"{_pct_cost(drag)} of what you lost was cost, not bad trades: {_fee_txt}"
                    + (f" and {_usd(-tr['funding'])} of funding" if (tr.get("funding") or 0) < 0 else "")
                    + f" against a {_usd(net)} net result"
                    + (f" — funding paid you {_usd(tr['funding'])}" if (tr.get("funding") or 0) > 0 else "") + ".")
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
    if comp:
        med = statistics.median(e["peak_notional"] for e in comp)
        big = [e for e in comp if e["peak_notional"] > 2 * med]
        if len(big) >= 3 and all(e["win"] for e in big):      # conviction sizing that pays is the point of this dimension
            s += 10; lines.append((1, f"Your biggest positions are your best — {len(big)} of {len(big)} above 2× the median size were winners."))
    ex = book.get("exposure_over_equity")
    if ex and ex > 5:
        s -= min(20, (ex - 5) * 3); lines.append((2, f"Gross exposure is {ex:.1f}× account value right now."))
    if book.get("largest_share") and book["largest_share"] > 0.6 and len(book["positions"]) > 1:
        s -= 10; lines.append((1, f"One position is {_pct(book['largest_share'])} of the book."))
    if not lines:
        if len(comp) < MIN_PATTERN_TRADES:
            # too few closed trades and nothing unusual in the live book: there is no sizing
            # BEHAVIOUR to read. Claiming "sizes are consistent and exposure is proportionate"
            # asserts the thing that was not measured — it scored 85 off a single trade.
            return None, ("No closed trades to judge sizing on, and the open book shows nothing unusual."
                          if not comp else
                          f"Only {len(comp)} closed trade{'s' if len(comp) > 1 else ''} — too few to read sizing from.")
        lines.append((0, "Sizes are consistent and exposure is proportionate."))
    return clamp(s), max(lines, key=lambda x: x[0])[1]


def dim_consistency(tr, pnl_curve):
    n = tr.get("trades") or 0
    wr, pf = tr.get("win_rate"), tr.get("profit_factor")
    if pf is None and wr is None:
        return None, "No closed trades in the window — nothing to judge consistency on."
    pf_ = 3.0 if pf in (None, float("inf")) else min(pf, 3.0)
    s = 50 + (pf_ - 1) * 20 + ((wr - 0.5) * 40 if wr is not None else 0)
    s = 50 + (s - 50) * (n / (n + 10.0)) if n else 50.0
    pfs = "∞" if pf == float("inf") else (f"{pf:.1f}" if pf is not None else "n/a")
    # The profit factor already had an n/a path and the win rate did not, so the one input shape the
    # guard above lets through — a measurable pf with no win rate — reached `_pct(None)` and raised
    # a TypeError, which is not an HLError, so desk.py prints a traceback and no desk. Same failure
    # mode as the all-losing book in #733; found while fixing item 11.
    line = f"Win rate {_pct(wr) if wr is not None else 'n/a'}, profit factor {pfs} across {n} trade{'s' if n != 1 else ''}"
    if tr.get("payoff_ratio"):
        line += f" — average winner {tr['payoff_ratio']:.1f}× the average loser"
    line += "."
    if n and n < 15:
        line += f" Only {n} trade{'s' if n != 1 else ''}: read this loosely."
    return clamp(s), line


def dim_market(book, mf):
    s, lines = 65.0, []
    if not mf or not book["positions"]:
        # nothing is held, so there is no fit to score. Returning 60 let a flat book carry a
        # measured-looking sixth of the headline on a dimension with no input at all.
        return None, "No open positions to fit against the market."
    s += min(25, 10 * mf["with_market"]) - min(45, 15 * mf["against"])
    ag = [r for r in mf["rows"] if r["fit"].startswith("AGAINST")]
    if ag:
        lines.append((3, f"{', '.join(r['coin'] for r in ag)} — {ag[0]['side'].lower()} into {'an' if str(ag[0]['trend']).upper() == 'UP' else 'a'} {str(ag[0]['trend']).lower()}-trend."))
    av = book.get("account_value") or 0
    fpd = mf.get("funding_per_day") or 0.0
    if fpd < 0 and av:
        yr = -fpd * 365 / av
        # capped at 20 this saturated at 40%/yr: a book paying 40% of equity a year in funding and
        # one paying 240% scored identically. Same shape as the drawdown cap fixed in 1.9.1.
        s -= min(45, yr * 50)
        lines.append((2, f"{mf['stance'].capitalize()} into {'positive' if book['net_exposure'] > 0 else 'negative'} funding — paying ~{_usd(-fpd)}/day to hold ({_pct(yr)} of your EQUITY a year — leverage makes this bigger than the headline rate on notional)."))
    elif fpd > 0:
        lines.append((1, f"Your book collects ~{_usd(fpd)}/day in funding at today's rates."))
    if not lines:
        lines.append((0, f"{mf['with_market']} of {len(mf['rows'])} positions sit with the trend; funding is near flat."))
    return clamp(s), max(lines, key=lambda x: x[0])[1]


def dimensions(tr, book, dd, tm, mf, sm, closed, pnl_curve):
    d = {}
    for key, (score, line) in {
        "timing": dim_timing(tm, sm, tr.get("coverage")), "risk": dim_risk(tr, book, dd), "cost": dim_cost(tr), "sizing": dim_sizing(tr, book, closed),
        "consistency": dim_consistency(tr, pnl_curve), "market_fit": dim_market(book, mf)}.items():
        d[key] = dict(score=None if score is None else round(score), line=line)
    # A dimension that could not be measured must not vote. Re-normalise over the ones that could,
    # rather than scoring an unmeasured dimension and letting its default move the headline.
    live = {k: w for k, w in WEIGHTS.items() if d[k]["score"] is not None}
    tot = sum(live.values())
    # Below half the dimensions there is no book to score. 0x31a7…7549 — one trade, net -$91 — had
    # four of six abstain and still printed 66/100, re-normalised onto cost (90, off $0 of fees) and
    # consistency (48, off that single trade). A confident headline from two noisy inputs is the
    # same failure as a confident dimension from no input.
    if len(live) < MIN_DIMENSIONS:
        return d, None
    quant = (sum(live[k] * d[k]["score"] for k in live) / tot) if tot > 0 else 0
    return d, round(quant)


# ---------------------------------------------------------------- archetype, flags, verdict
def archetype(tr, book, tm, act, opened=None):
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
    elif pre is not None and pre > 0 and _n(tm, "give_back_median", 1) < 0.45:
        noun = "trend rider"
    else:
        noun = "opportunist"
    if (tr.get("trades") or 0) < 5:
        n_t = tr.get("trades") or 0; fills = act.get("fills") or 0
        noun = (f"thin record ({n_t} closed trade{'s' if n_t != 1 else ''}, {fills:,} fills)" if fills >= 500       # a whale with one round trip is not "early days"
                else f"early days ({n_t} closed trade{'s' if n_t != 1 else ''})")
    bias = ""
    n_closed = tr.get("trades") or 0; n_open = len(book["positions"])
    longs = (tr.get("long_share") or 0) * n_closed + sum(1 for p in book["positions"] if p["side"] == "LONG")
    if n_closed + n_open >= 5:
        ls = longs / (n_closed + n_open)
        bias = "long-only " if ls >= 0.9 else ("short-only " if ls <= 0.1 else "")
    if noun.startswith(("thin record", "early days")):
        return f"{adj} book · {bias}{noun}"
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
    if _n(tm, "chased_share") >= 0.5:
        out.append("CHASING")
    if mf and _n(mf, "funding_per_day") < 0 and book.get("account_value") and -mf["funding_per_day"] * 365 / book["account_value"] > 0.1:
        out.append(f"PAYING FUNDING {_usd(-mf['funding_per_day'])}/DAY")
    for k in ("consistency", "risk", "activity"):
        v = (labels or {}).get(k)
        if v and v.upper() in ("CHOPPY", "STREAKY", "SNIPER", "DEGEN"):
            out.append(v.upper())
    return out


MIN_VERDICT_TRADES = 5   # below this, cost / timing / consistency cannot carry the headline
MIN_DIMENSIONS = 3       # fewer measurable dimensions than this and there is no book to score
NOISE_SHARE = 0.15       # a lever keeping less than this share of what it saves is a coin flip
MIN_PATTERN_TRADES = 5   # a hold-time or give-back leak is a pattern claim: it needs a sample


def verdict(tr, book, dims, leaks):
    pf, pr = tr.get("profit_factor"), tr.get("payoff_ratio")
    n = tr.get("trades") or 0
    negative = False     # a negative strength joins its weakness with "and", not "but"
    if pf and pf != float("inf") and pf >= 1.5 and n >= 10:
        strength = f"Real edge — profit factor {pf:.1f} on {n} trades"
    elif pr and pr >= 2 and n >= 10:
        strength = f"You let winners run ({pr:.1f}× payoff)"
    elif tr.get("win_rate") and tr["win_rate"] >= 0.55 and n >= 10:
        strength = f"You pick well — {_pct(tr['win_rate'])} win rate"
    elif (tr.get("ledger_net") is not None and tr["ledger_net"] > 0 and abs(tr["ledger_net"]) > 2 * abs(tr.get("net") or 0)):
        strength = f"Net {_usd(tr['ledger_net'])} on the ledger over the window (open book and funding included)"
    elif tr.get("ledger_net") is not None and tr["ledger_net"] < 0:
        strength = f"Down {_usd(-tr['ledger_net'])} on the ledger over the window (open book and funding included)"; negative = True
    elif (tr.get("net") or 0) > 0:
        strength = f"Net positive ({_usd(tr['net'])} realized over the window)"
    else:
        strength = "No edge shows up in this window"; negative = True
    # the weakest dimension carries the headline only when it is material: enough trades behind it, and costs
    # that are a real share of the result — $2 of fees on a $222 window is not a leak
    costs = abs(tr.get("fees") or 0) + max(0.0, -(tr.get("funding") or 0))
    base = abs(tr["ledger_net"] if tr.get("ledger_net") is not None else (tr.get("net") or 0))

    def material(k):
        # A dimension that could not be measured has no score to rank and cannot be "the weakest" —
        # nothing measured it. This guard has to come FIRST: sorted() runs before the filter below,
        # so a None reaching the key function raises TypeError and takes the whole desk down before
        # it renders a line. That shipped in 1.4.3 and crashed every run.
        if dims[k]["score"] is None:
            return False
        if k in ("cost", "timing", "consistency") and n < MIN_VERDICT_TRADES:
            return False
        if k == "cost" and base and costs < 0.05 * base:
            return False
        return True
    ranked = sorted([k for k in dims if material(k)], key=lambda k: dims[k]["score"])
    weakest = ranked[0] if ranked else None
    weak_line = imperative = None
    if weakest is not None and dims[weakest]["score"] < 60:
        # Use the dimension's OWN sentence. Keying a fixed phrase off the dimension NAME asserted a
        # reason the dimension had just denied: "your entries are late or chased" printed directly
        # above "Entries are not systematically late or chased over this window." Timing scores low
        # for give-back too, and the headline was naming the wrong half of it.
        _own = (dims[weakest].get("line") or "").strip().rstrip(".")
        weak_line = _lower_first(_own) if _own else {
            "risk": "you're carrying unprotected risk" if book["naked"] else "the risk side is where it leaks",
            "cost": "execution and funding are eating the gains" if (tr.get("funding") or 0) < 0 else "execution is eating the gains",
            "timing": "the timing side is where it leaks", "sizing": "sizing is working against you",
            "consistency": "the results are not repeatable yet",
            "market_fit": "the book is fighting the market it sits in"}[weakest]
        imperative = {"risk": "Fix the risk first.", "cost": "Cut the costs first.", "timing": "Fix the timing first.", "sizing": "Fix the sizing first.",
                      "consistency": "Build the sample before scaling.", "market_fit": "Get on the right side of the regime first."}[weakest]
    near = [p for p in book["positions"] if p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5]
    if near:
        p = min(near, key=lambda p: p["liq_distance_pct"])
        weak_line = f"{p['coin']} sits {p['liq_distance_pct']:.1f}% from liquidation with {'no' if p['stop_covered_share'] == 0 else 'a partial'} stop"
        imperative = "Protect that position today."
    av = book.get("account_value") or 0
    if av and (book.get("unrealized") or 0) < -0.2 * av:
        weak_line = f"the open book is {_usd(-book['unrealized'])} under water ({_pct(-book['unrealized'] / av)} of equity) with {len(book['naked'])} of {len(book['positions'])} positions unprotected"
        imperative = "Decide the exits before the market does."
    if weak_line:
        return f"{strength} — {'and' if negative else 'but'} {weak_line}. {imperative}"
    if n < MIN_VERDICT_TRADES:
        # "the live book is where the desk earns its keep" only holds if there IS a live book. On
        # 0x31a7…7549 it sat above a risk line reading "No open positions".
        tail = ("the live book is where the desk earns its keep today."
                if (book or {}).get("positions") else
                "and with nothing open, there is nothing for the desk to protect right now.")
        return f"{strength} — only {n} closed trade{'s' if n != 1 else ''} in the window, so the record is too thin to grade; {tail}"
    return f"{strength} — nothing in the record is leaking badly; the gains are in the details below."


# ---------------------------------------------------------------- leaks
def levers(rows, closed, tm=None, funding_late=0.0):
    """Every process fix the desk can price, each as a book-wide total that NETS its own costs.

    This is the single source both `leaks()` and `recoverable()` read. They used to compute the same
    fixes separately and disagreed on the page: a trailing lock was listed at $6,083 (the most
    conservative setting) while the quotable headline credited the same rule with $9,099 (the best
    setting, charged), and the sizing leak was listed at $53,792 against a charged value of $8,182 —
    so the desk named a "biggest leak" that was not the biggest lever once both were measured the
    same way. Reported by @shnoodles on #718.

    Each entry: kind, label (a finished sentence fragment), total (charged), gross (before the trades
    it costs), vals (per-trade contributions, for concentration).
    """
    tm, rows, out = tm or {}, rows or [], []

    grid = [("lock", f"{a:.2f}/{sh:.1f}", f"a trailing stop that arms at +{a:.0%} and keeps {sh:.0%} of the peak")
            for a, sh in timing.LOCK_GRID]
    grid += [("cut", f"{h:.0f}", f"closing anything still open after {h:.0f}h") for h in timing.CUT_GRID_H]
    for grp, k, label in grid:
        st = (((tm.get(grp) or {}).get("settings")) or {}).get(k) or {}
        # a lever is a pattern claim and needs the same sample the leaks require. Without this the
        # quotable headline was built from whatever setting scored highest, however few trades it
        # engaged on: on 0xb699…392e the winner was "closing anything still open after 48h" with
        # n=2, and it put $1.87M in front of a reader who lost $230,596.
        if (st.get("n") or 0) < MIN_PATTERN_TRADES:
            continue
        vals = [float(v) for v in ((t.get(f"{grp}_cf") or {}).get(k) for t in rows) if v is not None]
        total, gross = float(st.get("total") or 0.0), sum(v for v in vals if v > 0)
        # a rule that hands back almost everything it saves is a coin flip, not an edge. On
        # 0xccd2…c8a3 a time-cut netted $165 out of $5,701 saved — 3% — and was listed as a fix.
        # It used to `continue` here, which removed the setting from the family ENTIRELY — so
        # best_lever's median ran over a set the noise gate had already filtered BY SCORE, and a
        # family with one survivor quoted that survivor as its "median". Selection has to happen
        # over every adequately-sampled setting; the noise gate then applies to whatever selection
        # returns. (@danielmbirochi, #718, item 8.)
        out.append(dict(kind=grp, key=k, label=label, total=total, gross=gross,
                        vals=vals, n=int(st["n"]), noisy=bool(gross > 0 and total < NOISE_SHARE * gross)))

    m = None
    winners = [e["peak_size"] * e["entry_vwap"] for e in (closed or [])
               if e.get("win") and not e.get("truncated") and (e.get("peak_size") or 0) > 0
               and (e.get("entry_vwap") or 0) > 0]
    if len(winners) >= 3:
        m = statistics.median(winners)
    if m:
        over = [t for t in rows if (t.get("notional") or 0) > 1.5 * m]
        vals = [-t["realized"] * (1 - m / t["notional"]) for t in over]
        if len(vals) >= MIN_PATTERN_TRADES:
            _tot, _gr = sum(vals), sum(v for v in vals if v > 0)
            out.append(dict(kind="size", key="1.5x", label="capping size at your median winner",
                            total=_tot, gross=_gr, vals=vals,
                            n=len(over), median_winner=m, noisy=bool(_gr > 0 and _tot < NOISE_SHARE * _gr),
                            losers=len([t for t in over if not t.get("win")])))

    # Funding belongs here, competing with the exits rather than sitting outside them. It was left
    # out because capping a funding-paying hold IS the time-cut — but the union only ever credits
    # ONE lever, so there was never a double-count to prevent, and leaving it out made the headline
    # SMALLER than a leak listed under it: 0x767a…0ace quoted $99,228 above a $114,566 funding leak.
    # No sample gate: funding paid is a measured cost, like fees, not a pattern estimated from a
    # handful of trades.
    _cut24_s = (((tm.get("cut") or {}).get("settings")) or {}).get("24") or {}
    if funding_late > 50 and (_cut24_s.get("n") or 0) >= MIN_PATTERN_TRADES:
        # A 24h cap does not only stop the funding bill — it CLOSES the position, and the P&L
        # consequence of closing at 24h is exactly the 24h time-cut. Crediting the funding saved and
        # charging nothing for the exits it forces was the fourth survivorship bug (@0xsarvesh #718);
        # the first three were fixed in #712.
        # CHARGE only, never credit. A 24h cap forces exits, and if those exits cost money the
        # funding saving has to carry it — that was the survivorship gap. But when they GAIN, that
        # gain is the time-cut lever's, and adding it here summed two levers, which is the exact
        # double-count the union exists to prevent. It made a $122,440 funding bill read as a
        # $4,083,959 saving.
        # The gate above guarantees a sample; without one the credit used to ship uncharged, which
        # is the fourth survivorship bug wearing a different hat. (@danielmbirochi, #718, item 9.)
        _charge = min(0.0, float(_cut24_s.get("total") or 0.0))
        # The lever is legitimately worth funding + the P&L of the exits it forces. The LEAK beside
        # it is titled "you paid $X in funding", so quoting the combined figure there read as saving
        # 6x the bill ($502,094 against $80,256). Carry the two parts separately and let the leak
        # say which is which.
        out.append(dict(kind="funding", key="24h", label="capping holds that pay funding at 24h",
                        total=float(funding_late) + _charge, gross=float(funding_late),
                        funding_saved=float(funding_late), exit_effect=_charge, vals=[],
                        n=len(rows)))

    # n>=3 and no noise gate: every other pattern lever needs MIN_PATTERN_TRADES and has to keep
    # NOISE_SHARE of what it saves, and this one could win best_lever on three trades.
    # (@danielmbirochi, #718, item 7.)
    if (tm.get("chased_n") or 0) >= MIN_PATTERN_TRADES and (tm.get("chased_realized") or 0) < -50 \
            and (tm.get("calm_pf") or 0) > (tm.get("chased_pf") or 0):
        vals = [-float(t["realized"]) for t in rows if t.get("chased") and (t.get("realized") or 0) < 0]
        _tot, _gr = -float(tm["chased_realized"]), sum(vals)
        out.append(dict(kind="chase", key="3pct", label="skipping entries after a >=3% move",
                        total=_tot, gross=_gr, vals=vals, noisy=bool(_gr > 0 and _tot < NOISE_SHARE * _gr),
                        n=int(tm["chased_n"])))
    return out


def best_lever(lv, kind=None):
    """The lever to quote. MEDIAN within a family, max across families.

    Taking the max over all of them was a grid search reported as a finding: 3 lock settings and 3
    cut settings are six in-sample estimates of the same underlying thing — exit discipline — and
    the largest of six is biased high by construction. Charging each setting fixed the per-trade
    asymmetry (#712); the SELECTION step was still optimistic and nothing measured it (@0xsarvesh,
    #718). methodology.md has described the median-of-robust figure since 1.0.

    Across families the max is right: a size cap, a funding cap and an exit rule are different
    fixes, not six readings of one.
    """
    c = [x for x in (lv or []) if kind is None or x["kind"] == kind]
    if not c:
        return None
    best = None
    for k in {x["kind"] for x in c}:
        fam = sorted((x for x in c if x["kind"] == k), key=lambda x: x["total"])
        rep = fam[(len(fam) - 1) // 2]              # lower median: ties go to the more conservative
        if rep.get("noisy"):                        # the family's typical setting is a coin flip
            continue
        if best is None or rep["total"] > best["total"]:
            best = rep
    return best if best and best["total"] > 0 else None


def recoverable(rows, closed, tr, tm=None, lv=None):
    """One honest number: what a senpi runtime would have kept over this window.

    The leaks each price a different fix over the SAME trades, so they must never be added — one bad
    trade that was oversized, chased, held too long AND gave back its peak appears in several of
    them. On a real book the four printed leaks summed to $68k against $65k of actual losses.

    This is the most conservative honest answer: **the single best change, applied to every trade.**
    Not a sum of fixes, and not the best fix per trade either — letting each trade pick its own lever
    is hindsight fitting at a finer grain, and a user runs one rule, not a different one per
    position. Each lever is a book-wide total that nets its own costs:

    * **exits** — every trailing-lock and time-cut setting, charged on the trades it would have hurt
      as well as the ones it saved.
    * **sizing** — a cap at the median winner's size across every oversized trade, winners included,
      where the term is negative because the cap gives up that upside.
    * **entries** — skipping chased entries, available only when this book's chased entries really
      did do worse than its calm ones, and worth only what the chase leak itself claims.

    Fees are added on top as the one genuinely independent fix. Funding is excluded: capping a
    funding-paying hold is the same action as the time-cut.

    Returns `concentration` alongside the total, because the shape matters as much as the size. On
    that same book three trades were 83% of it and one was 50% — "you leak $46k across your book"
    would have been true arithmetic and a false picture. `rule` is the finished sentence, not the
    grid key: the phrasing has one home, here, next to every other string the desk shows a user.
    """
    lv = levers(rows, closed, tm) if lv is None else lv
    best = best_lever(lv)
    rule = best["label"] if best else None
    vals = best["vals"] if best else []
    total = best["total"] if best else 0.0

    rows = rows or []
    losses = -sum(t["realized"] for t in rows if (t.get("realized") or 0) < 0)
    fees = max(0.0, float(tr.get("fee_recoverable") or 0.0)) if (tr.get("taker_share") or 0) >= 0.25 else 0.0

    # Concentration answers "is this driven by a few trades or many", so the denominator is the sum
    # of everything that CONTRIBUTED — the positive per-trade terms plus fees, the most diffuse
    # contributor there is. It must NOT be the lever total, which is net of the trades the rule cost
    # money on: a single trade could then be reported as 111% of the number (#718).
    pos = sorted((v for v in vals if v > 0), reverse=True)
    denom = sum(pos) + fees
    conc = dict(top1=pos[0] / denom, top3=sum(pos[:3]) / denom, n_positive=len(pos)) if (denom > 0 and pos) else None

    return dict(usd=total + fees, fees=fees, n_trades=len(rows), rule=rule, concentration=conc,
                share_of_losses=((total + fees) / losses if losses > 0 else None))


def _cf_charged(l, days):
    """Charged value first, gross second. The gross figure is what the fix saves on the trades it
    helps; the charged one nets the trades it costs, and is the only one that can be quoted next to
    the headline without contradicting it."""
    g = l.get("gross") or 0.0
    s = f"Running {l['label']} over the whole book would have kept ~{_usd(l['total'])} over {days} days"
    return s + (f" — it saves {_usd(g)} on the trades it helps and gives some of that back on the ones it costs." if g > l["total"] + 1 else ".")


def leaks(tr, book, tm, funding_rows, closed, window_start, days, lv=None):
    out = []
    yr = 365.0 / days
    # charged lever totals, so a leak is listed at the value recoverable() would credit it with and
    # the ranking is by the same accounting as the headline (#718)
    ch = {x["kind"]: x for x in (lv or []) if not x.get("noisy")}
    # 1. costs — resting instead of crossing the spread
    if _n(tr, "fee_recoverable") >= 50 and (tr.get("taker_share") or 0) >= 0.25:
        out.append(dict(agent="Leak finder", title=f"{_pct(tr['taker_share'])} of your volume crossed the spread as a taker",
                        # On the indexed path `tr["fees"]` is discovery's CLOSED-trade fee and this is the
                        # fills-derived total over closed AND open positions, so the two differ — $5,021
                        # against $5,069 on 0xb699…392e. Both are right for what they measure; the page
                        # just has to say which, or a reader sees two numbers for "fees".
                        evidence=f"{_usd(_n(tr, 'fee_total_exec') or tr['fees'])} in fees across every fill in the window, on {_usd(tr['volume'])} of volume at {_bp(tr['fee_rate_taker'])} taker / {_bp(tr['fee_rate_maker'])} maker.",
                        counterfactual=f"Resting maker orders for the same fills would have kept ~{_usd(tr['fee_recoverable'])} over {days} days (≈{_usd(tr['fee_recoverable'] * yr)}/yr).",
                        usd=tr["fee_recoverable"], window=f"{days}d", cta=f"Execute through senpi and I'll rest your entries maker-first with a taker fallback — "
                            f"that's ~{_usd(tr['fee_recoverable'])} over {days} days (~{_usd(tr['fee_recoverable'] * yr)}/yr) "
                            f"you keep, on the same fills."))
    # 2. funding — hold time on funding-paying legs
    # _funding_after sums funding PAID; tr["funding"] is NET of funding collected elsewhere. Left
    # uncapped the leak read "You paid $123,764 in funding … would have kept ~$164,499" — a saving
    # larger than the cost in the same sentence.
    paid_late = ch["funding"]["total"] if "funding" in ch else \
        min(_funding_after(funding_rows, closed, window_start, 24.0), -float(tr.get("funding") or 0.0))
    if _n(tr, "funding") < -100 and paid_late > 50:
        worst = min(tr["coins"].items(), key=lambda kv: kv[1]["funding"])
        out.append(dict(agent="Market regime", title=f"You paid {_usd(-tr['funding'])} in funding over {days} days",
                        evidence=(f"{worst[0]} alone cost {_usd(-worst[1]['funding'])}"
                                  # the worst coin can exceed the NET total, because other coins collected —
                                  # printed bare that reads as an arithmetic error
                                  + (f" — more than the {_usd(-tr['funding'])} net, because other coins collected funding back"
                                     if -worst[1]["funding"] > -_n(tr, "funding") else "")
                                  + (f"; the book pays {_usd(-book['funding_per_day'])}/day at today's rates."
                                     if _n(book, "funding_per_day") < 0 else ".")),
                        counterfactual=(
                            f"A 24h cap on those holds would have kept ~{_usd(_fl['total'])} over {days} days — "
                            f"{_usd(_fl['funding_saved'])} of funding, and {_usd(_fl['exit_effect'])} from closing "
                            f"the positions that much earlier."
                            if (_fl := ch.get("funding")) and abs(_fl.get("exit_effect") or 0) > 50 else
                            f"A 24h cap on holds that pay funding would have kept ~{_usd(paid_late)} over {days} days."
                            if ch.get("funding") else
                            "Capping those holds at 24h also CLOSES them, and this window has too few holds past "
                            "24h to price what those exits would have done — so the desk does not put a number on "
                            "the fix. The funding above is what it actually cost."),
                        usd=paid_late if ch.get("funding") else 0.0, unpriced=not ch.get("funding"),
                        window=f"{days}d", cta="A funding-aware hold rule caps the cost without changing the thesis."))
    # 3. losers held too long — only when the time cut is robust
    cut_l = best_lever(lv, "cut")
    # `robust` is the pre-levers path: the median of every setting's total, sample gate or not. It
    # let the LEAK walk around every bar `levers()` applies. On 0xb699…392e the 24h setting had n=3
    # and 48h n=2, so the family was correctly declined — and the leak printed $341,779 from a
    # three-trade counterfactual while the headline beside it said $2,709. That is the same wallet
    # whose n=2 lever MIN_PATTERN_TRADES was written for, and the same "the list and the headline
    # disagree" shape @shnoodles reported. One table, one bar: if the lever is declined the claim is
    # unpriced, and only a leak with measured evidence of its own still has anything to say.
    cut = cut_l["total"] if cut_l else None
    _unpriced_cf = ("The desk does not put a number on this one: too few trades clear the sample bar for "
                    "a counterfactual to mean anything on this window. The pattern above is measured.")
    # The hold-ratio framing is the EVIDENCE for a time cut, not a precondition for one. Gating the
    # leak on it while the LEVER had no such gate meant a book that cuts losers 2.6x FASTER than
    # winners saw "~$180,892 recoverable — closing anything still open after 24h" as its headline
    # with no matching leak anywhere on the page. A winning lever must always be visible.
    _hold_evidence = (tr.get("hold_ratio") or 0) > 1.2 and tr.get("hold_losers_h") is not None
    if cut_l and cut > 50 and (tr.get("trades") or 0) >= MIN_PATTERN_TRADES and not _hold_evidence:
        out.append(dict(agent="Leak finder", title=f"Positions still open at 24h cost more than closing them",
                        evidence=f"Across {tr.get('complete_trades') or tr.get('trades')} complete trades, the ones still "
                                 f"open at the 24-hour mark gave back more than they added from there on.",
                        counterfactual=_cf_charged(cut_l, days) if cut_l else
                                       f"A time-cut at 24h would have kept roughly ~{_usd(cut)} over {days} days.",
                        usd=cut, window=f"{days}d", cta="A time-cut is a rule your quant can run for you."))
    if _hold_evidence and (tr.get("trades") or 0) >= MIN_PATTERN_TRADES and (cut_l is None or cut > 50):
        out.append(dict(agent="Leak finder", title=f"You hold losers {tr['hold_ratio']:.1f}× longer than winners",
                        evidence=f"Median loser {tr['hold_losers_h']:.1f}h vs winner {tr['hold_winners_h']:.1f}h across {tr['complete_trades']} complete trades.",
                        counterfactual=(_cf_charged(cut_l, days) if cut_l else _unpriced_cf),
                        usd=cut if cut_l else 0.0, unpriced=not cut_l,
                        window=f"{days}d", cta="A time-cut is a rule your quant can run for you."))
    # 4. giving back winners — only when the lock is robust
    lock_l = best_lever(lv, "lock")
    lock = lock_l["total"] if lock_l else None      # same as the cut: no walking around the bar
    # `give_back_median` and `mfe_median_winners` are taken over WINNERS only, so a book with no
    # winning complete trade has both as None — and `lock` still fires, because losers that armed
    # and retraced do produce a lever. _pct(None) then raised TypeError, which is not an HLError, so
    # desk.py printed a traceback and no desk at all. That is exactly the book
    # `--find --find-losers` sends a reader to. (@danielmbirochi, #718.)
    _has_winner_stats = tm and tm.get("give_back_median") is not None
    if (tr.get("trades") or 0) >= MIN_PATTERN_TRADES and not _has_winner_stats and (lock_l is None or lock > 50) and tm and tm.get("n"):
        out.append(dict(agent="Leak finder", title="Your positions give back their peak before you exit",
                        evidence=f"No complete trade closed green in this window, but {tm['n']} of them were in profit "
                                 f"at some point first.",
                        counterfactual=(_cf_charged(lock_l, days) if lock_l else _unpriced_cf),
                        usd=lock if lock_l else 0.0, unpriced=not lock_l,
                        window=f"{days}d", cta="A ratcheting stop locks the peak without capping the run."))
    if (tr.get("trades") or 0) >= MIN_PATTERN_TRADES and _has_winner_stats and (lock_l is None or lock > 50):
        out.append(dict(agent="Leak finder", title=f"You give back a median {_pct(tm['give_back_median'])} of a winner's peak",
                        evidence=f"Winners reach a median +{_pct(tm['mfe_median_winners'], 1)} before exit; {_pct(tm['losers_that_were_green'])} of losers were green first." if tm.get("losers_that_were_green") is not None else "",
                        counterfactual=(_cf_charged(lock_l, days) if lock_l else _unpriced_cf),
                        usd=lock if lock_l else 0.0, unpriced=not lock_l,
                        window=f"{days}d", cta="A ratcheting stop locks the peak without capping the run."))
    # 5. chasing
    if tm and _n(tm, "chased_n") >= MIN_PATTERN_TRADES and _n(tm, "chased_realized") < -50 and (tm.get("calm_pf") or 0) > (tm.get("chased_pf") or 0):
        out.append(dict(agent="Smart money", title=f"Entries after a ≥3% move lose money — {tm['chased_n']} of {tm['n']} trades",
                        evidence=f"Those trades realized {_usd(tm['chased_realized'])} (profit factor {tm['chased_pf']:.1f}) vs {min(tm['calm_pf'], 9.9):.1f} when you entered before the move.",
                        counterfactual=f"Skipping entries that had already run ≥3% would have kept ~{_usd(-tm['chased_realized'])} over {days} days.",
                        usd=-tm["chased_realized"], window=f"{days}d", cta="Enter with the flow, not after it — a signal-driven entry does this."))
    # 6. liquidations
    if tr.get("liquidations"):
        # "a stop halfway to liquidation would have kept half" was a GUESS, not a counterfactual: it
        # charges nothing for the positions that stop would have cut early and which then recovered,
        # and we do not know where the trader would have put it. Priced at $0 it states the fact and
        # stops topping a ranking it was never measured for — on one book it led the page at
        # $169,425 and next_steps sent the reader at it. (@0xsarvesh, #718.)
        out.append(dict(agent="Risk guard", title=f"{tr['liquidations']} liquidation(s) cost {_usd(-tr['liquidation_loss'])}",
                        evidence="A liquidation surrenders the whole margin plus the liquidation fee.",
                        counterfactual="How much a stop would have saved depends where you put it, so the desk does not price this one — but the cost above is what it actually took.",
                        usd=0.0, unpriced=True, window=f"{days}d", cta="A hard stop is the cheapest insurance there is."))
    # 7. sizing — oversized losers
    size_l = ch.get("size")
    if size_l and size_l["total"] > 50 and size_l.get("losers", 0) >= 2:
        out.append(dict(agent="Leak finder", title=f"{size_l['n']} positions were sized >1.5× your median winner",
                        evidence=f"Median winner {_usd(size_l['median_winner'])} notional; {size_l['losers']} of those {size_l['n']} lost money.",
                        counterfactual=_cf_charged(size_l, days),
                        usd=size_l["total"], window=f"{days}d", cta="Size by conviction, not by frustration — a fixed-fraction rule does this."))
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
