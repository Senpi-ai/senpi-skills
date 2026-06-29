"""HEDGEHOG — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 HEDGEHOG producer's `trend_structure`
+ `build_thesis` scoring (hedgehog-producer.py v1.0.1 / SKILL.md v1.0.0).
The math/indexing is reproduced VERBATIM so a fidelity harness can diff this
against the v2 producer on the same market snapshot.

HEDGEHOG is an equal-weight BTC + ETH + SOL basket; each asset is evaluated
INDEPENDENTLY (long OR short) on a hard-gated 4h trend + Smart-Money direction
thesis. Unlike Bison (all factors are score contributors), HEDGEHOG has HARD
GATES — if the 4h trend is NEUTRAL, or SM doesn't agree with the 4h direction,
or SM tilt is below the floor, the thesis is rejected (returns None).

Multi-asset, single-pass, unit-testable on plain candle lists. `sm` (smart-money
lean) is fetched by the caller and passed in, so this module stays pure."""


def _f(c, primary, alt=None, default=0.0):
    """Verbatim port of v2 producer `_f`: pull primary key, fall back to alt,
    coerce to float, default on failure."""
    val = c.get(primary) if isinstance(c, dict) else None
    if val is None and alt:
        val = c.get(alt) if isinstance(c, dict) else None
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from v2.

    v2-quirk: counting uses STRICT > comparisons, the BULLISH/BEARISH gate is
    `>= total * 0.6` where total = lookback - 1, and the returned strength is
    higher_lows/total (BULLISH) or lower_highs/total (BEARISH). Reproduced
    exactly."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_f(c, "low", "l") for c in candles[-lookback:]]
    highs = [_f(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def build_thesis(coin, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    HARD GATES (any failure -> None, verbatim from v2):
      - fewer than 6x 1h candles OR 6x 4h candles,
      - 4h trend NEUTRAL,
      - SM direction not LONG/SHORT, or SM direction != 4h direction,
      - SM tilt < smTiltMinPct.

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0.0) — the
    caller fetches it (fetch_sm_direction). minScore is NOT applied here; the
    caller gates on thesis['score'] (v2 main() did the same)."""
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

    # Score (max ~9), verbatim from v2 build_thesis:
    #   +3 4h trend (gate-confirmed)
    #   +2 1h trend confirms 4h direction
    #   +2 SM aligned (always granted once the SM gate passes)
    #   +1 SM strongly tilted (>= smStrongTiltPct)
    score = 3
    reasons = [f"4h_{t4.lower()}_{s4:.0%}"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{t1.lower()}")
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "trend_4h": t4, "trend_4h_strength": round(s4, 4), "trend_1h": t1,
        "sm_direction": sm_dir, "sm_tilt_pct": round(_f({"v": sm_tilt}, "v"), 2),
    }
