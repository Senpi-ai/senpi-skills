"""STINGRAY — pure thesis math (no I/O, no MCP, no clock).

Cross-asset smart-money ROTATION scorer. Where per-asset SM agents (bison, cheetah)
ask "is smart money long this coin?", Stingray ranks the WHOLE board by net
smart-money conviction and trades the rotation: LONG the assets SM is crowding into,
SHORT the ones SM is fleeing. The unit of analysis is the board, not one coin.

This module is pure: it consumes already-normalized per-asset SM rows (the caller,
scan.py, fetches + normalizes `leaderboard_get_markets`, copying bison's
`_get_sm_direction` per-asset long%/short% accumulation and cheetah's
`_fetch_sm_markets` field extraction) and returns the conviction-ranked board.
Unit-testable on plain dicts; no clock, no network. The trend-confirmation gate
(don't fight price) lives in scan.py because it needs a per-asset MCP read.

Net-tilt math is bison's formula, VERBATIM:
  long_ratio = long_pct / (long_pct + short_pct) * 100   (50 = balanced)
  net_tilt   = long_ratio - 50                            (+ = SM net long)
LONG side requires long_ratio >= minTiltLong (default 58); SHORT side requires
long_ratio <= minTiltShort (default 42) — the same 58/42 thresholds bison uses.
Conviction = |net_tilt| * weight, where weight folds in notional/volume + trader
breadth so a strong tilt on a deep, widely-held market outranks a strong tilt on a
thin one (the thin-coin-spike failure mode bison's whitelist guards against)."""

import math


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def net_tilt(long_pct, short_pct):
    """Bison's net-tilt formula. Returns (long_ratio, net_tilt).
    long_ratio in [0,100] (50 = balanced); net_tilt = long_ratio - 50 in [-50, +50]."""
    total = safe_float(long_pct) + safe_float(short_pct)
    if total <= 0:
        return 50.0, 0.0
    long_ratio = (safe_float(long_pct) / total) * 100.0
    return long_ratio, long_ratio - 50.0


def conviction_weight(volume, traders, vol_floor=1.0):
    """Notional/breadth weight folded into conviction so a strong tilt on a deep,
    widely-held market outranks the same tilt on a thin one.

    weight = log10(1 + volume/vol_floor) * (1 + log10(1 + traders))

    Both terms are log-damped so one whale-sized market can't swamp the ranking;
    breadth (trader_count) is a multiplicative bonus, not an additive one, so a
    market with zero reported breadth still gets its full notional weight (×1).
    When the board carries no volume/breadth fields at all the caller passes
    volume=0, traders=0 -> weight collapses to a constant (log10(1)=0 -> ... -> 0),
    so the caller falls back to pure |net_tilt| ranking (handled in scan.py)."""
    vol = max(0.0, safe_float(volume))
    trd = max(0.0, safe_float(traders))
    vf = vol_floor if vol_floor and vol_floor > 0 else 1.0
    vol_term = math.log10(1.0 + vol / vf)
    breadth_term = 1.0 + math.log10(1.0 + trd)
    return vol_term * breadth_term


def rank_board(rows, min_tilt_long=58.0, min_tilt_short=42.0, vol_floor=1.0):
    """Rank the whole SM board by conviction and split into LONG / SHORT sides.

    rows: list of normalized per-asset dicts (from scan._board), each:
      { token, dex, long_pct, short_pct, volume, traders }
    Returns (longs, shorts), each a list of conviction dicts sorted DESC by
    conviction:
      { token, dex, direction, long_ratio, net_tilt, weight, conviction,
        volume, traders }

    An asset is a LONG candidate iff long_ratio >= min_tilt_long, a SHORT
    candidate iff long_ratio <= min_tilt_short. Assets between the thresholds are
    rotation-neutral and dropped. `use_weight` (whether any row carried notional)
    is decided by the caller; here weight is always computed and conviction is
    |net_tilt| * max(weight, 0) — if every weight is ~0 the caller re-ranks on
    |net_tilt| alone (see scan.rank_board fallback)."""
    longs, shorts = [], []
    for r in rows:
        long_ratio, tilt = net_tilt(r.get("long_pct", 0), r.get("short_pct", 0))
        weight = conviction_weight(r.get("volume", 0), r.get("traders", 0), vol_floor)
        base = {
            "token": r.get("token", ""),
            "dex": r.get("dex", ""),
            "long_ratio": round(long_ratio, 2),
            "net_tilt": round(tilt, 2),
            "weight": round(weight, 4),
            "volume": safe_float(r.get("volume", 0)),
            "traders": safe_float(r.get("traders", 0)),
        }
        if long_ratio >= min_tilt_long:
            base = dict(base, direction="LONG",
                        conviction=round(abs(tilt) * max(weight, 0.0), 4))
            longs.append(base)
        elif long_ratio <= min_tilt_short:
            base = dict(base, direction="SHORT",
                        conviction=round(abs(tilt) * max(weight, 0.0), 4))
            shorts.append(base)
    return longs, shorts


def board_has_weight(rows):
    """True iff at least one board row carries a positive notional/volume field.
    When False the caller ranks on |net_tilt| alone (conviction weight collapses to
    a constant when no volume/breadth is present)."""
    for r in rows:
        if safe_float(r.get("volume", 0)) > 0 or safe_float(r.get("traders", 0)) > 0:
            return True
    return False


def margin_pct_for(net_tilt_abs, base_pct, max_pct=None):
    """Conviction-scaled margin PERCENT. Stronger net tilt -> bigger size, tiered
    on |net_tilt| (the rotation conviction). Mirrors bison's score-tiered margin
    shape (1.0 / 1.25 / 1.5) but keyed on |net_tilt| instead of a composite score:
      |net_tilt| >= 20 (long_ratio >= 70 / <= 30) -> base * 1.5
      |net_tilt| >= 12 (long_ratio >= 62 / <= 38) -> base * 1.25
      else                                         -> base
    Returns a PERCENT in (0,100]; the runtime sizes (marginPct/100)*withdrawable.
    Clamped to `max_pct` when supplied so the tier multiplier can't exceed the
    per-position cap."""
    if net_tilt_abs >= 20:
        pct = base_pct * 1.5
    elif net_tilt_abs >= 12:
        pct = base_pct * 1.25
    else:
        pct = base_pct
    if max_pct is not None and max_pct > 0:
        pct = min(pct, max_pct)
    return pct
