"""CUB — pure thesis math (no I/O, no MCP, no clock). Shared verbatim by all three books
(long "haves", short "have-nots", preipo pre-IPO ramp); the direction and the universe builder
differ per book but the scoring is one function. A faithful Runtime 3.0 port of the v2
cub-producer.py scoring (cub-producer.py v1.0.0) — the gates + point weights are copied EXACTLY
(marked `# v2-quirk` where the v2 behaviour is load-bearing and must not be redesigned), EXCEPT the
IPOP funding gate, which is CORRECTED: the v2 `|funding| <= 1e-7` "throttled pre-listing" signature
matched no live IPOP (pre-IPO perps fund like ordinary equities), so it is now opt-in and OFF by
default — see is_ipop. A fresh-listing 1h starter path is ALSO added for the preipo leg (off unless
inputs['freshListing']['enabled']) so a just-listed IPOP can be entered ~6h after launch at reduced
size instead of waiting ~24h for 4h history — see score_thematic. Unit-testable on plain candle lists
+ instrument dicts.

KEY DISTINCTION FROM COUGAR (do not "simplify" toward cougar): in Cub the hard gate is
ABSOLUTE TREND (long a have only while it actually trends up; short a have-not only while it
actually rolls over). Cross-sectional excess return vs the leg-universe mean is a SCORE
MODIFIER / TIEBREAKER, NOT a disqualifier — a genuinely-trending winner is NOT benched on a day
its peers ran harder. Cougar instead gates on excess (long requires excess>=0); Cub does not.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score. The
# 3.0 scaffold owns the wire envelope, so we keep the raw integer score on data{} and only use
# NORM_DIV if a caller wants the v2-equivalent normalised score.
NORM_DIV = 9.0

# v2 preipo defaults (cub-producer.py _DEFAULTS["preipo"] / cub-preipo-config.json)
DEFAULT_IPOP_FUNDING_MAX = 0.0     # 0 = OFF. Funding does NOT identify an IPOP: live pre-IPO perps
                                   # fund at the same ~1e-5..1e-4 magnitude as ordinary xyz equities
                                   # (e.g. UNITREE -3.16e-5 sits inside the NVDA/MU/TSM pack). The only
                                   # names with |funding| <= 1e-7 are DEAD zero-volume markets, which
                                   # fail the liquidity floor anyway — so the v2 1e-7 gate was empty-set
                                   # on EVERY tick and no live IPOP ever passed it. Leverage cap +
                                   # liquidity floor + absolute-trend discriminate; funding is opt-in.
DEFAULT_IPOP_LEV_CAP = 5           # IPOP leverage signature (fresh pre-IPO perps launch capped low)


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles.

def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])                                  # [t,o,h,l,c,v] -> close
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    return 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return _f(c[2])
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    return 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return _f(c[3])
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    return 0.0


# ── indicators (ported verbatim from v2 cub-producer.py) ──

def trend_structure(candles, lookback=6):
    """Higher-lows = BULLISH, lower-highs = BEARISH over the last `lookback` candles.
    Verbatim v2 trend_structure (>= total*0.6 gate, total = lookback-1)."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    """Simple-average RSI over the last `period` deltas. Verbatim v2 calc_rsi."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ── bare-ticker helper (sizing-weight + dedup lookups: 'xyz:NVDA' -> 'NVDA') ──

def bare(asset):
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


# ── the thematic thesis: ABSOLUTE trend is the gate, excess is a tiebreaker ──

def score_thematic(asset, candles_1h, candles_4h, excess, own24h, direction, inputs):
    """Port of v2 score_thematic. Returns a thesis dict (with `score`) or None.

    `direction` is "LONG" (haves + preipo discovered IPOPs) or "SHORT" (have-nots).
    `excess` = the asset's 24h return minus the leg-universe mean (cross-sectional).
    `own24h`  = the asset's own 24h return (sign is an absolute-momentum component).

    None is returned ONLY when: insufficient candle history for the chosen confirmation basis,
    OR the ABSOLUTE-trend hard gate fails (long: primary-TF BEARISH; short: 4h BULLISH).
    minScore is applied by the CALLER (scan.py), not here.

    FRESH-LISTING FAST PATH (LONG only; OFF unless inputs['freshListing']['enabled']): a just-listed
    IPOP has no 4h history for ~24h. When enabled and a name has >= minCandles1h 1h candles but not yet
    the full 4h history, confirm the ramp on the 1h structure instead and return a REDUCED starter size
    (size_factor = starterSizeFactor). The normal 4h engine takes over — at full size — once 24h of 4h
    history exists. long/short never set freshListing, so their behaviour is byte-for-byte unchanged.

    v2-quirk: excess is a SCORE MODIFIER (bonus only), NOT a gate — never disqualifies a
    genuinely-trending name. Do NOT add cougar's `excess < 0 -> return None` here.
    """
    # Confirmation basis: 4h normally (full size); 1h on the fresh-listing starter path (reduced size).
    fresh = inputs.get("freshListing") or {}
    fresh_on = bool(fresh.get("enabled")) and direction == "LONG"
    fresh_min_1h = int(fresh.get("minCandles1h", 6))
    if len(candles_1h) >= 8 and len(candles_4h) >= 6:
        basis, size_factor = "4h", 1.0
    elif fresh_on and len(candles_1h) >= fresh_min_1h:
        basis, size_factor = "1h", float(fresh.get("starterSizeFactor", 0.5))
    else:
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    # primary structure = the confirmation-basis TF (4h normally; 1h on the fresh-listing starter path)
    prim, s_prim = (trend4, s4) if basis == "4h" else (trend1, s1)
    rsi = calc_rsi(closes1)
    rs_thresh = float(inputs.get("rsThresholdPct", 3.0))

    score = 0
    reasons = []

    if direction == "LONG":     # haves (curated) + preipo (discovered IPOPs)
        # ── HARD GATE: never long a confirmed downtrend (on the confirmation-basis TF) ──
        if prim == "BEARISH":
            return None
        if prim == "BULLISH":
            score += 3
            reasons.append(f"{basis}_bullish_{s_prim:.0%}")
        else:
            score += 1
            reasons.append(f"{basis}_neutral")
        if basis == "4h":               # 1h is the SECONDARY confirmation only when 4h is the primary
            if trend1 == "BULLISH":
                score += 1
                reasons.append(f"1h_bullish_{s1:.0%}")
            elif trend1 == "BEARISH":
                score -= 1
                reasons.append("1h_bearish")
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"abs_up_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"rs_lag_{excess:+.1f}%")  # noted, not penalized
        rsi_ob = float(inputs.get("rsiOverbought", 82))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
        # fresh-listing starter: RSI-14 needs ~15h of 1h history (inert on a day-1 name), so guard the
        # blow-off with return-since-launch instead — never CHASE a fresh IPOP that already ran vertical.
        if basis == "1h" and own > float(fresh.get("maxRunPct", 25.0)):
            score -= 2
            reasons.append(f"fresh_chase_{own:+.1f}%")
    else:  # SHORT — have-nots
        # ── HARD GATE: never short a confirmed uptrend ──
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h_bearish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h_bearish_{s1:.0%}")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")
        if own <= 0:
            score += 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_up_{own:+.1f}%")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"rs_lead_{excess:+.1f}%")
        rsi_os = float(inputs.get("rsiOversold", 18))
        if rsi < rsi_os:
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "trend1h": trend1,
        "excess": excess,
        "own24h": own,
        "size_factor": size_factor,   # 1.0 normal (4h-confirmed) ; starterSizeFactor on the 1h fresh path
        "basis": basis,               # "4h" (full engine) or "1h" (fresh-listing starter)
    }


# ── sizing weight + leverage clamp (ported verbatim from v2) ──

def sizing_weight(asset, weights):
    """Per-group conviction multiplier (HYPE large, SOL modest, SP500 core). Keyed by bare
    ticker; falls back to '_default'. Clamped to [0.1, 3.0]. Verbatim v2 sizing_weight()."""
    if not isinstance(weights, dict):
        weights = {"_default": 1.0}
    try:
        w = float(weights.get(bare(asset), weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: xyz equities + IPOPs
    cap LOW at the venue — over-leveraging is a venue reject, so this clamp is load-bearing.
    Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


# ── IPOP discovery filter (preipo leg only) — funding gate CORRECTED vs v2 (see is_ipop + the
#    DEFAULT_IPOP_FUNDING_MAX note): the v2 `|funding| <= 1e-7` "throttled pre-listing" signature
#    matched NO live IPOP, so it is now opt-in/off; the leverage cap + liquidity floor identify a
#    fresh IPOP, and the absolute-trend gate (score_thematic) confirms the ramp. ──

def is_ipop(name, meta, max_funding=DEFAULT_IPOP_FUNDING_MAX, lev_cap=DEFAULT_IPOP_LEV_CAP,
            min_day_vol=0.0):
    """True iff a live xyz: instrument matches the structural IPOP signature:
        name.startswith("xyz:")
        AND 0 < venue max_leverage <= lev_cap (fresh pre-IPO perps launch leverage-capped low)
        AND 24h notional volume >= min_day_vol (budget-relative liquidity floor)
        AND (opt-in) abs(funding) <= max_funding — OFF by default (max_funding <= 0); funding does
            NOT discriminate IPOPs from ordinary equities, see the DEFAULT_IPOP_FUNDING_MAX note."""
    if not isinstance(name, str) or not name.lower().startswith("xyz:"):
        return False
    if not isinstance(meta, dict):
        return False
    try:
        lev = int(meta.get("max_leverage"))
    except (TypeError, ValueError):
        return False
    if lev <= 0 or lev > lev_cap:               # IPOP leverage signature (the real discriminator)
        return False
    if day_vol(meta) < min_day_vol:             # budget-relative liquidity floor
        return False
    if max_funding and max_funding > 0:         # OPT-IN funding band — OFF by default (see note above)
        ctx = meta.get("ctx", {}) if isinstance(meta.get("ctx"), dict) else {}
        try:
            funding_abs = abs(_f(ctx.get("funding", 0)))
        except (TypeError, ValueError):
            return False
        if funding_abs > max_funding:
            return False
    return True


# ── instrument-board accessors (ported verbatim from v2) ──

def day_vol(meta):
    """24h notional volume from an instrument's context. Verbatim v2 day_vol()."""
    ctx = (meta.get("ctx", {}) if isinstance(meta, dict) else {}) or {}
    try:
        return _f(ctx.get("dayNtlVlm", 0))
    except (TypeError, ValueError):
        return 0.0


def ret_24h(meta):
    """24h % return from markPx vs prevDayPx. None when unavailable. Verbatim v2 ret_24h()."""
    ctx = (meta.get("ctx", {}) if isinstance(meta, dict) else {}) or {}
    try:
        mark = _f(ctx.get("markPx", 0))
        prev = _f(ctx.get("prevDayPx", 0))
    except (TypeError, ValueError):
        return None
    if prev <= 0 or mark <= 0:
        return None
    return (mark - prev) / prev * 100.0
