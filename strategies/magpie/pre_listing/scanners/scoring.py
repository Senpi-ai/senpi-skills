"""MAGPIE · PRE-LISTING book — pure new-listing/IPOP-discovery + trend math.

A faithful Runtime 3.0 port of the v2 Magpie producer's PRE-LISTING leg
(magpie-producer.py: fetch_ipop_universe + build_thesis_pre_listing +
trend_structure + clamp_leverage). No I/O, no MCP, no clock — scan.py does the
reads, this does the numbers. The math/gates are reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same snapshot;
behaviour-preserving v2 quirks are kept and flagged `# v2-quirk`.

The thesis: trade.xyz pre-IPO perpetuals (IPOPs) carry a structural FUNDING
SIGNATURE — very low |funding| (<= ~1e-7, the ~1% throttled multiplier) and a
low leverage cap (<= 5). Discover them off market_list_instruments, score the
pre-listing trend (4h structure sets direction, 1h + Smart-Money confirm, SM
sparse pre-listing -> trend-only fallback), and ride the ramp into the IPO."""


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


# ── new-listing / IPOP DETECTION (universe discovery, ported verbatim) ──
#
# An instrument is an IPOP iff its name starts with "xyz:", it is not delisted,
# |funding| <= ipopFundingMaxAbs, AND max_leverage <= ipopMaxLeverageCap. The
# pre-listing universe additionally requires dayNtlVlm >= ipopMinDailyVolUsd.
# This is the SAME funding signature the graduation book classifies on; when a
# company IPOs the product converts (funding jumps ~100x, the cap lifts) and it
# stops matching. Validating the dynamic universe == applying this predicate to
# the live market_list_instruments read (no name is ever invented).

def is_ipop(funding_abs, max_leverage, ipop_funding_max, ipop_lev_cap):
    """The IPOP funding-signature predicate (shared with the graduation classifier).
    True iff |funding| <= ipop_funding_max AND max_leverage <= ipop_lev_cap."""
    try:
        f = abs(float(funding_abs))
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return False
    return f <= ipop_funding_max and lev <= ipop_lev_cap


def ipop_passes_universe(name, is_delisted, funding_abs, max_leverage, vol_usd, config):
    """Pre-listing-universe membership: a live, non-delisted xyz IPOP clearing the
    min-daily-volume floor. The xyz: prefix + not-delisted checks ensure we only
    ever consider instruments that ACTUALLY EXIST in the live universe."""
    if not isinstance(name, str) or not name.startswith("xyz:") or is_delisted:
        return False
    max_funding = float(config.get("ipopFundingMaxAbs", 1e-7))
    max_lev = int(config.get("ipopMaxLeverageCap", 5))
    min_vol = float(config.get("ipopMinDailyVolUsd", 100000))
    if int(max_leverage if max_leverage is not None else 999) > max_lev:
        return False
    if abs(float(funding_abs)) > max_funding:
        return False
    if float(vol_usd) < min_vol:
        return False
    return True


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
    when leaderboard data is absent — sparse pre-listing -> trend-only fallback)."""
    if len(c4h) < 6 or len(c1h) < 6:
        return None
    t4, s4 = trend_structure(c4h)
    t1, _ = trend_structure(c1h)
    if t4 == "NEUTRAL":
        return None
    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_min = float(config.get("smTiltMinPct", 55))
    sm_strong = float(config.get("smStrongTiltPct", 70))
    # IPOP SM data is sparse pre-listing — fall back to trend-only if absent.
    if sm_dir is None:
        sm_dir, sm_tilt = direction, sm_min
    elif sm_dir == "NEUTRAL" or sm_dir != direction or sm_tilt < sm_min:
        return None

    score = 3
    reasons = [f"4h trend {t4.lower()} ({s4:.0%} of bars)"]
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h trend {t1.lower()} confirms")
    score += 2
    reasons.append(f"smart money aligned at {sm_tilt:.0f}%" if sm_tilt > sm_min else "smart-money data sparse, assumed aligned")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("smart money strongly tilted")
    return {"coin": asset_name, "direction": direction, "score": score, "reasons": reasons,
            "trend4h": t4, "sm_tilt": sm_tilt}
