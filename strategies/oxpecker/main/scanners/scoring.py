"""OXPECKER — pure elite-conviction-mirror math (no I/O, no MCP, no clock).

NET-NEW Runtime 3.0 strategy (not a v2 port). Given trader/position dicts already
fetched by scan.py, this module:
  - parses the discovery_get_top_traders quality fields (tcsLabel / tcsValue /
    address / ROI) — field accessors COPIED from the Jackal/Raptor gold templates,
  - parses position notional + direction — COPIED VERBATIM from the Remora gold
    template (position_notional / mirror_direction / position_asset),
  - computes per-trader CONCENTRATION = largest-position notional / total notional
    across all of that trader's open positions (NEW — Oxpecker's whole edge),
  - aggregates concentrated elite positions into (asset, direction) candidates
    weighted by trader quality x concentration (consensus across multiple elite
    traders strengthens the score), and scores them.

Pure / single-pass / unit-testable on plain dicts. All clock/MCP lives in scan.py.

FIELD-SHAPE FLAGS (no live token in the build env — fields sourced from the gold
templates + the senpi-overview guide §8, NOT confirmed against a live response):
  - QUALITY TIER: request filter param is `consistency` (enum ELITE/RELIABLE/
    STREAKY/CHOPPY, guide §8); response field is `tcsLabel` with numeric `tcsValue`
    (TCS = Trader Consistency Score) — COPIED from raptor/main/scanners/scan.py
    (fetch_quality_hot_traders). FLAGGED: exact response key not re-verified live.
  - POSITION NOTIONAL for concentration: each open position's USD notional is taken
    via position_notional() (size x entry -> marginUsed -> size), the Remora chain.
    Concentration is derived FROM the positions themselves (largest / total), so it
    does not depend on any single trader-level "concentration" field existing —
    exactly the fallback the spec calls for. FLAGGED: positionValue (the
    discovery_get_trader_state notional field per guide) is tried first, then the
    Remora chain.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT


# Quality (consistency / TCS) tiers Oxpecker will mirror. ELITE/RELIABLE are the
# top two of the four-tier scale (ELITE > RELIABLE > STREAKY > CHOPPY, guide §8).
DEFAULT_QUALITY_TIERS = ("ELITE", "RELIABLE")

# Producer constants.
MIN_LEVERAGE = 1
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 4
DEFAULT_MIN_NOTIONAL_USD = 5000     # ignore dust positions when ranking the largest
DEFAULT_MIN_CONCENTRATION = 0.5     # largest notional / total notional floor


def safe_float(v, default=0.0):
    """Float coercion (Remora safe_float)."""
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


def _f(x, *keys, default=0.0):
    """Float from x. If keys given, x is a dict and we try each key in order
    (first non-None wins). Mirrors the gold-template camelCase/snake_case fallbacks."""
    if keys:
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ── trader-pool quality parsing (COPIED from jackal/raptor gold templates) ──

def trader_address(t):
    """Lower-cased trader address, or '' (jackal build_pool / raptor)."""
    addr = t.get("address") or t.get("trader_address") or t.get("traderAddress")
    return str(addr).lower() if addr else ""


def trader_quality_tier(t):
    """The consistency (TCS) tier label, upper-cased, or '' if absent.
    COPIED from raptor fetch_quality_hot_traders: response field is `tcsLabel`.
    Fallbacks (`consistency`, `tier`, `classification`) are defensive only."""
    label = (t.get("tcsLabel") or t.get("consistency") or t.get("consistencyLabel")
             or t.get("tier") or t.get("classification") or "")
    return str(label).upper() if label else ""


def trader_quality_value(t):
    """Numeric TCS value if present (raptor `tcsValue`), else 0.0."""
    return _f(t, "tcsValue", "tcs_value", default=0.0)


def trader_roi(t):
    """30d/period ROI % (jackal pool_field_roi fallbacks)."""
    return _f(t, "returnOnInvestment", "return_on_investment", "roi", default=0.0)


def build_pool(raw_traders, inputs):
    """Filter the raw discovery_get_top_traders list to the allowed quality tiers
    (ELITE/RELIABLE) and an optional numeric tcsValue floor, then keep top-N.

    Returns a list of pool-member dicts. The quality_score carried here is the
    numeric tcsValue when available, otherwise a tier rank (ELITE=2, RELIABLE=1)
    scaled so downstream quality-weighting has a usable signal even if only the
    label is returned. FLAGGED: tcsValue scale not re-verified live; the tier-rank
    fallback guarantees a non-degenerate weight."""
    allowed = {str(x).upper() for x in inputs.get("qualityTiers", DEFAULT_QUALITY_TIERS)}
    min_q = float(inputs.get("minQualityScore", 0.0))
    pool_size = int(inputs.get("poolSize", 30))

    out = []
    for t in raw_traders:
        if not isinstance(t, dict):
            continue
        addr = trader_address(t)
        if not addr:
            continue
        tier = trader_quality_tier(t)
        if allowed and tier not in allowed:
            continue
        qv = trader_quality_value(t)
        if min_q > 0 and qv < min_q:
            continue
        out.append({
            "address": addr,
            "user_id": t.get("user_id") or t.get("userId"),
            "username": t.get("username") or t.get("userName"),
            "tier": tier,
            "tcs_value": qv,
            "roi": trader_roi(t),
            "quality_score": _quality_score(tier, qv),
        })
    # rank: highest quality first (tcsValue, then tier rank, then ROI)
    out.sort(key=lambda x: (x["quality_score"], x["roi"]), reverse=True)
    return out[:pool_size]


def _tier_rank(tier):
    """ELITE=2, RELIABLE=1, anything else 0 (the two allowed tiers, ranked)."""
    return {"ELITE": 2, "RELIABLE": 1}.get((tier or "").upper(), 0)


def _quality_score(tier, tcs_value):
    """A 0..1 quality weight for margin/score weighting. Prefer the numeric
    tcsValue (normalized: TCS appears to be a 0-100-ish score in the templates, so
    /100 and clamp to 1), else fall back to the tier rank (ELITE=1.0, RELIABLE=0.6).
    FLAGGED: tcsValue scale assumed 0-100 (raptor carried it raw); the clamp makes
    a larger scale harmless and the tier fallback covers a missing value."""
    if tcs_value and tcs_value > 0:
        return min(1.0, tcs_value / 100.0)
    return {2: 1.0, 1: 0.6}.get(_tier_rank(tier), 0.0)


# ── position notional + direction + asset (COPIED VERBATIM from Remora) ──

def position_notional(pos):
    """USD notional of a position for conviction ranking. Prefers the
    discovery_get_trader_state notional field (`positionValue`), then the Remora
    chain: size x entry -> marginUsed -> raw size. FLAGGED: positionValue is the
    documented notional field (guide), tried first; the rest is verbatim Remora."""
    if not isinstance(pos, dict):
        return 0.0
    pv = abs(_f(pos, "positionValue", "position_value", "notional", default=0.0))
    if pv > 0:
        return pv
    size = abs(safe_float(pos.get("szi", pos.get("size", 0))))
    entry = safe_float(pos.get("entryPx", pos.get("entryPrice", pos.get("entry", 0))))
    notional = size * entry
    if notional > 0:
        return notional
    margin = abs(safe_float(pos.get("marginUsed", pos.get("margin", 0))))
    return margin if margin > 0 else size


def mirror_direction(pos):
    """LONG / SHORT for a position (explicit direction/side field, else szi sign).
    None if undeterminable. Verbatim from Remora."""
    if not isinstance(pos, dict):
        return None
    d = str(pos.get("direction", pos.get("side", ""))).upper()
    if d in ("LONG", "SHORT"):
        return d
    szi = safe_float(pos.get("szi", pos.get("size", 0)))
    if szi > 0:
        return "LONG"
    if szi < 0:
        return "SHORT"
    return None


def position_asset(pos):
    """Asset symbol, upper-cased. Verbatim from Remora."""
    if not isinstance(pos, dict):
        return ""
    return str(pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))).upper()


def position_leverage(pos):
    """Leverage of a position (dict {value} or scalar). Jackal _leverage_of."""
    lev = pos.get("leverage")
    if isinstance(lev, dict):
        return safe_float(lev.get("value"), default=0.0)
    return safe_float(lev, default=0.0)


# ── NEW: concentration = largest position notional / total notional ──

def concentrated_top(positions, min_notional=0.0):
    """Return (top_position, concentration) for one trader's open book, where:
      - top_position = the single largest-notional position with a determinable
        direction, an asset, and notional >= min_notional,
      - concentration = top_position notional / SUM of ALL open-position notionals
        (the whole book, NOT just the qualifying ones) — so a trader with one big
        bet plus a few tiny ones still reads as highly concentrated.

    Returns (None, 0.0) if the trader holds nothing with a usable top position.
    Concentration is derived FROM the positions themselves, so it does not depend
    on any trader-level "concentration" field existing (the spec's fallback)."""
    if not positions:
        return None, 0.0
    total = 0.0
    best, best_n = None, -1.0
    for p in positions:
        if not isinstance(p, dict):
            continue
        n = position_notional(p)
        if n <= 0:
            continue
        total += n
        if mirror_direction(p) is None or not position_asset(p):
            continue
        if n < min_notional:
            continue
        if n > best_n:
            best_n, best = n, p
    if best is None or total <= 0:
        return None, 0.0
    concentration = best_n / total if total > 0 else 0.0
    return best, round(min(1.0, concentration), 4)


# ── aggregate concentrated elite positions into (asset, direction) candidates ──

def aggregate_candidates(trader_tops):
    """Aggregate per-trader (trader, top_position, concentration) tuples into
    (asset, direction) candidates.

    `trader_tops` is a list of (trader_dict, top_position_dict, concentration)
    tuples already filtered to concentration >= minConcentration by the caller.
    Each candidate accumulates:
      - count            = how many elite traders hold this asset+direction (consensus),
      - max_notional     = the largest single mirrored notional,
      - max_concentration= the strongest single concentration,
      - sum_qc           = sum of (quality_score x concentration) across agreeing
                           traders (the conviction-weight the score uses),
      - best_quality     = the strongest single quality_score,
      - any_elite        = any agreeing trader is ELITE tier,
      - traders          = short ids for telemetry."""
    agg = {}
    for trader, top, concentration in trader_tops:
        if not top:
            continue
        asset = position_asset(top)
        direction = mirror_direction(top)
        if not asset or direction is None:
            continue
        notional = position_notional(top)
        q = float(trader.get("quality_score", 0.0))
        key = (asset, direction)
        entry = agg.setdefault(key, {
            "asset": asset, "direction": direction,
            "count": 0, "max_notional": 0.0, "max_concentration": 0.0,
            "sum_qc": 0.0, "best_quality": 0.0, "any_elite": False,
            "traders": [],
        })
        entry["count"] += 1
        entry["max_notional"] = max(entry["max_notional"], notional)
        entry["max_concentration"] = max(entry["max_concentration"], concentration)
        entry["sum_qc"] += q * concentration
        entry["best_quality"] = max(entry["best_quality"], q)
        if (trader.get("tier") or "").upper() == "ELITE":
            entry["any_elite"] = True
        entry["traders"].append({
            "address": trader.get("address", ""),
            "tier": trader.get("tier", ""),
            "concentration": concentration,
            "notional": round(notional, 2),
        })
    return list(agg.values())


def consensus_bonus(count):
    """Score bonus for how many elite traders independently hold the same
    concentrated asset+direction. 3+ is a strong consensus (Remora pattern)."""
    if count >= 3:
        return 3
    if count == 2:
        return 2
    return 0


def score_candidate(cand):
    """(score, reasons) for an aggregated candidate. Quality-tier x concentration
    is the edge:
      +3 base                         — a tracked elite trader's concentrated top bet,
      +2 if max_concentration >= 0.9  — overwhelming conviction (e.g. ~98% of book),
        +1 if max_concentration >= 0.7
      +round(sum_qc * 2)              — quality x concentration mass across agreeing
                                        traders (cap +3),
      +consensus_bonus(count)         — 2 traders +2, 3+ +3,
      +1 if any agreeing trader is ELITE tier.
    """
    score = 3
    reasons = [
        f"{cand['asset']}_{cand['direction']}",
        f"concentration_{cand['max_concentration']:.0%}",
        f"traders_{cand['count']}",
        f"notional_${cand['max_notional']:,.0f}",
    ]

    mc = cand["max_concentration"]
    if mc >= 0.9:
        score += 2
        reasons.append("overwhelming_conviction")
    elif mc >= 0.7:
        score += 1
        reasons.append("high_conviction")

    qc_bonus = min(3, int(round(cand["sum_qc"] * 2)))
    if qc_bonus > 0:
        score += qc_bonus
        reasons.append(f"quality_x_conc_{qc_bonus}")

    cb = consensus_bonus(cand["count"])
    if cb:
        score += cb
        reasons.append(f"consensus_{cand['count']}_traders")

    if cand.get("any_elite"):
        score += 1
        reasons.append("elite_tier")

    return score, reasons


# ── sizing (top-level marginPct PERCENT) ──

def margin_pct_for(cand, inputs):
    """marginPct INTENT as a PERCENT of withdrawable in (0,100] (runtime sizes
    (marginPct/100)*withdrawable).

    Default is FLAT at the base marginPct (convictionMarginScale defaults to 0).
    When an operator opts in (>0), margin scales up toward maxMarginPct by the
    candidate's conviction mass (quality x concentration). Defensive fraction guard
    (dire/koala/remora pattern): a base/cap pasted as a fraction (<= 1.0, e.g. 0.15)
    is multiplied x100 to a percent."""
    base = float(inputs.get("marginPct", 15))
    if base <= 1.0:                       # pasted fraction (0.15) -> percent (15)
        base *= 100.0
    cap = float(inputs.get("maxMarginPct", base))
    if cap <= 1.0:
        cap *= 100.0
    scale = float(inputs.get("convictionMarginScale", 0.0))   # 0 => faithful flat
    if scale <= 0:
        return round(min(base, cap), 4)
    # conviction mass: best quality x max concentration, in [0,1]
    mass = float(cand.get("best_quality", 0.0)) * float(cand.get("max_concentration", 0.0))
    return round(min(base * (1.0 + mass * scale), cap), 4)


def confidence_score(score, min_score):
    """A 0..1 confidence for data{} (score normalized over a soft ceiling)."""
    ceiling = max(float(min_score) + 6.0, 1.0)
    return round(min(1.0, float(score) / ceiling), 4)
