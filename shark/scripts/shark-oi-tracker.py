#!/usr/bin/env python3
"""shark-oi-tracker.py — OI + price + funding snapshot collection (every 5 min).

Fetches market_list_instruments for all assets, stores top N by OI value
as time-series snapshots in shark-oi-history.json. Keeps ~24h of 5-min data
(288 entries per asset). Rotates out small assets to keep file manageable.

Runs isolated. Output: heartbeat or error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

# Add scripts dir to path for shark_config
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-oi-tracker"


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)

    # Fetch all instruments (one call)
    instruments, err = cfg.fetch_instruments()
    if err:
        cfg.output_error(SCRIPT, f"market_list_instruments failed: {err}")
        return

    if not instruments:
        cfg.output_error(SCRIPT, "no instruments returned")
        return

    ts = cfg.now_ts()

    # Build snapshot data: compute OI in USD for ranking
    asset_data = []
    for inst in instruments:
        ctx = inst.get("context", {})
        name = inst.get("name", "")
        if not name or inst.get("is_delisted"):
            continue

        # Ban xyz assets (equities/commodities) — unreliable cascade signals
        if name.startswith("xyz:"):
            continue

        oi_raw = ctx.get("openInterest", "0")
        funding = ctx.get("funding", "0")
        mid_px = ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx")

        try:
            oi_units = float(oi_raw)
            price = float(mid_px) if mid_px else 0.0
            funding_rate = float(funding)
        except (TypeError, ValueError):
            continue

        if price <= 0 or oi_units <= 0:
            continue

        oi_usd = oi_units * price

        asset_data.append({
            "name": name,
            "oi": oi_units,
            "oi_usd": oi_usd,
            "price": price,
            "funding": funding_rate,
        })

    # Sort by OI USD value, keep top N
    asset_data.sort(key=lambda x: x["oi_usd"], reverse=True)
    top_assets = asset_data[:cfg.MAX_OI_ASSETS]

    # Update each strategy's OI history
    for strat in strategies:
        sk = strat.get("strategyId")
        if not sk:
            continue

        sd = cfg.state_dir(sk)
        history_path = os.path.join(sd, "shark-oi-history.json")
        history = cfg.load_json(history_path, {})

        # Add new snapshots
        for ad in top_assets:
            asset_name = ad["name"]
            entry = {
                "ts": ts,
                "oi": ad["oi"],
                "price": ad["price"],
                "funding": ad["funding"],
                "oi_usd": round(ad["oi_usd"], 2),
            }

            if asset_name not in history:
                history[asset_name] = []

            history[asset_name].append(entry)

            # Trim to max snapshots
            if len(history[asset_name]) > cfg.MAX_SNAPSHOTS_PER_ASSET:
                history[asset_name] = history[asset_name][-cfg.MAX_SNAPSHOTS_PER_ASSET:]

        # Remove assets no longer in top N (but keep if they have recent data < 2h old)
        cutoff = ts - 7200  # 2 hours
        tracked_names = {ad["name"] for ad in top_assets}
        to_remove = []
        for asset_name in list(history.keys()):
            if asset_name in tracked_names:
                continue
            # Check if any recent entries
            entries = history[asset_name]
            if entries and entries[-1].get("ts", 0) >= cutoff:
                continue  # Keep — still recent
            to_remove.append(asset_name)

        for name in to_remove:
            del history[name]

        cfg.atomic_write(history_path, history)

    cfg.output({
        "status": "ok",
        "script": SCRIPT,
        "assets_tracked": len(top_assets),
        "total_instruments": len(instruments),
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
