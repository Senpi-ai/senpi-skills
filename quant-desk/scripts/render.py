#!/usr/bin/env python3
"""The desk as Markdown for chat. Sections can be rendered alone (`--section`) so a follow-up question
("are my positions protected?") answers from the cached analysis without a refetch."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import datetime

import metrics
import score as score_mod

SECTIONS = ("overview", "strategy", "context", "protection", "performance", "leaks", "smart", "market", "edge", "scout", "next", "followups")
VERSION = "1.25.1"     # shown in the header line, so a stale install is visible at a glance


def pct_cost(x):
    """Cost shares under 1% keep a decimal — "0%" is a claim, "0.4%" is a measurement."""
    if x is None:
        return "—"
    if 0 < x < 0.01:
        return f"{100 * x:.1f}%" if x >= 0.0005 else "<0.1%"
    return pct(x)


def rank_line(rank):
    """Three windows, so one bad week (or one good one) cannot pass for the trader."""
    tp = rank["top_pct"]
    w = rank.get("windows") or {}
    wk = w.get("week") or {}
    s = (f"**#{rank['rank']:,} of {rank['of']:,}** on Hyperliquid's leaderboard this week" + (f" ({usd(wk['pnl'], signed=True)})" if wk.get("pnl") is not None else "")
         + " · " + (f"top {tp:.1f}%" if tp <= 50 else f"bottom {max(0.1, 100 - tp):.1f}%"))
    extra = []
    for key, label in (("month", "on the month"), ("allTime", "all-time")):
        rk = (rank.get("ranks") or {}).get(key)
        if rk and (w.get(key) or {}).get("pnl") is not None:
            extra.append(f"**#{rk:,}** {label} ({usd(w[key]['pnl'], signed=True)})")
    return s + (" · " + " · ".join(extra) if extra else "")


def coverage_note(tr, meta=None):
    """Which record the trade-level reads come from, in the desk's own words — never a data-source apology."""
    cov = tr.get("coverage") or {}
    src = ((meta or {}).get("sources") or {}).get("trades") or "public fills"
    if src != "public fills":
        return f"_Trade history: {src}._"
    # `effective` is `overall` after the ledger reconciliation — a book whose rebuilt P&L misses the
    # exchange's own delta by more than the open positions can explain has volume the jump heuristic
    # never saw, and quoting the unreconciled figure would read as fuller coverage than we have.
    seen = cov.get("effective") if cov.get("effective") is not None else cov.get("overall")
    if seen is None or seen >= 0.9:
        return None
    return f"_Trade-level reads cover about {pct(seen)} of executed volume; ledger figures are complete._"


def hold_fallback(tr):
    w, l = tr["hold_n"]["winners"], tr["hold_n"]["losers"]
    few = "of either" if (w < metrics.MIN_HOLD_N and l < metrics.MIN_HOLD_N) else ("losers" if l < metrics.MIN_HOLD_N else "winners")
    return f"**Hold time:** {w} winning and {l} losing round trips with a clean open and close — too few {few} for a hold-time read (the desk wants {metrics.MIN_HOLD_N} of each)."


def copy_warnings(book, cohorts):
    """What a copier inherits: naked positions and cohorts on the other side — grammar follows the counts."""
    warn = []
    if book["naked"]:
        n = len(book["naked"])
        warn.append(f"{n} of {len(book['positions'])} open positions {'has' if n == 1 else 'have'} no stop")
    against = [x for x in (cohorts or []) if x.get("agreement") is not None and x["agreement"] <= -0.5]
    if against:
        names = [{"proven": "proven cohort", "hot": "hot 30-day cohort"}.get(x["name"], x["name"]) for x in against]
        warn.append("the " + " and the ".join(names) + (" sits" if len(names) == 1 else " sit") + " on the other side of this book")
    return warn


def usd(x, signed=False):
    if x is None:
        return "—"
    s = f"${abs(x):,.0f}"
    return ("-" + s) if x < 0 else (("+" + s) if signed and x > 0 else s)


def pct(x, d=0, signed=False):
    if x is None:
        return "—"
    v = 100 * x
    return (f"{v:+.{d}f}%" if signed else f"{v:.{d}f}%")


def hrs(x):
    """Scalpers hold for seconds. At 1 decimal place every hold on a 1,117-trade book rendered
    "0.0h", including the median-hold column and the winners/losers line."""
    if x is None:
        return "—"
    if x >= 48:
        return f"{x / 24:.1f}d"
    if x >= 1:
        return f"{x:.1f}h"
    return f"{x * 60:.0f}m" if x * 60 >= 1 else "<1m"


def num(x, unit):
    if x is None:
        return "—"
    if x == float("inf"):
        return "∞"
    if unit == "x" and x > 100:
        return ">100×"
    return {"h": hrs(x), "%": pct(x), "x": f"{x:.1f}×"}[unit]


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}"


def header(r):
    a, tr, act, rank = r["address"], r["track"], r["activity"], r.get("rank")
    lines = [f"# Your desk — `{short(a)}`",
             f"{r['days']} days · {act['fills']:,} fills · {act['coins']} coins · updated {datetime.datetime.fromtimestamp(r['now_ms'] / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · **YOUR QUANT — LIVE · READ-ONLY** · v{VERSION}"]
    if rank:
        lines.append(rank_line(rank))
    lines.append(f"**{r['archetype']}**")
    lab = r.get("labels") or {}
    if any(lab.get(k) for k in ("consistency", "risk", "activity")):
        lines.append("senpi's read: " + " · ".join(str(lab[k]).upper() for k in ("consistency", "risk", "activity") if lab.get(k))
                     # senpi's trader-score consistency, NOT the desk's own Consistency dimension —
                     # unlabelled they collided on one page as "consistency score 33" beside
                     # "Consistency 100"
                     + (f" · senpi trader-score consistency {lab['tcs']}" if lab.get("tcs") is not None else ""))
    lines.append(f"> **{r['verdict']}**")
    if r["flags"]:
        lines.append(" ".join(f"`{f}`" for f in r["flags"]))
    return "\n".join(lines)


def overview(r):
    tr, d, book = r["track"], r["dimensions"], r["book"]
    q = r.get("quant_score")
    out = [f"## Quant score **{q}**/100" if q is not None else
           "## Quant score — _not enough of this book is measurable to score it_",
           "", "| Dimension | Score | What it means |", "|---|---:|---|"]
    names = {"timing": "Timing / edge", "risk": "Risk management", "cost": "Cost efficiency", "sizing": "Sizing / conviction", "consistency": "Consistency", "market_fit": "Market fit"}
    for k in ("timing", "risk", "cost", "sizing", "consistency", "market_fit"):
        # An unmeasured dimension shows a dash, not a number. A number here is a claim.
        sc = d[k]["score"]
        out.append(f"| {names[k]} | {'—' if sc is None else sc} | {d[k]['line']} |")
    if any(d[k]["score"] is None for k in names):
        out += ["", "_A dimension marked — could not be measured this window; the score is the weighted "
                    "average of the ones that could._"]
    eq = r["equity"]
    ledger = tr.get("ledger_net")
    out += ["", f"## Track record ({r['days']} days)", "", "| Net P&L (ledger, incl. unrealized) | Return on avg equity | Realized (trades + funding − fees) | Win rate | Max drawdown | Profit factor | Trades | Active days |", "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {usd(ledger, signed=True)} | {pct(eq.get('return_on_avg_equity'), 1, signed=True)} | {usd(tr['net'], signed=True)} | {pct(tr['win_rate'])} | {pct(-r['drawdown']['dd_pct'], 0, signed=True) if r['drawdown'].get('dd_pct') else '—'} | {num(tr['profit_factor'], 'x')} | {tr['trades']} | {r['activity']['active_days']} |"]
    note = coverage_note(tr, r.get("meta"))
    if note:
        # The blank line is load-bearing. A line placed straight after a table row is parsed as
        # ANOTHER ROW, so this caption rendered as a row with its text in column 1 and seven empty
        # cells trailing it — which is what a reader sees as "the table has an empty row".
        out += ["", note]
    cr = tr.get("cost_ratio"); wb = (r.get("benchmark") or {}).get("cost_ratio")
    n_tr = tr.get("trades") or 0
    out += ["", "## Where your P&L went", ""]
    if ledger is not None:
        out.append(f"Ledger over {r['days']} days: **{usd(ledger, signed=True)}** — every fill, funding payment and the open book included.")
    out.append(f"Closed trades ({n_tr}): gross **{usd(tr['gross_realized'], signed=True)}** → fees **{usd(-tr['fees'], signed=True)}** → funding **{usd(tr['funding'], signed=True)}** → net **{usd(tr['net'], signed=True)}**.")
    if ledger is not None and n_tr < 5 and (ledger < 0) != ((tr.get("net") or 0) < 0):
        out.append(f"The closed-trade record is thin ({n_tr} trade{'s' if n_tr != 1 else ''}); the ledger is the number to trust.")
    if cr is not None:
        if cr > 1:
            out.append("Costs exceeded what the trades and funding made over the window.")
        elif (tr.get("funding") or 0) > 0:
            out.append(f"Fees took **{pct_cost(cr)}** of what you made — trade P&L plus **{usd(tr['funding'])}** of funding collected.")
        else:
            out.append(f"Fees + funding took **{pct_cost(cr)}** of your gross" + (f" — the whale median is {pct(wb)}." if wb is not None else "."))
    if r["leaks"]:
        # The heading counts what is actually below it. "Top 3" over two items is a small lie the
        # reader checks in one glance, and it makes them wonder what else was rounded.
        top = r["leaks"][:3]
        out += ["", f"## Top {len(top)} thing{'s' if len(top) != 1 else ''} your agents found", ""]
        for i, l in enumerate(top, 1):
            head = f"**{l['agent']}**" if l.get("unpriced") else f"**{l['agent']} · ~{usd(l['usd'])} / {l['window']}**"
            out.append(f"{i}. {head} — **{l['title']}.** {l['evidence']} _{l['counterfactual']}_ → {l['cta']}")
    return "\n".join(out)


def protection(r):
    b = r["book"]; sm = {x["coin"]: x for x in (r.get("smart") or {}).get("rows", [])}
    n = len(b["positions"])
    out = ["## Live positions — protection audit", "",
           f"Account value **{usd(b['account_value'])}**" + (f" (perps equity {usd(b['account_value_perps'])})" if b.get("account_value_perps") and abs(b["account_value_perps"] - b["account_value"]) > 1 else "")
           + f" · margin used **{pct(b['margin_utilization'])}** · withdrawable **{usd(b['withdrawable'])}** · net uPnL **{usd(b['unrealized'], signed=True)}**",
           f"{n} open position{'s' if n != 1 else ''} · {len(b['naked'])} with no stop · {len(b['partial'])} partly covered · {r['market']['stance'] if r.get('market') else ''}" + (f" · paying {usd(-b['funding_per_day'])}/day in funding" if b['funding_per_day'] < 0 else (f" · collecting {usd(b['funding_per_day'])}/day in funding" if b['funding_per_day'] > 0 else ""))]
    if n:
        # chat-shaped: nine short columns; the prose lives under the table, one line per position that needs a hand
        out += ["", "| Coin | Side | Held | Notional | uPnL · ROE | Funding/day | To liq. | Stop | Status |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        todo = []
        for p in b["positions"]:
            status, note = _protection_note(p, sm.get(p["coin"]))
            liq = "—" if p["liq_distance_pct"] is None else (">100%" if p["liq_distance_pct"] > 100 else pct(p["liq_distance_pct"] / 100, 1))
            held = hrs((r["now_ms"] - p["opened_ms"]) / 3.6e6) if p.get("opened_ms") else "—"
            out.append(f"| {p['coin']} | {p['side']} {p['leverage'] or '—'}x | {held} | {usd(p['notional'])} | {usd(p['unrealized'], signed=True)} · {pct(p['roe'], 0, signed=True)} | {usd(p['funding_per_day'], signed=True)} | {liq} | {pct(p['stop_covered_share'])} | {status} |")
            if status != "PROTECTED":
                todo.append(f"- **{p['coin']}** — {note}.")
        out += ["", "**Your quant would…**"] + (todo or ["- nothing here — every position carries a full stop."])
    else:
        out.append("\nNo open positions right now.")
    return "\n".join(out)


def _protection_note(p, smrow):
    liq = p["liq_distance_pct"]; cov = p["stop_covered_share"]; against = smrow and smrow["read"].startswith("AGAINST")
    if liq is not None and liq < 5 and cov < 0.9:
        return "AT RISK", f"put a stop above the liquidation price now — at {p['leverage']}x a {liq:.1f}% move takes the whole margin"
    if cov == 0:
        return "UNPROTECTED", "attach a stop ladder: a hard floor plus a trailing lock as it runs" + (" — and you're against the whale cohort here" if against else "")
    if cov < 0.9:
        return "PARTLY COVERED", f"the other {pct(1 - cov)} rides naked — extend the stop to the full size"
    if p["stop_distance_pct"] is not None and p["stop_distance_pct"] < 1.0:
        return "PROTECTED", f"stop is {p['stop_distance_pct']:.1f}% from the mark — tight enough to be noise"
    return "PROTECTED", "stop in place — looks good" + (" — but the whale cohort is on the other side" if against else "")


def performance(r):
    tr = r["track"]
    out = ["## Performance", "", "| Coin | Trades | Win rate | Realized | Fees | Funding | Share of volume | Median hold |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, v in list(tr["coins"].items())[:10]:
        out.append(f"| {k} | {v['trades']} | {pct(v['win_rate'])} | {usd(v['realized'], signed=True)} | {usd(-v['fees'], signed=True)} | {usd(v['funding'], signed=True)} | {pct(v['volume_share'])} | {hrs(v['hold_median_h'])} |")
    L, S = tr["long"], tr["short"]
    out += ["", f"**Long / short:** longs {L['trades']} trades · win {pct(L['wins'] / L['trades']) if L['trades'] else '—'} · {usd(L['realized'], signed=True)}; shorts {S['trades']} trades · win {pct(S['wins'] / S['trades']) if S['trades'] else '—'} · {usd(S['realized'], signed=True)}",
            (f"**Hold time (median):** winners {hrs(tr['hold_winners_h'])} · losers {hrs(tr['hold_losers_h'])}" + (f" — you hold losers {tr['hold_ratio']:.1f}× longer" if tr.get('hold_ratio') and tr['hold_ratio'] > 1.2 else (f" — you cut losers {1 / tr['hold_ratio']:.1f}× faster than you let winners run" if tr.get('hold_ratio') and 0 < tr['hold_ratio'] < 0.8 else ""))) if (tr.get('hold_winners_h') is not None and tr.get('hold_losers_h') is not None) else hold_fallback(tr),
            f"**Execution:** {pct(tr['taker_share'])} taker · {score_mod._bp(tr['fee_rate_taker'])} taker / {score_mod._bp(tr['fee_rate_maker'])} maker · {tr['liquidations']} liquidation(s)"]
    sb = tr.get("size_buckets") or {}
    if sb.get("bands"):
        out += ["", f"**Size vs outcome** (median position {usd(sb['median_notional'])} notional):", "", "| Size band | Winners | Losers | Realized |", "|---|---:|---:|---:|"]
        out += [f"| {b['band']} | {b['winners']} | {b['losers']} | {usd(b['realized'], signed=True)} |" for b in sb["bands"]]
    return "\n".join(out)


def recoverable_line(r):
    """The one quotable number, with its shape. The leaks below are alternative fixes for the same
    trades, so a reader who adds them up gets a figure larger than the money ever at stake.

    The headline states the TOTAL and then attributes it. It used to read "<rule> would have kept
    ~$27,996" on a book where $18,898 of that was taker fees and the rule's own share was $9,098 —
    crediting a trailing stop with money that came from not crossing the spread."""
    rec = r.get("recoverable") or {}
    total = rec.get("usd") or 0
    # It used to also return [] when the leak list was empty, so a book whose only recoverable money
    # was TAKER FEES — measured, not a counterfactual, and always real — got no number at all, while
    # the sentence underneath said no leak clears the bar. The JSON still carried the figure, so the
    # agent could quote a number the desk had just denied. (@danielmbirochi, #718, item 10.)
    if total <= 0:
        return []
    fees, rule = rec.get("fees") or 0, rec.get("rule")
    lever = total - fees
    # "N% of what your losing trades gave up" was also gated on a NEGATIVE ledger, on the reasoning
    # that the frame only suits a book that lost money. But a headline with no denominator is what
    # made this number read as absurd in review: on a book that netted $36,481 after $140,698 of
    # fees, "~$171,808" invites the reader to divide by the net and get 4.71x. The share is exactly
    # the denominator that defuses it — 4% of what the losing trades gave up — and it is no less
    # true on a profitable book. The `<= 1.0` guard below is what keeps the sentence meaningful.
    # (B6, @0xsarvesh #718.)
    share = rec.get("share_of_losses")
    head = f"**Your quant would have kept ~{usd(total)} of this**"
    # a denominator that means something: on a book with almost no losses the share is a division by
    # noise (the fixture reads 20924%). The cap was 2.0, which still left "197% of what your losing
    # trades gave up" printable — above 100% the frame stops meaning anything to a reader, however
    # true the arithmetic is once fees come off the winners too. (@danielmbirochi, #718, round 2.)
    # …and not when it rounds to nothing. On a book that lost $274,940 with $2,709 recoverable the
    # share is 0.5%, and the clause printed "— 0% of what your losing trades gave up", which tells
    # the reader nothing and reads as a broken number. The denominator exists to make the headline
    # legible; below half a percent it does the opposite.
    # Keyed on what actually renders, not on a threshold guessed against the formatter: 0.005 still
    # prints "0%" under banker's rounding, so any constant here is one rounding rule away from wrong.
    if share and 0 < share <= 1.0 and pct(share, 0) != "0%":
        head += f" — {pct(share, 0)} of what your losing trades gave up"
    # when one trade IS the number, say so in the headline. The disclosure below is the first thing
    # a reader drops when they quote the figure, and on 0xb699…392e that figure was $1,072,010 of
    # which 97% came from a single position on an 8-trade book.
    top1 = ((rec.get("concentration") or {}).get("top1")) or 0
    if top1 >= 0.8:
        head += f" — though {pct(top1, 0)} of that is one trade, not a pattern"
    out = [head + ".", ""]

    fee_part = f"{usd(fees)} of it is taker fees you can stop paying on the same fills"
    n = rec["n_trades"]
    rule_part = (f"one rule — {rule} — applied to {'your one complete trade' if n == 1 else f'all {n} complete trades'} "
                 f"and charged on the ones it would have cost you")
    if rule and fees > 0:
        big, small = (fee_part, f"the other {usd(lever)} comes from {rule_part}") if fees >= lever \
            else (f"{usd(lever)} of it comes from {rule_part}", f"the other {usd(fees)} is taker fees on the same fills")
        out += [f"{big[0].upper() + big[1:]}, and {small}.", ""]
    elif rule:
        out += [f"All of it comes from {rule_part}.", ""]
    else:
        out += [f"{fee_part[0].upper() + fee_part[1:]}.", ""]

    c = rec.get("concentration") or {}
    if c.get("top1", 0) >= 0.4:
        out += [f"**That total is not spread across your book — it is {'one trade' if c['top1'] >= 0.6 else 'a few trades'}.** "
                f"The largest is {pct(c['top1'], 0)} of it on its own, and the top three are {pct(min(c['top3'], 1.0), 0)}. "
                f"A handful of positions drove it; the rest of the book is not the problem.", ""]
    elif c.get("n_positive"):
        out += [f"No single trade dominates it — the largest is {pct(c['top1'], 0)}, spread over "
                f"{c['n_positive']} of your trades. This one is a habit, not an accident.", ""]

    if r.get("leaks"):
        out += ["_The leaks below price each fix on its own. They land on the same trades — one oversized, "
                "chased, held-too-long position shows up in several — so **they do not add up**. The number "
                "above is the single best change, and it is the one to quote._", ""]
    return out


def leaks(r):
    out = ["## Leaks — ranked by $ impact · counterfactual, not history", ""]
    out += recoverable_line(r)
    if not r["leaks"]:
        _rec = r.get("recoverable") or {}
        if (r["track"].get("trades") or 0) < 5:
            out.append("Not enough closed trades to price a leak yet — the desk needs a handful of round trips before a counterfactual means anything.")
        elif (_rec.get("usd") or 0) > 0:
            # "No leak clears the bar" sat directly under a positive recoverable figure. Both were
            # true of different things: no PROCESS counterfactual survived being charged, and the
            # costs above are measured rather than counterfactual. Say which is which.
            out.append("No *process* leak clears the bar on this window — every exit, sizing and entry rule the desk tests "
                       "came out flat or negative once it was charged on the trades it would have cost. The figure above is "
                       "not one of those: costs are measured, not modelled, which is why it stands on its own.")
        else:
            out.append("No leak clears the bar on this window: every counterfactual the desk tests came out flat or negative, which means the process is not where the money is going.")
    for i, l in enumerate(r["leaks"], 1):
        tag = f"_{l['agent']}_" + ("" if l.get("unpriced") else f" · **~{usd(l['usd'])} / {l['window']}**")
        out += [f"**{i:02d} · {l['title']}** — {tag}", f"{l['evidence']} {l['counterfactual']}", f"→ {l['cta']}", ""]
    tm = r.get("timing") or {}
    if tm.get("n"):
        neg = [k for k, g in (("time-cut on losers", tm.get("cut")), ("trailing lock on winners", tm.get("lock")))
               if g and g.get("settings") and any(s["n"] for s in g["settings"].values()) and all(s["total"] <= 0 for s in g["settings"].values() if s["n"])]
        if neg:
            out.append(f"Tested and **rejected** for this book: a {' and a '.join(neg)} — each would have cost money on your biggest runs. Your edge is letting those run; don't fix what isn't leaking.")
    return "\n".join(out)


def market(r):
    m = r.get("market")
    if not m:
        return "## Market fit\n\nNo market read available."
    b = r["book"]
    out = ["## Market fit", "", f"**{m['headline']}**"]
    sent = []
    if m.get("median_funding_bp_8h") is not None:
        sent.append(f"Funding is {m['median_funding_bp_8h']:+.0f} bp/8h across the coins you hold.")
    if b["positions"]:
        sent.append(f"You're {m['stance']}" + (f" — paying ~{usd(-m['funding_per_day'])}/day to hold." if m["funding_per_day"] < 0 else (f" — collecting ~{usd(m['funding_per_day'])}/day." if m["funding_per_day"] > 0 else ".")))
    if m.get("btc"):
        sent.append(f"BTC is {m['btc']['trend'].lower()} ({pct(m['btc']['change_7d'], 1, signed=True)} on the week).")
    out.append(" ".join(sent))
    if m["rows"]:
        out += ["", "| Coin | Your side | Trend | Funding | Open interest | Fit |", "|---|---|---|---:|---:|---|"]
        for x in m["rows"]:
            fund = "—" if x["funding_bp_8h"] is None else "{:+.0f} bp/8h".format(x["funding_bp_8h"])
            oi = "—" if not x["open_interest_usd"] else "${:,.0f}M".format(x["open_interest_usd"] / 1e6)
            out.append("| {} | {} {}x | {} | {} | {} | **{}** |".format(x["coin"], x["side"].capitalize(), x["leverage"] or "", (x["trend"] or "—").capitalize(), fund, oi, x["fit"]))
    return "\n".join(out)


def edge(r):
    bs = r.get("setups") or {}
    out = ["## Where your edge actually is", ""]
    if bs.get("best"):
        b = bs["best"][0]
        out.append(f"**Your best setups:** {b['label']} — {b['wins']} of {b['n']} wins, profit factor {num(b['profit_factor'], 'x')}, {usd(b['realized'], signed=True)}." +
                   ("".join(f" Also {x['label']}: {x['wins']}/{x['n']}, PF {num(x['profit_factor'], 'x')}." for x in bs["best"][1:3])))
    else:
        out.append("No setup clears a 1.5 profit factor on 3+ trades in this window — the sample is too small or the edge is not repeatable yet.")
    if bs.get("worst"):
        w = bs["worst"][0]
        out.append(f"**Your worst:** {w['label']} — {w['wins']} of {w['n']} wins, {usd(w['realized'], signed=True)}.")
    fam = r.get("families") or []
    if fam:
        out.append(f"\nThe way you win maps to the **{' / '.join(f.replace('_', ' ') for f in fam)}** families in the strategy catalog — the quick start if you want your quant to run this for you, under your name.")
    out.append("\n_Process only — rules, risk and timing. Never a call to buy a coin._")
    return "\n".join(out)


def next_steps(r):
    if r.get("whose") == "other":
        return next_steps_other(r)
    b = r["book"]; out = ["## What your quant would do next", ""]
    i = 1
    at_risk = [p for p in b["positions"] if (p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5 and p["stop_covered_share"] < 0.9) or p["stop_covered_share"] == 0]
    if at_risk:
        # "a hard floor now, a trailing lock as it runs … a signature on positions you already hold"
        # promised something that does not exist for this reader. The integrated two-phase DSL is a
        # RUNTIME feature; on a raw position you get a FIXED stop plus an uncoordinated profit ladder,
        # and `ratchet_stop_add` is keyed to a senpi strategy wallet — so for a desk reader whose book
        # sits on their own wallet, senpi cannot attach anything today. Say what they can do now, and
        # what is coming, without claiming a signature there is nothing to sign.
        out.append(f"{i}. **Protect first.** {', '.join(p['coin'] for p in at_risk)}: every one of these "
                   f"is naked. Let me know if you want my help."); i += 1
    priced = [l for l in r["leaks"] if not l.get("unpriced")]
    if priced:
        l = priced[0]
        out.append(f"{i}. **Fix the biggest leak.** {l['title']} — ~{usd(l['usd'])}/{l['window']}. {l['cta']}"); i += 1
    fam = r.get("families") or []
    setup = fam[0].replace("_", " ") if fam else "your pattern"
    out.append(f"{i}. **Keep the agents on.** Reply *hire my quant* and I'll run this desk on your book "
               f"continuously — risk guard, smart money, market regime, leak finder — and turn your best "
               f"setup ({setup}) into a strategy you approve, deployed as **your** strategy."); i += 1
    # The reader who does not want their own history mechanised still has somewhere to go. It is also
    # the cheapest next step on the page: a sentence from them, no wallet, no deposit.
    out.append(f"{i}. **Or build something new.** Tell me your thesis — what you think is about to "
               f"happen and why — and I'll write the strategy for it: the rules, the risk, the sizing, "
               f"yours to approve before anything runs.")
    return "\n".join(out)


def strategy(r):
    st = r.get("strategy") or {}
    out = ["## What you've been doing", ""]
    if not st.get("statements"):
        out.append("Not enough trades in the window to read a strategy.")
        return "\n".join(out)
    out += [f"- {x[0].upper() + x[1:]}." for x in st["statements"]]
    fp = st.get("fingerprint") or {}
    rows = [x for x in fp.get("class_side") or [] if x["trades"] >= 2][:6]
    if rows:
        out += ["", "| Where the trades went | Trades | Wins | Realized | Profit factor |", "|---|---:|---:|---:|---:|"]
        out += [f"| {x['label']} {x['side'].lower()}s ({', '.join(x['coins'][:3])}) | {x['trades']} | {x['wins']} | {usd(x['realized'], signed=True)} | {num(x['pf'], 'x')} |" for x in rows]
    if st.get("critique"):
        out += ["", "**The critique:**"] + [f"- {c}" for c in st["critique"]]
    return "\n".join(out)


def context(r):
    c = r.get("context") or {}; b = c.get("breadth") or {}
    out = ["## The market you're trading in — right now", ""]
    if b:
        day = {"risk_on": "RISK-ON", "risk_off": "RISK-OFF", "mixed": "MIXED"}.get(b.get("day"), "UNKNOWN")
        head = f"**{day}** — BTC {pct((b.get('btc_change_pct') or 0) / 100, 1, signed=True)} on the day, {pct(b.get('share_up'))} of perps up"
        if b.get("memes_vs_majors") is not None:
            head += f", memes {pct(b['memes_vs_majors'] / 100, 1, signed=True)} vs majors"
        out.append(head + ".")
        fr = c.get("funding_regime")
        if fr and fr.get("regime"):
            out.append(f"Funding regime (senpi): **{fr['regime']}**" + (f" — {fr['extreme_count']} extreme-funding assets" if fr.get("extreme_count") is not None else "") + ".")
        groups = sorted((g for g in b.get("groups", {}).values()), key=lambda g: -g["oi_usd"])[:8]
        if groups:
            out += ["", "| Class | Avg 24h | Up / down | Median funding |", "|---|---:|---:|---:|"]
            out += [f"| {g['label']} | {pct(g['avg_change_pct'] / 100, 1, signed=True)} | {g['up']} / {g['down']} | {g['median_funding_bp_8h']:+.1f} bp/8h |" for g in groups]
    at = c.get("attention")
    if at and at.get("markets"):
        out += ["", "**Where the top traders' gains are right now** (senpi Hyperfeed, 4h): " + "; ".join(f"{m['coin']} {m['direction'].lower()}s {m['share_of_gains']:.0f}% ({m['traders']} traders)" for m in at["markets"][:5]) + "."]
        if at.get("overlap"):
            out.append("You: " + ", ".join(f"{o['coin']} **{o['read']}** the top traders" for o in at["overlap"]) + ".")
        if at.get("momentum"):
            out.append("Momentum events in the last 4h: " + ", ".join(f"{m['coin']} {m['direction'].lower()} ×{m['events']}" for m in at["momentum"][:5])
                       + (f" — with you on {', '.join(at['with_momentum'])}" if at.get("with_momentum") else "") + (f"; against you on {', '.join(at['against_momentum'])}" if at.get("against_momentum") else "") + ".")
    rp = c.get("regime_performance") or {}
    cells = rp.get("cells") or {}
    if cells:
        hdr = ["", f"**How you trade the tape** — your record by the regime of the day you entered (last {r['days']} days: " + ", ".join(f"{v} {k.replace('_', '-')} days" for k, v in (rp.get('days') or {}).items()) + "):", "",
               "| Entered on | Trades | Win rate | Realized | Profit factor |", "|---|---:|---:|---:|---:|"]
        rows = []
        for key in ("risk_on/ALL", "risk_off/ALL", "mixed/ALL", "risk_on/LONG", "risk_off/LONG", "risk_on/SHORT", "risk_off/SHORT"):
            x = cells.get(key)
            if x and x["trades"] >= 3:
                rows.append(f"| {x['regime'].replace('_', '-')} days · {x['side'].lower() if x['side'] != 'ALL' else 'all'} | {x['trades']} | {pct(x['wins'] / x['trades'])} | {usd(x['realized'], signed=True)} | {num(x['pf'], 'x')} |")
        if rows:      # a header with no rows is a table that says nothing
            out += hdr + rows
        today = b.get("day")
        best = max((x for x in cells.values() if x["side"] == "ALL" and x["trades"] >= 3), key=lambda x: (x["pf"] if x["pf"] not in (None, float("inf")) else 99), default=None)
        if today and best:
            same = best["regime"] == today
            out.append(f"\nToday is **{today.replace('_', '-')}** — " + ("your best tape." if same else f"your best tape is {best['regime'].replace('_', '-')}; size accordingly."))
    return "\n".join(out)


def smart_v2(r):
    cohorts = r.get("cohorts") or []
    out = ["## You vs smart money", ""]
    if not cohorts:
        out.append("No cohort view was available for this run.")
        return "\n".join(out)
    for cv in cohorts:
        title = {"proven": "The proven cohort", "hot": "The hot 30-day cohort"}.get(cv["name"], cv["name"])
        out += [f"**{title}** — _{cv['source']}_ · {cv['wallets']} wallets, {cv['coins']} coins held", ""]
        if cv["rows"]:
            out += ["| Coin | You | Cohort | Read |", "|---|---|---|---|"]
            out += ["| {} | {} | {} | **{}** |".format(x["coin"], x["you"], x["cohort"], x["read"]) for x in cv["rows"]]
        ag = cv.get("agreement")
        if ag is not None:
            out.append(f"\nBook-level agreement with this cohort: **{ag:+.2f}** (+1 = same side everywhere, −1 = opposite).")
        if cv.get("tilt"):
            out.append("The cohort's book by class: " + "; ".join(f"{t['label']} {'long' if t['bias'] > 0 else 'short'} {abs(t['bias']):.0%} net ({t['long']}L/{t['short']}S)" for t in cv["tilt"][:4]) + ".")
        if cv.get("yours"):
            out.append("Your book: " + "; ".join(f"{t['label']} {'long' if t['bias'] > 0 else 'short'} {abs(t['bias']):.0%} net, {t['weight']:.0%} of the book" for t in cv["yours"][:4]) + ".")
        if cv.get("they_hold"):
            out.append("The cohort holds, you don't: " + ", ".join(f"{h['coin']} ({h['side'].lower()}, {h['members']} wallets)" for h in cv["they_hold"][:6]) + ".")
        if cv.get("you_alone"):
            out.append("You hold, none of the cohort do: " + ", ".join(cv["you_alone"]) + ".")
        if cv.get("entry_lag_h") is not None:
            on = ', '.join(cv.get('lag_coins') or []) or 'the coins you share'
            out.append(f"Entry timing: you entered in step with this cohort's median entry on {on}." if abs(cv['entry_lag_h']) < 1
                       else f"Entry timing: you are {abs(cv['entry_lag_h']):.0f}h {'behind' if cv['entry_lag_h'] > 0 else 'ahead of'} this cohort's median entry on {on}.")
        out.append("")
    bt = r.get("benchmark_table")
    if bt and (r.get("benchmark") or {}).get("n", 0) >= 5:
        out += [f"**You vs whale median** _(n={r['benchmark']['n']}, {r['benchmark'].get('computed_at', '')})_", "", "| Metric | You | Whale median |", "|---|---:|---:|"]
        for row in bt:
            you = num(row["you"], row["unit"]); wh = num(row["whale"], row["unit"]); mark = ""
            if row["you"] is not None and row["whale"] is not None and row["you"] != float("inf"):
                worse = (row["you"] > row["whale"]) if row["better"] == "lower" else (row["you"] < row["whale"])
                mark = " 🔴" if worse else " 🟢"
            out.append(f"| {row['metric']} | {you}{mark} | {wh} |")
    return "\n".join(out)


def scout(r):
    opps = r.get("opportunities") or []
    out = ["## Live matches — where today's market and your pattern agree", ""]
    if not opps:
        out.append("Nothing clears the bar right now: no coin has the cohorts, the tape and your own pattern on the same side. That is a read, not a gap.")
    for o in opps:
        held = f" — you already hold it {o['held'].lower()}" if o.get("held") else ""
        out.append(f"- **{o['coin']} {o['side'].lower()}** ({o['cls']}, score {o['score']:.1f}){held}: " + "; ".join(o["why"]) + ".")
    out.append("\n_Process only — rules, risk and timing. Never a call to buy a coin._")
    return "\n".join(out)


def followups_section(r):
    fu = r.get("followups") or []
    if not fu:
        return ""
    out = ["## Your quant is ready to go deeper", ""] + [f"{i}. {f['prompt']}" for i, f in enumerate(fu, 1)]
    return "\n".join(out)


def render_deep(mode, d, r):
    if mode == "protect":
        out = ["## Stop ladder — every open position", "", f"Dollars at risk before: **{usd(d['total_risk_now'])}** → after: **{usd(d['total_risk_after'])}**", "",
               "| Coin | Side | Mark | Hard stop | Distance | Daily range | Lock arms at | Covered today | Note |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for x in d["rows"]:
            atr = "—" if x["atr_pct"] is None else "{:.1f}%".format(x["atr_pct"])
            out.append("| {} | {} | {:,.4g} | {:,.4g} | {:.1f}% | {} | {:,.4g} | {} | {} |".format(x["coin"], x["side"], x["mark"], x["hard_stop"], x["hard_stop_pct"], atr, x["lock_arms_at"], pct(x["covered_now"]), x["note"]))
        # Same overclaim as the next-steps block: there is no signature to give for a book on the
        # reader's own wallet. These levels are still the most actionable thing on the page — they are
        # a worksheet, so say that plainly.
        out += ["", "The hard stop sits one and a half days of normal range from the mark — the average "
                    "24-hour high-to-low of the last two weeks — or closer when liquidation is nearer "
                    "than that, because the stop has to trigger first. The lock trails at half the peak "
                    "gain once the trade is two ranges in the money.",
                "", "**These are yours to place.** The *Hard stop* column is the number to set on each "
                    "position onchain on Hyperliquid; the *Lock arms at* column is where a trailing "
                    "stop should begin once the trade is in the money. Tell me if you want help with "
                    "any of them."]
        return "\n".join(out)
    if mode == "replay":
        if not d or d.get("empty"):
            return "No losing week in the window."
        out = ["## Your worst week", "", f"Week of {datetime.datetime.fromtimestamp(d['start'] / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')}: **{usd(d['realized'], signed=True)}** over {d['trades']} trades ({d['losers']} losers).", "",
               "| Coin | Side | Hold | Realized |", "|---|---|---:|---:|"]
        out += [f"| {e['coin']} | {e['direction']} | {hrs(e['hold_h'])} | {usd(e['realized'], signed=True)} |" for e in d["biggest"]]
        for label, g in (("A time-cut on losers", d.get("cut")), ("A trailing lock on winners", d.get("lock"))):
            if g and g.get("settings"):
                out.append(f"\n**{label}** on exactly these trades: " + ", ".join(f"{k}: {usd(v['total'], signed=True)}" for k, v in g["settings"].items() if v["n"]) + (f" → robust {usd(g['robust'], signed=True)}" if g.get("robust") else " → not robust; would not have helped"))
        if d.get("green_first") is not None:
            out.append(f"\n{pct(d['green_first'])} of that week's losers were green first.")
        return "\n".join(out)
    if mode == "funding":
        out = ["## Funding — next 30 days at today's rates", "", "| Coin | Side | Notional | Rate | Per day | 30 days | Paid since open |", "|---|---|---:|---:|---:|---:|---:|"]
        out += [f"| {x['coin']} | {x['side']} | {usd(x['notional'])} | {x['rate_bp_8h']:+.1f} bp/8h | {usd(x['per_day'], signed=True)} | {usd(x['thirty_days'], signed=True)} | {usd(x['since_open'], signed=True)} |" for x in d["rows"]]
        out.append(f"\nTotal: **{usd(d['thirty_days'], signed=True)}** over 30 days" + (f" — {pct(d['share_of_equity'])} of your equity" if d.get("share_of_equity") is not None else "") + (f". The payers: {', '.join(d['payers'])}." if d.get("payers") else "."))
        return "\n".join(out)
    if mode == "compare":
        rc, pr = d.get("recent"), d.get("prior")
        if not rc or not pr:
            return "Not enough trades in both windows to compare."
        rows = [("Trades", rc["trades"], pr["trades"], None), ("Win rate", rc["win_rate"], pr["win_rate"], "%"), ("Profit factor", rc["pf"], pr["pf"], "x"), ("Realized", rc["realized"], pr["realized"], "$"),
                ("Fees", rc["fees"], pr["fees"], "$"), ("Avg size", rc["avg_size"], pr["avg_size"], "$"), ("Median hold — winners", rc["hold_w"], pr["hold_w"], "h"), ("Median hold — losers", rc["hold_l"], pr["hold_l"], "h"), ("Taker share", rc["taker"], pr["taker"], "%")]
        out = ["## Last 30 days vs the 60 before", "", "| Metric | Last 30d | Prior 60d |", "|---|---:|---:|"]
        for m, a, b, u in rows:
            f = (lambda v: "—" if v is None else (f"{v:,.0f}" if u is None else (usd(v) if u == "$" else num(v, u))))
            out.append(f"| {m} | {f(a)} | {f(b)} |")
        return "\n".join(out)
    if mode == "rules":
        out = ["## Your strategy as a rule set", ""]
        out += [f"- **Entry setups:** {'; '.join(d['entries']) if d['entries'] else 'no setup clears the bar yet'}", f"- **Entry timing:** {d['entry_rule']}", f"- **Holding:** {d['hold_rule']}", f"- **Sizing:** {d['size_rule']}", f"- **Risk:** {d['risk_rule']}", f"- **Catalog families:** {', '.join(f.replace('_', ' ') for f in d['families'])}", "", d["handoff"] + "."]
        return "\n".join(out)
    if mode == "regime":
        rr = dict(r); rr["context"] = dict(r.get("context") or {}, breadth=dict((r.get("context") or {}).get("breadth") or {}, day=d.get("today")))
        return context(rr)
    if mode == "watch":
        out = ["## What your agents would watch", ""] + [f"- {x}" for x in d["items"]] + ["", "Say *hire my quant* to keep them on your book."]
        return "\n".join(out)
    if mode == "smart":
        return smart_v2(dict(r, cohorts=d.get("cohorts")))
    if mode == "scout":
        return scout(dict(r, opportunities=d.get("opportunities")))
    if mode == "strategy":
        return strategy(dict(r, strategy=d))
    return ""


def next_steps_other(r):
    bs = (r.get("setups") or {}).get("best") or []
    fam = r.get("families") or []
    b = r["book"]
    out = ["## What to take from this trader", ""]
    if bs:
        x = bs[0]
        out.append(f"1. **The playbook.** {x['label']} — {x['wins']} of {x['n']} wins, profit factor {num(x['profit_factor'], 'x')}. Say *write their playbook as rules* and your quant turns it into a rule set that runs under **your** name — the {' / '.join(f.replace('_', ' ') for f in fam) if fam else 'closest'} templates are the quick start.")
    else:
        out.append("1. **The playbook.** No setup clears the bar on this window — what works here is not yet repeatable enough to copy.")
    warn = copy_warnings(b, r.get("cohorts"))
    out.append("2. **Before copying anything.** " + ("; ".join(warn) + ". Mirroring inherits all of that." if warn else "The book is protected and the cohorts are with it — the process is copyable; the timing is not.") + " Say *is this trader worth copying* for the copyability read.")
    out.append("3. **Learn the pattern, not the position.** The regime table says which tape they win in; the size-vs-outcome table says how they size. Those transfer. Their entries do not — by the time you see them, the move is theirs.")
    return "\n".join(out)


COMPARE_ROWS = (("Weekly rank", lambda r: f"#{r['rank']['rank']:,}" if r.get("rank") else "—"), ("Archetype", lambda r: r["archetype"]),
                ("Quant score", lambda r: str(r["quant_score"]) if r.get("quant_score") is not None else "—"), ("Net P&L (ledger)", lambda r: usd(r["track"].get("ledger_net"), signed=True)),
                ("Return on avg equity", lambda r: pct(r["equity"].get("return_on_avg_equity"), 1, signed=True)), ("Max drawdown", lambda r: pct(-(r["drawdown"].get("dd_pct") or 0), 0, signed=True)),
                ("Trades / win rate", lambda r: f"{r['track']['trades']} / {pct(r['track'].get('win_rate'))}"), ("Profit factor", lambda r: num(r["track"].get("profit_factor"), "x")),
                ("Taker share", lambda r: pct(r["track"].get("taker_share"))), ("Costs ÷ gross income", lambda r: pct(r["track"].get("cost_ratio"))),
                ("Open positions · unprotected", lambda r: f"{len(r['book']['positions'])} · {len(r['book']['naked'])}"), ("Margin used", lambda r: pct(r["book"].get("margin_utilization"))),
                ("vs proven cohort", lambda r: _agree(r, "proven")), ("vs hot 30-day cohort", lambda r: _agree(r, "hot")),
                ("Biggest leak", lambda r: f"{r['leaks'][0]['title']} (~{usd(r['leaks'][0]['usd'])})" if r["leaks"] else "—"),
                ("Best setup", lambda r: (lambda b: f"{b['label']} ({b['wins']}/{b['n']}, PF {num(b['profit_factor'], 'x')})")(r["setups"]["best"][0]) if (r.get("setups") or {}).get("best") else "—"))


def _agree(r, name):
    c = next((c for c in r.get("cohorts") or [] if c["name"] == name), None)
    if not c or c.get("agreement") is None:
        return "—"
    a = c["agreement"]
    return ("with" if a > 0.3 else ("against" if a < -0.3 else "split")) + f" ({a:+.2f})"


def render_compare(rs):
    heads = [f"`{short(r['address'])}`" for r in rs]
    out = ["# Side by side — " + " · ".join(heads), "", "| | " + " | ".join(heads) + " |", "|---|" + "---|" * len(rs)]
    for label, fn in COMPARE_ROWS:
        cells = []
        for r in rs:
            try:
                cells.append(fn(r))
            except Exception:  # noqa: BLE001
                cells.append("—")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out += ["", "**Verdicts**"] + [f"- `{short(r['address'])}`: {r['verdict']}" for r in rs]
    # what separates them: the widest gaps on the things that transfer
    def val(r, k):
        return r["track"].get(k)
    seps = []
    for k, label, better_low in (("profit_factor", "profit factor", False), ("taker_share", "taker share", True), ("cost_ratio", "cost share of income", True), ("win_rate", "win rate", False)):
        vals = [(val(r, k), short(r["address"])) for r in rs if val(r, k) not in (None, float("inf"))]
        if len(vals) >= 2:
            lo, hi = min(vals), max(vals)
            if hi[0] and lo[0] is not None and (hi[0] - lo[0]) > (0.15 if k in ("taker_share", "cost_ratio", "win_rate") else 0.8):
                best = lo if better_low else hi
                seps.append(f"{label}: `{best[1]}` leads ({num(best[0], 'x') if k == 'profit_factor' else pct(best[0])} vs {num((hi if better_low else lo)[0], 'x') if k == 'profit_factor' else pct((hi if better_low else lo)[0])})")
    naked = [(len(r["book"]["naked"]), short(r["address"])) for r in rs]
    if max(n for n, _ in naked) != min(n for n, _ in naked):
        seps.append("protection: " + ", ".join(f"`{a}` {n} unprotected" for n, a in naked))
    if seps:
        out += ["", "**What separates them**"] + [f"- {s}" for s in seps]
    import voice
    return voice.third_person("\n".join(out))


RENDERERS = {"overview": overview, "strategy": strategy, "context": context, "protection": protection, "performance": performance, "leaks": leaks, "smart": smart_v2,
             "market": market, "edge": edge, "scout": scout, "next": next_steps, "followups": followups_section}


def render(r, sections=None):
    parts = [header(r), ""]
    for s in (sections or SECTIONS):
        parts += [RENDERERS[s](r), ""]
    meta = r.get("meta") or {}
    if meta.get("warnings"):
        parts.append("_Notes: " + " · ".join(meta["warnings"]) + "_\n")
    md = "\n".join(parts)
    if r.get("whose") == "other":
        import voice
        md = voice.third_person(md, short(r["address"]))
    return md
