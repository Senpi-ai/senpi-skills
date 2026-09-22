#!/usr/bin/env python3
"""Candle-based timing analyses per complete round trip: the move before entry (chasing), favourable and
adverse excursions, give-back of peak gains, and counterfactuals that are only ever surfaced when they
are positive across a small fixed grid (never a fitted parameter). Dollar figures use peak size × price
move — an approximation that ignores adds and partials, and is labelled as such."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import bisect
import statistics

H = 3_600_000.0
CUT_GRID_H = (12.0, 24.0, 48.0)                 # time-cut on losers
LOCK_GRID = ((0.03, 0.5), (0.05, 0.5), (0.05, 0.3))  # (arm at +peak%, lock this share of the peak)
CHASE_PCT = 0.03                                 # an entry after a ≥3% move in its direction over 24h


def load_candles(raw):
    out = {}
    for coin, rows in (raw or {}).items():
        if rows:
            out[coin] = ([r[0] for r in rows], rows)
    return out


def px_at(c, t):
    times, rows = c
    i = bisect.bisect_right(times, t) - 1
    return rows[i][4] if i >= 0 else None


def path(c, t0, t1):
    times, rows = c
    return rows[bisect.bisect_left(times, t0):bisect.bisect_right(times, t1)]


def _fav(r, sgn, ent):
    return sgn * ((r[2] if sgn > 0 else r[3]) / ent - 1)


def _adv(r, sgn, ent):
    return sgn * ((r[3] if sgn > 0 else r[2]) / ent - 1)


def _at(path, times, t, p):
    """What the trade would show if the WHOLE position were closed at time `t`, price `p`: the P&L
    already booked by then, plus mark-to-market on the size actually open at that moment against its
    average entry.

    The old form was `return x peak_size x entry_vwap` — it priced the PEAK size as if it had been
    held from entry through to `t`. On a position scaled in and out those are not the same trade and
    the error scales with the notional: xyz:SKHX ran 507 fills with $16.3M entered against a $7.1M
    peak and its 48h cut came back at +$96,858 against a $44,302 loss, 2.2x the thing it was
    "fixing". Three of four losing books were offered a recovery larger than their entire loss.
    Pricing on the exposure that existed removes the assumption rather than bounding its damage.
    (B6, @0xsarvesh #718.)"""
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return None
    _, size, avg, booked = path[i]
    if avg is None:
        return None
    return booked + size * (p - avg)


def per_trade(episodes, candles):
    out = []
    for e in episodes:
        c = candles.get(e["coin"]); ent = e["entry_vwap"]
        if not c or not ent or not e.get("complete"):
            continue
        sgn = 1 if e["direction"] == "LONG" else -1
        p24 = px_at(c, e["open_time"] - 24 * H)
        pre24 = sgn * (ent / p24 - 1) if p24 else None
        rows = [r for r in path(c, e["open_time"] - H, e["close_time"]) if r[0] >= e["open_time"] - H]
        ntl = e["peak_size"] * ent
        # There IS per-moment exposure in this data — `size_path` carries it — so the exit grid no
        # longer has to assume the peak size was held throughout, and no longer has to decline the
        # positions where that assumption fails worst. The `cycled` skip this replaces was the right
        # call while the counterfactual was constant-size; it dropped 10 of 11 rows on one of the
        # books in the report, which is its own kind of wrong. (B6, @0xsarvesh #718.)
        # senpi's indexed rows are aggregate positions, not fills: one size, one entry price, no
        # intra-trade path — and for those the constant-size form was always exactly right
        # (`entry_val == peak_notional`, so they never tripped the old `cycled` skip either). The
        # degenerate one-step path below reproduces the old arithmetic for them to the cent, so this
        # change lands only where there IS a path to be wrong about.
        spath = e.get("size_path") or [(e["open_time"], sgn * e["peak_size"], ent, 0.0)]
        stimes = [x[0] for x in spath]
        mfe = mae = 0.0
        cut_px = {h: None for h in CUT_GRID_H}
        for r in rows:
            mfe = max(mfe, _fav(r, sgn, ent)); mae = min(mae, _adv(r, sgn, ent))
            for h in CUT_GRID_H:
                if cut_px[h] is None and r[0] >= e["open_time"] + h * H:
                    cut_px[h] = (r[0], r[4])
        realized_pct = e["realized"] / ntl if ntl else 0.0
        give_back = ((mfe - realized_pct) / mfe) if (mfe > 0 and realized_pct < mfe) else 0.0
        locks = {}
        for arm, share in LOCK_GRID:
            locks[f"{arm:.2f}/{share:.1f}"] = _lock_cf(rows, e, sgn, ent, spath, stimes, mfe, arm, share)
        cuts = {}
        for h in CUT_GRID_H:
            hit = cut_px[h]
            # every trade still open at hour h, winner or loser. Charging a time-cut only on the
            # losers is survivorship bias — at hour h you do not yet know which is which — and it
            # inflated the cut counterfactual to more than the book's whole equity.
            v = _at(spath, stimes, hit[0], hit[1]) if (hit and e["hold_h"] > h) else None
            cuts[f"{h:.0f}"] = (v - e["realized"]) if v is not None else None
        out.append(dict(coin=e["coin"], direction=e["direction"], realized=e["realized"], hold_h=e["hold_h"], win=e["win"], pre24=pre24,
                        chased=(pre24 is not None and pre24 >= CHASE_PCT), mfe=mfe, mae=mae, realized_pct=realized_pct, give_back=give_back,
                        notional=ntl, lock_cf=locks, cut_cf=cuts, open_time=e["open_time"]))
    return out


def _lock_cf(rows, e, sgn, ent, spath, stimes, mfe, arm, share):
    """Exit when price gives back (1-share) of the peak gain, once the peak reached `arm`. Returns the
    dollar difference vs what was realized, or None when the rule never engaged.

    The RULE is unchanged — a fixed grid measured against the entry VWAP, never fitted. What changed
    is the pricing of the exit it produces: the stop fills the position that exists at that moment,
    not the peak size assumed held since entry."""
    if mfe < arm:
        return None
    armed, lvl = False, None
    for r in rows:
        if r[0] < e["open_time"]:
            continue
        fav = _fav(r, sgn, ent)
        if fav >= arm:
            armed = True
        if armed:
            lvl = max(lvl or 0.0, fav * share)
            if _adv(r, sgn, ent) <= lvl:
                v = _at(spath, stimes, r[0], ent * (1 + sgn * lvl))
                return None if v is None else v - e["realized"]
    return None


def summarize(rows):
    med = lambda xs: statistics.median(xs) if xs else None  # noqa: E731
    wins = [r for r in rows if r["win"]]; losses = [r for r in rows if not r["win"]]
    chased = [r for r in rows if r["chased"]]; calm = [r for r in rows if r["pre24"] is not None and not r["chased"]]
    def pf(rs):
        w = sum(r["realized"] for r in rs if r["realized"] > 0); l = -sum(r["realized"] for r in rs if r["realized"] <= 0)
        return (w / l) if l else (None if not w else float("inf"))
    cuts = {h: [r["cut_cf"][h] for r in rows if r["cut_cf"][h] is not None] for h in (f"{x:.0f}" for x in CUT_GRID_H)}
    locks = {k: [r["lock_cf"][k] for r in rows if r["lock_cf"][k] is not None] for k in rows[0]["lock_cf"]} if rows else {}
    return dict(n=len(rows),
                pre24_median=med([r["pre24"] for r in rows if r["pre24"] is not None]),
                chased_share=(len(chased) / len([r for r in rows if r["pre24"] is not None])) if rows and any(r["pre24"] is not None for r in rows) else None,
                chased_n=len(chased), chased_realized=sum(r["realized"] for r in chased), chased_pf=pf(chased), calm_pf=pf(calm),
                mfe_median_winners=med([r["mfe"] for r in wins]), give_back_median=med([r["give_back"] for r in wins if r["mfe"] > 0]),
                mae_median_losers=med([r["mae"] for r in losses]),
                losers_that_were_green=(sum(1 for r in losses if r["mfe"] >= 0.01) / len(losses)) if losses else None,
                cut=_grid(cuts), lock=_grid(locks))


def _grid(d):
    """Per setting: total $ and count; plus `robust` = the median setting's total when ≥ 2 of the settings are
    positive — the only number the desk is allowed to call a leak."""
    per = {k: dict(total=sum(v), n=len(v)) for k, v in d.items()}
    totals = sorted(x["total"] for x in per.values() if x["n"])
    robust = None
    if len(totals) >= 2 and sum(1 for t in totals if t > 0) >= 2:
        robust = statistics.median(totals)
    return dict(settings=per, robust=robust)
