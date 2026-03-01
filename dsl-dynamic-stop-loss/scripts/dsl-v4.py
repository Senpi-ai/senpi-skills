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
"""
import json, sys, subprocess, os, time
from datetime import datetime, timezone

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

# ─── Fetch price via mcporter ───
try:
    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    r = subprocess.run(
        [mcporter_bin, "call", "senpi.market_get_prices"],
        capture_output=True, text=True, timeout=20
    )
    data = json.loads(r.stdout)
    prices = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(prices, dict) and "prices" in prices:
        prices = prices["prices"]
    price = float(prices[state["asset"]])
    state["consecutiveFetchFailures"] = 0
except Exception as e:
    fails = state.get("consecutiveFetchFailures", 0) + 1
    state["consecutiveFetchFailures"] = fails
    state["lastCheck"] = now
    if fails >= max_fetch_failures:
        state["active"] = False
        state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
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
hw = state.get("highWaterPrice", entry)
if "highWaterPrice" not in state:
    state["highWaterPrice"] = hw
phase = state.get("phase", 1)
breach_count = state.get("currentBreachCount", 0)
tier_idx = state.get("currentTierIndex", -1)
tier_floor = state.get("tierFloorPrice", 0)
tiers = state.get("tiers", [])
force_close = state.get("pendingClose", False)

_default_phase1 = {"retraceThreshold": 0.02, "consecutiveBreachesRequired": 3,
                   "absoluteFloor": round(entry * 0.98, 4) if is_long else round(entry * 1.02, 4)}
_default_phase2 = {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 2}
if "phase1" not in state:
    state["phase1"] = _default_phase1
if "phase2" not in state:
    state["phase2"] = _default_phase2

# ─── uPnL ───
if is_long:
    upnl = (price - entry) * size
else:
    upnl = (entry - price) * size
margin = entry * size / state["leverage"]
upnl_pct = upnl / margin * 100

# ─── Update high water ───
if is_long and price > hw:
    hw = price
    state["highWaterPrice"] = hw
elif not is_long and price < hw:
    hw = price
    state["highWaterPrice"] = hw

# ─── Tier upgrades ───
previous_tier_idx = tier_idx
tier_changed = False
for i, tier in enumerate(tiers):
    if i <= tier_idx:
        continue
    if upnl_pct >= tier["triggerPct"]:
        tier_idx = i
        tier_changed = True
        if is_long:
            tier_floor = round(entry * (1 + tier["lockPct"] / 100 / state["leverage"]), 4)
        else:
            tier_floor = round(entry * (1 - tier["lockPct"] / 100 / state["leverage"]), 4)
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

# ─── Auto-close on breach (with retry) ───
closed = False
close_result = None

if should_close:
    wallet = state.get("wallet", "")
    asset = state["asset"]
    if wallet:
        for attempt in range(close_retries):
            try:
                cr = subprocess.run(
                    [os.environ.get("MCPORTER_CMD", "mcporter"), "call", "senpi", "close_position", "--args",
                     json.dumps({
                         "strategyWalletAddress": wallet,
                         "coin": asset,
                         "reason": f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, price {price}, floor {effective_floor}"
                     })],
                    capture_output=True, text=True, timeout=30
                )
                result_text = cr.stdout.strip()
                if cr.returncode == 0 and "error" not in result_text.lower():
                    closed = True
                    close_result = result_text
                    state["active"] = False
                    state["pendingClose"] = False
                    state["closedAt"] = now
                    state["closeReason"] = f"DSL breach: Phase {phase}, price {price}, floor {effective_floor}"
                    break
                else:
                    close_result = f"api_error_attempt_{attempt+1}: {result_text}"
            except Exception as e:
                close_result = f"error_attempt_{attempt+1}: {str(e)}"
            if attempt < close_retries - 1:
                time.sleep(close_retry_delay)
        if not closed:
            state["pendingClose"] = True
    else:
        close_result = "error: no wallet in state file"
        state["pendingClose"] = True

# ─── Save state ───
state["lastCheck"] = now
state["lastPrice"] = price
with open(STATE_FILE, "w") as f:
    json.dump(state, f, indent=2)

# ─── Output ───
if is_long:
    retrace_from_hw = (1 - price / hw) * 100 if hw > 0 else 0
else:
    retrace_from_hw = (price / hw - 1) * 100 if hw > 0 else 0

tier_name = f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%→lock {tiers[tier_idx]['lockPct']}%)" if tier_idx >= 0 else "None"

previous_tier_name = None
if tier_changed:
    if previous_tier_idx >= 0:
        t = tiers[previous_tier_idx]
        previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']}%→lock {t['lockPct']}%)"
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
    distance_to_next_tier = round(tiers[next_tier_idx]["triggerPct"] - upnl_pct, 2)

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
