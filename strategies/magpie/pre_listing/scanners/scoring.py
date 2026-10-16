"""MAGPIE · PRE-LISTING book — pure new-listing/IPOP-discovery + trend math.

A faithful Runtime 3.0 port of the v2 Magpie producer's PRE-LISTING leg
(magpie-producer.py: fetch_ipop_universe + build_thesis_pre_listing +
trend_structure + clamp_leverage). No I/O, no MCP, no clock — scan.py does the
reads, this does the numbers. The math/gates are reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same snapshot;
behaviour-preserving v2 quirks are kept and flagged `# v2-quirk`.

The thesis: trade.xyz pre-IPO perpetuals (IPOPs) carry a structural signature —
the VENUE'S LEVERAGE CAP (listed at <= 5x, lifted on graduation) plus a volume
floor; the v2 funding band (|funding| <= ~1e-7) is opt-in and off, see the
signature block below. Discover them off market_list_instruments, score the
pre-listing trend on CLOSED candles (4h structure sets direction, 1h + Smart-Money
confirm, SM sparse pre-listing -> trend-only fallback), and ride the ramp into
the IPO. The predicate is no longer the v2 producer's verbatim port (1.2.0)."""


def _f(c, primary, alt=None, default=0.0):
    # v2-quirk: candle accessor reads dict keys with a short-alias fallback
    # (low|l, high|h, close|c, volume|v); ported verbatim from the v2 producer.
    val = c.get(primary) if isinstance(c, dict) else None
    if val is None and alt:
        val = c.get(alt) if isinstance(c, dict) else None
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ── THE IPOP SIGNATURE (shared VERBATIM by both books; tests pin the two copies equal) ──
#
# A pre-IPO perpetual is identified by the VENUE'S LEVERAGE CAP: trade.xyz lists one at or under
# ipopMaxLeverageCap (5x) and lifts the cap when it graduates to a standard equity perp. A volume
# floor goes with it — a cap-5 market with no volume is a market that has not traded, not a
# listing. The FUNDING BAND the v2 producer used (|funding| <= ~1e-7, read as the throttled
# pre-listing multiplier) is OPT-IN and off by default: live pre-IPO perps fund at the same ~1e-5
# magnitude as ordinary xyz equities, so a near-zero funding read marks a market that has not
# traded. Measured on the live xyz board, 2026-10-12 (114 names): one name at cap <= 5, one at
# |funding| <= 1e-7, none at both; median |funding| 8.3e-6 — the band-AND-cap predicate matched
# nothing, so neither book could ever see an IPOP. Kept behind ipopFundingBandEnabled for an
# operator who wants it as an extra check.


def ipop_capped(max_leverage, funding_abs, config):
    """The cap half of the signature: listed at or under ipopMaxLeverageCap and, only when
    ipopFundingBandEnabled, |funding| <= ipopFundingBandAbs. Unreadable inputs -> False."""
    try:
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return False
    if lev > int(config.get("ipopMaxLeverageCap", 5)):
        return False
    if config.get("ipopFundingBandEnabled", False):
        try:
            if abs(float(funding_abs)) > float(config.get("ipopFundingBandAbs", 1e-7)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def is_ipop(max_leverage, vol_usd, funding_abs, config):
    """The IPOP signature: ipop_capped AND dayNtlVlm >= ipopMinDayVolumeUsd (a dead zero-volume
    market is not a listing). Unreadable inputs -> False."""
    if not ipop_capped(max_leverage, funding_abs, config):
        return False
    try:
        return float(vol_usd) >= float(config.get("ipopMinDayVolumeUsd", 25000))
    except (TypeError, ValueError):
        return False


def ipop_passes_universe(name, is_delisted, funding_abs, max_leverage, vol_usd, config):
    """Pre-listing-universe membership: a live, non-delisted xyz name carrying the IPOP
    signature AND clearing the BOOK's own trading floor (ipopMinDailyVolUsd, above the
    signature's floor: recognising a listing takes less volume than putting money in it). The
    xyz: prefix + not-delisted checks ensure we only ever consider instruments that ACTUALLY
    EXIST in the live universe."""
    if not isinstance(name, str) or not name.startswith("xyz:") or is_delisted:
        return False
    if not is_ipop(max_leverage, vol_usd, funding_abs, config):
        return False
    return float(vol_usd) >= float(config.get("ipopMinDailyVolUsd", 100000))


HOUR_SECONDS = 3600
FOUR_HOUR_SECONDS = 14400


def _t(c):
    """Candle open, epoch ms, or -1 when unreadable."""
    return _f(c, "t", default=-1.0) if isinstance(c, dict) else -1.0


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first (the Moose/Vulture idiom).

    `t` is the candle OPEN in epoch milliseconds, so a candle has closed once
    t/1000 + candle_seconds <= now. The feed's last row is the bar in progress: its low and high
    are partial, so a structure counted over it (higher lows, lower highs) can flip inside the
    bar. Rows with no readable `t` are dropped; nothing here assumes fixed spacing."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


# ── shared technical helpers (ported verbatim) ──

def trend_structure(candles, lookback=6):
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


def clamp_leverage(desired, cap):
    """Clamp desired leverage to the instrument's venue max (per-signal leverage)."""
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = desired
    if cap <= 0:
        cap = desired
    return max(1, min(int(desired), cap))


# ── PRE-LISTING thesis (4h structure + 1h + Smart-Money), ported verbatim ──

def build_thesis_pre_listing(asset_name, c1h, c4h, sm_dir, sm_tilt, config):
    """Returns a scored thesis dict or None if a gate blocks. `sm_dir`/`sm_tilt`
    are the smart-money lean for this asset (scan.py fetches them; sm_dir is None
    when the board has no row for the name — the normal pre-listing case — and the
    SM component is then NEUTRAL: no gate, no points; the trend must carry the score)."""
    if len(c4h) < 6 or len(c1h) < 6:
        return None
    t4, s4 = trend_structure(c4h)
    t1, _ = trend_structure(c1h)
    if t4 == "NEUTRAL":
        return None
    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_min = float(config.get("smTiltMinPct", 55))
    sm_strong = float(config.get("smStrongTiltPct", 70))
    # No board row for this name (the normal pre-listing case): neutral — trend-only, no SM points.
    if sm_dir is None:
        sm_tilt = 0.0
    elif sm_dir == "NEUTRAL" or sm_dir != direction or sm_tilt < sm_min:
        return None

    score = 3
    reasons = [f"4h_{t4.lower()}_{s4:.0%}"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{t1.lower()}")
    if sm_dir is not None:                   # a board row that agrees — never an absent one
        score += 2
        reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    else:
        reasons.append("sm_no_board_row")
    return {"coin": asset_name, "direction": direction, "score": score, "reasons": reasons,
            "trend4h": t4, "sm_tilt": sm_tilt}
