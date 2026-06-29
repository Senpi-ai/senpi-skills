"""RACCOON — pure thesis math (no I/O, no MCP, no wall-clock).

A faithful Runtime 3.0 port of the v2 RACCOON producer's directional-move
detection + weekend-window gate + per-asset scoring (SKILL.md / producer
v1.0.1, "Weekend XYZ reconciliation trader"). The gate thresholds, scoring
table, and weekend-window boundaries are reproduced VERBATIM so a fidelity
harness can diff this against the v2 producer on the same snapshot.

Pure + unit-testable on plain lists/dicts. The caller (scan.py) owns the clock
and the MCP reads; `in_weekend_window` takes the UTC `datetime` as an argument so
this module never calls `datetime.now()` itself (keeps it pure)."""

from datetime import timezone

# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from the v2 raccoon-producer.py v1.0.1
# ═══════════════════════════════════════════════════════════════

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55          # smTiltMinPct — SM-agreement floor (gate)
DEFAULT_SM_STRONG = 70           # smStrongTiltPct — strongly-tilted scoring bonus
DEFAULT_MIN_MOVE_PCT = 2.0       # minMoveAbsPct — directional-move gate
DEFAULT_MIN_VOL_USD = 1_000_000  # minVolUsd — universe liquidity floor
DEFAULT_MIN_MAX_LEV = 10         # minMaxLeverage — excludes IPOPs (max_lev 5, Lemur's territory)

# Margin is a PERCENT of withdrawable in (0,100] — the Runtime 3.0 wire
# convention — NOT the v2 fraction (0.15). v2 emitted marginUsd =
# account_value * 0.15; the runtime now sizes (marginPct/100)*withdrawable.
DEFAULT_MARGIN_PCT = 15.0        # v2 config marginPct 0.15 -> 15 PERCENT

# Weekend window: Fri 22:00 UTC -> Mon 00:00 UTC (the trade.xyz no-external-price
# window when XYZ uses its internal oracle only). VERBATIM from v2.
WEEKEND_START_DOW = 4   # Friday (Python weekday(): Mon=0 .. Sun=6)
WEEKEND_START_HOUR = 22
WEEKEND_END_DOW = 0     # Monday
WEEKEND_END_HOUR = 0


def _f(v, d=0.0):
    """Numeric coercion (matches v2 _f)."""
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def in_weekend_window(now):
    """True iff `now` (a tz-aware UTC datetime) falls in Fri 22:00 UTC ->
    Mon 00:00 UTC. Ported VERBATIM from v2 in_weekend_window. The caller passes
    `now` so this stays pure (no datetime.now() here)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    dow = now.weekday()   # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    if dow == WEEKEND_START_DOW and hour >= WEEKEND_START_HOUR:   # Fri >= 22:00
        return True
    if dow == 5 or dow == 6:                                      # Sat / Sun — all day
        return True
    if dow == WEEKEND_END_DOW and hour < WEEKEND_END_HOUR:        # Mon < 00:00 (never true; covered by Sun)
        return True
    return False


def detect_directional_move(candles_1h, min_pct):
    """Returns (direction, abs_move_pct, vol_x). Looks at the move from ~48h ago
    (approximating Friday close) to the latest 1h close, with volume confirmation
    (recent 6h avg vs prior 18h avg). Ported VERBATIM from v2 detect_directional_move.

    Returns (None, 0.0, 1.0) when there is insufficient history or the move is below
    `min_pct`."""
    if len(candles_1h) < 24:
        return None, 0.0, 1.0
    # Compare latest close vs ~48h ago (approximates Fri close -> now move).
    earlier = (_f(candles_1h[-48].get("close", candles_1h[-48].get("c")))
               if len(candles_1h) >= 48
               else _f(candles_1h[0].get("close", candles_1h[0].get("c"))))
    latest = _f(candles_1h[-1].get("close", candles_1h[-1].get("c")))
    if earlier <= 0 or latest <= 0:
        return None, 0.0, 1.0
    move_pct = ((latest - earlier) / earlier) * 100
    abs_move = abs(move_pct)
    if abs_move < min_pct:
        return None, 0.0, 1.0
    # Volume ratio: recent 6h avg vs prior 18h avg.
    if len(candles_1h) >= 24:
        recent_vol = sum(_f(c.get("volume", c.get("v"))) for c in candles_1h[-6:]) / 6
        prior_vol = sum(_f(c.get("volume", c.get("v"))) for c in candles_1h[-24:-6]) / 18
        vol_x = recent_vol / prior_vol if prior_vol > 0 else 1.0
    else:
        vol_x = 1.0
    direction = "LONG" if move_pct > 0 else "SHORT"
    return direction, abs_move, vol_x


def build_thesis(asset, candles_1h, sm_dir, sm_tilt, inputs=None):
    """Score one XYZ asset for the weekend-reconciliation setup.

    `candles_1h` = list of 1h candle dicts (oldest -> newest)
    `sm_dir`     = smart-money direction for this asset ("LONG"/"SHORT"/"NEUTRAL"/None)
    `sm_tilt`    = smart-money tilt PERCENT for this asset
    `inputs`     = optional overrides (minMoveAbsPct/smTiltMinPct/smStrongTiltPct)

    Returns a scored thesis dict, or None if any hard gate fails. Gate thresholds
    and the scoring table are VERBATIM from v2 build_thesis."""
    inputs = inputs or {}
    min_pct = float(inputs.get("minMoveAbsPct", DEFAULT_MIN_MOVE_PCT))
    sm_min = float(inputs.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(inputs.get("smStrongTiltPct", DEFAULT_SM_STRONG))

    if len(candles_1h) < 24:
        return None

    direction, move_pct, vol_x = detect_directional_move(candles_1h, min_pct)
    if direction is None:
        return None

    # HARD GATE: SM direction must exist AND agree with the move.
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    # HARD GATE: SM tilt floor.
    if sm_tilt < sm_min:
        return None

    score = 0
    reasons = []
    # Move magnitude
    if move_pct >= 4.0:
        score += 3
        reasons.append(f"move_strong_{move_pct:+.2f}%")
    elif move_pct >= 2.5:
        score += 2
        reasons.append(f"move_{move_pct:+.2f}%")
    else:
        score += 1
        reasons.append(f"move_weak_{move_pct:+.2f}%")
    # SM aligned (gate-confirmed)
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    # SM strongly tilted
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")
    # Volume confirmation
    if vol_x >= 1.5:
        score += 1
        reasons.append(f"vol_{vol_x:.1f}x")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        # v2 signed move_pct in the data block (negative for SHORT)
        "move_pct": round(move_pct, 3) if direction == "LONG" else -round(move_pct, 3),
        "vol_ratio": round(vol_x, 2),
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
    }
