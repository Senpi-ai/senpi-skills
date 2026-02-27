#!/usr/bin/env python3
"""DSL v4/v5 — Thin cron entry. Delegates to dsl_engine (canonical logic).

Single-position mode: DSL_STATE_FILE env or default. Fetches price via dsl_common,
runs dsl_engine.process_position(), saves state atomically, prints JSON.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsl_common import (
    get_state_path,
    load_state,
    save_state,
    fetch_mid_price,
    is_xyz_state,
    close_position as close_position_mcp,
)
from dsl_engine import process_position

STATE_FILE = get_state_path()
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

state = load_state(STATE_FILE)
if not state.get("active") and not state.get("pendingClose"):
    print(json.dumps({"status": "inactive"}))
    sys.exit(0)

asset_name = state.get("asset", "")
dex = "xyz" if is_xyz_state(state) else None
try:
    price = fetch_mid_price(asset_name, dex=dex)
    if price is None:
        raise Exception(
            f"Asset {asset_name} not found in market_get_prices" + (" (dex=xyz)" if dex else "")
        )
    state["consecutiveFetchFailures"] = 0
except Exception as e:
    fails = state.get("consecutiveFetchFailures", 0) + 1
    state["consecutiveFetchFailures"] = fails
    state["lastCheck"] = now
    max_ff = state.get("maxFetchFailures", 10)
    if fails >= max_ff:
        state["active"] = False
        state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
    save_state(STATE_FILE, state)
    print(
        json.dumps({
            "status": "error",
            "error": f"price_fetch_failed: {str(e)}",
            "asset": state.get("asset"),
            "consecutive_failures": fails,
            "deactivated": fails >= max_ff,
            "pending_close": state.get("pendingClose", False),
            "time": now,
        })
    )
    sys.exit(1)


def close_fn(wallet: str, coin: str, reason: str):
    return close_position_mcp(
        wallet, coin, reason,
        retries=state.get("closeRetries", 2),
        retry_delay_sec=state.get("closeRetryDelaySec", 3),
    )


result = process_position(
    state,
    price,
    now_iso=now,
    close_fn=close_fn,
)
save_state(STATE_FILE, state)

# Output: same shape as before for agent compatibility
out = result.to_dict()
# Normalize status for agent: active -> active, closed -> inactive, pending_close -> pending_close
if result.status == "closed":
    out["status"] = "inactive"
elif result.status == "active":
    out["status"] = "active"
out["close_result"] = result.close_result
print(json.dumps(out))
