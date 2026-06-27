"""SCORPION — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Scorpion producer's `score_market` +
multi-factor scoring (producer v5.0.0, thesis preserved from v3.2). The
math/indexing is reproduced VERBATIM so a fidelity harness can diff this against
the v2 producer on the same `leaderboard_get_markets` snapshot. Behaviour-preserving
quirks from v2 are kept and flagged `# v2-quirk`; fix them only as a separate,
labelled change AFTER the port is validated.

Scorpion scores ONE row of leaderboard_get_markets per call (each row is already a
{token, dex, direction, ...} smart-money market). It is NOT candle-based — the whole
thesis lives in the SM-concentration + price-change fields the leaderboard returns.
`scan.py` fetches the market rows + enrichment via ctx.senpi_mcp and hands plain dicts
to the functions here.

FIDELITY NOTE — universe: v2 hardcoded XYZ_ASSETS = {CL, BRENTOIL, GOLD, SPX}. `xyz:SPX`
is NOT a live Hyperliquid instrument (validated against market_list_instruments
2026-06-26); the real S&P index ticker is `SP500`. v2's SPX gate therefore matched no
live market and silently never traded the index. This port passes the universe via
inputs (default xyzAssets includes "SP500", not "SPX") so the intended index actually
trades. All scoring math is unchanged; only the universe membership token is corrected.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(token, xyz_assets):
    return token in xyz_assets


def coin_label(token, is_xyz_asset):
    """v2 coin_label — XYZ assets get the `xyz:` prefix; crypto stays bare."""
    return f"xyz:{token}" if is_xyz_asset else token


# ═══════════════════════════════════════════════════════════════
# SIGNAL SCORING (ported verbatim from v2 score_market — unchanged logic)
# ═══════════════════════════════════════════════════════════════

def score_market(m, inputs):
    """Score a single leaderboard market row. Returns dict or None if below
    threshold / off-universe / mis-aligned. Ported verbatim from the v2 producer's
    `score_market`; the only changes are (a) the asset universe + thresholds come
    from `inputs` instead of module constants, and (b) the SPX→SP500 universe fix
    (see module docstring). The scoring arithmetic is byte-for-byte the v2 logic."""
    crypto_assets = set(inputs.get("cryptoAssets", []))
    xyz_assets = set(inputs.get("xyzAssets", []))
    min_score_crypto = float(inputs.get("minScoreCrypto", 11))
    min_score_xyz = float(inputs.get("minScoreXyz", 9))
    min_4h_crypto = float(inputs.get("min4hAlignedPctCrypto", 1.0))
    min_4h_xyz = float(inputs.get("min4hAlignedPctXyz", 0.5))
    min_traders = int(inputs.get("minTraders", 5))

    token = str(m.get("token", "")).upper()
    dex = str(m.get("dex", "")).lower()

    is_xyz_asset = (dex == "xyz" and token in xyz_assets)
    is_crypto_asset = (dex != "xyz" and token in crypto_assets)
    if not is_xyz_asset and not is_crypto_asset:
        return None

    sm_direction = str(m.get("direction", "")).upper()
    if sm_direction not in ("LONG", "SHORT"):
        return None

    pct = _f(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = _f(m.get("token_price_change_pct_4h", 0))
    p1h = _f(m.get("token_price_change_pct_1h",
                   m.get("price_change_1h", 0)))
    cc_15m = _f(m.get("contribution_pct_change_15m", 0))
    cc_1h = _f(m.get("contribution_pct_change_1h", 0))
    cc_4h = _f(m.get("contribution_pct_change_4h", 0))

    if traders < min_traders:
        return None

    # 4H price alignment gate — SM direction must match price trend
    min_4h = min_4h_xyz if is_xyz_asset else min_4h_crypto
    price_aligned = (sm_direction == "LONG" and p4h >= min_4h) or \
                    (sm_direction == "SHORT" and p4h <= -min_4h)
    if not price_aligned:
        return None

    score = 0
    reasons = []

    # SM concentration (0-3)
    if pct >= 15:
        score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

    # 4H price alignment (0-3)
    big_move = 3.0 if is_xyz_asset else 5.0
    med_move = 1.5 if is_xyz_asset else 3.0
    if abs(p4h) >= big_move:
        score += 3; reasons.append(f"STRONG_TREND {p4h:+.1f}%")
    elif abs(p4h) >= med_move:
        score += 2; reasons.append(f"TREND {p4h:+.1f}%")
    elif abs(p4h) >= min_4h:
        score += 1; reasons.append(f"ALIGNED {p4h:+.1f}%")

    # 15m SM velocity (0-2, penalty -1 on fade)
    if cc_15m > 1.0:
        score += 2; reasons.append(f"15M_SM_BUILDING {cc_15m:+.2f}")
    elif cc_15m > 0.3:
        score += 1; reasons.append(f"15M_SM_FRESH {cc_15m:+.2f}")
    elif cc_15m < -0.5:
        score -= 1; reasons.append(f"15M_SM_FADING {cc_15m:+.2f}")

    # 1H acceleration
    if sm_direction == "LONG" and p1h > 0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")
    elif sm_direction == "SHORT" and p1h < -0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")

    # Trader depth
    if traders >= 50:
        score += 1; reasons.append(f"DEEP_SM ({traders}t)")

    # 4H contribution shift
    if abs(cc_4h) >= 5.0:
        score += 1; reasons.append(f"4H_CONVICTION {cc_4h:+.1f}")

    min_score = min_score_xyz if is_xyz_asset else min_score_crypto
    if score < min_score:
        return None

    reasons.insert(0, f"TREND_FOLLOW {coin_label(token, is_xyz_asset)} {sm_direction}")

    return {
        "asset": coin_label(token, is_xyz_asset),
        "token": token,
        "is_xyz": is_xyz_asset,
        "direction": sm_direction,
        "score": score,
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
    }


# ═══════════════════════════════════════════════════════════════
# ENRICHMENT (ported verbatim from v2 — pure portions only)
# ═══════════════════════════════════════════════════════════════

def build_macro_context(candidate, btc_macro):
    """v2 build_macro_context — BTC macro is structurally irrelevant to XYZ assets,
    so v2 set NOT_APPLICABLE for them (the v2 LLM cited BTC macro to wrongly skip
    oil/brent). Crypto gets the real BTC direction/pct. Ported verbatim."""
    if candidate["is_xyz"]:
        return {"direction": "NOT_APPLICABLE", "pct": 0.0}
    return {
        "direction": btc_macro.get("direction") or "UNKNOWN",
        "pct": btc_macro.get("pct") or 0,
    }


def compute_xyz_peer_momentum(candidates):
    """v2 compute_xyz_peer_momentum — for each XYZ direction, count peer XYZ assets
    trending the same way in the same scan (macro-tailwind signal). Ported verbatim.
    Returns {(token, direction): peer_count}."""
    xyz_signals = [c for c in candidates if c["is_xyz"]]
    result = {}
    for c in xyz_signals:
        peers = sum(
            1 for p in xyz_signals
            if p["token"] != c["token"] and p["direction"] == c["direction"]
        )
        result[(c["token"], c["direction"])] = peers
    return result


def btc_macro_from_candles(candles_1h):
    """v2 fetch_btc_macro math (the pure part): 24h % change from the last 24 1h
    candles' first-open vs last-close. The I/O (the MCP read) stays in scan.py;
    this is the byte-for-byte arithmetic. Returns {direction, pct} or {None, None}."""
    if not candles_1h or len(candles_1h) < 24:
        return {"direction": None, "pct": None}
    window = candles_1h[-24:]
    opens = [_f(c.get("open", c.get("o", 0))) for c in window]
    closes = [_f(c.get("close", c.get("c", 0))) for c in window]
    if opens[0] <= 0:
        return {"direction": None, "pct": None}
    pct = (closes[-1] - opens[0]) / opens[0] * 100
    return {"direction": "UP" if pct > 0 else "DOWN", "pct": round(pct, 2)}
