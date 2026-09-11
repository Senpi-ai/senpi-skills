#!/usr/bin/env python3
"""The desk as Markdown for chat. Sections can be rendered alone (`--section`) so a follow-up question
("are my positions protected?") answers from the cached analysis without a refetch."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import datetime

SECTIONS = ("overview", "protection", "performance", "leaks", "smart", "market", "edge", "next")
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


RENDERERS = {"overview": overview, "protection": protection, "performance": performance, "leaks": leaks, "smart": smart, "market": market, "edge": edge, "next": next_steps}


def render(r, sections=None):
    parts = [header(r), ""]
    for s in (sections or SECTIONS):
        parts += [RENDERERS[s](r), ""]
    meta = r.get("meta") or {}
    if meta.get("warnings"):
        parts.append("_Notes: " + " · ".join(meta["warnings"]) + "_\n")
    parts.append(FOOTER)
    return "\n".join(parts)
