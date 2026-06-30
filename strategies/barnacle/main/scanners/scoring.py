"""BARNACLE — pure thesis math (no I/O, no MCP, no clock).

Index-inclusion / passive-flow front-runner. NOT a port — this is a net-new
Runtime 3.0 strategy. The math is original but the candle-accessor + trend +
volume helpers follow the fleet gold-template idiom (bison/bobcat scoring.py) so a
fidelity harness can diff structurally.

THESIS. When a stock is announced for ADD to a major index (S&P 500 / Nasdaq-100 /
Russell), index funds are FORCED to buy it by the rebalance effective date — a
predictable passive-buying wave that lifts price INTO the date. Barnacle rides that
anticipatory flow from detection up to ~the effective date and exits BEFORE the
rebalance (the scan stops emitting once inside the exit buffer; the DSL/exit owns
the close). DELETE events mirror as SHORTS (forced selling into the date).

Every function here is pure and unit-testable on plain candle lists + plain event
dicts. The scan layer (scan.py) supplies the wall clock, the account/held read,
the per-asset candle read, and the ctx.state dedup. Direction comes from the EVENT
side (add->LONG, delete->SHORT), NOT from price — the confirmation signature only
gates WHETHER to ride and the proximity scaler only sizes it.
"""

import sys


# ── numeric + candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──

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


def _high(c):
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[3])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (fleet-idiom: same trend_structure / volume_trend as bison/bobcat) ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Fleet-standard structure read
    (strict > counting, >= total*0.6 gate; total = lookback-1). Strength is the
    matching count / total; NEUTRAL -> 0.0."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Fleet-standard."""
    if len(candles) < lookback + 2:
        return 0.0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier == 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ── event parsing ──

def parse_effective_epoch(value):
    """Coerce an event's `effectiveDate` to an epoch (seconds). Accepts:
      - an ISO date "YYYY-MM-DD" (interpreted as 00:00:00 UTC of that day, the
        rebalance effective open — see THESIS: forced buying completes by the
        open of the effective date),
      - an ISO datetime "YYYY-MM-DDTHH:MM:SS[Z]",
      - an int/float/epoch-string (seconds since epoch).
    Returns a float epoch, or None if it cannot be parsed (caller skips the event).
    Pure: no wall clock is read here."""
    if value is None:
        return None
    # numeric epoch (int/float, or a string of digits)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # bare epoch string
        try:
            if s.replace(".", "", 1).isdigit():
                return float(s)
        except (TypeError, ValueError):
            pass
        # ISO date / datetime -> UTC epoch
        from datetime import datetime, timezone
        iso = s.replace("Z", "+00:00")
        for fmt_try in (None,):  # use fromisoformat once; fall through on failure
            try:
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                break
        # last resort: plain date
        try:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def normalize_event(ev):
    """Validate + normalize one config event dict. Returns a normalized dict
      {asset, effectiveEpoch, side ('add'|'delete'), index} or None if malformed.
    A malformed event (missing asset, unparseable date, unknown side) -> None so
    the caller can skip-with-note. Pure."""
    if not isinstance(ev, dict):
        return None
    asset = ev.get("asset")
    if not asset or not isinstance(asset, str):
        return None
    side = str(ev.get("side", "add")).strip().lower()
    if side not in ("add", "delete"):
        return None
    eff = parse_effective_epoch(ev.get("effectiveDate"))
    if eff is None:
        return None
    return {
        "asset": asset.strip(),
        "effectiveEpoch": eff,
        "side": side,
        "index": str(ev.get("index", "")).strip(),
    }


def direction_for_side(side):
    """add -> LONG (forced buying), delete -> SHORT (forced selling)."""
    return "LONG" if side == "add" else "SHORT"


# ── proximity-scaled margin (closer to the effective date = larger conviction) ──

def proximity_margin_pct(hours_to_effective, exit_buffer_hours, base_pct, max_pct,
                         window_hours):
    """Scale margin PERCENT UP by proximity to the effective date.

    At the START of the tradeable window (now == effectiveDate - window_hours) the
    forced-flow thesis is youngest -> size at `base_pct`. As `now` approaches the
    exit buffer (now -> effectiveDate - exit_buffer_hours) the passive wave is most
    imminent -> size scales linearly toward `max_pct`. Clamped to [base, max].

    `hours_to_effective` is (effectiveEpoch - now)/3600 (POSITIVE before the date).
    The tradeable proximity band runs from window_hours down to exit_buffer_hours.
    Returns a PERCENT in (0, 100]. Pure."""
    base_pct = float(base_pct)
    max_pct = float(max_pct)
    if max_pct < base_pct:
        max_pct = base_pct
    span = float(window_hours) - float(exit_buffer_hours)   # width of the ramp band
    if span <= 0:
        return max_pct
    # progress 0.0 at window start -> 1.0 at the exit buffer edge
    elapsed = float(window_hours) - float(hours_to_effective)
    progress = elapsed / span
    if progress < 0.0:
        progress = 0.0
    if progress > 1.0:
        progress = 1.0
    return base_pct + (max_pct - base_pct) * progress


def clamp_leverage(requested, lo=1, hi=5, venue_max=None):
    """Clamp leverage to [lo, hi], then to the instrument's venue max if given.
    Mirrors the fleet leverage-clamp idiom. Pure."""
    try:
        lev = int(requested)
    except (TypeError, ValueError):
        lev = lo
    lev = max(lo, min(lev, hi))
    if venue_max is not None:
        try:
            vm = int(venue_max)
            if vm > 0:
                lev = min(lev, vm)
        except (TypeError, ValueError):
            pass
    return lev


# ── confirmation signature: does passive-anticipation flow actually show up? ──

def anticipation_confirms(direction, candles_4h, candles_1h, min_vol_trend):
    """Confirm the passive-anticipation SIGNATURE before riding an event.

    The forced-flow thesis is only worth riding if the market is already moving the
    expected way INTO the date: a 4h trend in the event's direction AND rising 4h
    volume (passive desks accumulating). 1h is a softer confirm used for the reason
    string only. Returns (ok: bool, reasons: list[str], detail: dict). Pure.

    For an ADD (LONG) we want BULLISH 4h structure + rising volume; for a DELETE
    (SHORT) we want BEARISH 4h structure + rising volume (distribution)."""
    reasons = []
    detail = {}
    if len(candles_4h) < 6:
        return False, ["insufficient_4h_history"], detail

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h) if len(candles_1h) >= 6 else ("NEUTRAL", 0.0)
    vt4 = volume_trend(candles_4h)
    detail = {"trend4h": t4, "trend4hStrength": round(s4, 4),
              "trend1h": t1, "volTrend4hPct": round(vt4, 2)}

    want = "BULLISH" if direction == "LONG" else "BEARISH"
    if t4 != want:
        reasons.append(f"4h_not_{want.lower()}_{t4.lower()}")
        return False, reasons, detail
    reasons.append(f"4h_{t4.lower()}_{s4:.0%}")

    if vt4 < float(min_vol_trend):
        reasons.append(f"vol_flat_{vt4:+.0f}%")
        return False, reasons, detail
    reasons.append(f"vol_rising_{vt4:+.0f}%")

    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        reasons.append(f"1h_confirms_{t1.lower()}")

    return True, reasons, detail


def _note(msg):
    """One-line stderr note helper (kept here so scan stays terse)."""
    print(f"[barnacle.scoring] {msg}", file=sys.stderr)
