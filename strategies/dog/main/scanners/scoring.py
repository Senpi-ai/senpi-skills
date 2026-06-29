"""DOG — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Dog producer's `score_market` +
`regime_confirms_fade` (dog-producer.py v3.0.0, thesis frozen at v2.5). Every
scoring component, weight, threshold, gate, and the CONTRARIAN FLIP are reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
market snapshot.

Dog is a CONTRARIAN FADER: it scores the smart-money (SM) direction, then emits the
OPPOSITE direction (the fade). All hard gates that returned None in v2 return None
here too. Per-asset side-data the v2 producer fetched via MCP (funding regime,
per-asset funding rate, persistence/trend) is FETCHED BY THE CALLER (scan.py) and
passed in, so this module stays pure and unit-testable on plain dicts.

The only thing the caller owns that this module reproduces as an INPUT is the clock:
v2 read datetime.utcnow().hour for the US-session bonus; here `utc_hour` is passed in.
"""


ASSETS = ["BTC", "ETH", "SOL", "HYPE"]

# Scoring thresholds — verbatim from v2.5
MIN_SCORE = 8                          # contrarian floor — applied by the caller
MIN_EXHAUSTION_PCT = 3.0               # v2.5 sweet spot (2.5 too loose, 4.5 never fires)
EXHAUSTION_BONUS_SEVERE_PCT = 4.0      # +2 points for deep exhaustion
EXHAUSTION_BONUS_MODERATE_PCT = 2.5    # +1 point

# Leverage tiers — conservative for contrarian (verbatim from v2.5)
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7

# Max leverage caps per asset (from Hyperliquid meta; validated 2026-06-29:
# BTC 40 / ETH 25 / SOL 20 / HYPE 10 all match live HL maxLeverage exactly).
ASSET_MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10}


def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def regime_confirms_fade(fade_direction, regime):
    """Fade is confirmed when regime shows crowding in the OPPOSITE direction of
    the trade. Dog goes SHORT to fade LONG_CROWDED. Returns True/False/None
    (None = neutral, doesn't confirm or deny). Verbatim from v2.5."""
    if regime is None or regime == "NEUTRAL":
        return None
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


def score_market(m, regime, fh, asset_funding, utc_hour):
    """Score one market. Returns a candidate dict (post contrarian flip) or None
    to skip. v2.5 `score_market` logic preserved VERBATIM.

    Inputs the caller resolves (so this module stays pure):
      regime        — market-wide funding regime string or None
                      (LONG_CROWDED / SHORT_CROWDED / NEUTRAL).
      fh            — per-asset funding_history dict {persistence_hours, trend}
                      or None.
      asset_funding — per-asset 8h funding rate (float), 0.0 if unavailable.
      utc_hour      — current UTC hour (int) for the US-session bonus.
    """
    if not isinstance(m, dict):
        return None
    token = str(m.get("token", "")).upper()
    dex = m.get("dex", "")
    if dex or token not in ASSETS:
        return None

    sm_direction = str(m.get("direction", "")).upper()
    if sm_direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                            m.get("price_change_1h", 0)))
    cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
    cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

    # Hard gate: minimum SM engagement
    if traders < 30:
        return None

    # CONTRARIAN EXHAUSTION GATE
    if abs(p4h) < MIN_EXHAUSTION_PCT:
        return None  # Not exhausted yet — don't fight a fresh trend
    if (sm_direction == "LONG" and p4h < 0) or (sm_direction == "SHORT" and p4h > 0):
        return None  # SM direction opposes price — not an exhaustion pattern

    score, reasons = 0, []

    # ── SM concentration (0-3) ──
    if pct >= 15:
        score += 3
        reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2
        reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1
        reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # ── Trader depth (0-1) ──
    if traders >= 100:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    # ── 4H price alignment (+/-2) ──
    if abs(p4h) >= 2.0:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1
            reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # ── MOVE EXHAUSTION — INVERTED for contrarian ──
    if abs(p4h) >= EXHAUSTION_BONUS_SEVERE_PCT:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}% (great fade)")
    elif abs(p4h) >= EXHAUSTION_BONUS_MODERATE_PCT:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"EXHAUSTION {p4h:+.1f}% (fadeable)")

    # ── 1H momentum (0-1) ──
    if (sm_direction == "LONG" and p1h > 0.2) or (sm_direction == "SHORT" and p1h < -0.2):
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # ── 15m velocity freshness gate ──
    # For contrarian: SM must be actively building the position we're about to fade.
    # If SM is already unwinding (15m <= 0), the fade opportunity is passing — skip.
    if cc_15m <= 0:
        return None
    if cc_15m > 2.0:
        score += 3
        reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.5:
        score += 2
        reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1:
        score += 1
        reasons.append(f"15M_BUILDING +{cc_15m:.2f}")

    # ── 1h acceleration (0-1) ──
    if cc_1h > 1.0:
        score += 1
        reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

    # ── Funding alignment (0-1) — Dog likes funded trades ──
    # asset_funding is resolved by the caller (market_get_asset_data). SM direction
    # is the to-be-faded direction; Dog will EMIT the opposite. Funding "pays the
    # fade" means SM is short into positive funding (paying longs) OR long into
    # negative funding (paying shorts) — same as v2.5 logic.
    fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"
    asset_funding = safe_float(asset_funding)
    if (fade_direction == "SHORT" and asset_funding > 0.0002) or \
       (fade_direction == "LONG" and asset_funding < -0.0002):
        score += 1
        reasons.append(f"FUNDING_PAYS {asset_funding*100:.4f}%")

    # ── Regime HARD GATE — fade direction must align with crowded regime ──
    confirms = regime_confirms_fade(fade_direction, regime)
    if confirms is False:
        return None  # regime contradicts fade — skip
    if confirms is True:
        score += 2
        reasons.append(f"REGIME_CONFIRMS_{regime}")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # ── Persistence + trend via funding_history ──
    ph_val = None
    crowding_trend = None
    if fh:
        ph = fh.get("persistence_hours")
        crowding_trend = (fh.get("trend") or "").upper() or None
        try:
            ph_val = float(ph) if ph is not None else None
        except (TypeError, ValueError):
            ph_val = None
        if ph_val is not None:
            if ph_val >= 12:
                score += 2
                reasons.append(f"MATURE_CROWDING_{ph_val:.0f}h")
            elif ph_val >= 6:
                score += 1
                reasons.append(f"STABLE_CROWDING_{ph_val:.0f}h")
        if crowding_trend == "INCREASING":
            score -= 1
            reasons.append("CROWDING_STILL_BUILDING (early fade)")
        elif crowding_trend == "DECREASING":
            score += 1
            reasons.append("CROWDING_UNWINDING (fade confirmed)")

    # ── US session bonus (0-1) ──
    if 13 <= utc_hour <= 21:
        score += 1
        reasons.append("US_SESSION")

    # CONTRARIAN FLIP: emit OPPOSITE direction
    reasons.insert(0, f"CONTRARIAN_FLIP {token} (SM is {sm_direction})")

    # Leverage tier (verbatim from v2.5)
    leverage = DEFAULT_LEVERAGE
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            leverage = tier["leverage"]
            break
    leverage = min(leverage, ASSET_MAX_LEVERAGE.get(token, 10))

    return {
        "asset": token,
        "direction": fade_direction,
        "sm_direction": sm_direction,
        "score": score,
        "leverage": leverage,
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
        "regime": regime or "UNKNOWN",
        "persistence_hours": ph_val,
        "crowding_trend": crowding_trend,
        "asset_funding": asset_funding,
    }


def leverage_for_score(score, token):
    """Conviction-tiered leverage, clamped to the per-asset cap. Verbatim from
    v2.5 (10x at score >=10, else 7x), then min() against ASSET_MAX_LEVERAGE."""
    leverage = DEFAULT_LEVERAGE
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            leverage = tier["leverage"]
            break
    return min(leverage, ASSET_MAX_LEVERAGE.get(token, 10))
