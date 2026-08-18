"""RAPTOR — pure hot-streak-follower math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Raptor v4.0.0 producer (raptor-producer.py).
Given quality-trader dicts + their position lists + the smart-money map (all already
fetched by scan.py), this selection/scoring is reproduced VERBATIM from the v2
`build_signal` / `get_leverage_for_score` so a fidelity harness can diff it against the
v2 producer on the same snapshot. `# v2-quirk` marks behavior preserved deliberately
(not redesigned).

Thesis: find a proven-AND-hot trader (ELITE/RELIABLE on TCS, currently winning the
weekly window), isolate the single strongest open position driving the streak, confirm
the smart-money crowd leans the same way and the asset hasn't already run past the
whale's entry, then follow that one trade with conviction-scaled leverage."""


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# alias used by scan.py's read parsing
_f = safe_float


def get_leverage_for_score(score, tiers, default_leverage):
    """Conviction-tiered leverage. v2 sorted tiers desc by minScore and took the first
    tier whose minScore the score clears. tiers = [[minScore, leverage], ...]."""
    # v2-quirk: tiers come in as [minScore, leverage] pairs; sort desc by minScore.
    for t in sorted(tiers, key=lambda x: x[0], reverse=True):
        if score >= t[0]:
            return int(t[1])
    return int(default_leverage)


def select_best_position(positions, inputs):
    """Port of v2 build_signal's position-selection loop: pick the strongest open
    position by |delta_pnl|, dropping xyz: assets, and compute local concentration.
    Returns (best_pos_dict | None, concentration). best_pos has asset/direction/
    delta_pnl/whale_entry_px. Returns (None, 0.0) if no position clears the gates."""
    xyz_banned = bool(inputs.get("xyzBanned", True))
    min_position_pnl = float(inputs.get("minPositionPnl", 100_000))
    min_concentration = float(inputs.get("minConcentration", 0.35))

    best_pos = None
    best_abs_pnl = 0.0
    total_abs_pnl = 0.0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        asset = str(
            pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
        ).upper()
        if not asset:
            continue
        if xyz_banned and asset.lower().startswith("xyz:"):
            continue
        delta_pnl = safe_float(
            pos.get("delta_pnl",
                    pos.get("deltaPnl",
                            pos.get("unrealizedPnl",
                                    pos.get("unrealized_pnl",
                                            pos.get("pnl", 0)))))
        )
        direction = str(
            pos.get("direction",
                    pos.get("side",
                            "LONG" if delta_pnl >= 0 else "SHORT"))  # v2-quirk: sign-of-pnl fallback
        ).upper()
        if direction not in ("LONG", "SHORT"):
            continue
        whale_entry_px = safe_float(
            pos.get("entryPx",
                    pos.get("entry_px",
                            pos.get("entryPrice",
                                    pos.get("entry_price",
                                            pos.get("avgEntryPx",
                                                    pos.get("avg_entry_px", 0))))))
        )
        abs_pnl = abs(delta_pnl)
        total_abs_pnl += abs_pnl
        if abs_pnl > best_abs_pnl:
            best_abs_pnl = abs_pnl
            best_pos = {
                "asset": asset,
                "direction": direction,
                "delta_pnl": delta_pnl,
                "whale_entry_px": whale_entry_px,
            }

    if not best_pos or best_abs_pnl < min_position_pnl:
        return None, 0.0

    concentration = (best_abs_pnl / total_abs_pnl) if total_abs_pnl > 0 else 0
    if concentration < min_concentration:
        return None, concentration

    return best_pos, concentration


def sm_gate(sm, best_pos, inputs):
    """Port of v2 build_signal's smart-money gate. `sm` is the smart-money map entry
    for the asset (or None). Returns True if it passes all SM gates."""
    if not sm:
        return False
    if inputs.get("requireDirectionMatch", True) and sm["direction"] != best_pos["direction"]:
        return False
    if sm["pct"] < float(inputs.get("minSmPct", 2.0)):
        return False
    if sm["traders"] < int(inputs.get("minSmTraders", 10)):
        return False
    return True


def price_run_blocks(best_pos, current_px, inputs):
    """v3.3 ENTRY DISCIPLINE (verbatim): don't buy the whale's top. If the asset has
    already run > maxPriceRunPctFromWhaleEntry in the whale's favor since their entry,
    block. Returns True to BLOCK. No price (current_px<=0 or whale_entry_px<=0) => no
    block (v2 only evaluated when both were positive)."""
    max_run = float(inputs.get("maxPriceRunPctFromWhaleEntry", 5.0))
    whale_entry_px = best_pos.get("whale_entry_px", 0)
    if whale_entry_px <= 0 or current_px <= 0:
        return False
    if best_pos["direction"] == "LONG":
        run_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
    else:
        run_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
    return run_pct > max_run


def score_signal(trader, best_pos, concentration, sm, current_px, inputs):
    """Port of v2 build_signal's scoring block (VERBATIM). Returns a signal dict (with
    `score`/`reasons`) or None if the TCS gate inside scoring rejects. Assumes the
    position/SM/price-run gates already passed in the caller."""
    score = 0
    reasons = []

    tcs = trader["tcs_label"]
    if tcs == "ELITE":
        score += 3
        reasons.append(f"elite-rated trader, TCS {trader['tcs_value']:.0f}")
    elif tcs == "RELIABLE":
        score += 2
        reasons.append(f"reliable-rated trader, TCS {trader['tcs_value']:.0f}")
    else:
        return None  # v2-quirk: non-ELITE/RELIABLE rejected inside scoring (belt-and-suspenders)

    trader_delta = trader["unrealized_pnl"]
    if trader_delta >= float(inputs.get("tier3Threshold", 3_000_000)):
        score += 3; reasons.append(f"whale up a massive ${trader_delta/1e6:.1f}M unrealized")
    elif trader_delta >= float(inputs.get("tier2Threshold", 1_500_000)):
        score += 2; reasons.append(f"whale up a large ${trader_delta/1e6:.1f}M unrealized")
    else:
        score += 1; reasons.append(f"whale up ${trader_delta/1e6:.1f}M unrealized")

    if trader["roi"] >= 50:
        score += 1; reasons.append(f"whale ROI {trader['roi']:.0f}%")

    if concentration >= 0.70:
        score += 2; reasons.append(f"high conviction, {concentration:.0%} of whale's book")
    elif concentration >= 0.55:
        score += 1; reasons.append(f"{concentration:.0%} of the whale's book in this bet")

    if sm["pct"] >= 8:
        score += 2; reasons.append(f"smart money strong at {sm['pct']:.1f}%")
    elif sm["pct"] >= 4:
        score += 1; reasons.append(f"smart money aligned at {sm['pct']:.1f}%")

    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    if best_pos["direction"] == "LONG":
        if p4h > 0.5 and p1h > 0.2:
            score += 2; reasons.append(f"4h and 1h confirm, +{p4h:.1f}% / +{p1h:.1f}%")
        elif p4h > 0.5:
            score += 1; reasons.append(f"4h trend confirms, +{p4h:.1f}%")
        elif p4h < -2:
            score -= 1; reasons.append(f"4h trend against us, {p4h:.1f}%")
    else:
        if p4h < -0.5 and p1h < -0.2:
            score += 2; reasons.append(f"4h and 1h confirm, {p4h:.1f}% / {p1h:.1f}%")
        elif p4h < -0.5:
            score += 1; reasons.append(f"4h trend confirms, {p4h:.1f}%")
        elif p4h > 2:
            score -= 1; reasons.append(f"4h trend against us, +{p4h:.1f}%")

    c15m = sm.get("contrib_15m", 0)
    if c15m > 0.5:
        score += 1; reasons.append(f"smart-money flow spiking, +{c15m:.2f} in 15m")
    elif c15m <= 0:
        score -= 1; reasons.append(f"15m smart-money flow stale ({c15m:.2f})")

    # v3.2 entry-discipline BONUS — reward getting in BETTER than the whale
    whale_entry_px = best_pos.get("whale_entry_px", 0)
    if whale_entry_px > 0 and current_px > 0:
        if best_pos["direction"] == "LONG":
            edge_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
        else:
            edge_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
        if edge_pct >= 5:
            score += 2; reasons.append(f"entry {edge_pct:.1f}% better than the whale's")
        elif edge_pct >= 2:
            score += 1; reasons.append(f"entry edge over the whale, {edge_pct:.1f}%")

    return {
        "asset": best_pos["asset"],
        "direction": best_pos["direction"],
        "score": score,
        "reasons": reasons,
        "traderId": trader["address"][:10] + "...",
        "fullTraderId": trader["address"],
        "tcs": tcs,
        "traderDeltaPnl": trader_delta,
        "positionDeltaPnl": best_pos["delta_pnl"],
        "concentration": concentration,
        "smPct": sm["pct"],
        "smTraders": sm["traders"],
        "priceChg4h": p4h,
        "priceChg1h": p1h,
        "whaleEntryPx": whale_entry_px if whale_entry_px > 0 else None,
        "currentPx": current_px if (whale_entry_px > 0 and current_px > 0) else None,
    }


def margin_pct_for(score, inputs):
    """Conviction-scaled marginPct INTENT as a PERCENT of withdrawable in (0,100] (the
    runtime sizes (marginPct/100)*withdrawable). v2-quirk: a two-step (NOT linear) ladder
    — base below highConvScore, high-conv at/above it. v2 stored these as FRACTIONS
    (0.25 / 0.35); ported here as PERCENTS (25 / 35) for the 3.0 sizing contract."""
    base = float(inputs.get("marginPctBase", 25))
    high = float(inputs.get("marginPctHighConv", 35))
    high_conv_score = float(inputs.get("highConvScore", 10))
    return high if score >= high_conv_score else base
