"""MAGPIE · GRADUATION book — pure conversion-detection + momentum math.

A faithful Runtime 3.0 port of the v2 Magpie producer's GRADUATION leg
(magpie-producer.py: classify_instrument + detect_conversion + momentum_pct +
conversion_direction + volume_trend + build_thesis_graduation + clamp_leverage).
No I/O, no MCP, no clock — scan.py does the reads + conversion-window state, this
does the numbers. Math/gates reproduced VERBATIM for fidelity; v2 quirks kept and
flagged `# v2-quirk`.

The thesis: classify every live xyz instrument IPOP-vs-STANDARD each tick by the
VENUE'S LEVERAGE CAP plus a volume floor (the v2 funding band is opt-in and off,
see the signature block below), compare each name's cap against the prior-tick
cache to detect the CONVERSION (the cap lifts above the IPOP cap on a name trading
over the floor — the throttle comes off), stamp it into a conversion-eligibility
window, and within that window ride the explosive post-conversion price-discovery
momentum — the SpaceX day-1 pattern. The predicate is no longer the v2 producer's
verbatim port (1.2.0)."""


def _f(c, primary, alt=None, default=0.0):
    # v2-quirk: candle accessor reads dict keys with a short-alias fallback
    # (close|c, volume|v); ported verbatim from the v2 producer.
    val = c.get(primary) if isinstance(c, dict) else None
    if val is None and alt:
        val = c.get(alt) if isinstance(c, dict) else None
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


HOUR_SECONDS = 3600


def _t(c):
    """Candle open, epoch ms, or -1 when unreadable."""
    return _f(c, "t", default=-1.0) if isinstance(c, dict) else -1.0


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first (the Moose/Vulture idiom).

    `t` is the candle OPEN in epoch milliseconds, so a candle has closed once
    t/1000 + candle_seconds <= now. The feed's last row is the bar in progress: a momentum read
    off it is the live price against a six-bar-old close, so it changes every tick and a thesis
    could appear and vanish inside one bar. Rows with no readable `t` are dropped; nothing here
    assumes fixed spacing."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


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


def classify_instrument(max_leverage, vol_usd, funding_abs, config):
    """IPOP if the name carries the signature, else STANDARD."""
    return "IPOP" if is_ipop(max_leverage, vol_usd, funding_abs, config) else "STANDARD"


def detect_conversion(prev, curr_max_leverage, curr_vol_usd, config):
    """A conversion = the venue lifting a pre-IPO name's cap ABOVE ipopMaxLeverageCap: the prior
    tick saw the name at or under the cap (and inside the funding band, when that is on) and
    this tick sees it above the cap, trading at least ipopMinDayVolumeUsd a day. `prev` is the
    cached prior record — {class, max_leverage, funding} from 1.2.0 on, or the bare class string
    an older state file carries (a string is read as its class). The prior test is the CAP, not
    the class: a pre-IPO name can sit under the volume floor the tick before it lists (xyz:OURA
    traded $22k/day at cap 5 on 2026-10-12, under the $25k floor) and must still flip when the
    cap lifts. Unknown prior -> never a flip; a quiet day under the floor is not a flip; funding
    moving on its own is not a flip."""
    if isinstance(prev, dict) and prev.get("max_leverage") is not None:
        prev_capped = ipop_capped(prev.get("max_leverage"), prev.get("funding"), config)
    elif isinstance(prev, dict):
        prev_capped = prev.get("class") == "IPOP"
    else:
        prev_capped = prev == "IPOP"
    if not prev_capped:
        return False
    try:
        lifted = int(curr_max_leverage) > int(config.get("ipopMaxLeverageCap", 5))
        live = float(curr_vol_usd) >= float(config.get("ipopMinDayVolumeUsd", 25000))
    except (TypeError, ValueError):
        return False
    return lifted and live


# ── momentum helpers (ported verbatim) ──

def momentum_pct(closes, lookback):
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def conversion_direction(momentum, min_momentum_pct):
    if momentum is None or abs(momentum) < min_momentum_pct:
        return None
    return "LONG" if momentum > 0 else "SHORT"


def volume_trend(candles, lookback=6):
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


def clamp_leverage(desired, cap):
    """Clamp desired leverage to the instrument's venue max (per-signal leverage)."""
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = desired
    if cap <= 0:
        cap = desired
    return max(1, min(int(desired), cap))


# ── GRADUATION thesis (post-conversion momentum + SM + volume), ported verbatim ──

def build_thesis_graduation(name, c1h, max_leverage_cap, sm_dir, sm_tilt, config):
    """Returns a scored thesis dict or None if a gate blocks. `c1h` are 1h candles;
    `sm_dir`/`sm_tilt` are the smart-money lean (scan.py fetches them). The caller
    only passes names that are inside the conversion-eligibility window."""
    if len(c1h) < 8:
        return None
    closes = [_f(c, "close", "c") for c in c1h]
    lookback = int(config.get("momentumLookbackBars", 6))
    min_mom = float(config.get("minMomentumPct", 3.0))
    strong_mom = float(config.get("strongMomentumPct", 8.0))

    mom = momentum_pct(closes, lookback)
    direction = conversion_direction(mom, min_mom)
    if direction is None:
        return None

    sm_min = float(config.get("smTiltMinPct", 55))
    sm_strong = float(config.get("smStrongTiltPct", 70))
    vt = volume_trend(c1h)

    score = 3
    reasons = ["converted_ipop_to_equity", f"mom_{mom:+.1f}%"]
    if abs(mom) >= strong_mom:
        score += 2
        reasons.append(f"mom_strong_{mom:+.1f}%")
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vt > 15:
        score += 1
        reasons.append(f"vol_rising_{vt:+.0f}%")
    return {"coin": name, "direction": direction, "score": score, "reasons": reasons,
            "momentum_pct": round(mom, 2), "max_leverage_cap": max_leverage_cap}
