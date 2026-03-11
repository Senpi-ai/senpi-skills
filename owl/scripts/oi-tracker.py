#!/usr/bin/env python3
"""OWL OI Tracker — Snapshots OI, funding, and volume for all assets every 5 minutes.
Stores in crowding-history.json for crowding persistence tracking and OI baseline calculation.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from owl_config import (
    get_strategy_dirs, load_config, load_crowding_history,
    save_crowding_history, mcporter_call, output,
)


def run():
    dirs = get_strategy_dirs()
    if not dirs:
        output({"success": True, "heartbeat": "NO_REPLY", "reason": "no strategies"})
        return

    # Fetch all instruments (1 call — OI, funding, volume for everything)
    instruments = mcporter_call("market_list_instruments", timeout=15)
    if not instruments.get("success", True) if "success" in instruments else not instruments.get("data"):
        # Try to extract data even if no explicit success field
        pass

    # Normalize: Senpi returns instruments in data.instruments or as top-level array
    asset_list = []
    if isinstance(instruments, dict):
        data = instruments.get("data", instruments)
        if isinstance(data, dict):
            asset_list = data.get("instruments", data.get("assets", []))
        elif isinstance(data, list):
            asset_list = data
    elif isinstance(instruments, list):
        asset_list = instruments

    if not asset_list:
        output({"success": False, "error": "No instruments returned"})
        return

    now = datetime.now(timezone.utc).isoformat()

    # Process each strategy's crowding history
    for state_dir in dirs:
        config = load_config(state_dir)
        history = load_crowding_history(state_dir)
        max_hours = config.get("crowding", {}).get("maxHistoryHours", 48)
        max_snapshots = int(max_hours * 12)  # 5min intervals

        for asset_data in asset_list:
            try:
                name = asset_data.get("name", asset_data.get("coin", ""))
                if not name:
                    continue

                oi = asset_data.get("openInterest") or asset_data.get("oi")
                funding = asset_data.get("fundingRate") or asset_data.get("funding")
                volume = asset_data.get("dayNtlVlm") or asset_data.get("volume24h") or asset_data.get("volume")

                if oi is None:
                    continue

                oi = float(oi)
                funding = float(funding) if funding is not None else 0.0
                volume = float(volume) if volume is not None else 0.0

                # Annualize funding (8h rate → annual)
                funding_annualized = abs(funding) * 3 * 365 * 100  # as percentage

                snapshot = {
                    "timestamp": now,
                    "oi": oi,
                    "fundingRate": funding,
                    "fundingAnnualized": round(funding_annualized, 2),
                    "volume": volume,
                }

                # Append to history
                if name not in history["snapshots"]:
                    history["snapshots"][name] = []
                history["snapshots"][name].append(snapshot)

                # Prune old snapshots
                if len(history["snapshots"][name]) > max_snapshots:
                    history["snapshots"][name] = history["snapshots"][name][-max_snapshots:]

                # Update OI baselines (rolling averages)
                snapshots = history["snapshots"][name]
                oi_values = [s["oi"] for s in snapshots if s.get("oi")]

                if len(oi_values) >= 12:  # At least 1h of data
                    # 24h avg: last 288 snapshots (or all if less)
                    avg_24h = sum(oi_values[-288:]) / len(oi_values[-288:])
                    # 7d avg: all available (up to 48h)
                    avg_all = sum(oi_values) / len(oi_values)

                    if name not in history["oiBaselines"]:
                        history["oiBaselines"][name] = {}
                    history["oiBaselines"][name]["avg24h"] = round(avg_24h, 2)
                    history["oiBaselines"][name]["avg7d"] = round(avg_all, 2)
                    history["oiBaselines"][name]["current"] = oi
                    history["oiBaselines"][name]["updatedAt"] = now

            except Exception:
                continue  # Per-asset error isolation

        save_crowding_history(state_dir, history)

    output({
        "success": True,
        "heartbeat": "NO_REPLY",
        "assetsTracked": len(asset_list),
        "strategiesUpdated": len(dirs),
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        output({"success": False, "error": str(e)})
