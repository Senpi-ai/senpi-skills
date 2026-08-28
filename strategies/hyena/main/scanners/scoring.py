"""HYENA — pure thesis math (no I/O, no MCP, no clock).

Hyena is a NET-NEW Runtime 3.0 strategy: the fleet's dedicated SHORT-biased
crypto/crypto-proxy agent — a hedge/defense sleeve. It is SHORT_ONLY by design.
It NEVER goes long; when risk-off conditions do not confirm it emits nothing and
idles (WAITING).

This module is pure and unit-testable on plain dicts/lists. All side-data the
caller resolves via MCP (funding regime, per-asset smart-money lean, 4h candles,
per-asset funding rate) is passed in, so scoring.py stays I/O-free.

THESIS — Crypto risk-off SHORT (a hedge that pairs with long crypto exposure):
  Short the crypto block ONLY when risk-off confirms on three independent axes:
    1. MASTER GATE — market-wide funding regime is LONG_CROWDED (crowded longs =
       squeeze fuel for the short). Set/enforced by the caller (scan.py); if the
       regime is NEUTRAL or SHORT_CROWDED, scan.py emits nothing this tick.
       (A per-asset crowded-long funding rate can stand in when the market-wide
       regime read is unavailable — see scan.py's regime fallback.)
    2. Smart money is net-SHORT the name (rotating OUT of crypto).
    3. Price is confirming DOWN — a 4h downtrend (lower-highs structure).
  All three must agree for a name to be SHORT-eligible. There is no LONG branch.

Parsing shapes are COPIED from the Dog gold template (market_get_funding_regime
+ leaderboard_get_markets) — they are not invented. The smart-money direction
band (long_ratio > 58 -> LONG, < 42 -> SHORT) follows the Bison/Dog leaderboard
convention; Hyena only ever acts on the SHORT side of it.
"""


# Default crypto + crypto-proxy universe (validated live on HL meta 2026-06-30:
# BTC/ETH/SOL/HYPE on the main DEX; xyz:MSTR/xyz:COIN/xyz:CRCL on the XYZ DEX).
UNIVERSE = ["BTC", "ETH", "SOL", "HYPE", "xyz:MSTR", "xyz:COIN", "xyz:CRCL"]

# Per-asset max leverage caps (from Hyperliquid meta; validated 2026-06-30:
# BTC 40 / ETH 25 / SOL 20 / HYPE 10; xyz crypto-proxies 10). The leverage
# emitted is clamped to [1,5] (config) THEN min()'d against this venue cap.
ASSET_MAX_LEVERAGE = {
    "BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10,
    "XYZ:MSTR": 10, "XYZ:COIN": 10, "XYZ:CRCL": 10,
}

# Smart-money direction band (leaderboard_get_markets long-ratio), Bison/Dog
# convention. Hyena only ever acts on SHORT.
SM_LONG_RATIO = 58.0      # long_ratio > 58 -> SM LONG
SM_SHORT_RATIO = 42.0     # long_ratio < 42 -> SM SHORT (the side Hyena needs)

# 4h downtrend structure gate (lower-highs), strict-> count, >=0.6 of (lookback-1).
TREND_LOOKBACK = 6


def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {high|h, low|l, close|c} OR list
#    [t,o,h,l,c,v]) — same defensive pattern as Bison/Dog scoring. ──

def _high(c):
    if isinstance(c, dict):
        return safe_float(c.get("high", c.get("h", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return safe_float(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return safe_float(c.get("low", c.get("l", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return safe_float(c[3])
    return 0.0


def _close(c):
    if isinstance(c, dict):
        return safe_float(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return safe_float(c[4])
    return 0.0


def downtrend_structure(candles, lookback=TREND_LOOKBACK):
    """Lower-highs => DOWNTREND confirming. Returns (is_down, strength).

    Mirrors Bison/Dog `trend_structure` BEARISH branch (strict > counting,
    >= 0.6 * (lookback-1) gate). Hyena only cares about the DOWN side, so this
    returns the bearish verdict + strength directly; insufficient history or a
    non-bearish structure returns (False, 0.0)."""
    if len(candles) < lookback:
        return False, 0.0
    highs = [_high(c) for c in candles[-lookback:]]
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if total <= 0:
        return False, 0.0
    if lower_highs >= total * 0.6:
        return True, lower_highs / total
    return False, 0.0


def price_change_pct(candles, n_bars=1):
    """% change over the last n_bars closes (negative = price falling)."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def sm_short_tilt(long_pct, short_pct):
    """Net smart-money lean from leaderboard long/short %. Returns
    (direction, tilt_pct). Bison/Dog band: long_ratio > 58 -> LONG, < 42 ->
    SHORT, else NEUTRAL. `tilt_pct` for SHORT is (100 - long_ratio) — the
    magnitude of the short tilt (the score input Hyena cares about)."""
    long_pct = safe_float(long_pct)
    short_pct = safe_float(short_pct)
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100.0
    if long_ratio > SM_LONG_RATIO:
        return "LONG", long_ratio
    if long_ratio < SM_SHORT_RATIO:
        return "SHORT", 100.0 - long_ratio
    return "NEUTRAL", 50.0


def leverage_for(token, base_leverage, lev_min=1, lev_max=5):
    """Clamp the configured leverage to [lev_min, lev_max] (config) then to the
    per-asset venue cap. SHORT-only, conservative-by-design hedge sleeve."""
    lev = int(round(safe_float(base_leverage, lev_max)))
    lev = max(lev_min, min(lev, lev_max))
    cap = ASSET_MAX_LEVERAGE.get(str(token).upper(), lev_max)
    return min(lev, cap)


def score_short(token, candles_4h, sm, regime, asset_funding, inputs):
    """Score a SHORT-eligibility thesis for one name. Returns a candidate dict
    or None (not SHORT-eligible / not enough data).

    Inputs the caller (scan.py) resolves so this stays pure:
      candles_4h    — list of 4h candles (dict or [t,o,h,l,c,v]).
      sm            — (sm_direction, sm_tilt_pct) from leaderboard_get_markets
                      (sm_short_tilt), or (None, 0.0) if not found.
      regime        — market-wide funding regime string
                      (LONG_CROWDED / SHORT_CROWDED / NEUTRAL) or None. This is
                      the MASTER risk-off gate; scan.py only calls score_short
                      when the regime permits new shorts, but the regime is also
                      a score contributor here.
      asset_funding — per-asset 8h funding rate (float). Positive funding into a
                      crowded long = squeeze fuel paying the short.
      inputs        — scanner inputs (thresholds).

    SHORT-eligibility (ALL required, no LONG branch ever):
      - smart money net-SHORT the name (sm direction == SHORT), AND
      - 4h downtrend confirming (lower-highs structure).
    The funding regime master-gate is enforced upstream in scan.py; here a
    LONG_CROWDED regime adds confirmation score and a SHORT_CROWDED regime
    (should never reach here) returns None defensively.
    """
    min_sm_tilt = float(inputs.get("smShortTiltMinPct", 55))
    strong_sm_tilt = float(inputs.get("smStrongTiltPct", 70))
    crowded_funding = float(inputs.get("crowdedLongFundingThreshold", 0.0002))

    if len(candles_4h) < TREND_LOOKBACK:
        return None

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    sm_dir = (sm_dir or "").upper()

    # ── GATE 1: smart money must be net-SHORT the name (rotating OUT) ──
    if sm_dir != "SHORT":
        return None
    if sm_tilt < min_sm_tilt:
        return None

    # ── GATE 2: 4h downtrend confirming (lower-highs) ──
    is_down, down_strength = downtrend_structure(candles_4h)
    if not is_down:
        return None

    # ── GATE 3 (defensive): never short into a SHORT_CROWDED regime ──
    # The MASTER gate is enforced in scan.py (it only calls score_short when the
    # regime is LONG_CROWDED, or a per-asset funding fallback confirms). This is
    # belt-and-suspenders: if a SHORT_CROWDED regime ever reaches here, bail.
    if regime == "SHORT_CROWDED":
        return None

    score, reasons = 0, []

    # ── Funding regime (master risk-off confirmation, +3 / +1) ──
    if regime == "LONG_CROWDED":
        score += 3
        reasons.append("market funding shows crowded longs (squeeze fuel)")
    elif regime is None or regime == "NEUTRAL":
        # scan.py reached here via the per-asset funding fallback (no usable
        # market-wide regime). Smaller, asset-local confirmation.
        score += 1
        reasons.append("funding on this asset shows crowded longs")

    # ── Smart-money short tilt magnitude (+2 / +1) ──
    if sm_tilt >= strong_sm_tilt:
        score += 2
        reasons.append(f"smart money strongly short at {sm_tilt:.0f}%")
    else:
        score += 1
        reasons.append(f"smart money net short at {sm_tilt:.0f}%")

    # ── 4h downtrend strength (+2 / +1) ──
    if down_strength >= 0.8:
        score += 2
        reasons.append(f"strong 4-hour downtrend ({down_strength:.0%} strength)")
    else:
        score += 1
        reasons.append(f"4-hour downtrend at {down_strength:.0%} strength")

    # ── 4h price already falling, momentum confirmation (+1) ──
    p4h = price_change_pct(candles_4h, 1)
    if p4h < 0:
        score += 1
        reasons.append(f"price already falling, {p4h:+.1f}% last 4h")

    # ── Per-asset funding: positive funding into a crowded long pays the short
    #    (squeeze pressure) (+1). Negative funding (shorts already paying) -1. ──
    af = safe_float(asset_funding)
    if af > crowded_funding:
        score += 1
        reasons.append(f"funding pays shorts ({af * 100:.4f}% per 8h)")
    elif af < -crowded_funding:
        score -= 1
        reasons.append(f"funding works against shorts ({af * 100:.4f}%)")

    leverage = leverage_for(
        token,
        inputs.get("leverage", inputs.get("leverageDefault", 4)),
        int(inputs.get("leverageMin", 1)),
        int(inputs.get("leverageMax", 5)),
    )

    return {
        "asset": token,
        "direction": "SHORT",            # SHORT_ONLY — there is no LONG branch
        "score": score,
        "leverage": leverage,
        "reasons": reasons,
        "smDirection": sm_dir,
        "smTiltPct": round(sm_tilt, 1),
        "downStrength": round(down_strength, 3),
        "priceChange4hPct": round(p4h, 2),
        "fundingRegime": regime or "UNKNOWN",
        "assetFunding": af,
    }
