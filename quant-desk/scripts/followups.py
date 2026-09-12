#!/usr/bin/env python3
"""The bank of ten follow-ups the quant is prepared to go into, and which three to five to offer after a
desk. Each maps to a `--deep <mode>`; the desk holds these back on purpose — the more the trader asks, the
more of their own book they see."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
BANK = {
    "protect":  "Want me to draft the stop ladder for each open position — hard floor, trailing lock, and what each one changes about your worst case?",
    "smart":    "Want the full smart-money picture — every coin you trade against the proven cohort and the hot 30-day cohort, and when they moved?",
    "scout":    "Want me to scout today's market for setups that match how you actually win?",
    "replay":   "Want me to replay your worst week and show what a time-cut and a trailing lock would have done, trade by trade?",
    "funding":  "Want your funding bill for the next 30 days at today's rates, position by position?",
    "regime":   "Want to see how you trade in risk-off tape versus risk-on — and which one today is?",
    "compare":  "Want your last 30 days against the 60 before — are you getting better or worse at the things that cost you?",
    "rules":    "Want your strategy written up as a rule set your quant can run for you, under your name?",
    "strategy": "Want the long version of what you've been doing — position by position, and where the thesis holds and breaks?",
    "watch":    "Want me to keep watching this book — a missing stop, a whale flipping against you, a regime change — and tell you the moment it happens?",
}
ORDER = ["protect", "smart", "scout", "replay", "regime", "funding", "compare", "rules", "strategy", "watch"]


def offer(r, n=4):
    """Pick the follow-ups this desk earned, most relevant first."""
    book, tr, tm = r["book"], r["track"], r.get("timing") or {}
    score = {k: 0.0 for k in BANK}
    naked = len(book["naked"]) + len(book["partial"])
    near = any(p["liq_distance_pct"] is not None and p["liq_distance_pct"] < 5 for p in book["positions"])
    score["protect"] += 3 * bool(naked) + 3 * near
    cohorts = r.get("cohorts") or []
    against = sum(len(c.get("against") or []) for c in cohorts)
    score["smart"] += 2 + against + (1 if any(c.get("they_hold") for c in cohorts) else 0)
    score["scout"] += 2 + (1 if r.get("opportunities") else 0) + (1 if (r.get("setups") or {}).get("best") else 0)
    worst = [l for l in r["leaks"] if "losers" in l["title"] or "give back" in l["title"]]
    score["replay"] += 1 + 2 * bool(worst) + (1 if (tr.get("largest_loss") or 0) < -0.05 * max(1, book.get("account_value") or 1) else 0)
    rp = (r.get("context") or {}).get("regime_performance") or {}
    score["regime"] += 1 + (2 if rp.get("cells") else 0)
    score["funding"] += 1 + (2 if (book.get("funding_per_day") or 0) < 0 else 0) + (1 if any("funding" in l["title"] for l in r["leaks"]) else 0)
    score["compare"] += 1 + (1 if (tr.get("trades") or 0) >= 30 else 0)
    score["rules"] += 1 + (2 if (r.get("setups") or {}).get("best") else 0)
    score["strategy"] += 1 + (1 if (r.get("strategy") or {}).get("critique") else 0)
    score["watch"] += 1 + (1 if naked else 0)
    ranked = sorted(BANK, key=lambda k: (-score[k], ORDER.index(k)))
    return [dict(mode=k, prompt=BANK[k]) for k in ranked[:n]]
