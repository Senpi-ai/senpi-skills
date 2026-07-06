"""GORILLA — pure thesis logic (no I/O, no MCP).

A SELF-DIRECTED thesis hedge fund. Where thesis-fund asks the user to bring a
macro view, Gorilla DERIVES its own at deploy time from a full market scan and
then trades it with patience:

  UNIVERSE (derive_universe) — from the LIVE instrument list each refresh:
          every main-dex perp over a 24h-notional floor, top-N by volume.
          The fund reads the market, not a preset list.
  THESIS  (derive_thesis)  — stance (RISK_ON / RISK_OFF / NEUTRAL) from the BTC
          4h tide + universe breadth; leaders (top relative-strength names the
          smart money isn't against) become the LONG bucket, laggards the SHORT
          bucket; a plain-English `narrative` travels with every signal.
  PRESS   (score_entry)    — a bucket name is only pressed when the tape
          confirms its direction (4h/1h structure + momentum + SM lean),
          conviction-banded apex/good/base.
  HOLD    — between boundaries nothing re-decides; the DSL owns price exits.
  RETHINK (thesis_shift)   — every thesisRefreshHours (default 48h) the thesis
          is re-derived; held names no longer in the book's bucket are closed
          via the CLOSE_POSITION action.
  REBALANCE (weekly_rebalance) — every rebalanceDays (default 7d) held names
          re-scoring below exitScore are recycled.

Shared VERBATIM by both instances; the book's identity comes from inputs.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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


def rs_score(c1, c4):
    """Relative-strength composite for ranking: 7d + 24h momentum + 4h structure.
    Signed — positive = leadership, negative = laggardship."""
    m7d = mom(c1, 168)          # degrades to available history when shorter
    m24 = mom(c1, 24)
    t4, s4 = trend_structure(c4)
    t_term = s4 * 10 if t4 == "up" else -s4 * 10 if t4 == "down" else 0.0
    return 0.4 * _clamp(m7d, -25, 25) + 0.4 * _clamp(m24, -10, 10) * 2.5 + 0.2 * t_term


# ── UNIVERSE — derived from the LIVE market, never a preset list ────────────

def derive_universe(rows, inputs):
    """rows: [{name, vol}] from market_list_instruments (main-dex perps, not
    delisted). Reads the MARKET: volume floor -> exclude set -> top-N by 24h
    notional. Returns the universe list for this thesis refresh. A preset list
    would contradict a self-directed fund — the only whitelist here is the
    optional excludeAssets."""
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


# ── THESIS — derived from one full market read ──────────────────────────────

def derive_thesis(views, btc_c1, btc_c4, regime, inputs, now):
    """views: {NAME: {rs, sm_dir, sm_pct}} for the whole universe.
    Returns the standing thesis dict (persisted by the scanners)."""
    bucket_n = int(_f(inputs.get("bucketSize"), 4))
    t4, s4 = trend_structure(btc_c4)
    t1, _ = trend_structure(btc_c1)
    up_frac = 0.0
    if views:
        up_frac = sum(1 for v in views.values() if v["rs"] > 0) / len(views)

    if t4 == "up" and t1 != "down" and up_frac >= 0.55:
        stance = "RISK_ON"
    elif t4 == "down" and t1 != "up" and up_frac <= 0.45:
        stance = "RISK_OFF"
    else:
        stance = "NEUTRAL"

    ranked = sorted(views.items(), key=lambda kv: kv[1]["rs"], reverse=True)
    # smart money >= 58% against a bucket's direction disqualifies the name
    leaders = [n for n, v in ranked
               if v["rs"] > 0 and not (v["sm_dir"] == "SHORT" and v["sm_pct"] >= 58)][:bucket_n]
    laggards = [n for n, v in reversed(ranked)
                if v["rs"] < 0 and not (v["sm_dir"] == "LONG" and v["sm_pct"] >= 58)][:bucket_n]

    caps = {"RISK_ON": {"LONG": 5, "SHORT": 2},
            "RISK_OFF": {"LONG": 2, "SHORT": 5},
            "NEUTRAL": {"LONG": 3, "SHORT": 3}}[stance]

    narrative = (f"{stance}: BTC 4h {t4} ({s4:.0%}), breadth {up_frac:.0%} of "
                 f"{len(views)} names up, funding regime {regime or 'UNKNOWN'}; "
                 f"long {','.join(leaders) or '—'}; short {','.join(laggards) or '—'}")
    return {"stance": stance, "leaders": leaders, "laggards": laggards,
            "caps": caps, "breadth": round(up_frac, 3), "regime": regime or "UNKNOWN",
            "universe": sorted(views.keys()), "narrative": narrative, "derived_at": now}


def bucket_for(side, thesis):
    return (thesis or {}).get("leaders" if side == "LONG" else "laggards", [])


def cap_for(side, thesis):
    return int(_f(((thesis or {}).get("caps") or {}).get(side), 3))


# ── ENTRY press — tape must confirm the thesis direction ────────────────────

def score_entry(asset, side, c1, c4, sm, inputs):
    """0-10 press score for a bucket name. None on insufficient candles."""
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

    thesis_shift     — at the 48h rethink, a held name no longer in this book's
                       bucket (or the stance flipped it out) is closed.
    weekly_rebalance — at the 7d rebalance, a held name re-scoring below
                       exitScore is recycled.
    """
    exit_score = _f(inputs.get("exitScore"), 3.5)
    out = []
    if thesis_due and new_thesis:
        keep = set(bucket_for(side, new_thesis))
        old_stance = (old_thesis or {}).get("stance", "?")
        for p in held:
            if p["asset"] not in keep:
                out.append({"asset": p["asset"], "direction": p["direction"],
                            "trigger": "thesis_shift",
                            "reason": (f"rethink {old_stance}->{new_thesis['stance']}: "
                                       f"{p['asset']} left the {side} bucket")})
    closed = {s["asset"] for s in out}
    if rebalance_due:
        for p in held:
            if p["asset"] in closed:
                continue
            th = scored_by_asset.get(p["asset"])
            if th and _f(th.get("score")) < exit_score:
                out.append({"asset": p["asset"], "direction": p["direction"],
                            "trigger": "weekly_rebalance",
                            "reason": f"rebalance re-score {th.get('score')} < {exit_score}"})
    return out
