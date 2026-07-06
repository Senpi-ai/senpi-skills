"""CUTTLEFISH — pure scoring (no I/O, no MCP).

A regime-adaptive long/short basket book. Per asset, in the book's mandated
direction, a 0-10 composite fuses:
  - the MARKET-PULSE read: 4h/1h trend structure, 24h momentum, volume ratio,
    market-wide funding regime;
  - the SMART-MONEY DIVERGENCE core: the leaderboard smart-money lean vs the
    crowd (funding sign) and vs price (SM accumulating INTO weakness is the
    strongest form of early positioning).

The TIDE (BTC 4h trend + 1h confirm) gates which book may build: the long book
stands down in a BEAR tide, the short book in a BULL tide. Conviction bands
(apex/good/base) map to per-band leverage + marginPct.

`close_triggers` is the auto-adjust brain (consumed by rebalance.py → the
runtime's CLOSE_POSITION action — the first fleet package to use it): tide
flip (confirmed N ticks), per-asset smart-money reversal, and the X-hourly
basket-refresh laggard cut.

Shared VERBATIM by both instances; the book's identity comes from inputs
(`side`, tiers, DSL in runtime.yaml).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def closes(candles):
    return [_f(c.get("c")) for c in (candles or []) if isinstance(c, dict)]


def vols(candles):
    return [_f(c.get("v")) for c in (candles or []) if isinstance(c, dict)]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mom(candles, n=1):
    cl = closes(candles)
    if len(cl) < n + 1 or cl[-1 - n] == 0:
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


# ── TIDE — the market read that gates each book ─────────────────────────────

def tide_from_btc(c1, c4):
    """('BULL'|'BEAR'|'MIXED', detail). BTC 4h trend is the tide; 1h must not
    hard-disagree. Both books read the SAME tide — the long book builds while
    it is not BEAR, the short book while it is not BULL."""
    t4, s4 = trend_structure(c4)
    t1, _s1 = trend_structure(c1)
    detail = f"btc4h={t4}({s4:.0%}) 1h={t1}"
    if t4 == "up" and t1 != "down":
        return "BULL", detail
    if t4 == "down" and t1 != "up":
        return "BEAR", detail
    return "MIXED", detail


def tide_allows(side, tide):
    return tide != ("BEAR" if side == "LONG" else "BULL")


# ── per-asset composite (side-aware; all contributions >= 0) ────────────────

def score_asset(asset, side, c1, c4, asset_ctx, sm, regime, inputs):
    """0-10 composite for `asset` in the book's `side`. Returns thesis dict or
    None on insufficient candles. SM hard block: smart money leaning >=58%
    AGAINST the side zeroes the asset (never fight the divergence core)."""
    if len(closes(c1)) < 8 or len(closes(c4)) < 4:
        return None
    w = inputs.get("weights") or {}
    w_sm = _f(w.get("smLean"), 2.5)
    w_div = _f(w.get("divergence"), 3.0)
    w_t4 = _f(w.get("trend4h"), 1.5)
    w_al = _f(w.get("align1h"), 1.0)
    w_m24 = _f(w.get("mom24h"), 1.0)
    w_vol = _f(w.get("volRatio"), 0.5)
    w_rg = _f(w.get("regimeBonus"), 0.5)
    max_possible = w_sm + w_div + w_t4 + w_al + w_m24 + w_vol + w_rg

    sign = 1 if side == "LONG" else -1
    funding = _f((asset_ctx or {}).get("funding"))
    m24 = mom(c1, 24)
    reasons = []

    # smart-money lean — the direction the best wallets hold this asset
    sm_dir = (sm or {}).get("direction", "NEUTRAL")
    sm_pct = _f((sm or {}).get("pct"), 50)
    opposite = "SHORT" if side == "LONG" else "LONG"
    if sm_dir == opposite and sm_pct >= 58:
        return {"asset": asset, "score": 0.0, "blocked": "sm_hard_block",
                "reasons": [f"SM {sm_dir} {sm_pct:.0f}% against book"]}
    sm_c = w_sm * _clamp((sm_pct - 50) / 30.0, 0, 1) if sm_dir == side else 0.0
    if sm_c:
        reasons.append(f"SM {side} {sm_pct:.0f}%")

    # divergence core — SM in-direction while the crowd/price hasn't followed:
    # crowd contra = funding still paying the other way; price contra = 24h move
    # against SM (accumulation into weakness). Each leg is half the weight.
    div_c = 0.0
    if sm_dir == side:
        crowd_contra = (funding * sign) <= 0.0
        price_contra = (m24 * sign) < 0.0
        div_c = w_div * (0.5 * crowd_contra + 0.5 * price_contra)
        if crowd_contra:
            reasons.append("crowd not positioned (funding contra)")
        if price_contra:
            reasons.append(f"SM early vs price ({m24:+.1f}% 24h)")

    # market-pulse context — trend/momentum/volume in the mandated direction
    t4, s4 = trend_structure(c4)
    want_t = "up" if side == "LONG" else "down"
    t4_c = w_t4 * s4 if t4 == want_t else 0.0
    t1, _ = trend_structure(c1)
    al_c = w_al if (t1 == want_t and t4 == want_t) else 0.0
    m24_c = w_m24 * _clamp((m24 * sign) / 5.0, 0, 1)
    vv = vols(c1)
    vol_c = 0.0
    if len(vv) >= 25:
        base = sum(vv[-25:-1]) / 24.0
        if base > 0:
            vol_c = w_vol * _clamp(vv[-1] / base - 1.0, 0, 1)
    if t4_c:
        reasons.append(f"4h {t4} {s4:.0%}")

    # regime bonus — a crowded OPPOSITE regime is squeeze fuel for this side
    rg_c = 0.0
    if regime == ("SHORT_CROWDED" if side == "LONG" else "LONG_CROWDED"):
        rg_c = w_rg
        reasons.append(f"{regime} squeeze fuel")

    total = sm_c + div_c + t4_c + al_c + m24_c + vol_c + rg_c
    score = _clamp(10.0 * total / max_possible, 0.0, 10.0) if max_possible > 0 else 0.0
    return {"asset": asset, "score": round(score, 2), "blocked": None,
            "sm_dir": sm_dir, "sm_pct": round(sm_pct, 1), "funding": funding,
            "mom24h": round(m24, 2), "trend4h": t4,
            "components": {"smLean": round(sm_c, 3), "divergence": round(div_c, 3),
                           "trend4h": round(t4_c, 3), "align1h": round(al_c, 3),
                           "mom24h": round(m24_c, 3), "volRatio": round(vol_c, 3),
                           "regimeBonus": round(rg_c, 3)},
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


# ── close triggers — the auto-adjust brain (consumed by rebalance.py) ───────

def close_triggers(side, tide, tide_against_streak, held, scored_by_asset, inputs, due_refresh):
    """Returns [{asset, direction, reason, trigger}] for the CLOSE_POSITION action.

    held: [{asset, direction}] — this book's open positions (direction == side).
    scored_by_asset: {asset: thesis-dict} — fresh re-scores of the HELD names.
    Triggers:
      tide_flip           — the tide has been AGAINST this book for
                            `tideFlipConfirmTicks` consecutive rebalance ticks
                            → close the whole book (anti-whipsaw via streak).
      divergence_reversed — smart money now leans >= 58% AGAINST a held
                            position → close that name immediately.
      basket_refresh      — on the X-hourly refresh, a held name re-scoring
                            below exitScore is a stale thesis → recycle it.
    """
    confirm = int(_f(inputs.get("tideFlipConfirmTicks"), 2))
    exit_score = _f(inputs.get("exitScore"), 3.5)
    out = []
    if not tide_allows(side, tide) and tide_against_streak >= confirm:
        for p in held:
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "tide_flip",
                        "reason": f"tide {tide} against {side} book x{tide_against_streak}"})
        return out  # book-level flip supersedes per-name triggers
    opposite = "SHORT" if side == "LONG" else "LONG"
    for p in held:
        th = scored_by_asset.get(p["asset"])
        if not th:
            continue
        if th.get("sm_dir") == opposite and _f(th.get("sm_pct"), 50) >= 58:
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "divergence_reversed",
                        "reason": f"SM flipped {opposite} {th.get('sm_pct')}%"})
        elif due_refresh and _f(th.get("score")) < exit_score:
            out.append({"asset": p["asset"], "direction": p["direction"],
                        "trigger": "basket_refresh",
                        "reason": f"refresh re-score {th.get('score')} < {exit_score}"})
    return out
