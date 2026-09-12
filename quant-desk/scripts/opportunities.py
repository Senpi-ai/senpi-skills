#!/usr/bin/env python3
"""Live matches: where today's market, the cohorts and the trader's own best pattern point the same way.
Process framing only — a match is a setup that fits how this trader wins, never a call to buy a coin."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import taxonomy

CHASE_PCT_24H = 5.0


def _pattern_keys(setups, closed, majors, large):
    """The trader's winning patterns as (coin, side) and (class, side) keys with their evidence."""
    keys = {}
    for b in (setups or {}).get("best") or []:
        lab = b["label"]
        for e in closed:
            cs = f"{e['coin']} {e['direction'].lower()}s"
            if lab == cs:
                keys[("coin", e["coin"], e["direction"])] = b
        if lab.endswith("s held < 4h") or "held" in lab or "entries" in lab:
            continue
    # class-level: best class×side by profit factor with ≥ 4 trades
    import collections
    g = collections.defaultdict(list)
    for e in closed:
        g[(taxonomy.classify(e["coin"], majors, large), e["direction"])].append(e)
    for (cls, side), eps in g.items():
        if len(eps) >= 4:
            w = sum(x["realized"] for x in eps if x["realized"] > 0); l = -sum(x["realized"] for x in eps if x["realized"] <= 0)
            pf = (w / l) if l else (float("inf") if w else None)
            if pf and pf >= 1.5:
                keys[("class", cls, side)] = dict(label=f"{taxonomy.label(cls)} {side.lower()}s", n=len(eps), wins=sum(1 for x in eps if x["win"]), profit_factor=pf)
    return keys


def scout(closed, setups, book, breadth, regimes, cohorts, attention, majors, large, limit=6):
    """Rank (coin, side) candidates the cohorts lean on or the trader already wins on, by fit."""
    patterns = _pattern_keys(setups, closed, majors, large)
    mine = {p["coin"]: p["side"] for p in book["positions"]}
    cands = {}
    def add(coin, side, why, pts):
        d = cands.setdefault((coin, side), dict(coin=coin, side=side, score=0.0, why=[]))
        d["score"] += pts; d["why"].append(why)
    for cv in cohorts or []:
        for h in cv.get("they_hold") or []:
            if h["members"] >= 5:
                add(h["coin"], h["side"], f"{cv['name']} cohort: {h['members']} wallets {h['side'].lower()} (bias {h['bias']:+.2f})", 2.0 if cv["name"] == "proven" else 1.0)
        for r in cv.get("rows") or []:
            if r["read"].startswith("WITH") and r["members"] >= 5:
                add(r["coin"], mine[r["coin"]], f"{cv['name']} cohort is with your {mine[r['coin']].lower()} ({r['members']} wallets)", 1.0)
    for m in (attention or {}).get("markets") or []:
        if m["coin"] and m["traders"] >= 5:
            add(m["coin"], m["direction"], f"top traders' gains: {m['share_of_gains']:.0f}% from {m['direction'].lower()}s ({m['traders']} traders)", 1.0)
    for m in (attention or {}).get("momentum") or []:
        add(m["coin"], m["direction"], f"{m['events']} momentum event(s) {m['direction'].lower()} in the last 4h", 0.5)
    for (kind, key, side), ev in patterns.items():
        if kind == "coin":
            add(key, side, f"your own pattern: {ev['wins']} of {ev['n']} wins on {key} {side.lower()}s", 3.0)
    out = []
    for (coin, side), d in cands.items():
        cls = taxonomy.classify(coin, majors, large)
        pat = patterns.get(("class", cls, side))
        if pat:
            pf = "∞" if pat["profit_factor"] == float("inf") else f"{pat['profit_factor']:.1f}×"
            d["score"] += 2.0; d["why"].append(f"fits how you win: {pat['label']} {pat['wins']} of {pat['n']}, profit factor {pf}")
        r = (regimes or {}).get(coin)
        if r:
            if r["trend"] == "RANGING":
                d["why"].append("trend: ranging")
            elif (side == "LONG") == (r["trend"] == "UP"):
                d["score"] += 2.0; d["why"].append(f"trend: {r['trend'].lower()} — with you")
            else:
                d["score"] -= 2.5; d["why"].append(f"trend: {r['trend'].lower()} — against")
        a = (breadth or {}).get("assets", {}).get(coin)
        if a:
            fb = a["funding_bp_8h"]
            pays = (side == "LONG" and fb > 10) or (side == "SHORT" and fb < -10)
            if pays:
                d["score"] -= 1.0; d["why"].append(f"funding {fb:+.0f} bp/8h — you would pay to hold")
            elif (side == "LONG" and fb < -3) or (side == "SHORT" and fb > 3):
                d["score"] += 0.5; d["why"].append(f"funding {fb:+.0f} bp/8h — the side that collects")
            ch = a["change_pct"]
            if (side == "LONG" and ch > CHASE_PCT_24H) or (side == "SHORT" and ch < -CHASE_PCT_24H):
                d["score"] -= 1.0; d["why"].append(f"already moved {ch:+.1f}% today — entering here is a chase")
        d["held"] = mine.get(coin)
        d["cls"] = taxonomy.label(cls)
        out.append(d)
    out.sort(key=lambda d: -d["score"])
    return [d for d in out if d["score"] > 0][:limit]
