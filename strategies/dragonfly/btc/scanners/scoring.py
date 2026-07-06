"""DRAGONFLY — pure composite scoring (no I/O, no MCP).

Port of a user-authored BTC+HYPE dual-score spec to the Runtime 3.0 supervised
contract. Each asset gets a DIRECTIONAL 0-10 composite: every factor returns a
signed contribution in [-w, +w]; score = 5 + 5 * (sum / max_possible), clamped
to [0, 10]. Direction gate: LONG >= longThreshold (7), SHORT <= shortThreshold
(4), HOLD between. Conviction strength = score (LONG) or 10 - score (SHORT),
banded apex/good/base -> per-band leverage + marginPct, then scaled by the
session modifier (day/hour windows from the source spec).

Factor registry is driven by which keys exist in inputs["weights"] — one shared
module serves both the BTC and HYPE instances with different factor sets.

FIDELITY NOTES (vs the source spec):
- oi_acceleration + impact_price + oi_exchange_share dropped — no verified MCP
  field for them (never emit a field name from memory).
- order_book_depth became a SPREAD HARD GATE (kestrel-verbatim parse) instead of
  a scored factor — level sizes have no verified extraction path fleet-wide.
- partial exits (30% off at +20%/+15%) are not expressible in the v2 DSL tier
  schema (all-or-nothing locks) — the profit-lock ladder carries the intent.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import time


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def closes(candles):
    return [_f(c.get("c")) for c in (candles or []) if isinstance(c, dict)]


def vols(candles):
    return [_f(c.get("v")) for c in (candles or []) if isinstance(c, dict)]


def mom(candles, n=1):
    """Pct change over the last n bars (close vs close n back). 0.0 if short."""
    cl = closes(candles)
    if len(cl) < n + 1 or cl[-1 - n] == 0:
        return 0.0
    return (cl[-1] / cl[-1 - n] - 1.0) * 100.0


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _trend_sign(candles, bars=3):
    """+1 rising / -1 falling / 0 mixed over the last `bars` closes."""
    cl = closes(candles)
    if len(cl) < bars + 1:
        return 0
    window = cl[-(bars + 1):]
    ups = sum(1 for a, b in zip(window, window[1:]) if b > a)
    downs = sum(1 for a, b in zip(window, window[1:]) if b < a)
    if ups == bars:
        return 1
    if downs == bars:
        return -1
    return 0


def pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 8:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / ((vx ** 0.5) * (vy ** 0.5))


def returns_1h(candles, bars=24):
    cl = closes(candles)[-(bars + 1):]
    return [(b / a - 1.0) for a, b in zip(cl, cl[1:]) if a != 0]


# ── session modifiers (source spec verbatim; UTC) ──────────────────────────
# Overlapping weekday windows MULTIPLY (e.g. Fri 14-20 x US-open 14-17);
# the weekend is exclusive and also hard-caps leverage per asset.

def get_session_factor(ts=None, weekend_leverage_cap=None):
    """Returns (factor, leverage_cap_or_None, label)."""
    t = time.gmtime(ts if ts is not None else time.time())
    dow, hour = t.tm_wday, t.tm_hour          # Mon=0 .. Sun=6
    if dow >= 5:
        return 0.6, weekend_leverage_cap, "weekend x0.6"
    factor, labels = 1.0, []
    if dow == 0 and 0 <= hour < 8:
        factor *= 1.2
        labels.append("mon-early x1.2")
    if dow == 3 and 10 <= hour < 18:
        factor *= 1.3
        labels.append("thu x1.3")
    if dow == 4 and 14 <= hour < 20:
        factor *= 0.8
        labels.append("fri-late x0.8")
    if 14 <= hour < 17:
        factor *= 1.15
        labels.append("us-open x1.15")
    return _clamp(factor, 0.5, 1.5), None, " ".join(labels) or "base x1.0"


# ── spread gate (kestrel-verbatim dual-path order-book parse) ───────────────

def spread_pct_from_book(order_book):
    """Best bid/ask spread as a fraction of mid, or None when unreadable."""
    ob = order_book if isinstance(order_book, dict) else {}
    levels = ob.get("levels", [])
    bids, asks = [], []
    if isinstance(levels, list) and len(levels) >= 2:
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
    else:
        bids = ob.get("bids", ob.get("bid", []))
        asks = ob.get("asks", ob.get("ask", []))
    if not bids or not asks:
        return None
    def _px(lvl):
        if isinstance(lvl, list) and lvl:
            return _f(lvl[0])
        if isinstance(lvl, dict):
            return _f(lvl.get("price", lvl.get("px", 0)))
        return 0.0
    best_bid, best_ask = _px(bids[0]), _px(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2
    return (best_ask - best_bid) / mid if mid > 0 else None


# ── factor functions — each returns a SIGNED contribution in [-w, +w] ───────

def f_ret4h(view, w):
    return _clamp(mom(view.get("c4"), 1) / 2.0, -1, 1) * w


def f_align1h(view, w):
    t1, t4 = _trend_sign(view.get("c1")), _trend_sign(view.get("c4"))
    if t1 != 0 and t1 == t4:
        return t1 * w
    return 0.0


def f_vol_ratio(view, w):
    vv = vols(view.get("c1"))
    if len(vv) < 25:
        return 0.0
    base = sum(vv[-25:-1]) / 24.0
    if base <= 0:
        return 0.0
    ratio = vv[-1] / base
    if ratio <= 1.0:
        return 0.0
    m1 = mom(view.get("c1"), 1)
    return _clamp(ratio - 1.0, 0, 1) * w * (1 if m1 >= 0 else -1)


def f_oi_velocity(view, w):
    """Flat-path oi_velocity.oi_change_pct_1h only (never the nested form —
    that path does not exist; see reference_cobra_antipattern)."""
    oi = view.get("oi_1h")
    if oi is None:
        return 0.0
    m1 = mom(view.get("c1"), 1)
    return (1 if m1 >= 0 else -1) * _clamp(_f(oi) / 5.0, -1, 1) * w


def f_funding(view, w):
    """Crowding fade: rich positive funding leans SHORT, deep negative leans LONG."""
    ann = _f(view.get("funding")) * 24 * 365 * 100      # hourly rate -> % APR
    return _clamp(-ann / 20.0, -1, 1) * w


def f_funding_regime(view, w):
    regime = view.get("regime")
    if regime == "LONG_CROWDED":
        return -w
    if regime == "SHORT_CROWDED":
        return w
    return 0.0


def f_funding_persistence(view, w):
    """A crowded regime that has PERSISTED >= 6h is the higher-conviction fade."""
    if _f(view.get("regime_hours")) < 6:
        return 0.0
    return f_funding_regime(view, w)


def f_sm_exposure(view, w):
    sm = view.get("sm")
    if not sm:
        return 0.0
    lean = (_f(sm.get("pct"), 50) - 50) / 50.0
    d = sm.get("direction")
    if d == "LONG":
        return _clamp(lean, 0, 1) * w
    if d == "SHORT":
        return -_clamp(lean, 0, 1) * w
    return 0.0


def f_sm_pnl(view, w):
    """Winners pressing (leaderboard contribution change, 15m) in their direction."""
    sm = view.get("sm")
    if not sm or sm.get("direction") not in ("LONG", "SHORT"):
        return 0.0
    sign = 1 if sm["direction"] == "LONG" else -1
    return sign * _clamp(abs(_f(sm.get("cc_15m"))) / 5.0, 0, 1) * w


def f_premium(view, w):
    """Mark>oracle premium = aggressive longs (dire's SM proxy), and vice versa."""
    prem = _f(view.get("premium"))
    if abs(prem) < 0.0001:
        return 0.0
    return _clamp(prem / 0.001, -1, 1) * w


def f_pair_correlation(view, w):
    """Pair (HYPE for the BTC book) confirming this asset's direction."""
    corr = pearson(returns_1h(view.get("c1")), returns_1h(view.get("pair_c1")))
    if corr is None or corr < 0.5:
        return 0.0
    pm = mom(view.get("pair_c1"), 1)
    return (1 if pm >= 0 else -1) * corr * w


def f_pair_gap(view, w):
    """Catch-up: pair (BTC for the HYPE book) moved, this asset lags -> follow."""
    gap = mom(view.get("pair_c4"), 1) - mom(view.get("c4"), 1)
    return _clamp(gap / 2.0, -1, 1) * w


def f_pair_divergence(view, w):
    """Decoupling from the pair weakens this asset's own momentum case."""
    corr = pearson(returns_1h(view.get("c1")), returns_1h(view.get("pair_c1")))
    if corr is None or corr >= 0.3:
        return 0.0
    m1 = mom(view.get("c1"), 1)
    return -(1 if m1 >= 0 else -1) * w


def f_cross_flow(view, w):
    """market_get_cross_asset_flows: leader (BTC) moved >= minLeaderMovePct and
    this asset is a listed laggard with follow_rate >= minFollowRate."""
    flow = view.get("flow")
    if not isinstance(flow, dict):
        return 0.0
    leader = flow.get("leader") or {}
    move = _f(leader.get("move_pct", flow.get("leader_move_pct")))
    if abs(move) < _f(view.get("min_leader_move_pct"), 2.0):
        return 0.0
    me = str(view.get("asset", "")).upper()
    for lag in flow.get("laggards") or []:
        if not isinstance(lag, dict):
            continue
        if str(lag.get("asset", "")).upper() != me:
            continue
        if _f(lag.get("follow_rate")) < _f(view.get("min_follow_rate"), 0.8):
            continue
        direction = str(leader.get("direction", "")).upper()
        if direction == "LONG":
            return w
        if direction == "SHORT":
            return -w
    return 0.0


def f_vol_trend(view, w):
    """Volume building under the move (serves volume_profile AND volume_trend)."""
    vv = vols(view.get("c1"))
    if len(vv) < 7:
        return 0.0
    recent, prior = sum(vv[-3:]) / 3.0, sum(vv[-6:-3]) / 3.0
    if prior <= 0:
        return 0.0
    slope = _clamp((recent / prior - 1.0) / 0.5, -1, 1)
    m3 = mom(view.get("c1"), 3)
    return (1 if m3 >= 0 else -1) * max(slope, 0.0) * w


_FACTORS = {
    "ret4h": f_ret4h,
    "align1h": f_align1h,
    "volRatio": f_vol_ratio,
    "oiVelocity": f_oi_velocity,
    "funding": f_funding,
    "fundingRegime": f_funding_regime,
    "fundingPersistence": f_funding_persistence,
    "smExposure": f_sm_exposure,
    "smPnl": f_sm_pnl,
    "premiumOracle": f_premium,
    "pairCorrelation": f_pair_correlation,
    "pairGap": f_pair_gap,
    "pairDivergence": f_pair_divergence,
    "crossAssetFlow": f_cross_flow,
    "volProfile": f_vol_trend,
    "volTrend": f_vol_trend,
}


def compute(view, inputs, ts=None):
    """Composite score for one asset view. Returns a thesis dict or None.

    None means HARD-GATED (insufficient candles / spread too wide) — distinct
    from a HOLD (dict with direction None)."""
    if len(closes(view.get("c1"))) < 8 or len(closes(view.get("c4"))) < 3:
        return None
    spread_max = _f(inputs.get("spreadMaxPct"), 0.002)
    spread = view.get("spread_pct")
    if spread is not None and spread > spread_max:
        return None

    weights = inputs.get("weights") or {}
    components, total, max_possible = {}, 0.0, 0.0
    for key, w in weights.items():
        fn = _FACTORS.get(key)
        w = _f(w)
        if fn is None or w <= 0:
            continue
        val = fn(view, w)
        components[key] = round(val, 4)
        total += val
        max_possible += w
    if max_possible <= 0:
        return None

    score = _clamp(5.0 + 5.0 * (total / max_possible), 0.0, 10.0)
    long_th = _f(inputs.get("longThreshold"), 7.0)
    short_th = _f(inputs.get("shortThreshold"), 4.0)
    direction = "LONG" if score >= long_th else "SHORT" if score <= short_th else None
    strength = score if direction == "LONG" else (10.0 - score) if direction == "SHORT" else 0.0

    band = None
    if direction:
        if strength >= _f(inputs.get("apexScore"), 9.0):
            band = "apex"
        elif strength >= _f(inputs.get("goodScore"), 8.0):
            band = "good"
        else:
            band = "base"

    factor, lev_cap, session_label = get_session_factor(
        ts, weekend_leverage_cap=inputs.get("weekendLeverageCap"))

    leverage = margin_pct = None
    if band:
        lev_tiers = inputs.get("leverageTiers") or {}
        mgn_tiers = inputs.get("marginPctTiers") or {}
        leverage = _f(lev_tiers.get(band), 5)
        if lev_cap is not None:
            leverage = min(leverage, _f(lev_cap, leverage))
        margin_pct = max(round(_f(mgn_tiers.get(band), 20) * factor, 2), 5.0)

    reasons = [f"score {score:.2f} ({'HOLD' if not direction else direction} band={band})",
               f"session {session_label}"]
    top = sorted(components.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]
    reasons.extend(f"{k} {v:+.2f}" for k, v in top)

    return {
        "score": round(score, 2), "direction": direction, "band": band,
        "strength": round(strength, 2), "leverage": leverage, "margin_pct": margin_pct,
        "session_factor": factor, "session_label": session_label,
        "spread_pct": spread, "components": components, "reasons": reasons,
    }
