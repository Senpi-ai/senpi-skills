"""LYNX — pure thesis + self-tuning math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Lynx producer's two pure cores
(SKILL.md v1.0.0):

  1. The MOMENTUM SCORER (max ~8): 4h trend strength, 1h confirmation,
     smart-money alignment, volume rising. Ported verbatim from
     lynx-producer.py (pct_move, trend_direction, lynx_score).

  2. The SELF-TUNER (archetype #15): given the agent's own closed-trade
     history bucketed by the entry score logged in each trade, decide
     whether to RAISE the MIN_SCORE floor above any bucket at-or-above the
     current floor that has accumulated enough samples AND is bleeding.
     Ported verbatim (parse_score_from_reasoning, compute_bucket_stats,
     recommend_min_score, should_update_threshold). Lynx ratchets UP only —
     it never lowers its own floor — and caps at maxMinScore.

The math/indexing is reproduced VERBATIM so a fidelity harness can diff
this against the v2 producer on the same snapshot. `scan.py` does the MCP
reads + state; this module stays pure (plain candle lists + trade dicts)
and unit-testable. `sm` (smart-money lean) is fetched by the caller and
passed in.
"""

import re


# ── coercion + candle accessors (dual-shape: dict {close|c} OR list) ──
# v2 read dicts via _f(c, "close", "c"); the list branch is defensive and
# never fires on dict candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ═══════════════════════════════════════════════════════════════
# SELF-TUNER — pure logic (ported verbatim from lynx-producer.py)
# ═══════════════════════════════════════════════════════════════

_SCORE_RE = re.compile(r"\bscore[:\s=]+(\d+)\b", re.IGNORECASE)


def parse_score_from_reasoning(text):
    """Extract `score N` from ai_reasoning text. Looks for 'score 5' or
    'score: 5' or 'Score=5' (case-insensitive). Returns int or None.
    Verbatim from v2."""
    if not text or not isinstance(text, str):
        return None
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return None


def compute_bucket_stats(trades):
    """Bucket trades by score and compute per-bucket stats. Verbatim from v2.

    Each trade: {"score": int, "roe_pct": float} (other fields ignored).
    Returns {score: {"n", "avg_roe_pct", "win_rate_pct"}}. Trades with no
    score are dropped (no bucket). Win = ROE > 0."""
    buckets = {}
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        s = t.get("score")
        if s is None:
            continue
        try:
            score = int(s)
            roe = float(t.get("roe_pct", 0))
        except (TypeError, ValueError):
            continue
        b = buckets.setdefault(score, {"n": 0, "total_roe": 0.0, "wins": 0})
        b["n"] += 1
        b["total_roe"] += roe
        if roe > 0:
            b["wins"] += 1
    out = {}
    for score, b in buckets.items():
        out[score] = {
            "n": b["n"],
            "avg_roe_pct": (b["total_roe"] / b["n"]) if b["n"] else 0.0,
            "win_rate_pct": (b["wins"] * 100.0 / b["n"]) if b["n"] else 0.0,
        }
    return out


def recommend_min_score(bucket_stats, current_min_score, min_bucket_n, bucket_bleed_pct, max_min_score):
    """Decide whether to raise MIN_SCORE based on bucket stats. Verbatim from v2.

    Rule: find the HIGHEST score bucket at-or-above the current floor that
    has accumulated `min_bucket_n` samples AND is bleeding (avg_roe_pct
    below `bucket_bleed_pct`). If found, recommend MIN_SCORE = that score + 1
    (so the next eligible bucket starts above it). Caps at `max_min_score`.
    If no bucket meets the bleed criterion, returns the current_min_score."""
    bleeding_floors = [
        score for score, stats in bucket_stats.items()
        if score >= current_min_score
        and stats["n"] >= min_bucket_n
        and stats["avg_roe_pct"] < bucket_bleed_pct
    ]
    if not bleeding_floors:
        return current_min_score
    highest_bleeding = max(bleeding_floors)
    recommended = highest_bleeding + 1
    return min(recommended, max_min_score)


def should_update_threshold(current, recommended, hysteresis=1):
    """Only update if the recommended threshold is at least `hysteresis`
    above the current. Prevents flapping on small noise. Verbatim from v2 —
    note this is RAISE-ONLY (Lynx never lowers its own floor)."""
    if recommended is None or current is None:
        return False
    return recommended >= current + hysteresis


def bleeding_buckets(bucket_stats, current_min, min_n, bleed_pct):
    """Helper for the adjustment audit-trail: the buckets that triggered a
    raise (score >= floor, n >= min_n, avg_roe < bleed_pct). Mirrors the v2
    `bleeding_buckets` list comprehension in run_audit_if_due."""
    return [
        {"score": s, "stats": v}
        for s, v in bucket_stats.items()
        if s >= current_min and v["n"] >= min_n and v["avg_roe_pct"] < bleed_pct
    ]


# ═══════════════════════════════════════════════════════════════
# MOMENTUM SCORER — pure logic (ported verbatim from lynx-producer.py)
# ═══════════════════════════════════════════════════════════════

def pct_move(closes, lookback):
    """% change over `lookback` bars back. Verbatim from v2 pct_move."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def trend_direction(strength_pct, threshold):
    """LONG/SHORT/None from a signed % move vs an absolute threshold.
    Verbatim from v2 trend_direction."""
    if strength_pct is None or abs(strength_pct) < threshold:
        return None
    return "LONG" if strength_pct > 0 else "SHORT"


def lynx_score(trend_strength_4h, trend_1h_aligned, sm_aligned, volume_rising):
    """Compose a Lynx score from boolean / magnitude inputs. Verbatim from v2.

    - 4h trend strength: +3 if |move| >= 4%, +2 if >= 2%, +1 if >= 1%
    - 1h confirmation:   +2 if aligned to 4h direction
    - SM aligned:        +2 if SM tilts in same direction past threshold
    - Volume rising:     +1 if recent vol > prior baseline
    Max ~8. Floor is the (adaptive) MIN_SCORE."""
    s = 0
    if trend_strength_4h is None:
        return 0
    abs_t = abs(trend_strength_4h)
    if abs_t >= 4.0:
        s += 3
    elif abs_t >= 2.0:
        s += 2
    elif abs_t >= 1.0:
        s += 1
    if trend_1h_aligned:
        s += 2
    if sm_aligned:
        s += 2
    if volume_rising:
        s += 1
    return s


# ── the thesis (direction + 4-component momentum score), ported verbatim ──

def build_thesis(coin, candles_4h, candles_1h, sm, current_min_score, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when:
      - insufficient candle history (len(c4h) < 8 or len(c1h) < 8), OR
      - no 4h trend direction resolves (|move_4h| < 1.0%), OR
      - the score is below `current_min_score` (the v2 gate; the producer
        applied MIN_SCORE inside build_thesis, so it is reproduced here).

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0) — the
    caller fetches it (get_sm_direction)."""
    sm_min = float(inputs.get("smTiltMinPct", 55))

    closes_4h = [_close(c) for c in candles_4h]
    closes_1h = [_close(c) for c in candles_1h]
    if len(closes_4h) < 8 or len(closes_1h) < 8:
        return None

    move_4h = pct_move(closes_4h, 6)    # 24h on 4h bars
    move_1h = pct_move(closes_1h, 4)    # 4h on 1h bars
    direction = trend_direction(move_4h, 1.0)
    if direction is None:
        return None

    one_h_aligned = (move_1h is not None) and ((move_1h > 0) == (move_4h > 0)) and (abs(move_1h) >= 0.5)

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    sm_aligned = (sm_dir == direction and sm_tilt >= sm_min)

    # Volume surge: recent-3 vs prior-9 mean on 4h bars, ratio >= 1.3
    vols = [_vol(c) for c in candles_4h]
    vol_rising = False
    if len(vols) >= 12:
        recent = sum(vols[-3:]) / 3
        baseline = sum(vols[-12:-3]) / 9
        vol_rising = baseline > 0 and (recent / baseline) >= 1.3

    score = lynx_score(move_4h, one_h_aligned, sm_aligned, vol_rising)
    if score < current_min_score:
        return None

    reasons = [f"trend4h_{move_4h:+.1f}%", f"score_{score}", f"floor_{current_min_score}"]
    if one_h_aligned:
        reasons.append("1h_aligned")
    if sm_aligned:
        reasons.append(f"sm_{sm_tilt:.0f}%")
    if vol_rising:
        reasons.append("vol_rising")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h_pct": round(move_4h, 2),
        "trend_1h_pct": round(move_1h, 2) if move_1h is not None else 0.0,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": _f(sm_tilt),
        "vol_rising": vol_rising,
    }
