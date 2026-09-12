#!/usr/bin/env python3
"""The deep modes behind the follow-ups. Each takes the cached analysis (plus candles where needed) and
returns a dict the renderer knows. Same rules as the desk: computed, counterfactual, process only."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import bisect
import collections
import statistics

import timing as timing_mod

H = 3_600_000.0
DAY_MS = 86_400_000


def _atr(candles, coin, hours=24):
    c = candles.get(coin)
    if not c:
        return None
    rows = c[1][-hours:]
    trs = [r[2] - r[3] for r in rows]
    return statistics.mean(trs) if trs else None


def protect(r, candles):
    """A stop ladder per open position: hard floor above liquidation and beyond one day's range, a
    trailing lock once the trade has paid, and the dollars at risk before vs after."""
    out = []
    for p in r["book"]["positions"]:
        atr = _atr(candles, p["coin"]); mark = p["mark"]; sgn = 1 if p["side"] == "LONG" else -1
        if not mark:
            continue
        floor_dist = (1.5 * atr) if atr else 0.03 * mark
        liq = p["liq_px"]
        hard = mark - sgn * floor_dist
        if liq:
            buffer = 0.4 * abs(mark - liq)
            hard = (max(hard, liq + buffer) if sgn > 0 else min(hard, liq - buffer))
        risk_now = p["margin_used"] if p["liq_px"] else p["notional"] * 0.5
        risk_after = abs(mark - hard) * p["size"]
        lock_arm = mark + sgn * (2 * (atr or 0.02 * mark))
        out.append(dict(coin=p["coin"], side=p["side"], mark=mark, hard_stop=hard, hard_stop_pct=100 * abs(mark - hard) / mark, atr_pct=(100 * atr / mark) if atr else None,
                        liq_px=liq, lock_arms_at=lock_arm, lock_share=0.5, risk_now=risk_now, risk_after=risk_after,
                        covered_now=p["stop_covered_share"], note=("already covered" if p["stop_covered_share"] >= 0.9 else ("extend to full size" if p["stop_covered_share"] > 0 else "no stop today"))))
    return dict(rows=out, total_risk_now=sum(x["risk_now"] for x in out), total_risk_after=sum(x["risk_after"] for x in out))


def replay(r, candles):
    """The worst 7-day window by realized P&L, its trades, and the two counterfactuals on exactly those."""
    eps = [e for e in r["episodes"] if e.get("close_time")]
    if not eps:
        return None
    eps.sort(key=lambda e: e["close_time"])
    worst, best_t = 0.0, None
    for e in eps:
        t0 = e["close_time"]; s = sum(x["realized"] for x in eps if t0 <= x["close_time"] < t0 + 7 * DAY_MS)
        if s < worst:
            worst, best_t = s, t0
    if best_t is None:
        return dict(empty=True)
    week = [e for e in eps if best_t <= e["close_time"] < best_t + 7 * DAY_MS]
    rows = timing_mod.per_trade([e for e in week if e.get("complete")], candles) if candles else []
    s = timing_mod.summarize(rows) if rows else None
    return dict(start=best_t, realized=worst, trades=len(week), losers=sum(1 for e in week if not e["win"]),
                biggest=sorted(week, key=lambda e: e["realized"])[:5], cut=(s or {}).get("cut"), lock=(s or {}).get("lock"),
                green_first=(s or {}).get("losers_that_were_green"))


def funding_forecast(r):
    rows = []
    for p in r["book"]["positions"]:
        rows.append(dict(coin=p["coin"], side=p["side"], notional=p["notional"], per_day=p["funding_per_day"], thirty_days=30 * p["funding_per_day"],
                         rate_bp_8h=p["funding_rate_hourly"] * 8 * 1e4, since_open=-p["funding_since_open"]))
    tot = sum(x["thirty_days"] for x in rows)
    av = r["book"].get("account_value") or 0
    return dict(rows=rows, thirty_days=tot, share_of_equity=(tot / av) if av else None, payers=[x["coin"] for x in rows if x["per_day"] < 0])


def compare_windows(r):
    """Last 30 days vs the 60 before, on the things that cost money."""
    now = r["now_ms"]; cut = now - 30 * DAY_MS
    recent = [e for e in r["episodes"] if (e["close_time"] or 0) >= cut]; prior = [e for e in r["episodes"] if 0 < (e["close_time"] or 0) < cut]
    def stats(eps):
        if not eps:
            return None
        w = [e for e in eps if e["win"]]; l = [e for e in eps if not e["win"]]
        ws = sum(e["realized"] for e in w); ls = -sum(e["realized"] for e in l)
        comp_w = [e["hold_h"] for e in w if e.get("complete")]; comp_l = [e["hold_h"] for e in l if e.get("complete")]
        return dict(trades=len(eps), win_rate=len(w) / len(eps), pf=(ws / ls) if ls else (float("inf") if ws else None), realized=sum(e["realized"] for e in eps),
                    fees=sum(e["fees"] for e in eps), avg_size=statistics.mean(e["peak_notional"] for e in eps if e["peak_notional"] > 0) if any(e["peak_notional"] > 0 for e in eps) else None,
                    hold_w=statistics.median(comp_w) if len(comp_w) >= 3 else None, hold_l=statistics.median(comp_l) if len(comp_l) >= 3 else None,
                    taker=(sum(e["taker_volume"] for e in eps) / sum(e["volume"] for e in eps)) if sum(e["volume"] for e in eps) else None)
    return dict(recent=stats(recent), prior=stats(prior))


def rules(r):
    """The trader's strategy as a rule set — what the record says they do when it works — for the coder."""
    fp = (r.get("strategy") or {}).get("fingerprint") or {}
    setups = (r.get("setups") or {}).get("best") or []
    tr = r["track"]
    ent = []
    for b in setups[:2]:
        ent.append(f"{b['label']} — {b['wins']} of {b['n']} wins, profit factor {b['profit_factor'] if b['profit_factor'] != float('inf') else '∞'}")
    tm = r.get("timing") or {}
    entry_rule = ("enter before the move: skip anything already up ≥ 3% on the day" if (tm.get("chased_pf") or 9) < (tm.get("calm_pf") or 0) else "entries after strength are fine for you — keep buying breakouts")
    hold = f"winners run a median {tr['hold_winners_h']:.0f}h; losers should not outlive {min(24, max(4, tr['hold_winners_h'] or 12)):.0f}h" if tr.get("hold_winners_h") else "hold time: not enough complete trades to set"
    size = f"size at your median ({fp.get('trades_per_active_day', 0):.1f} trades/day); never above 1.5× median on a loser's thesis" if fp else "fixed-fraction sizing"
    risk = "hard stop on every position; a trailing lock at 50% of the peak once +2 ATR; daily entry cap at your median day"
    fams = r.get("families") or ["trend_following"]
    return dict(entries=ent, entry_rule=entry_rule, hold_rule=hold, size_rule=size, risk_rule=risk, families=fams,
                handoff=f"senpi-strategy-discover: closest templates in the {' / '.join(f.replace('_', ' ') for f in fams)} families; senpi-strategy-author to write these rules from scratch, deployed as your strategy")


def regime(r):
    ctx = r.get("context") or {}
    return dict(today=ctx.get("breadth", {}).get("day"), performance=ctx.get("regime_performance"))


def watch(r):
    book = r["book"]
    items = []
    if book["naked"] or book["partial"]:
        items.append(f"Risk guard: a stop missing or a position within 5% of liquidation ({', '.join(book['naked'] + book['partial'])} today)")
    items.append("Smart money: the proven cohort flipping against a coin you hold, or building a coin you don't")
    items.append("Market regime: the day turning risk-off while your book is net long — or funding turning against you")
    items.append("Leak finder: weekly — fees, funding and hold time with the dollar figure")
    return dict(items=items)


MODES = {"protect": "protect", "smart": "smart", "scout": "scout", "replay": "replay", "funding": "funding", "regime": "regime", "compare": "compare", "rules": "rules", "strategy": "strategy", "watch": "watch"}
