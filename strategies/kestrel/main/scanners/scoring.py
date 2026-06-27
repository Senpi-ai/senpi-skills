"""KESTREL — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 KESTREL producer's `score_asset_breakout`
(SKILL.md v2.0.0 / producer v3.0.4). KESTREL is the multi-asset, non-crypto XYZ
macro breakout rider: a 1H breakout HARD GATE (>=1.5%) then 4H trend alignment,
a move-exhaustion penalty, a volume surge, smart-money confirmation, a spread
gate, and funding alignment — summed into a breakout score.

The math/indexing is reproduced VERBATIM from `kestrel-producer.py` so a
fidelity harness can diff this against the v2 producer on the same market
snapshot. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`;
fix them only as a separate, labelled change AFTER the port is validated.

Multi-asset, single-pass, unit-testable on plain candle lists. The caller
(scan.py) owns the MCP reads, the per-asset SM map, the universe loop, and the
top-candidate selection; this module scores one asset given its already-fetched
market snapshot.

XYZ / 24-7 TUNING PRESERVED (do NOT redesign):
  - Universe is 12 NON-crypto XYZ (HIP-3 DEX) names — commodities, metals,
    indices, mega-cap equities. macroAsset = "" (no BTC-correlation factor).
  - Wide natural XYZ spreads -> spread gate relaxed to 0.35% (v2.0).
  - XYZ trades 24/7 incl weekends — there is deliberately NO market-hours /
    session / weekday gating anywhere here.
  - SM confirmation reads the XYZ-dex-filtered leaderboard map (built by the
    caller); the crypto SM tracker is filtered to dex == "xyz".
"""


# ── v2 thresholds (producer code constants, preferred over config.json) ──
MIN_SCORE_DEFAULT = 5                  # v2.0: was 6 (v1.2 calibration relax)
BREAKOUT_THRESHOLD_1H = 1.5           # 1.5% 1H breakout = mandatory hard gate
BREAKOUT_THRESHOLD_4H = 3.0          # 3% 4H trend = strong alignment
EXHAUSTION_THRESHOLD = 6.0           # v2.0: was 4 (relaxed)
TIRING_THRESHOLD = 4.0               # v2.0: was 2.5 (relaxed)
SPREAD_MAX = 0.0035                  # v2.0: was 0.002 (loosened for XYZ)

# Score-tiered leverage (v2 LEVERAGE_TIERS, evaluated high-to-low).
LEVERAGE_TIERS = [
    {"min_score": 9, "leverage": 5},
    {"min_score": 5, "leverage": 3},
]
DEFAULT_LEVERAGE = 3


def safe_float(v, d=0.0):
    """v2: safe_float."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score, tiers=None, default_leverage=DEFAULT_LEVERAGE):
    """v2 get_leverage_for_score — first tier (high-to-low) whose min_score the
    score clears; else default. Tiers are dicts {min_score, leverage}."""
    tiers = tiers if tiers is not None else LEVERAGE_TIERS
    for tier in tiers:
        if score >= int(tier["min_score"]):
            return int(tier["leverage"])
    return int(default_leverage)


# ── candle accessor (v2 read dict {close|c}; list branch defensive only) ──

def _close(c):
    if isinstance(c, dict):
        return safe_float(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return safe_float(c[4])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return safe_float(c[5])
    return 0.0


def score_breakout(asset, candles_1h, asset_context, order_book, sm_record, config=None):
    """Score a single XYZ asset for a breakout entry. VERBATIM port of v2
    `score_asset_breakout` (the body after its MCP read). Returns a thesis dict
    or None if a HARD gate (insufficient candles, sub-threshold 1H move, or a
    too-wide spread) blocks.

    Args (all already fetched by the caller — this stays pure):
      asset         : bare token (e.g. "NVDA") for telemetry/labels
      candles_1h    : list of 1h candles (newest last)
      asset_context : the market_get_asset_data asset_context dict (funding)
      order_book    : the order_book dict ({"levels": [[bids],[asks]]} or legacy)
      sm_record     : the XYZ-dex SM market record for this token, or None
      config        : optional inputs dict (threshold overrides)
    """
    config = config or {}
    bt_1h = float(config.get("breakoutThreshold1h", BREAKOUT_THRESHOLD_1H))
    bt_4h = float(config.get("breakoutThreshold4h", BREAKOUT_THRESHOLD_4H))
    exhaustion = float(config.get("exhaustionThreshold", EXHAUSTION_THRESHOLD))
    tiring = float(config.get("tiringThreshold", TIRING_THRESHOLD))
    spread_max = float(config.get("spreadMax", SPREAD_MAX))

    candles_1h = candles_1h or []
    if len(candles_1h) < 3:
        return None

    # ── 1H breakout (mandatory hard gate) ──
    # v3.0.1 rolling candle boundary fix (preserved verbatim): also evaluate the
    # just-closed hour (candles[-2] vs candles[-3]) and use whichever delta has
    # the larger magnitude — catches end-of-hour breakouts on the next tick.
    last_1h = candles_1h[-1]
    prev_1h = candles_1h[-2]
    close_now = _close(last_1h)
    close_prev = _close(prev_1h)

    if close_prev <= 0 or close_now <= 0:
        return None

    pct_1h_current = ((close_now - close_prev) / close_prev) * 100

    pct_1h_recent_closed = 0.0
    if len(candles_1h) >= 3:
        close_prev_prev = _close(candles_1h[-3])
        if close_prev_prev > 0:
            pct_1h_recent_closed = ((close_prev - close_prev_prev) / close_prev_prev) * 100

    if abs(pct_1h_recent_closed) > abs(pct_1h_current):
        pct_1h = pct_1h_recent_closed
    else:
        pct_1h = pct_1h_current

    if abs(pct_1h) < bt_1h:
        return None

    # ── 4H trend (trailing window via 1H candles, v1.1 fix preserved) ──
    pct_4h = 0
    if len(candles_1h) >= 5:
        close_1h_4h_ago = _close(candles_1h[-5])
        if close_1h_4h_ago > 0:
            pct_4h = ((close_now - close_1h_4h_ago) / close_1h_4h_ago) * 100

    breakout_dir = "LONG" if pct_1h > 0 else "SHORT"

    score = 0
    reasons = []

    # ── 1H breakout magnitude (v2.0: weighted heavier; +5/+4/+3) ──
    if abs(pct_1h) >= 3.0:
        score += 5  # v2-quirk: was 4 pre-v2.0
        reasons.append(f"MASSIVE_BREAKOUT_1H {pct_1h:+.2f}%")
    elif abs(pct_1h) >= 2.0:
        score += 4  # v2-quirk: was 3 pre-v2.0
        reasons.append(f"STRONG_BREAKOUT_1H {pct_1h:+.2f}%")
    elif abs(pct_1h) >= bt_1h:
        score += 3  # v2-quirk: was 2 pre-v2.0
        reasons.append(f"BREAKOUT_1H {pct_1h:+.2f}%")

    # ── 4H trend alignment ──
    if abs(pct_4h) >= bt_4h:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score += 2
            reasons.append(f"4H_TREND_CONFIRMS {pct_4h:+.2f}%")
    elif abs(pct_4h) >= 1.0:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score += 1
            reasons.append(f"4H_ALIGNED {pct_4h:+.2f}%")

    # ── Move-exhaustion penalty (v2.0: relaxed thresholds 6% / 4%) ──
    if abs(pct_4h) >= exhaustion:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_PENALTY {pct_4h:+.2f}%")
    elif abs(pct_4h) >= tiring:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_PENALTY {pct_4h:+.2f}%")

    # ── Volume surge ──
    if len(candles_1h) >= 4:
        vols = [_vol(c) for c in candles_1h[-4:]]
        if len(vols) >= 4 and vols[:-1] and sum(vols[:-1]) > 0:
            avg_prev = sum(vols[:-1]) / len(vols[:-1])
            vol_vs_avg = vols[-1] / avg_prev if avg_prev > 0 else 0
            if vol_vs_avg >= 2.0:
                score += 2
                reasons.append(f"VOLUME_SURGE {vol_vs_avg:.1f}x")
            elif vol_vs_avg >= 1.3:
                score += 1
                reasons.append(f"VOLUME_UP {vol_vs_avg:.1f}x")

    # ── SM confirmation (caller passes the XYZ-dex-filtered record or None) ──
    sm = sm_record
    sm_pct = 0.0
    sm_traders = 0
    sm_dir = ""
    if sm:
        sm_dir = str(sm.get("direction", "")).upper()
        sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
        sm_traders = int(sm.get("trader_count", 0))

        if sm_dir == breakout_dir and sm_pct >= 3:
            score += 2
            reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
        elif sm_dir == breakout_dir and sm_pct >= 1:
            score += 1
            reasons.append(f"SM_BUILDING {sm_pct:.1f}% ({sm_traders}t)")
        elif sm_dir != breakout_dir and sm_pct >= 5:
            score += 1
            reasons.append(f"SM_TRAPPED_{sm_dir} {sm_pct:.1f}%")

    # ── Spread gate (v2.0: relaxed 0.2% -> 0.35%) ──
    # v3.0.1 order-book parsing fix (preserved verbatim): parse the nested
    # `levels` structure first ({"levels": [[bids],[asks]]}), fall back to legacy
    # top-level bids/asks. A too-wide spread is a HARD reject (return None).
    spread_pct = None
    ob = order_book if isinstance(order_book, dict) else {}
    if isinstance(ob, dict):
        levels = ob.get("levels", [])
        bids, asks = [], []
        if isinstance(levels, list) and len(levels) >= 2:
            bids = levels[0] if isinstance(levels[0], list) else []
            asks = levels[1] if isinstance(levels[1], list) else []
        else:
            # Legacy schema fallback (kept for backward compat)
            bids = ob.get("bids", ob.get("bid", []))
            asks = ob.get("asks", ob.get("ask", []))
        if bids and asks:
            best_bid = safe_float(bids[0][0] if isinstance(bids[0], list)
                                  else bids[0].get("price", 0)
                                  if isinstance(bids[0], dict)
                                  else 0)
            best_ask = safe_float(asks[0][0] if isinstance(asks[0], list)
                                  else asks[0].get("price", 0)
                                  if isinstance(asks[0], dict)
                                  else 0)
            if best_bid > 0 and best_ask > 0:
                mid = (best_bid + best_ask) / 2
                spread_pct = (best_ask - best_bid) / mid
                if spread_pct > spread_max:
                    return None
                reasons.append(f"SPREAD_OK {spread_pct * 100:.3f}%")

    # ── Funding alignment ──
    funding = 0.0
    asset_ctx = asset_context if isinstance(asset_context, dict) else {}
    if isinstance(asset_ctx, dict):
        funding = safe_float(asset_ctx.get("funding", 0))
        if (breakout_dir == "LONG" and funding < -0.001) or \
           (breakout_dir == "SHORT" and funding > 0.001):
            score += 1
            reasons.append(f"FUNDING_ALIGNED {funding * 100:.4f}%")

    return {
        "token": asset,
        "direction": breakout_dir,
        "pct_1h": pct_1h,
        "pct_4h": pct_4h,
        "score": score,
        "reasons": reasons,
        "sm_pct": sm_pct,
        "sm_traders": sm_traders,
        "sm_dir": sm_dir,
        "funding": funding,
        "spread_pct": spread_pct or 0.0,
    }
