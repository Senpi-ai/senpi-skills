"""REGIME ALLOCATOR — pure engine (no I/O, no MCP). Shared VERBATIM by Chimp
(daily recalibration) and Gorilla (weekly); the cadence + DSL live in runtime.yaml.

The design (fixes the cuttlefish/gorilla-v1 mistakes):
  * The market read runs on a SLOW clock (recalibrationHours: 24 or 168) — never
    nonstop. build_posture() re-derives a standing POSTURE from the latest
    market-pulse + smart-money read; between recalibrations the posture is held.
  * The strategy NEVER closes a position. A regime flip only changes what it
    OPENS next; prior-regime positions retire on their own DSL trailing stops
    (rotate-by-attrition). So there is ONE scanner, ONE OPEN action, no
    CLOSE_POSITION, no cross-scanner coherence problem. DSL owns every exit.
  * The universe spans BOTH dexes — main crypto perps AND xyz tokenized
    equities/commodities/FX — so risk-off can rotate into real havens.

Engine math ported verbatim from the senpi-market-pulse (compute_signals: day
classification + GOLD/DXY/VIX checklist + dispersion) and senpi-smart-money
(proven-vs-crowd cohort bias) skills. Cohorts are an ENRICHMENT that degrades
cleanly to the leaderboard board — the posture never depends on a user-scoped
discovery token.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

LEAN_THRESHOLD = 0.40       # proven-cohort bias past this = a real directional lean
MIN_MEMBERS = 5
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


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def pct_change(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


def closes(candles):
    return [_f(c.get("c")) for c in (candles or []) if isinstance(c, dict)]


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
    s = ups / bars
    if s >= 0.65:
        return "up", s
    if s <= 0.35:
        return "down", 1.0 - s
    return "mixed", 0.5


def clamp_leverage(desired, venue_max):
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def due(now, anchor_ts, every_seconds):
    """True when a recalibration boundary has elapsed (0 anchor = never yet)."""
    return anchor_ts <= 0 or (every_seconds > 0 and (now - anchor_ts) >= every_seconds)


# ── UNIVERSE — derived live from BOTH dexes ─────────────────────────────────

def classify(name, inputs):
    """'defensive' (havens/commodities/defensive FX) | 'risk' (everything else:
    crypto, equities, indices). Defensive set is tunable via inputs."""
    bare = str(name).split(":", 1)[-1].upper()
    defensives = {str(x).upper() for x in (inputs.get("defensiveAssets") or
                  ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "BRENTOIL", "CL", "NATGAS",
                   "DXY", "JPY"])}
    return "defensive" if bare in defensives else "risk"


def derive_universe(main_rows, xyz_rows, inputs):
    """rows: [{name, vol, change_pct}] per dex (already parsed by scan.py).
    main perps + xyz instruments over the per-dex volume floor, top-N each,
    minus excludeAssets. Returns [{name, dex, chg, vol, class}] (name carries the
    xyz: prefix for xyz)."""
    main_floor = _f(inputs.get("universeVolFloorUsd"), 25_000_000)
    xyz_floor = _f(inputs.get("xyzVolFloorUsd"), 3_000_000)
    max_main = int(_f(inputs.get("maxMainNames"), 14))
    max_xyz = int(_f(inputs.get("maxXyzNames"), 16))
    exclude = {str(x).upper() for x in (inputs.get("excludeAssets") or [])}

    # `seen` is SHARED across both picks: scan.py sources main_rows from
    # market_list_instruments(dex="") which returns BOTH sub-DEXes (232 main +
    # 103 xyz today), so every xyz instrument otherwise lands in the main pool
    # AND again in the xyz pool -> duplicated names, and the main copy is
    # misclassified as crypto by classify().
    seen = set()

    def _pick(rows, floor, cap, dex):
        out = []
        for r in rows or []:
            name = str((r or {}).get("name", "")).strip()
            if not name:
                continue
            # route by prefix — an `xyz:` name belongs only to the xyz pick, a
            # bare name only to the main pick.
            if name.lower().startswith("xyz:") != (dex == "xyz"):
                continue
            bare = name.split(":", 1)[-1].upper()
            if bare in seen or bare in exclude:
                continue
            seen.add(bare)
            if _f(r.get("vol")) < floor:
                continue
            out.append((name, _f(r.get("vol")), r.get("change_pct")))
        out.sort(key=lambda t: t[1], reverse=True)
        return [{"name": n, "dex": dex, "vol": v, "chg": c,
                 "class": classify(n, inputs)} for n, v, c in out[:cap]]

    return _pick(main_rows, main_floor, max_main, "") + _pick(xyz_rows, xyz_floor, max_xyz, "xyz")


# ── PULSE — cross-asset day read (market-pulse compute_signals port) ────────

def group_averages(changes, groups):
    out = {}
    for g, syms in (groups or {}).items():
        vals = [changes.get(str(s).upper()) for s in (syms or [])]
        vals = [v for v in vals if v is not None]
        out[g] = round(sum(vals) / len(vals), 2) if vals else None
    return out


def pulse_stance(changes, groups, vix_price=None):
    """day classification (groups down vs up at +/-0.5%, 2:1, >=3) + GOLD/DXY/VIX
    checklist + dispersion (index calm while worst group breaks > 2.5% = rotation)."""
    gavg = group_averages(changes, groups)
    breadth = [v for v in gavg.values() if v is not None]
    down = sum(1 for x in breadth if x < -0.5)
    up = sum(1 for x in breadth if x > 0.5)
    if breadth:
        day = ("risk_off" if down >= up * 2 and down >= 3
               else "risk_on" if up >= down * 2 and up >= 3 else "mixed")
    else:
        day = None

    gold, dxy = changes.get("GOLD"), changes.get("DXY")
    checklist = {
        "gold": ("haven bid intact" if (gold is not None and gold > -2)
                 else "haven also selling" if gold is not None else None),
        "dxy": ("dollar calm" if (dxy is not None and abs(dxy) < 0.6)
                else "dollar bid — funding stress" if (dxy is not None and dxy > 0.6) else None),
        "vix": ("fear contained" if (vix_price is not None and vix_price < 22)
                else "fear elevated" if vix_price is not None else None),
    }
    sp500 = changes.get("SP500")
    worst_g, worst_avg = None, 0.0
    for g, a in gavg.items():
        if a is not None and a < worst_avg:
            worst_g, worst_avg = g, a
    dispersion = ("rotation" if (sp500 is not None and worst_g and (sp500 - worst_avg) > 2.5)
                  else "broad" if (sp500 is not None and worst_g) else None)
    return {"day": day, "groups_up": up, "groups_down": down, "group_avgs": gavg,
            "checklist": checklist, "dispersion": dispersion, "worst_group": worst_g,
            "vix_price": vix_price}


# ── COHORTS — proven-vs-crowd bias (smart-money port; ENRICHMENT, degrades) ─

def _signed_notional(p):
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
    return per


def smart_lean(name, cohort, board):
    """Directional lean for `name`: proven cohort bias if available (>= members),
    else the leaderboard board (near-term). Returns bias in [-1,1] (0 = neutral)."""
    au = str(name).split(":", 1)[-1].upper()
    if (cohort or {}).get("available"):
        d = (cohort.get("smart") or {}).get(au)
        if d and d.get("members", 0) >= MIN_MEMBERS:
            return _clamp(d.get("bias", 0.0), -1, 1)
    b = (board or {}).get(au)
    if b:
        if b.get("direction") == "LONG":
            return _clamp((_f(b.get("pct"), 50) - 50) / 50.0, 0, 1)
        if b.get("direction") == "SHORT":
            return -_clamp((_f(b.get("pct"), 50) - 50) / 50.0, 0, 1)
    return 0.0


# ── POSTURE — the standing thesis, re-derived on the recalibration clock ─────

def build_posture(pool, pulse, funding_regime, cohort, board, inputs, now):
    """Map the market read -> {stance, mode, longs[], shorts[], size_scale,
    narrative, built_at}. longs/shorts are ranked candidate name lists; scan.py
    fills free slots from them (tape-confirmed) — it does NOT open all of them."""
    day = (pulse or {}).get("day")
    dispersion = (pulse or {}).get("dispersion")

    def rs(item):
        # relative strength = 24h change biased by the proven/board lean
        return _f(item.get("chg")) + 4.0 * smart_lean(item["name"], cohort, board)

    ranked = sorted(pool, key=rs, reverse=True)
    strong = [i["name"] for i in ranked]
    weak = [i["name"] for i in reversed(ranked)]
    defensive_up = [i["name"] for i in ranked
                    if i["class"] == "defensive" and _f(i.get("chg")) > 0]
    risk_down = [i["name"] for i in reversed(ranked)
                 if i["class"] == "risk" and _f(i.get("chg")) < 0]

    if day == "risk_on":
        stance, mode = "RISK_ON", "risk_on"
        longs = [i["name"] for i in ranked if _f(i.get("chg")) > 0][:12]
        shorts, size = [], 1.0
    elif day == "risk_off":
        stance, mode = "RISK_OFF", "risk_off"
        longs = defensive_up[:8]                 # rotate into havens that are working
        shorts = risk_down[:8]                   # short the breaking risk complex
        size = _f(inputs.get("riskOffSizeScale"), 0.7)
    else:                                        # mixed / rotation -> dispersion play
        stance = "ROTATION" if dispersion == "rotation" else "MIXED"
        mode = "rotation" if dispersion == "rotation" else "mixed"
        longs = strong[:8]
        shorts = weak[:8]
        size = _f(inputs.get("mixedSizeScale"), 0.6)

    # squeeze note: a crowded funding regime favors the fade side (informational)
    reg = funding_regime or "UNKNOWN"
    chk = (pulse or {}).get("checklist") or {}
    chk_bits = "; ".join(v for v in (chk.get("gold"), chk.get("dxy"), chk.get("vix")) if v)
    disp_bit = (f"; DISPERSION rotation (worst: {pulse.get('worst_group')})"
                if dispersion == "rotation" else "")
    coh = "" if (cohort or {}).get("available") else " [cohorts unavailable — board lean]"
    narrative = (f"{stance} (pulse {day or 'no-read'}: {(pulse or {}).get('groups_up',0)} up / "
                 f"{(pulse or {}).get('groups_down',0)} down; {chk_bits or 'no checklist'}"
                 f"{disp_bit}); funding {reg}; long {','.join(longs[:5]) or '—'}; "
                 f"short {','.join(shorts[:5]) or '—'}{coh}")
    return {"stance": stance, "mode": mode, "longs": longs, "shorts": shorts,
            "size_scale": size, "regime": reg, "dispersion": dispersion,
            "cohorts_available": bool((cohort or {}).get("available")),
            "narrative": narrative, "built_at": now}


# ── ENTRY — tape must confirm the posture direction for THIS name ───────────

def score_candidate(name, side, c1, c4, cohort, board, inputs):
    """0-10 conviction for opening `name` in `side`. None on thin candles;
    None (as a soft skip) when the tape does not confirm the posture direction."""
    if len(closes(c1)) < 8 or len(closes(c4)) < 4:
        return None
    sign = 1 if side == "LONG" else -1
    want = "up" if side == "LONG" else "down"
    t4, s4 = trend_structure(c4)
    t1, _ = trend_structure(c1)
    if t4 != want:                               # hard confirm: 4h structure must agree
        return None
    m24 = mom(c1, 24)
    lean = smart_lean(name, cohort, board) * sign

    w_t4, w_t1, w_m, w_sm = 3.0, 2.0, 3.0, 2.0
    t4_c = w_t4 * s4
    t1_c = w_t1 if t1 == want else 0.0
    m_c = w_m * _clamp((m24 * sign) / 6.0, 0, 1)
    sm_c = w_sm * _clamp(lean, 0, 1)
    score = _clamp(10.0 * (t4_c + t1_c + m_c + sm_c) / (w_t4 + w_t1 + w_m + w_sm), 0, 10)
    return {"asset": name, "score": round(score, 2), "trend4h": t4,
            "mom24h": round(m24, 2), "lean": round(lean, 3),
            "reasons": [f"4h trend {t4} at {s4:.0%} strength", f"price {m24:+.1f}% in 24h", f"smart money lean {lean:+.2f} toward this side"]}


def band_for(score, inputs):
    if score >= _f(inputs.get("apexScore"), 8.0):
        return "apex"
    if score >= _f(inputs.get("goodScore"), 6.5):
        return "good"
    if score >= _f(inputs.get("minScore"), 5.5):
        return "base"
    return None


def sizing_for(band, size_scale, inputs, venue_max=None):
    lev = _f((inputs.get("leverageTiers") or {}).get(band), 3)
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 10) * _f(size_scale, 1.0)
    return clamp_leverage(lev, venue_max if venue_max is not None else lev), max(round(mgn, 2), 3.0)
