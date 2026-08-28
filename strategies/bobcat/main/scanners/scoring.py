"""BOBCAT — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Bobcat producer's `trend_structure` +
`build_thesis` scoring (SKILL.md v1.0.0 / bobcat-producer.py v1.0.1). The
math/indexing is reproduced VERBATIM so a fidelity harness can diff this against
the v2 producer on the same market snapshot. Behaviour-preserving quirks from v2
are kept and flagged `# v2-quirk`.

Multi-asset (12-name big-tech whitelist), single-pass, unit-testable on plain
candle lists. Smart-money lean (`sm`) is fetched by the caller (scan.py) and
passed in, so this module stays pure.

Bobcat is a GATED scorer (unlike Bison, where signals are score contributors):
  - 4h trend must be non-neutral (else None),
  - Smart-Money direction must be LONG/SHORT AND equal the 4h-derived direction,
  - SM tilt must be >= smTiltMinPct.
If any gate fails, build_thesis returns None. The minScore floor is applied by
the CALLER (scan.py), exactly as v2 main() did.
"""


# ── candle accessors (dual-key: dict {low|l}/{high|h}/{close|c}) ──
# v2 used `_f(c, "low", "l")` etc.; reproduced here as helpers over the same keys.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ck(c, primary, alt=None, default=0.0):
    """Verbatim port of the v2 producer's `_f(c, primary, alt, default)` candle
    field accessor: read `primary`, fall back to `alt`, coerce to float."""
    if not isinstance(c, dict):
        return default
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    return _f(val if val is not None else default, default)


# ── indicators (ported verbatim from v2 bobcat-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from v2.

    v2-quirk: thresholds use strict (>) for higher-lows / lower-highs counting,
    and the BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1.
    Reproduced exactly. Strength returned is higher_lows/total (BULLISH) or
    lower_highs/total (BEARISH); NEUTRAL returns 0.0."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_ck(c, "low", "l") for c in candles[-lookback:]]
    highs = [_ck(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


# ── the thesis (gated 4h-trend + SM-direction scorer), ported verbatim ──

def build_thesis(coin, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned (a GATE FAILED) when:
      - insufficient candle history (len(c4h) < 6 or len(c1h) < 6), OR
      - the 4h trend is NEUTRAL, OR
      - Smart-Money direction is not LONG/SHORT, OR
      - Smart-Money direction != the 4h-derived direction, OR
      - Smart-Money tilt < smTiltMinPct.
    The minScore floor is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0.0) — the caller
    fetches it (scan._get_sm_direction) and passes it in.

    Score components (verbatim from v2; max ~9):
      +3  4h trend (gate-confirmed)
      +2  1h trend confirms the 4h direction
      +2  SM aligned (gate-confirmed; always added post-gate)
      +1  SM strongly tilted (tilt >= smStrongTiltPct)
    """
    sm_min = float(inputs.get("smTiltMinPct", 55))
    sm_strong = float(inputs.get("smStrongTiltPct", 70))

    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    if t4 == "NEUTRAL":
        return None

    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    score = 3  # 4h trend (gate-confirmed)
    reasons = [f"4h trend {t4.lower()} at {s4:.0%} strength"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h trend confirms the {t1.lower()} move")
    score += 2
    reasons.append(f"smart money {sm_tilt:.0f}% on this side")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("smart money heavily tilted this way")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "trend_4h": t4, "trend_4h_strength": round(s4, 4), "trend_1h": t1,
        "sm_direction": sm_dir, "sm_tilt_pct": round(_f(sm_tilt), 2),
    }
