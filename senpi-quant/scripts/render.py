#!/usr/bin/env python3
"""The desk as Markdown for chat. Sections can be rendered alone (`--section`) so a follow-up question
("are my positions protected?") answers from the cached analysis without a refetch."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import datetime

SECTIONS = ("overview", "strategy", "context", "protection", "performance", "leaks", "smart", "market", "edge", "scout", "next", "followups")
FOOTER = "_Analysis of public on-chain data. Not financial advice._"


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
    if x is None:
        return "—"
    return f"{x / 24:.1f}d" if x >= 48 else f"{x:.1f}h"


def num(x, unit):
    if x is None:
        return "—"
    if x == float("inf"):
        return "∞"
    return {"h": hrs(x), "%": pct(x), "x": f"{x:.1f}×"}[unit]


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}"


def header(r):
    a, tr, act, rank = r["address"], r["track"], r["activity"], r.get("rank")
    lines = [f"# Your desk — `{short(a)}`",
             f"{r['days']} days · {act['fills']:,} fills · {act['coins']} coins · updated {datetime.datetime.utcfromtimestamp(r['now_ms'] / 1000).strftime('%Y-%m-%d %H:%M UTC')} · **YOUR QUANT — LIVE · READ-ONLY**"]
    if rank:
        tp = rank["top_pct"]
        lines.append(f"**#{rank['rank']:,} of {rank['of']:,}** on Hyperliquid's leaderboard this week · " + (f"top {tp:.1f}%" if tp <= 50 else f"bottom {100 - tp:.0f}%"))
    lines.append(f"**{r['archetype']}**")
    lab = r.get("labels") or {}
    if any(lab.get(k) for k in ("consistency", "risk", "activity")):
        lines.append("Senpi's read: " + " · ".join(str(lab[k]).upper() for k in ("consistency", "risk", "activity") if lab.get(k))
                     + (f" · consistency score {lab['tcs']}" if lab.get("tcs") is not None else ""))
    lines.append(f"> **{r['verdict']}**")
    if r["flags"]:
        lines.append(" ".join(f"`{f}`" for f in r["flags"]))
    return "\n".join(lines)


def overview(r):
    tr, d, book = r["track"], r["dimensions"], r["book"]
    out = [f"## Quant score **{r['quant_score']}**/100", "", "| Dimension | Score | What it means |", "|---|---:|---|"]
    names = {"timing": "Timing / edge", "risk": "Risk management", "cost": "Cost efficiency", "sizing": "Sizing / conviction", "consistency": "Consistency", "market_fit": "Market fit"}
    for k in ("timing", "risk", "cost", "sizing", "consistency", "market_fit"):
        out.append(f"| {names[k]} | {d[k]['score']} | {d[k]['line']} |")
    eq = r["equity"]
    ledger = tr.get("ledger_net")
    out += ["", f"## Track record ({r['days']} days)", "", "| Net P&L (ledger) | Return on avg equity | Realized on trades | Win rate | Max drawdown | Profit factor | Trades | Active days |", "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {usd(ledger, signed=True)} | {pct(eq.get('return_on_avg_equity'), 1, signed=True)} | {usd(tr['net'], signed=True)} | {pct(tr['win_rate'])} | {pct(-r['drawdown']['dd_pct'], 0, signed=True) if r['drawdown'].get('dd_pct') else '—'} | {num(tr['profit_factor'], 'x')} | {tr['trades']} | {r['activity']['active_days']} |"]
    cov = tr.get("coverage") or {}
    if cov.get("overall") is not None and cov["overall"] < 0.9:
        out.append(f"_Hyperliquid's public API returned about {pct(cov['overall'])} of your executed volume ({cov['episodes_incomplete']} of {cov['episodes']} round trips have gaps — TWAP slices older than a day aren't kept). Ledger figures are complete; trade-level patterns are read from the fills it returned._")
    cr = tr.get("cost_ratio"); wb = (r.get("benchmark") or {}).get("cost_ratio")
    out += ["", "## Where your P&L went", "", f"Gross **{usd(tr['gross_realized'], signed=True)}** → fees **{usd(-tr['fees'], signed=True)}** → funding **{usd(tr['funding'], signed=True)}** → net **{usd(tr['net'], signed=True)}**."]
    if cr is not None:
        out.append(f"Fees + funding took **{pct(cr)}** of your gross" + (f" — the whale median is {pct(wb)}." if wb is not None else "."))
    if r["leaks"]:
        out += ["", "## Top 3 things your agents found", ""]
        for i, l in enumerate(r["leaks"][:3], 1):
            out.append(f"{i}. **{l['agent']} · ~{usd(l['usd'])} / {l['window']}** — **{l['title']}.** {l['evidence']} _{l['counterfactual']}_ → {l['cta']}")
    return "\n".join(out)


def protection(r):
    b = r["book"]; sm = {x["coin"]: x for x in (r.get("smart") or {}).get("rows", [])}
    n = len(b["positions"])
    out = ["## Live positions — protection audit", "",
           f"Account value **{usd(b['account_value'])}** · margin used **{pct(b['margin_utilization'])}** · withdrawable **{usd(b['withdrawable'])}** · net uPnL **{usd(b['unrealized'], signed=True)}**",
           f"{n} open position{'s' if n != 1 else ''} · {len(b['naked'])} with no stop · {len(b['partial'])} partly covered · {r['market']['stance'] if r.get('market') else ''}" + (f" · paying {usd(-b['funding_per_day'])}/day in funding" if b['funding_per_day'] < 0 else (f" · collecting {usd(b['funding_per_day'])}/day in funding" if b['funding_per_day'] > 0 else ""))]
    if n:
        out += ["", "| Coin | Side | Lev | Notional | uPnL | ROE | Funding/day | To liq. | Stop cover | Status | Your quant would… |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
        for p in b["positions"]:
            status, note = _protection_note(p, sm.get(p["coin"]))
            out.append(f"| {p['coin']} | {p['side']} | {p['leverage'] or '—'}x | {usd(p['notional'])} | {usd(p['unrealized'], signed=True)} | {pct(p['roe'], 0, signed=True)} | {usd(p['funding_per_day'], signed=True)} | {pct(p['liq_distance_pct'] / 100, 1) if p['liq_distance_pct'] is not None else '—'} | {pct(p['stop_covered_share'])} | {status} | {note} |")
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
            (f"**Hold time (median):** winners {hrs(tr['hold_winners_h'])} · losers {hrs(tr['hold_losers_h'])}" + (f" — you hold losers {tr['hold_ratio']:.1f}× longer" if tr.get('hold_ratio') and tr['hold_ratio'] > 1.2 else (f" — you cut losers {1 / tr['hold_ratio']:.1f}× faster than you let winners run" if tr.get('hold_ratio') and 0 < tr['hold_ratio'] < 0.8 else ""))) if (tr.get('hold_winners_h') is not None and tr.get('hold_losers_h') is not None) else f"**Hold time:** only {tr['hold_n']['winners']} winning and {tr['hold_n']['losers']} losing round trips were fully observed — no hold-time claim on that sample.",
            f"**Execution:** {pct(tr['taker_share'])} taker · {tr['fee_rate_taker'] * 1e4:.1f} bp taker / {tr['fee_rate_maker'] * 1e4:.1f} bp maker · {tr['liquidations']} liquidation(s)"]
    sb = tr.get("size_buckets") or {}
    if sb.get("bands"):
        out += ["", f"**Size vs outcome** (median position {usd(sb['median_notional'])} notional):", "", "| Size band | Winners | Losers | Realized |", "|---|---:|---:|---:|"]
        out += [f"| {b['band']} | {b['winners']} | {b['losers']} | {usd(b['realized'], signed=True)} |" for b in sb["bands"]]
    return "\n".join(out)


def leaks(r):
    out = ["## Leaks — ranked by $ impact · counterfactual, not history", ""]
    if not r["leaks"]:
        out.append("No leak clears the bar on this window: every counterfactual the desk tests came out flat or negative, which means the process is not where the money is going.")
    for i, l in enumerate(r["leaks"], 1):
        out += [f"**{i:02d} · {l['title']}** — _{l['agent']}_ · **~{usd(l['usd'])} / {l['window']}**", f"{l['evidence']} {l['counterfactual']}", f"→ {l['cta']}", ""]
    tm = r.get("timing") or {}
    if tm.get("n"):
        neg = [k for k, g in (("time-cut on losers", tm.get("cut")), ("trailing lock on winners", tm.get("lock"))) if g and g.get("settings") and all(s["total"] <= 0 for s in g["settings"].values() if s["n"])]
        if neg:
            out.append(f"Tested and **rejected** for this book: a {' and a '.join(neg)} — each would have cost money on your biggest runs. Your edge is letting those run; don't fix what isn't leaking.")
    return "\n".join(out)


def smart(r):
    sm = r.get("smart"); out = ["## You vs smart money", ""]
    if not sm or not sm.get("rows"):
        out.append("No cohort view was available for this run." if not sm else "No open positions to compare.")
    else:
        out += [f"_Cohort: {sm['source']}_", "", "| Coin | You | Smart money | Read |", "|---|---|---|---|"]
        for x in sm["rows"]:
            out.append("| {} | {} | {} | **{}** |".format(x["coin"], x["you"], x["cohort"], x["read"]))
    bt = r.get("benchmark_table")
    if bt and (r.get("benchmark") or {}).get("n", 0) >= 5:
        out += ["", f"**You vs whale median** _(top-{(r.get('benchmark') or {}).get('n', '?')} public cohort, {(r.get('benchmark') or {}).get('computed_at', '')})_", "", "| Metric | You | Whale median |", "|---|---:|---:|"]
        for row in bt:
            you = num(row["you"], row["unit"]); wh = num(row["whale"], row["unit"])
            mark = ""
            if row["you"] is not None and row["whale"] is not None and row["you"] != float("inf"):
                worse = (row["you"] > row["whale"]) if row["better"] == "lower" else (row["you"] < row["whale"])
                mark = " 🔴" if worse else " 🟢"
            out.append(f"| {row['metric']} | {you}{mark} | {wh} |")
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
    b = r["book"]; out = ["## What your quant would do next", ""]
    i = 1
    at_risk = [p for p in b["positions"] if (p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5 and p["stop_covered_share"] < 0.9) or p["stop_covered_share"] == 0]
    if at_risk:
        out.append(f"{i}. **Protect first.** {', '.join(p['coin'] for p in at_risk)}: a stop ladder under each — a hard floor now, a trailing lock as it runs. This is a signature on positions you already hold, not a deposit."); i += 1
    if r["leaks"]:
        l = r["leaks"][0]
        out.append(f"{i}. **Fix the biggest leak.** {l['title']} — ~{usd(l['usd'])}/{l['window']}. {l['cta']}"); i += 1
    fam = r.get("families") or []
    out.append(f"{i}. **Keep the agents on.** Say *hire my quant* and senpi runs this desk on your book — risk guard, smart money, market regime, leak finder — and can code your best setup ({fam[0].replace('_', ' ') if fam else 'your pattern'}) into a strategy you approve, deployed as **your** strategy.")
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
            out.append(f"Funding regime (Senpi): **{fr['regime']}**" + (f" — {fr['extreme_count']} extreme-funding assets" if fr.get("extreme_count") is not None else "") + ".")
        groups = sorted((g for g in b.get("groups", {}).values()), key=lambda g: -g["oi_usd"])[:8]
        if groups:
            out += ["", "| Class | Avg 24h | Up / down | Median funding |", "|---|---:|---:|---:|"]
            out += [f"| {g['label']} | {pct(g['avg_change_pct'] / 100, 1, signed=True)} | {g['up']} / {g['down']} | {g['median_funding_bp_8h']:+.1f} bp/8h |" for g in groups]
    at = c.get("attention")
    if at and at.get("markets"):
        out += ["", "**Where the top traders' gains are right now** (Senpi Hyperfeed, 4h): " + "; ".join(f"{m['coin']} {m['direction'].lower()}s {m['share_of_gains']:.0f}% ({m['traders']} traders)" for m in at["markets"][:5]) + "."]
        if at.get("overlap"):
            out.append("You: " + ", ".join(f"{o['coin']} **{o['read']}** the top traders" for o in at["overlap"]) + ".")
        if at.get("momentum"):
            out.append("Momentum events in the last 4h: " + ", ".join(f"{m['coin']} {m['direction'].lower()} ×{m['events']}" for m in at["momentum"][:5])
                       + (f" — with you on {', '.join(at['with_momentum'])}" if at.get("with_momentum") else "") + (f"; against you on {', '.join(at['against_momentum'])}" if at.get("against_momentum") else "") + ".")
    rp = c.get("regime_performance") or {}
    cells = rp.get("cells") or {}
    if cells:
        out += ["", f"**How you trade the tape** — your record by the regime of the day you entered (last {r['days']} days: " + ", ".join(f"{v} {k.replace('_', '-')} days" for k, v in (rp.get('days') or {}).items()) + "):", "",
                "| Entered on | Trades | Win rate | Realized | Profit factor |", "|---|---:|---:|---:|---:|"]
        for key in ("risk_on/ALL", "risk_off/ALL", "mixed/ALL", "risk_on/LONG", "risk_off/LONG", "risk_on/SHORT", "risk_off/SHORT"):
            x = cells.get(key)
            if x and x["trades"] >= 3:
                out.append(f"| {x['regime'].replace('_', '-')} days · {x['side'].lower() if x['side'] != 'ALL' else 'all'} | {x['trades']} | {pct(x['wins'] / x['trades'])} | {usd(x['realized'], signed=True)} | {num(x['pf'], 'x')} |")
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
            out.append("Their book by class: " + "; ".join(f"{t['label']} {'long' if t['bias'] > 0 else 'short'} {abs(t['bias']):.0%} net ({t['long']}L/{t['short']}S)" for t in cv["tilt"][:4]) + ".")
        if cv.get("yours"):
            out.append("Yours: " + "; ".join(f"{t['label']} {'long' if t['bias'] > 0 else 'short'} {abs(t['bias']):.0%} net, {t['weight']:.0%} of the book" for t in cv["yours"][:4]) + ".")
        if cv.get("they_hold"):
            out.append("They hold, you don't: " + ", ".join(f"{h['coin']} ({h['side'].lower()}, {h['members']} wallets)" for h in cv["they_hold"][:6]) + ".")
        if cv.get("you_alone"):
            out.append("You hold, none of them do: " + ", ".join(cv["you_alone"]) + ".")
        if cv.get("entry_lag_h") is not None:
            out.append(f"Entry timing: you are {abs(cv['entry_lag_h']):.0f}h {'behind' if cv['entry_lag_h'] > 0 else 'ahead of'} this cohort's median entry on the coins you share.")
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
               "| Coin | Side | Mark | Hard stop | Distance | 24h range | Lock arms at | Covered today | Note |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for x in d["rows"]:
            atr = "—" if x["atr_pct"] is None else "{:.1f}%".format(x["atr_pct"])
            out.append("| {} | {} | {:,.4g} | {:,.4g} | {:.1f}% | {} | {:,.4g} | {} | {} |".format(x["coin"], x["side"], x["mark"], x["hard_stop"], x["hard_stop_pct"], atr, x["lock_arms_at"], pct(x["covered_now"]), x["note"]))
        out += ["", "The hard stop sits beyond one and a half days of normal range and above the liquidation price; the lock trails at half the peak gain once the trade is two ranges in the money. One signature on positions you already hold — no deposit."]
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "replay":
        if not d or d.get("empty"):
            return "No losing week in the window.\n\n" + FOOTER
        out = ["## Your worst week", "", f"Week of {datetime.datetime.utcfromtimestamp(d['start'] / 1000).strftime('%Y-%m-%d')}: **{usd(d['realized'], signed=True)}** over {d['trades']} trades ({d['losers']} losers).", "",
               "| Coin | Side | Hold | Realized |", "|---|---|---:|---:|"]
        out += [f"| {e['coin']} | {e['direction']} | {hrs(e['hold_h'])} | {usd(e['realized'], signed=True)} |" for e in d["biggest"]]
        for label, g in (("A time-cut on losers", d.get("cut")), ("A trailing lock on winners", d.get("lock"))):
            if g and g.get("settings"):
                out.append(f"\n**{label}** on exactly these trades: " + ", ".join(f"{k}: {usd(v['total'], signed=True)}" for k, v in g["settings"].items() if v["n"]) + (f" → robust {usd(g['robust'], signed=True)}" if g.get("robust") else " → not robust; would not have helped"))
        if d.get("green_first") is not None:
            out.append(f"\n{pct(d['green_first'])} of that week's losers were green first.")
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "funding":
        out = ["## Funding — next 30 days at today's rates", "", "| Coin | Side | Notional | Rate | Per day | 30 days | Paid since open |", "|---|---|---:|---:|---:|---:|---:|"]
        out += [f"| {x['coin']} | {x['side']} | {usd(x['notional'])} | {x['rate_bp_8h']:+.1f} bp/8h | {usd(x['per_day'], signed=True)} | {usd(x['thirty_days'], signed=True)} | {usd(x['since_open'], signed=True)} |" for x in d["rows"]]
        out.append(f"\nTotal: **{usd(d['thirty_days'], signed=True)}** over 30 days" + (f" — {pct(d['share_of_equity'])} of your equity" if d.get("share_of_equity") is not None else "") + (f". The payers: {', '.join(d['payers'])}." if d.get("payers") else "."))
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "compare":
        rc, pr = d.get("recent"), d.get("prior")
        if not rc or not pr:
            return "Not enough trades in both windows to compare.\n\n" + FOOTER
        rows = [("Trades", rc["trades"], pr["trades"], None), ("Win rate", rc["win_rate"], pr["win_rate"], "%"), ("Profit factor", rc["pf"], pr["pf"], "x"), ("Realized", rc["realized"], pr["realized"], "$"),
                ("Fees", rc["fees"], pr["fees"], "$"), ("Avg size", rc["avg_size"], pr["avg_size"], "$"), ("Median hold — winners", rc["hold_w"], pr["hold_w"], "h"), ("Median hold — losers", rc["hold_l"], pr["hold_l"], "h"), ("Taker share", rc["taker"], pr["taker"], "%")]
        out = ["## Last 30 days vs the 60 before", "", "| Metric | Last 30d | Prior 60d |", "|---|---:|---:|"]
        for m, a, b, u in rows:
            f = (lambda v: "—" if v is None else (f"{v:,.0f}" if u is None else (usd(v) if u == "$" else num(v, u))))
            out.append(f"| {m} | {f(a)} | {f(b)} |")
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "rules":
        out = ["## Your strategy as a rule set", ""]
        out += [f"- **Entry setups:** {'; '.join(d['entries']) if d['entries'] else 'no setup clears the bar yet'}", f"- **Entry timing:** {d['entry_rule']}", f"- **Holding:** {d['hold_rule']}", f"- **Sizing:** {d['size_rule']}", f"- **Risk:** {d['risk_rule']}", f"- **Catalog families:** {', '.join(f.replace('_', ' ') for f in d['families'])}", "", d["handoff"] + "."]
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "regime":
        rr = dict(r); rr["context"] = dict(r.get("context") or {}, breadth=dict((r.get("context") or {}).get("breadth") or {}, day=d.get("today")))
        return context(rr) + "\n\n" + FOOTER
    if mode == "watch":
        out = ["## What your agents would watch", ""] + [f"- {x}" for x in d["items"]] + ["", "Say *hire my quant* to keep them on your book."]
        return "\n".join(out) + "\n\n" + FOOTER
    if mode == "smart":
        return smart_v2(dict(r, cohorts=d.get("cohorts"))) + "\n\n" + FOOTER
    if mode == "scout":
        return scout(dict(r, opportunities=d.get("opportunities"))) + "\n\n" + FOOTER
    if mode == "strategy":
        return strategy(dict(r, strategy=d)) + "\n\n" + FOOTER
    return FOOTER


RENDERERS = {"overview": overview, "strategy": strategy, "context": context, "protection": protection, "performance": performance, "leaks": leaks, "smart": smart_v2,
             "market": market, "edge": edge, "scout": scout, "next": next_steps, "followups": followups_section}


def render(r, sections=None):
    parts = [header(r), ""]
    for s in (sections or SECTIONS):
        parts += [RENDERERS[s](r), ""]
    meta = r.get("meta") or {}
    if meta.get("warnings"):
        parts.append("_Notes: " + " · ".join(meta["warnings"]) + "_\n")
    parts.append(FOOTER)
    return "\n".join(parts)
