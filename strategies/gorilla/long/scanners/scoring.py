"""GORILLA — pure thesis logic (no I/O, no MCP).

A SELF-DIRECTED thesis hedge fund built on the REAL machinery of the two skills
it composes (math ported from their engines, constants verbatim):

  UNIVERSE (derive_universe) — from the LIVE instrument list each refresh:
          every main-dex perp over a 24h-notional floor, top-N by volume.
  PULSE   (pulse_stance)     — ported from senpi-market-pulse compute_signals:
          cross-asset day classification (groups up vs down off prevDayPx
          changes: crypto + semis + megacap + indices + commodities + FX) with
          the confirmation checklist (GOLD haven bid, DXY funding stress, VIX
          fear). The stance is the whole market's read, not a BTC proxy.
  COHORTS (cohort_positions_bias / smart_conviction / divergences) — ported
          from senpi-smart-money: the PROVEN cohort (lifetime realized >= $1M)
          vs the CROWD ($10k..$100k), per-coin bias = net/gross signed
          notional; a divergence = opposite sides or gap >= 0.50 with >= 5
          members each. This is the entry edge; the 4h leaderboard is only a
          near-term confirm at press time.
  THESIS  (derive_thesis)    — stance from the pulse; the LONG bucket from
          smart-long divergences/conviction (crowd hasn't followed), the SHORT
          bucket from smart-short; 24h-change relative strength is the
          FALLBACK ranking when cohorts are unavailable (flagged honestly in
          the narrative — never silently pretend).
  PRESS   (score_entry)      — a bucket name is only pressed when the tape
          confirms (4h/1h structure + momentum + near-term SM lean).
  RETHINK/REBALANCE (close_triggers) — 48h thesis_shift + smart-bias reversal,
          7d weekly_rebalance laggard recycle, via CLOSE_POSITION.

Shared VERBATIM by both instances; the book's identity comes from inputs.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

# smart-money engine constants (smartmoney.py verbatim; overridable via inputs.cohorts)
SMART_MIN_REALIZED = 1_000_000
CROWD_MIN_REALIZED = 10_000
CROWD_MAX_REALIZED = 100_000
MIN_MEMBERS = 5
LEAN_THRESHOLD = 0.40
DIVERGENCE_MIN_GAP = 0.50


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct_change(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


def closes(candles):
    return [_f(c.get("c")) for c in (candles or []) if isinstance(c, dict)]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mom(candles, n=1):
    cl = closes(candles)
    if len(cl) < 2:
        return 0.0
    n = min(n, len(cl) - 1)
    if cl[-1 - n] == 0:
        return 0.0
    return (cl[-1] / cl[-1 - n] - 1.0) * 100.0


def trend_structure(candles, bars=6):
    """('up'|'down'|'mixed', strength 0..1) over the last `bars` closes."""
    cl = closes(candles)
    if len(cl) < bars + 1:
        return "mixed", 0.0
    window = cl[-(bars + 1):]
    ups = sum(1 for a, b in zip(window, window[1:]) if b > a)
    strength = ups / bars
    if strength >= 0.65:
        return "up", strength
    if strength <= 0.35:
        return "down", 1.0 - strength
    return "mixed", 0.5


def clamp_leverage(desired, venue_max):
    """Clamp desired leverage to [1, venue_max] (wolf-verbatim)."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


# ── UNIVERSE — derived from the LIVE market, never a preset list ────────────

def derive_universe(rows, inputs):
    """rows: [{name, vol}] from market_list_instruments (main-dex perps, not
    delisted). Volume floor -> exclude set -> top-N by 24h notional."""
    floor = _f(inputs.get("universeVolFloorUsd"), 25_000_000)
    max_names = int(_f(inputs.get("universeMaxNames"), 20))
    exclude = {str(x).upper() for x in (inputs.get("excludeAssets") or [])}
    seen = set()
    qualifiers = []
    for r in rows or []:
        name = str((r or {}).get("name", "")).strip()
        if not name or ":" in name:          # main-dex perps only (xyz: = other dex)
            continue
        au = name.upper()
        if au in seen or au in exclude:
            continue
        seen.add(au)
        vol = _f(r.get("vol"))
        if vol < floor:
            continue
        qualifiers.append((name, vol))
    qualifiers.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in qualifiers[:max_names]]


# ── PULSE — cross-asset day read (senpi-market-pulse compute_signals port) ──

def group_averages(changes, groups):
    """changes: {SYM: change_pct|None}; groups: {name: [SYM..]} ->
    {name: {avg_change_pct, n}} (names with no quote are skipped, never zeroed)."""
    out = {}
    for gname, syms in (groups or {}).items():
        vals = [changes.get(str(s).upper()) for s in (syms or [])]
        vals = [v for v in vals if v is not None]
        out[gname] = {"avg_change_pct": round(sum(vals) / len(vals), 2) if vals else None,
                      "n": len(vals)}
    return out


def pulse_stance(changes, groups, vix_price=None):
    """Ported from pulse.py compute_signals: day classification (groups down vs
    up at +/-0.5% with 2:1 dominance and >= 3 groups) + the confirmation
    checklist (GOLD haven bid / DXY funding stress / VIX fear)."""
    gavg = group_averages(changes, groups)
    breadth = [g["avg_change_pct"] for g in gavg.values() if g["avg_change_pct"] is not None]
    down = sum(1 for x in breadth if x < -0.5)
    up = sum(1 for x in breadth if x > 0.5)
    if breadth:
        if down >= up * 2 and down >= 3:
            day = "risk_off"
        elif up >= down * 2 and up >= 3:
            day = "risk_on"
        else:
            day = "mixed"
    else:
        day = None

    gold, dxy, vix_chg = changes.get("GOLD"), changes.get("DXY"), changes.get("VIX")
    checklist = {
        "gold": ("haven bid intact" if (gold is not None and gold > -2)
                 else "haven also selling — possible liquidity event" if gold is not None else None),
        "dxy": ("dollar calm" if (dxy is not None and abs(dxy) < 0.6)
                else "dollar bid — funding stress" if (dxy is not None and dxy > 0.6) else None),
        "vix": ("fear contained" if (vix_price is not None and vix_price < 22)
                else "fear elevated" if vix_price is not None else None),
    }
    return {"day": day, "groups_up": up, "groups_down": down,
            "group_avgs": {k: v["avg_change_pct"] for k, v in gavg.items()},
            "checklist": checklist, "vix_price": vix_price, "vix_change_pct": vix_chg}


# ── COHORTS — smart-vs-crowd divergence (senpi-smart-money port) ────────────

def _signed_notional(p):
    """smartmoney.py verbatim: signed position notional from a trader-state position."""
    def f(*keys):
        for k in keys:
            v = _num((p or {}).get(k))
            if v is not None:
                return v
        return 0.0
    szi = f("szi", "size")
    val = f("positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * f("entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def cohort_positions_bias(traders, per=None):
    """Aggregate a cohort's NET positioning per coin (smartmoney.py cohort_bias
    inner math): bias = net/gross in [-1,+1] + long/short member counts.
    Feed batches through repeatedly with the same `per` dict, then finalize."""
    per = per if per is not None else {}
    for t in traders or []:
        if not isinstance(t, dict):
            continue
        for p in (t.get("openPositions") or t.get("open_positions") or []):
            if not isinstance(p, dict):
                continue
            coin = p.get("coin") or p.get("asset")
            sn = _signed_notional(p) if coin else 0.0
            if not coin or sn == 0:
                continue
            d = per.setdefault(str(coin).upper(),
                               {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0})
            d["net"] += sn
            d["gross"] += abs(sn)
            d["n_long" if sn > 0 else "n_short"] += 1
    return per


def finalize_bias(per):
    for d in (per or {}).values():
        d["bias"] = round(d["net"] / d["gross"], 3) if d["gross"] > 0 else 0.0
        d["members"] = d["n_long"] + d["n_short"]
        d["net"] = round(d["net"], 2)
    return per


def _dir(bias):
    return "long" if bias > 0 else ("short" if bias < 0 else "flat")


def smart_conviction(smart_per, cfg=None):
    """Where the proven cohort is most net-directional (smartmoney.py verbatim)."""
    cfg = cfg or {}
    min_members = int(_f(cfg.get("minMembers"), MIN_MEMBERS))
    lean = _f(cfg.get("leanThreshold"), LEAN_THRESHOLD)
    out = []
    for coin, d in (smart_per or {}).items():
        if d.get("members", 0) >= min_members and abs(d.get("bias", 0)) >= lean:
            out.append({"asset": coin, "bias": d["bias"], "direction": _dir(d["bias"]),
                        "members": d["members"]})
    out.sort(key=lambda x: abs(x["bias"]) * x["members"], reverse=True)
    return out


def divergences(smart_per, crowd_per, cfg=None):
    """Proven cohort vs crowd on OPPOSITE sides (or far apart) — the core
    signal (smartmoney.py verbatim: opposite signs always flag; else
    |gap| >= divergenceMinGap; both cohorts need minMembers)."""
    cfg = cfg or {}
    min_members = int(_f(cfg.get("minMembers"), MIN_MEMBERS))
    min_gap = _f(cfg.get("divergenceMinGap"), DIVERGENCE_MIN_GAP)
    out = []
    for coin, sd in (smart_per or {}).items():
        if sd.get("members", 0) < min_members:
            continue
        cd = (crowd_per or {}).get(coin)
        if not cd or cd.get("members", 0) < min_members:
            continue
        gap = round(sd["bias"] - cd["bias"], 3)
        opposite = (sd["bias"] > 0) != (cd["bias"] > 0) and sd["bias"] != 0 and cd["bias"] != 0
        if opposite or abs(gap) >= min_gap:
            out.append({"asset": coin, "gap": gap, "opposite_sides": opposite,
                        "smart_bias": sd["bias"], "smart_direction": _dir(sd["bias"]),
                        "smart_members": sd["members"],
                        "crowd_bias": cd["bias"], "crowd_direction": _dir(cd["bias"])})
    out.sort(key=lambda x: (x["opposite_sides"], abs(x["gap"])), reverse=True)
    return out


# ── THESIS — pulse stance x cohort divergence ───────────────────────────────

def derive_thesis(views, pulse, cohort, regime, inputs, now):
    """views: {NAME: {chg (24h %), sm_dir, sm_pct}} for the derived universe
    (chg comes free off the instruments read — no candle pulls at refresh).
    pulse: pulse_stance() output. cohort: {smart, crowd, available}.
    Buckets are DIVERGENCE-FIRST: smart-long names the crowd hasn't followed
    fill the LONG bucket (smart-short -> SHORT), topped up by smart conviction,
    then by 24h relative strength as the flagged fallback."""
    bucket_n = int(_f(inputs.get("bucketSize"), 4))
    cfg = inputs.get("cohorts") or {}

    day = (pulse or {}).get("day")
    stance = {"risk_on": "RISK_ON", "risk_off": "RISK_OFF"}.get(day, "NEUTRAL")

    universe = set(views.keys())
    longs, shorts, src = [], [], {}

    def _take(name, side_list, tag):
        if name in universe and name not in side_list and len(side_list) < bucket_n:
            side_list.append(name)
            src[name] = tag

    if (cohort or {}).get("available"):
        divs = divergences(cohort.get("smart"), cohort.get("crowd"), cfg)
        for d in divs:
            if d["smart_direction"] == "long":
                _take(d["asset"], longs, f"divergence gap {d['gap']:+.2f}")
            elif d["smart_direction"] == "short":
                _take(d["asset"], shorts, f"divergence gap {d['gap']:+.2f}")
        for c in smart_conviction(cohort.get("smart"), cfg):
            if c["direction"] == "long":
                _take(c["asset"], longs, f"smart conviction {c['bias']:+.2f}")
            elif c["direction"] == "short":
                _take(c["asset"], shorts, f"smart conviction {c['bias']:+.2f}")

    # fallback / top-up: 24h-change relative strength, SM-board hard block kept
    ranked = sorted(views.items(), key=lambda kv: _f(kv[1].get("chg")), reverse=True)
    for n, v in ranked:
        if _f(v.get("chg")) > 0 and not (v.get("sm_dir") == "SHORT" and _f(v.get("sm_pct"), 50) >= 58):
            _take(n, longs, "rs fallback")
    for n, v in reversed(ranked):
        if _f(v.get("chg")) < 0 and not (v.get("sm_dir") == "LONG" and _f(v.get("sm_pct"), 50) >= 58):
            _take(n, shorts, "rs fallback")

    caps = {"RISK_ON": {"LONG": 5, "SHORT": 2},
            "RISK_OFF": {"LONG": 2, "SHORT": 5},
            "NEUTRAL": {"LONG": 3, "SHORT": 3}}[stance]

    chk = (pulse or {}).get("checklist") or {}
    chk_bits = "; ".join(v for v in (chk.get("gold"), chk.get("dxy"), chk.get("vix")) if v)
    cohort_note = ("" if (cohort or {}).get("available")
                   else " [cohorts unavailable — RS-ranked fallback]")
    narrative = (f"{stance} (pulse: {day or 'no read'}, "
                 f"{(pulse or {}).get('groups_up', 0)} groups up / "
                 f"{(pulse or {}).get('groups_down', 0)} down; {chk_bits or 'no checklist'}); "
                 f"funding regime {regime or 'UNKNOWN'}; "
                 f"long {','.join(longs) or '—'}; short {','.join(shorts) or '—'}"
                 f"{cohort_note}")
    return {"stance": stance, "leaders": longs, "laggards": shorts, "caps": caps,
            "bucket_src": src, "pulse": {k: (pulse or {}).get(k) for k in
                                         ("day", "groups_up", "groups_down", "group_avgs")},
            "cohorts_available": bool((cohort or {}).get("available")),
            "smart_bias": {k: v.get("bias") for k, v in ((cohort or {}).get("smart") or {}).items()},
            "regime": regime or "UNKNOWN", "universe": sorted(universe),
            "narrative": narrative, "derived_at": now}


def bucket_for(side, thesis):
    return (thesis or {}).get("leaders" if side == "LONG" else "laggards", [])


def cap_for(side, thesis):
    return int(_f(((thesis or {}).get("caps") or {}).get(side), 3))


# ── ENTRY press — tape must confirm the thesis direction ────────────────────

def score_entry(asset, side, c1, c4, sm, inputs):
    """0-10 press score for a bucket name. None on insufficient candles.
    `sm` here is the 4h leaderboard lean — the NEAR-TERM confirm layer (the
    cohort divergence already earned the name its bucket slot)."""
    if len(closes(c1)) < 8 or len(closes(c4)) < 4:
        return None
    sign = 1 if side == "LONG" else -1
    want = "up" if side == "LONG" else "down"
    t4, s4 = trend_structure(c4)
    t1, _ = trend_structure(c1)
    m24 = mom(c1, 24)

    w_t4, w_t1, w_m, w_sm = 3.0, 2.0, 3.0, 2.0
    t4_c = w_t4 * s4 if t4 == want else 0.0
    t1_c = w_t1 if (t1 == want and t4 == want) else 0.0
    m_c = w_m * _clamp((m24 * sign) / 6.0, 0, 1)
    sm_dir = (sm or {}).get("direction", "NEUTRAL")
    sm_pct = _f((sm or {}).get("pct"), 50)
    sm_c = w_sm * _clamp((sm_pct - 50) / 30.0, 0, 1) if sm_dir == side else 0.0

    score = _clamp(10.0 * (t4_c + t1_c + m_c + sm_c) / (w_t4 + w_t1 + w_m + w_sm), 0.0, 10.0)
    return {"asset": asset, "score": round(score, 2), "trend4h": t4,
            "mom24h": round(m24, 2), "sm_dir": sm_dir, "sm_pct": round(sm_pct, 1),
            "reasons": [f"4h {t4} {s4:.0%}", f"24h {m24:+.1f}%",
                        f"SM {sm_dir} {sm_pct:.0f}%"]}


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 8.0):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 6.5):
        return "good"
    if score >= _f(inputs.get("minScore"), 5.5):
        return "base"
    return None


def sizing_for(band, inputs, venue_max=None):
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 4)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 10)
    return clamp_leverage(lev, venue_max if venue_max is not None else lev), mgn


# ── boundary clocks + close triggers ────────────────────────────────────────

def due(now, anchor_ts, every_seconds):
    """True when a boundary has elapsed since anchor_ts (0 anchor = never set)."""
    return anchor_ts > 0 and every_seconds > 0 and (now - anchor_ts) >= every_seconds


def close_triggers(side, held, new_thesis, old_thesis, scored_by_asset, inputs,
                   thesis_due, rebalance_due):
    """[{asset, direction, reason, trigger}] for the CLOSE_POSITION action.

    thesis_shift        — at the 48h rethink, a held name no longer in this
                          book's bucket is closed.
    divergence_reversed — at the rethink, the PROVEN cohort's bias has flipped
                          >= leanThreshold against a held name (cohort data
                          from the fresh thesis; skipped when unavailable).
    weekly_rebalance    — at the 7d rebalance, a held name re-scoring below
                          exitScore is recycled.
    """
    exit_score = _f(inputs.get("exitScore"), 3.5)
    cfg = inputs.get("cohorts") or {}
    lean = _f(cfg.get("leanThreshold"), LEAN_THRESHOLD)
    out, closed = [], set()

    def _close(p, trigger, reason):
        if p["asset"] in closed:
            return
        closed.add(p["asset"])
        out.append({"asset": p["asset"], "direction": p["direction"],
                    "trigger": trigger, "reason": reason})

    if thesis_due and new_thesis:
        keep = set(bucket_for(side, new_thesis))
        old_stance = (old_thesis or {}).get("stance", "?")
        smart_bias = new_thesis.get("smart_bias") or {}
        against = -1 if side == "LONG" else 1
        for p in held:
            bias = smart_bias.get(str(p["asset"]).upper())
            if (new_thesis.get("cohorts_available") and bias is not None
                    and (bias * against) >= lean):
                _close(p, "divergence_reversed",
                       f"proven cohort flipped {('short' if side == 'LONG' else 'long')} "
                       f"(bias {bias:+.2f}) on {p['asset']}")
            elif p["asset"] not in keep:
                _close(p, "thesis_shift",
                       f"rethink {old_stance}->{new_thesis['stance']}: "
                       f"{p['asset']} left the {side} bucket")
    if rebalance_due:
        for p in held:
            if p["asset"] in closed:
                continue
            th = scored_by_asset.get(p["asset"])
            if th and _f(th.get("score")) < exit_score:
                _close(p, "weekly_rebalance",
                       f"rebalance re-score {th.get('score')} < {exit_score}")
    return out
