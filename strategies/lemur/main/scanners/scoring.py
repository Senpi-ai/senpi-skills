"""LEMUR — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 LEMUR producer's IPOP universe filter +
build_thesis 4-component scoring (lemur-producer.py v1.0.1 / SKILL.md v1.0.0).
The math/indexing is reproduced VERBATIM so a fidelity harness can diff this
against the v2 producer on the same market snapshot. Behaviour-preserving quirks
from v2 are kept and flagged `# v2-quirk`.

LEMUR is a Pre-IPO Perpetual (IPOP) trend follower on Hyperliquid XYZ. It
auto-discovers IPOPs from the live xyz: instrument list via a structural funding
signature (1% funding multiplier vs 0.5 standard => ~100x smaller funding rates)
+ a pre-listing leverage cap (<=5x) + a liquidity floor. Today's universe is
[xyz:SPCX] (SpaceX); it auto-expands when trade.xyz lists more IPOPs and
auto-drops a name once it IPOs (funding jumps ~100x out of the signature band).

This module is single-pass, unit-testable on plain candle lists + instrument
dicts. `sm` (smart-money lean) is fetched by the caller (scan.py) and passed in,
so this module stays pure. minScore is applied by the CALLER, not here."""


# v2 defaults (lemur-producer.py / lemur-config.json)
DEFAULT_IPOP_FUNDING_MAX = 1e-7      # abs(funding) <= 1e-7 (1% multiplier => ~100x smaller)
DEFAULT_IPOP_LEV_CAP = 5            # max_leverage <= 5 (pre-listing cap)
DEFAULT_IPOP_MIN_VOL = 100000       # dayNtlVlm >= $100k liquidity floor
DEFAULT_SM_TILT_MIN = 55           # smTiltMinPct
DEFAULT_SM_STRONG = 70             # smStrongTiltPct
MAX_LEVERAGE = 5                   # hardcoded venue ceiling for IPOPs
SCORE_NORM_DIVISOR = 9.0           # v2 push_signal normalized score = score/9 (informational)


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {high|h, low|l} OR list [t,o,h,l,c,v]) ──
# v2 read dicts via _f(c, "low", "l"); the list branch is defensive and never
# fires on dict candles, so it does not change v2 behaviour.

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


# ── IPOP universe filter (ported verbatim from v2 fetch_ipop_universe) ──

def is_ipop(inst, max_funding=DEFAULT_IPOP_FUNDING_MAX,
            max_lev=DEFAULT_IPOP_LEV_CAP, min_vol=DEFAULT_IPOP_MIN_VOL):
    """True iff `inst` matches the structural IPOP signature (verbatim v2):
        name.startswith("xyz:") AND not is_delisted
        AND max_leverage <= max_lev (pre-listing cap)
        AND abs(funding) <= max_funding (1% multiplier => ~100x smaller)
        AND dayNtlVlm >= min_vol (liquidity floor)."""
    if not isinstance(inst, dict):
        return False
    name = inst.get("name", "")
    if not name.startswith("xyz:"):
        return False
    if inst.get("is_delisted", False):
        return False
    # v2 cast: int(inst.get("max_leverage", 999)) — a missing field => 999 => excluded
    try:
        if int(inst.get("max_leverage", 999)) > max_lev:
            return False
    except (TypeError, ValueError):
        return False
    ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
    funding_abs = abs(_f(ctx.get("funding", 0)))
    if funding_abs > max_funding:
        return False
    vol_usd = _f(ctx.get("dayNtlVlm", 0))
    if vol_usd < min_vol:
        return False
    return True


def filter_ipop_universe(instruments, max_funding=DEFAULT_IPOP_FUNDING_MAX,
                         max_lev=DEFAULT_IPOP_LEV_CAP, min_vol=DEFAULT_IPOP_MIN_VOL):
    """Filter a raw instruments list to IPOP-signature dicts (verbatim v2 shape):
    [{name, max_leverage, funding, vol_usd}]."""
    universe = []
    if not isinstance(instruments, list):
        return universe
    for inst in instruments:
        if not is_ipop(inst, max_funding, max_lev, min_vol):
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        universe.append({
            "name": inst.get("name", ""),
            "max_leverage": int(_f(inst.get("max_leverage", 5))),
            "funding": abs(_f(ctx.get("funding", 0))),
            "vol_usd": _f(ctx.get("dayNtlVlm", 0)),
        })
    return universe


# ── trend structure (ported verbatim from v2 trend_structure) ──

def trend_structure(candles, lookback=6):
    """Higher lows => BULLISH, lower highs => BEARISH. Verbatim from v2.

    v2-quirk: the BULLISH/BEARISH gate is `>= total * 0.6` where total =
    lookback - 1, and strict (>) comparison for the higher-lows / lower-highs
    count. Strength returned is higher_lows/total (BULLISH) or lower_highs/total
    (BEARISH). Reproduced exactly."""
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


# ── the thesis (4h-trend gate + SM gate + 4-component score), verbatim ──

def build_thesis(coin, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when:
      - insufficient candle history (len(c4h) < 6 or len(c1h) < 6), OR
      - 4h trend is NEUTRAL (hard gate), OR
      - SM direction is available AND (NEUTRAL OR disagrees with 4h trend OR
        tilt < smTiltMinPct).
    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, tilt_pct) from the caller; when SM
    data is unavailable the CALLER passes (None, 0.0) and this function applies
    the v2 fallback: assume aligned at the minimum tilt (sm_data_sparse).

    Score components (max ~9; verbatim v2):
      +3  4h trend aligned (always added once 4h non-neutral gate passes)
      +2  1h trend confirms the 4h direction
      +2  SM aligned (or sm_data_sparse_assumed_aligned)
      +1  SM strongly tilted (tilt >= smStrongTiltPct)"""
    sm_min = float(inputs.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(inputs.get("smStrongTiltPct", DEFAULT_SM_STRONG))

    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    if t4 == "NEUTRAL":
        return None

    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    # Note: IPOP SM data may be sparse pre-listing — fall back to 4h-trend-only.
    if sm_dir is None:
        sm_dir = direction      # fallback: assume aligned (SM data not available)
        sm_tilt = sm_min        # minimum tilt for scoring purposes
    elif sm_dir == "NEUTRAL" or sm_dir != direction:
        return None
    elif sm_tilt < sm_min:
        return None

    score = 0
    reasons = []
    score += 3
    reasons.append(f"4h trend {t4.lower()} ({s4:.0%} of bars)")
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h trend {t1.lower()} confirms")
    score += 2
    # v2-quirk: the "sm_aligned" reason uses strict > DEFAULT_SM_TILT_MIN (55),
    # NOT the configurable sm_min — reproduced exactly.
    reasons.append(
        f"smart money aligned at {sm_tilt:.0f}%" if sm_tilt > DEFAULT_SM_TILT_MIN
        else "smart-money data sparse, assumed aligned"
    )
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("smart money strongly tilted this way")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": t4,
        "trend_4h_strength": round(s4, 4),
        "trend_1h": t1,
        "sm_direction": sm_dir,
        "sm_tilt_pct": round(_f(sm_tilt), 2),
    }


def leverage_for(config_leverage, instrument_max_leverage):
    """Auto-cap config leverage to the IPOP's own max_leverage and the venue
    ceiling. Verbatim v2: min(config_leverage, max_leverage_cap, MAX_LEVERAGE)."""
    return min(int(config_leverage), int(instrument_max_leverage), MAX_LEVERAGE)
