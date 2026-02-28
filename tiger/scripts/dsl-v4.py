#!/usr/bin/env python3
"""DSL v4 — Enhanced 2-phase with configurable tier ratcheting.
Supports LONG and SHORT. Auto-closes positions on breach via mcporter.
v4 over v3:
  - Error handling: graceful degradation on API failure
  - Close retry: retries + pendingClose flag for failed closes
  - Per-tier retrace: tighten trailing stops as profit grows
  - Breach decay: soft mode (decay by 1) vs hard (reset to 0)
  - Enriched output: tier_changed, elapsed_minutes, distance_to_next_tier
Backward-compatible with v3/v2 state files (all new fields have defaults).

Units convention:
  - upnl_pct is in whole numbers (2.1 = 2.1%)
  - triggerPct/lockPct in tiers are decimals (0.05 = 5%, 0.20 = 20%)
  - Comparisons multiply decimals by 100 to match upnl_pct
"""
import json, sys, os, time
from datetime import datetime, timezone

# Use shared infra for atomic writes, mcporter, and price fetching
from tiger_config import (
    atomic_write, mcporter_call, get_prices, close_position, load_config
)

# Load config once (caches for other imports)
_config = load_config()

STATE_FILE = os.environ.get("DSL_STATE_FILE", "/data/workspace/trailing-stop-state.json")

with open(STATE_FILE) as f:
    state = json.load(f)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

if not state.get("active"):
    if not state.get("pendingClose"):
        print(json.dumps({"status": "inactive"}))
        sys.exit(0)

direction = state.get("direction", "LONG").upper()
is_long = direction == "LONG"
breach_decay_mode = state.get("breachDecay", "hard")
close_retries = state.get("closeRetries", 2)
close_retry_delay = state.get("closeRetryDelaySec", 3)
max_fetch_failures = state.get("maxFetchFailures", 10)

# ─── Fetch price via shared mcporter infra ───
try:
    prices_result = get_prices()
    prices_data = prices_result.get("data", prices_result)
    if isinstance(prices_data, dict) and "prices" in prices_data:
        mids = prices_data["prices"]
    elif isinstance(prices_data, dict):
        mids = prices_data
    else:
        mids = {}
    price = float(mids[state["asset"]])
    state["consecutiveFetchFailures"] = 0
except Exception as e:
    fails = state.get("consecutiveFetchFailures", 0) + 1
    state["consecutiveFetchFailures"] = fails
    state["lastCheck"] = now
    if fails >= max_fetch_failures:
        state["active"] = False
        state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
    atomic_write(STATE_FILE, state)
    print(json.dumps({
        "status": "error",
        "error": f"price_fetch_failed: {str(e)}",
        "asset": state.get("asset"),
        "consecutive_failures": fails,
        "deactivated": fails >= max_fetch_failures,
        "pending_close": state.get("pendingClose", False),
        "time": now
    }))
    sys.exit(1)

entry = state["entryPrice"]
size = state["size"]
hw = state["highWaterPrice"]
phase = state["phase"]
breach_count = state["currentBreachCount"]
tier_idx = state["currentTierIndex"]
tier_floor = state["tierFloorPrice"]
tiers = state["tiers"]
force_close = state.get("pendingClose", False)

# ─── uPnL ───
if is_long:
    upnl = (price - entry) * size
else:
    upnl = (entry - price) * size
margin = entry * size / state["leverage"]
upnl_pct = upnl / margin * 100  # Whole number: 2.1 = 2.1%

# ─── Update high water ───
if is_long and price > hw:
    hw = price
    state["highWaterPrice"] = hw
elif not is_long and price < hw:
    hw = price
    state["highWaterPrice"] = hw

# ─── Tier upgrades ───
# CRITICAL: triggerPct is decimal (0.05 = 5%), upnl_pct is whole (2.1 = 2.1%)
# Must multiply triggerPct by 100 to compare in same units
# lockPct is also decimal (0.20 = 20%) — do NOT divide by 100 again in floor calc
previous_tier_idx = tier_idx
tier_changed = False
for i, tier in enumerate(tiers):
    if i <= tier_idx:
        continue
    if upnl_pct >= tier["triggerPct"] * 100:
        tier_idx = i
        tier_changed = True
        if is_long:
            tier_floor = round(entry * (1 + tier["lockPct"] / state["leverage"]), 4)
        else:
            tier_floor = round(entry * (1 - tier["lockPct"] / state["leverage"]), 4)
        state["currentTierIndex"] = tier_idx
        state["tierFloorPrice"] = tier_floor
        if phase == 1:
            phase2_trigger = state.get("phase2TriggerTier", 0)
            if tier_idx >= phase2_trigger:
                phase = 2
                state["phase"] = 2
                breach_count = 0
                state["currentBreachCount"] = 0

# ─── Effective floor ───
if phase == 1:
    retrace = state["phase1"]["retraceThreshold"]
    breaches_needed = state["phase1"]["consecutiveBreachesRequired"]
    abs_floor = state["phase1"]["absoluteFloor"]
    if is_long:
        trailing_floor = round(hw * (1 - retrace), 4)
        effective_floor = max(abs_floor, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace), 4)
        effective_floor = min(abs_floor, trailing_floor)
else:
    if tier_idx >= 0:
        retrace = tiers[tier_idx].get("retrace", state["phase2"]["retraceThreshold"])
    else:
        retrace = state["phase2"]["retraceThreshold"]
    breaches_needed = state["phase2"]["consecutiveBreachesRequired"]
    if is_long:
        trailing_floor = round(hw * (1 - retrace), 4)
        effective_floor = max(tier_floor or 0, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace), 4)
        effective_floor = min(tier_floor or float('inf'), trailing_floor)

state["floorPrice"] = round(effective_floor, 4)

# ─── Breach check ───
if is_long:
    breached = price <= effective_floor
else:
    breached = price >= effective_floor

if breached:
    breach_count += 1
else:
    if breach_decay_mode == "soft":
        breach_count = max(0, breach_count - 1)
    else:
        breach_count = 0
state["currentBreachCount"] = breach_count

should_close = breach_count >= breaches_needed or force_close

# ─── Auto-close on breach (with retry via shared infra) ───
closed = False
close_result = None

if should_close:
    wallet = state.get("wallet", "")
    asset = state["asset"]
    if wallet:
        reason = f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, price {price}, floor {effective_floor}"
        # close_position already has 3-retry built in via mcporter_call
        result = close_position(wallet, asset, reason=reason)
        if isinstance(result, dict) and not result.get("error"):
            closed = True
            close_result = json.dumps(result)
            state["active"] = False
            state["pendingClose"] = False
            state["closedAt"] = now
            state["closeReason"] = reason
        else:
            close_result = f"close_failed: {result.get('error', 'unknown')}"
            state["pendingClose"] = True
    else:
        close_result = "error: no wallet in state file"
        state["pendingClose"] = True

# ─── Save state atomically ───
state["lastCheck"] = now
state["lastPrice"] = price
atomic_write(STATE_FILE, state)

# ─── Output ───
if is_long:
    retrace_from_hw = (1 - price / hw) * 100 if hw > 0 else 0
else:
    retrace_from_hw = (price / hw - 1) * 100 if hw > 0 else 0

# Display: convert decimals to whole numbers for readability
tier_name = f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']*100:.0f}%→lock {tiers[tier_idx]['lockPct']*100:.0f}%)" if tier_idx >= 0 else "None"

previous_tier_name = None
if tier_changed:
    if previous_tier_idx >= 0:
        t = tiers[previous_tier_idx]
        previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']*100:.0f}%→lock {t['lockPct']*100:.0f}%)"
    else:
        previous_tier_name = "None (Phase 1)"

if tier_floor:
    locked_profit = round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)
else:
    locked_profit = 0

elapsed_minutes = 0
if state.get("createdAt"):
    try:
        created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
        elapsed_minutes = round((datetime.now(timezone.utc) - created).total_seconds() / 60)
    except (ValueError, TypeError):
        pass

distance_to_next_tier = None
next_tier_idx = tier_idx + 1
if next_tier_idx < len(tiers):
    distance_to_next_tier = round(tiers[next_tier_idx]["triggerPct"] * 100 - upnl_pct, 2)

print(json.dumps({
    "status": "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active"),
    "asset": state["asset"], "direction": direction,
    "price": price, "upnl": round(upnl, 2), "upnl_pct": round(upnl_pct, 2),
    "phase": phase, "hw": hw, "floor": effective_floor,
    "trailing_floor": trailing_floor, "tier_floor": tier_floor,
    "tier_name": tier_name, "locked_profit": locked_profit,
    "retrace_pct": round(retrace_from_hw, 2),
    "breach_count": breach_count, "breaches_needed": breaches_needed,
    "breached": breached, "should_close": should_close,
    "closed": closed, "close_result": close_result, "time": now,
    "tier_changed": tier_changed, "previous_tier": previous_tier_name,
    "elapsed_minutes": elapsed_minutes,
    "distance_to_next_tier_pct": distance_to_next_tier,
    "pending_close": state.get("pendingClose", False),
    "consecutive_failures": state.get("consecutiveFetchFailures", 0)
}))
