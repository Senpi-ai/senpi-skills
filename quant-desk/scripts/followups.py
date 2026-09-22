#!/usr/bin/env python3
"""The bank of ten follow-ups the quant is prepared to go into, and which three to five to offer after a
desk. Each maps to a `--deep <mode>`; the desk holds these back on purpose — the more the trader asks, the
more of their own book they see."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
# Two of these answer from what is ALREADY on screen and carry no `--deep` mode: the desk output
# holds the numbers, so the quant expands them in conversation rather than re-running anything.
# `eli5` is named ELI5 on purpose: traders know the term, and it signals "ask me anything" better
# than "plain English" does. It leads because the reader most likely to bounce is the one who did
# not follow the vocabulary,
# and it is the cheapest possible next step — no wallet, no signup, no wait.
MODELESS = {"eli5", "leak"}

BANK = {
    "eli5":     "Want the ELI5 — what this means and what to do about it, without the trading vocabulary?",
    "leak":     "Want me to walk through the biggest one — {leak} — and what it would take to stop it?",
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
ORDER = ["eli5", "leak", "protect", "smart", "scout", "replay", "regime", "funding", "compare", "rules", "strategy", "watch"]

# the same ten modes, asked about SOMEONE ELSE's book — learning from a trader, not fixing your own
BANK_OTHER = {
    "eli5":     "Want the ELI5 — what this trader does well, what they do badly, without the vocabulary?",
    "leak":     "Want me to walk through their biggest one — {leak} — and what it would take to stop it?",
    "rules":    "Want their playbook written up as a rule set — the setups, the holds, the sizing — that your quant could run under your name?",
    "smart":    "Want the full smart-money picture on their coins — where the proven cohort and the hot 30-day cohort agree and disagree with them, and when they moved?",
    "strategy": "Want the long version of what they've been doing — position by position, and where the thesis holds and breaks?",
    "scout":    "Want me to scout today's market for setups that match how they win?",
    "regime":   "Want to see how they trade risk-off tape versus risk-on — and whether today is their kind of day?",
    "replay":   "Want me to replay their worst week and show what a time-cut and a trailing lock would have done to it?",
    "compare":  "Want their last 30 days against the 60 before — are they getting better or worse?",
    "protect":  "Want the stop ladder their book is missing — what each open position would look like protected?",
    "funding":  "Want their funding bill for the next 30 days at today's rates — is the carry paying them or costing them?",
    "watch":    "Want me to keep watching this wallet — a new position, a flip, a size change — and tell you when they move?",
}
ORDER_OTHER = ["eli5", "leak", "rules", "smart", "strategy", "scout", "regime", "replay", "compare", "protect", "funding", "watch"]


def offer(r, n=4, whose="mine"):
    """Pick the follow-ups this desk earned, most relevant first."""
    if whose == "other":
        return _offer_other(r, n)
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
    # Plain English leads for the reader who did not follow the vocabulary — UNLESS the book is in
    # trouble. A position that is unprotected AND near liquidation outranks everything; the desk's own
    # recommendation is "Protect first", and a follow-up list that opens with "want this explained?"
    # over a book 38% from liquidation is the desk disagreeing with itself.
    urgent = bool(naked) and near
    score["eli5"] += 40 if urgent else 99
    score["protect"] += 100 if urgent else 0
    score["leak"] += 50 if r.get("leaks") else -99
    ranked = sorted(BANK, key=lambda k: (-score[k], ORDER.index(k)))
    return _fill(ranked[:n], BANK, r)


def _offer_other(r, n):
    score = {k: 0.0 for k in BANK_OTHER}
    setups = (r.get("setups") or {}).get("best") or []
    cohorts = r.get("cohorts") or []
    score["rules"] += 3 + (1 if setups else 0)
    score["smart"] += 2 + sum(len(c.get("against") or []) for c in cohorts)
    score["strategy"] += 2 + (1 if (r.get("strategy") or {}).get("critique") else 0)
    score["scout"] += 1 + (1 if r.get("opportunities") else 0)
    score["regime"] += 1 + (1 if ((r.get("context") or {}).get("regime_performance") or {}).get("cells") else 0)
    score["replay"] += 1 + (1 if any("losers" in l["title"] or "give back" in l["title"] for l in r["leaks"]) else 0)
    score["compare"] += 1 + (1 if (r["track"].get("trades") or 0) >= 30 else 0)
    score["protect"] += 1 + (1 if r["book"]["naked"] else 0)
    score["funding"] += 1 + (1 if abs(r["book"].get("funding_per_day") or 0) > 0.001 * max(1, r["book"].get("account_value") or 1) else 0)
    score["watch"] += 2
    score["eli5"] += 99
    score["leak"] += 50 if r.get("leaks") else -99
    ranked = sorted(BANK_OTHER, key=lambda k: (-score[k], ORDER_OTHER.index(k)))
    return _fill(ranked[:n], BANK_OTHER, r)


def _fill(keys, bank, r):
    """Name the finding in the prompt. "Walk through the biggest one" is a menu item; "walk through the
    71% of every winner's peak you give back" is a question about THEM, and that is the whole difference
    between a reader who asks a second question and one who closes the tab."""
    leaks = r.get("leaks") or []
    top = (leaks[0].get("title") or "").rstrip(".") if leaks else ""
    out = []
    for k in keys:
        prompt = bank[k]
        if "{leak}" in prompt:
            if not top:
                continue
            prompt = prompt.replace("{leak}", top[0].lower() + top[1:] if top else top)
        out.append(dict(mode=None if k in MODELESS else k, prompt=prompt))
    return out
