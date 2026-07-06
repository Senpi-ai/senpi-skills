"""CUTTLEFISH — pure scoring (no I/O, no MCP), built on the REAL engines of the
two skills it composes (math ported from their scripts, constants verbatim):

  UNIVERSE (derive_universe) — from the LIVE instrument list every tick:
          every main-dex perp over a 24h-notional floor, top-N by volume.
  PULSE   (pulse_stance)     — senpi-market-pulse compute_signals port: the
          cross-asset day classification (crypto + semis + megacap + indices +
          commodities + FX groups off prevDayPx) + the GOLD/DXY/VIX checklist.
          The long book stands down on a risk_off day, the short book on
          risk_on — this replaces the v1 BTC-only "tide".
  COHORTS (cohort_positions_bias / smart_conviction / divergences) — the
          senpi-smart-money engine: PROVEN cohort (lifetime realized >= $1M)
          vs CROWD ($10k..$100k), per-coin bias = net/gross signed notional,
          divergence = opposite sides or gap >= 0.50 with >= 5 members each.
          This is the divergence core; the 4h leaderboard board is only the
          NEAR-TERM confirm factor (its actual role in those engines).
  ADAPT   (close_triggers)   — the 15m auto-adjust: pulse_flip (day against
          the book, confirmed N ticks), divergence_reversed (the proven
          cohort flips >= leanThreshold against a held name; board fallback
          when cohorts are unavailable), basket_refresh (4h re-rank recycles
          names re-scoring below exitScore).

Cuttlefish vs Gorilla: same engines, opposite temperament — Cuttlefish holds
NO standing thesis (it re-reads the pulse every 15 minutes and adapts; cohorts
cache on a 4h clock because discovery paging is heavy), Gorilla derives a
thesis and sits on it for 48h. Shared VERBATIM by both instances.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

# smart-money engine constants (smartmoney.py verbatim; overridable via inputs.cohorts)
SMART_MIN_REALIZED = 1_000_000
CROWD_MIN_REALIZED = 10_000
CROWD_MAX_REALIZED = 100_000
MIN_MEMBERS = 5
LEAN_THRESHOLD = 0.40       # entry hard-block: cohort this far against = don't open
REVERSAL_THRESHOLD = 0.55   # close trigger: cohort DECISIVELY against = close held
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


def vols(candles):
    return [_f(c.get("v")) for c in (candles or []) if isinstance(c, dict)]


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
    max_names = int(_f(inputs.get("universeMaxNames"), 16))
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
    out = {}
    for gname, syms in (groups or {}).items():
        vals = [changes.get(str(s).upper()) for s in (syms or [])]
        vals = [v for v in vals if v is not None]
        out[gname] = {"avg_change_pct": round(sum(vals) / len(vals), 2) if vals else None,
                      "n": len(vals)}
    return out


def pulse_stance(changes, groups, vix_price=None):
    """pulse.py compute_signals port: day classification (groups down vs up at
    +/-0.5% with 2:1 dominance and >= 3 groups) + GOLD/DXY/VIX checklist."""
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

    # dispersion (pulse.py verbatim, threshold 2.5): the headline index calm while a
    # sector breaks = ROTATION (stock-picking day — the adaptive L/S book's home turf);
    # index moving with its components = BROAD (macro day — the direction gate rules).
    sp500 = changes.get("SP500")
    worst_group, worst_avg = None, 0.0
    for gname, g in gavg.items():
        a = g["avg_change_pct"]
        if a is not None and a < worst_avg:
            worst_group, worst_avg = gname, a
    dispersion = {
        "sp500_change_pct": sp500,
        "worst_group": worst_group,
        "worst_group_avg_pct": round(worst_avg, 2) if worst_group else None,
        "read": ("rotation" if (sp500 is not None and worst_group and (sp500 - worst_avg) > 2.5)
                 else "broad" if (sp500 is not None and worst_group) else None),
    }
    return {"day": day, "groups_up": up, "groups_down": down,
            "group_avgs": {k: v["avg_change_pct"] for k, v in gavg.items()},
            "checklist": checklist, "dispersion": dispersion,
            "vix_price": vix_price, "vix_change_pct": vix_chg}


def top_movers(changes, universe, n=3):
    """The pulse's standout-mover surfacing: universe names ranked by |24h change|."""
    ranked = sorted(((str(a).upper(), changes.get(str(a).upper()))
                     for a in (universe or [])),
                    key=lambda kv: abs(kv[1]) if kv[1] is not None else -1, reverse=True)
    return [{"asset": a, "chg": c} for a, c in ranked[:n] if c is not None]


def pulse_allows(side, day):
    """The long book stands down on a risk_off day, the short book on risk_on;
    mixed / no-read allows both (the adaptive book trades dispersion)."""
    if day == "risk_off":
        return side != "LONG"
    if day == "risk_on":
        return side != "SHORT"
    return True


# ── COHORTS — smart-vs-crowd divergence (senpi-smart-money port) ────────────

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
    """smartmoney.py cohort_bias inner math: per-coin net/gross + member counts.
    Feed batches through with the same `per`, then finalize_bias."""
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
    """smartmoney.py verbatim: opposite sides always flag; else |gap| >= min gap;
    both cohorts need minMembers."""
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


def cohort_view_for(asset, side, cohort, cfg=None):
    """Per-asset slice of the cohort read, side-aware, for score_asset /
    close_triggers: {available, smart_bias, smart_members, divergent, gap,
    against, reversed}.

    TWO cohort thresholds create HYSTERESIS across the two scanners' separate
    4h cohort caches (entries and rebalance each cache independently; within a
    refresh window they can differ):
      against  = proven cohort leaning >= leanThreshold (0.40) the other way
                 -> ENTRY hard-block (score_asset won't open it).
      reversed = proven cohort leaning >= reversalThreshold (0.55) the other way
                 -> CLOSE trigger (rebalance closes a held name).
    The 0.40..0.55 band is un-openable but not force-closed, so a name straddling
    the entry bar in one cache can't be simultaneously opened by entries and
    closed by rebalance — the caches would have to disagree by > 0.15 to fight."""
    cfg = cfg or {}
    lean = _f(cfg.get("leanThreshold"), LEAN_THRESHOLD)
    reversal = _f(cfg.get("reversalThreshold"), REVERSAL_THRESHOLD)
    min_members = int(_f(cfg.get("minMembers"), MIN_MEMBERS))
    au = str(asset).upper()
    available = bool((cohort or {}).get("available"))
    sd = ((cohort or {}).get("smart") or {}).get(au) if available else None
    view = {"available": available, "smart_bias": None, "smart_members": 0,
            "divergent": False, "gap": None, "against": False, "reversed": False}
    if not sd:
        return view
    view["smart_bias"] = sd.get("bias")
    view["smart_members"] = sd.get("members", 0)
    sign = 1 if side == "LONG" else -1
    lean_against = (sd.get("bias", 0) * -sign)
    if sd.get("members", 0) >= min_members and lean_against >= lean:
        view["against"] = True
    if sd.get("members", 0) >= min_members and lean_against >= reversal:
        view["reversed"] = True
    for d in divergences(cohort.get("smart"), cohort.get("crowd"), cfg):
        if d["asset"] == au:
            view["divergent"] = (d["smart_direction"] == ("long" if side == "LONG" else "short"))
            view["gap"] = d["gap"]
            break
    return view


# ── per-asset composite (side-aware; all contributions >= 0) ────────────────

def score_asset(asset, side, c1, c4, asset_ctx, cohort_view, near_term, regime, inputs):
    """0-10 composite for `asset` in the book's `side`. None on insufficient
    candles. HARD BLOCK when the proven cohort leans >= leanThreshold against
    the side (board >= 58% fallback when cohorts are unavailable)."""
    if len(closes(c1)) < 8 or len(closes(c4)) < 4:
        return None
    w = inputs.get("weights") or {}
    w_sm = _f(w.get("smartLean"), 2.5)
    w_div = _f(w.get("divergence"), 3.0)
    w_nt = _f(w.get("nearTerm"), 1.0)
    w_t4 = _f(w.get("trend4h"), 1.5)
    w_al = _f(w.get("align1h"), 1.0)
    w_m24 = _f(w.get("mom24h"), 1.0)
    w_vol = _f(w.get("volRatio"), 0.5)
    w_rg = _f(w.get("regimeBonus"), 0.5)
    max_possible = w_sm + w_div + w_nt + w_t4 + w_al + w_m24 + w_vol + w_rg

    sign = 1 if side == "LONG" else -1
    cv = cohort_view or {}
    nt_dir = (near_term or {}).get("direction", "NEUTRAL")
    nt_pct = _f((near_term or {}).get("pct"), 50)
    opposite = "SHORT" if side == "LONG" else "LONG"
    reasons = []

    # hard block — the REAL smart-money read outranks everything
    if cv.get("against"):
        return {"asset": asset, "score": 0.0, "blocked": "smart_cohort_against",
                "reasons": [f"proven cohort {cv.get('smart_bias'):+.2f} against {side}"]}
    if not cv.get("available") and nt_dir == opposite and nt_pct >= 58:
        return {"asset": asset, "score": 0.0, "blocked": "sm_board_against",
                "reasons": [f"board {nt_dir} {nt_pct:.0f}% against (cohorts unavailable)"]}

    # smart-money core (proven cohort bias in the book's direction)
    sm_c = 0.0
    bias = cv.get("smart_bias")
    if cv.get("available") and bias is not None:
        sm_c = w_sm * _clamp(bias * sign, 0, 1)
        if sm_c:
            reasons.append(f"proven cohort {bias:+.2f}")

    # divergence core (crowd on the other side of the proven cohort)
    div_c = 0.0
    if cv.get("divergent"):
        div_c = w_div
        reasons.append(f"divergence gap {cv.get('gap'):+.2f}")
    elif cv.get("available") and bias is not None and (bias * sign) >= _f(
            (inputs.get("cohorts") or {}).get("leanThreshold"), LEAN_THRESHOLD):
        div_c = 0.5 * w_div
        reasons.append(f"smart conviction {bias:+.2f} (no crowd read)")

    # near-term confirm — the 4h leaderboard's actual role
    nt_c = w_nt * _clamp((nt_pct - 50) / 30.0, 0, 1) if nt_dir == side else 0.0

    # pulse context — trend/momentum/volume in the mandated direction
    t4, s4 = trend_structure(c4)
    want = "up" if side == "LONG" else "down"
    t4_c = w_t4 * s4 if t4 == want else 0.0
    t1, _ = trend_structure(c1)
    al_c = w_al if (t1 == want and t4 == want) else 0.0
    m24 = mom(c1, 24)
    m24_c = w_m24 * _clamp((m24 * sign) / 5.0, 0, 1)
    vv = vols(c1)
    vol_c = 0.0
    if len(vv) >= 25:
        base = sum(vv[-25:-1]) / 24.0
        if base > 0:
            vol_c = w_vol * _clamp(vv[-1] / base - 1.0, 0, 1)
    if t4_c:
        reasons.append(f"4h {t4} {s4:.0%}")

    rg_c = 0.0
    if regime == ("SHORT_CROWDED" if side == "LONG" else "LONG_CROWDED"):
        rg_c = w_rg
        reasons.append(f"{regime} squeeze fuel")

    total = sm_c + div_c + nt_c + t4_c + al_c + m24_c + vol_c + rg_c
    score = _clamp(10.0 * total / max_possible, 0.0, 10.0) if max_possible > 0 else 0.0
    return {"asset": asset, "score": round(score, 2), "blocked": None,
            "smart_bias": bias, "divergent": bool(cv.get("divergent")),
            "cohorts_available": bool(cv.get("available")),
            "nt_dir": nt_dir, "nt_pct": round(nt_pct, 1),
            "mom24h": round(m24, 2), "trend4h": t4,
            "components": {"smartLean": round(sm_c, 3), "divergence": round(div_c, 3),
                           "nearTerm": round(nt_c, 3), "trend4h": round(t4_c, 3),
                           "align1h": round(al_c, 3), "mom24h": round(m24_c, 3),
                           "volRatio": round(vol_c, 3), "regimeBonus": round(rg_c, 3)},
            "reasons": reasons}


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
    mgn = _f((inputs.get("marginPctTiers") or {}).get(band), 12)
    return clamp_leverage(lev, venue_max if venue_max is not None else lev), mgn


def due(now, anchor_ts, every_seconds):
    return anchor_ts > 0 and every_seconds > 0 and (now - anchor_ts) >= every_seconds


# ── close triggers — the 15m auto-adjust brain (consumed by rebalance.py) ───

def close_triggers(side, day, day_against_streak, held, views_by_asset, inputs, due_refresh):
    """[{asset, direction, reason, trigger}] for the CLOSE_POSITION action.

    views_by_asset: {asset: {"cohort": cohort_view_for(...), "score": float|None,
    "nt_dir": str, "nt_pct": float}} — fresh reads of the HELD names.

    COHERENCE — closes are per-name with HYSTERESIS below the entry bars, so
    the entries and rebalance scanners (separate ctx.state, separate cohort
    caches) can't fight: entry needs score >= minScore AND the cohort not
    against (>= leanThreshold); a close needs score < exitScore (<< minScore)
    OR the cohort DECISIVELY reversed (>= reversalThreshold > leanThreshold).
    No name is simultaneously openable and closeable. enforce_hysteresis()
    guards the config.

      pulse_flip          — the pulse day AGAINST this book for
                            `pulseFlipConfirmTicks` consecutive ticks -> close
                            the whole book (anti-whipsaw). Book-level.
      divergence_reversed — the PROVEN cohort leans >= reversalThreshold (NOT
                            the entry leanThreshold) against a held name (board
                            >= 58% fallback when cohorts unavailable) -> close.
      basket_refresh      — on the refresh boundary, a held name re-scoring
                            below exitScore is a stale thesis -> recycle it.
    """
    confirm = int(_f(inputs.get("pulseFlipConfirmTicks"), 2))
    exit_score = _f(inputs.get("exitScore"), 3.5)
    out = []
    if not pulse_allows(side, day) and day_against_streak >= confirm:
        for p in held:
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "pulse_flip",
                        "reason": f"pulse {day} against {side} book x{day_against_streak}"})
        return out  # book-level flip supersedes per-name triggers
    opposite = "SHORT" if side == "LONG" else "LONG"
    for p in held:
        v = views_by_asset.get(p["asset"]) or {}
        cv = v.get("cohort") or {}
        if cv.get("reversed"):           # DECISIVELY against (> entry bar) — hysteresis
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "divergence_reversed",
                        "reason": f"proven cohort decisively flipped {cv.get('smart_bias'):+.2f} "
                                  f"against {side} on {p['asset']}"})
        elif (not cv.get("available") and v.get("nt_dir") == opposite
              and _f(v.get("nt_pct"), 50) >= 58):
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "divergence_reversed",
                        "reason": f"board flipped {v.get('nt_dir')} {v.get('nt_pct')}% "
                                  f"(cohorts unavailable)"})
        elif due_refresh and v.get("score") is not None and _f(v.get("score")) < exit_score:
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "basket_refresh",
                        "reason": f"refresh re-score {v.get('score')} < {exit_score}"})
    return out


def enforce_hysteresis(inputs):
    """The cross-scanner coherence guarantee holds only when exitScore < minScore
    AND reversalThreshold > leanThreshold (a name can't be simultaneously
    openable and closeable on either the score or the cohort axis). (ok, detail)."""
    mn = _f(inputs.get("minScore"), 5.5)
    ex = _f(inputs.get("exitScore"), 3.5)
    cfg = inputs.get("cohorts") or {}
    lean = _f(cfg.get("leanThreshold"), LEAN_THRESHOLD)
    rev = _f(cfg.get("reversalThreshold"), REVERSAL_THRESHOLD)
    ok = ex < mn and rev > lean
    return (ok, f"need exitScore {ex} < minScore {mn} and "
                f"reversalThreshold {rev} > leanThreshold {lean}")
