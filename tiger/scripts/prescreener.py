#!/usr/bin/env python3
"""
prescreener.py — Two-phase pre-screening for TIGER strategy.
Phase 1: Score all ~230 assets using cheap instrument-level data (one API call).
Phase 2: Scanners use the top 30 candidates instead of their own top-12 selection.

Output: /data/workspace/recipes/tiger/state/prescreened.json
"""

import sys
import os
import json
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, get_all_instruments, output,
    STATE_DIR, atomic_write
)

PRESCREENED_FILE = os.path.join(STATE_DIR, "prescreened.json")
BLACKLIST = {"PUMP"}
TOP_N = 30


def score_instrument(inst: dict, config: dict) -> dict | None:
    """Score a single instrument using cheap context data. Returns candidate dict or None."""
    name = inst.get("name", "")
    if inst.get("is_delisted"):
        return None
    if name in BLACKLIST:
        return None

    max_lev = inst.get("max_leverage", 0)
    if max_lev < config.get("min_leverage", 5):
        return None

    ctx = inst.get("context", {})
    day_vol = float(ctx.get("dayNtlVlm", 0))
    if day_vol < 100_000:  # Very low floor — prescreener casts a wide net
        return None

    prev_day_px = float(ctx.get("prevDayPx", 0))
    mark_px = float(ctx.get("markPx", 0))
    funding = float(ctx.get("funding", 0))
    oi = float(ctx.get("openInterest", 0))

    if prev_day_px <= 0 or mark_px <= 0:
        return None

    # --- Scoring components (each 0-1) ---

    # 1. Price change from prevDay (momentum proxy)
    price_change_pct = ((mark_px - prev_day_px) / prev_day_px) * 100
    abs_change = abs(price_change_pct)
    # 0-1 scale: 0% -> 0, 5%+ -> 1
    momentum_score = min(abs_change / 5.0, 1.0)

    # 2. Funding rate extremes (funding arb proxy)
    funding_ann = abs(funding) * 3 * 365 * 100  # annualized %
    # 0-1 scale: 0% -> 0, 50%+ -> 1
    funding_score = min(funding_ann / 50.0, 1.0)

    # 3. OI relative to volume (activity/positioning proxy)
    # High OI relative to volume = crowded positioning = opportunity
    oi_vol_ratio = (oi / day_vol) if day_vol > 0 else 0
    # Normalize: ratio of 1-5 is interesting
    oi_score = min(max(oi_vol_ratio - 0.5, 0) / 4.0, 1.0)

    # 4. Volume rank — computed later as a percentile, use raw volume for now
    # (will be normalized after collecting all candidates)

    # Composite score (without volume rank, added later)
    # Weights: momentum 0.35, funding 0.20, OI 0.15, volume_rank 0.30
    partial_score = momentum_score * 0.35 + funding_score * 0.20 + oi_score * 0.15

    return {
        "name": name,
        "partial_score": partial_score,
        "price_change_pct": round(price_change_pct, 3),
        "abs_price_change": abs_change,
        "funding_rate": funding,
        "funding_annualized_pct": round(funding_ann, 1),
        "momentum_score": round(momentum_score, 3),
        "funding_score": round(funding_score, 3),
        "oi_score": round(oi_score, 3),
        "day_ntl_vlm": day_vol,
        "open_interest": oi,
        "max_leverage": max_lev,
        "mark_px": mark_px,
    }


def main():
    config = load_config()
    instruments = get_all_instruments()
    if not instruments:
        output({"error": "Failed to fetch instruments"})
        return

    # Score all instruments
    scored = []
    for inst in instruments:
        c = score_instrument(inst, config)
        if c:
            scored.append(c)

    if not scored:
        output({"error": "No candidates passed pre-screening"})
        return

    # Add volume rank component (0-1 percentile)
    scored.sort(key=lambda x: x["day_ntl_vlm"], reverse=True)
    n = len(scored)
    for i, c in enumerate(scored):
        vol_rank = 1.0 - (i / max(n - 1, 1))  # 1.0 = highest volume, 0.0 = lowest
        c["volume_rank_score"] = round(vol_rank, 3)
        c["volume_rank"] = i + 1
        c["prescreen_score"] = round(c["partial_score"] + vol_rank * 0.30, 3)

    # Sort by composite score
    scored.sort(key=lambda x: x["prescreen_score"], reverse=True)

    # Take top N
    top = scored[:TOP_N]

    # Build clean candidate list
    candidates = []
    for c in top:
        candidates.append({
            "name": c["name"],
            "prescreen_score": c["prescreen_score"],
            "price_change_pct": c["price_change_pct"],
            "funding_rate": c["funding_rate"],
            "funding_annualized_pct": c["funding_annualized_pct"],
            "volume_rank": c["volume_rank"],
            "max_leverage": c["max_leverage"],
            "day_ntl_vlm": c["day_ntl_vlm"],
            "open_interest": c["open_interest"],
            "mark_px": c["mark_px"],
        })

    # Split into two groups of 15 by volume (group_a = higher volume)
    by_vol = sorted(candidates, key=lambda x: x["day_ntl_vlm"], reverse=True)
    half = len(by_vol) // 2
    group_a = [c["name"] for c in by_vol[:half]]
    group_b = [c["name"] for c in by_vol[half:]]

    result = {
        "timestamp": int(time.time()),
        "candidates": candidates,
        "group_a": group_a,
        "group_b": group_b,
        "total_screened": n,
        "passed_prescreen": len(candidates),
    }

    # Write prescreened.json atomically
    atomic_write(PRESCREENED_FILE, result)

    # Output summary
    output({
        "action": "prescreen",
        "total_screened": n,
        "passed_prescreen": len(candidates),
        "group_a": group_a,
        "group_b": group_b,
        "top_5": [{"name": c["name"], "score": c["prescreen_score"], "change": c["price_change_pct"]} for c in candidates[:5]],
        "file": PRESCREENED_FILE,
    })


if __name__ == "__main__":
    main()
