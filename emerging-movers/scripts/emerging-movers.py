import json
lev = json.load(open('/data/workspace/max-leverage.json'))
max_lev = lev.get(asset, 0)
if max_lev < 10:
    # SKIP — can't size properly at low leverage
    pass
```
```python
#!/usr/bin/env python3
"""Emerging Movers Detector v3
Tracks SM market concentration rank changes over time.
Flags assets accelerating up the ranks EARLY — catch at #50→#20, not #5.

v3 changes (from v2):
- IMMEDIATE_MOVER signal: trigger on FIRST big jump (10+ ranks from #25+ in ONE scan)
- Don't wait for confirmation — act on the first appearance of a big move
- NEW_ENTRY_DEEP: assets appearing in top 20 for first time get highest priority
- Contribution explosion detection: 3x+ contrib increase in one scan
- Run every 60s instead of 3min for faster detection

v3.1 changes (2026-02-23 — from session learnings):
- Erratic rank history filter: skip assets with >5 rank reversals (zigzag = noise)
- Minimum velocity gate: contribVelocity < +0.03 → exclude from IMMEDIATE signals
- Both filters add "erratic" and "lowVelocity" flags to output for transparency

v2 changes (from v1):
- Track top 50 markets (was 25)
- DEEP_CLIMBER signal: assets jumping 10+ ranks from #30-50 range
- Lower alert threshold for deep movers (rank #15-50)
- Separate "early" vs "confirmed" signals

Uses: leaderboard_get_markets (single API call)
"""
import json, subprocess, sys, os
from datetime import datetime, timezone

HISTORY_FILE = os.environ.get("EMERGING_HISTORY", "/data/workspace/emerging-movers-history.json")
MAX_HISTORY = 60
TOP_N = 50  # v2: track top 50 (was 25)
RANK_CLIMB_THRESHOLD = 3
CONTRIBUTION_ACCEL_THRESHOLD = 0.003
MIN_SCANS_FOR_TREND = 2
MIN_VELOCITY_FOR_IMMEDIATE = 0.0003  # 0.03 in percentage terms (contribVelocity * 100 >= 0.03)
ERRATIC_REVERSAL_THRESHOLD = 5  # rank reversal > 5 = zigzag noise

# ─── Load history ───
try:
    with open(HISTORY_FILE) as f:
        history = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    history = {"scans": []}

# ─── Fetch current market concentration ───
try:
    r = subprocess.run(
        ["mcporter", "call", "senpi", "leaderboard_get_markets", "limit=100"],
        capture_output=True, text=True, timeout=30
    )
    result = json.loads(r.stdout)
    if not result.get("success"):
        print(json.dumps({"status": "error", "error": "API call failed", "detail": r.stdout[:500]}))
        sys.exit(1)
    
    raw_markets = result["data"]["markets"]["markets"]
except Exception as e:
    print(json.dumps({"status": "error", "error": str(e)}))
    sys.exit(1)

# ─── Parse current scan ───
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
current_scan = {
    "time": now,
    "markets": []
}

for i, m in enumerate(raw_markets[:TOP_N]):
    current_scan["markets"].append({
        "token": m["token"],
        "dex": m.get("dex", ""),
        "rank": i + 1,
        "direction": m["direction"],
        "contribution": round(m["pct_of_top_traders_gain"], 6),
        "traders": m["trader_count"],
        "price_chg_4h": round(m.get("token_price_change_pct_4h") or 0, 4)
    })

# ─── Analyze trends ───
alerts = []
prev_scans = history["scans"]

if len(prev_scans) >= MIN_SCANS_FOR_TREND:
    def get_market_in_scan(scan, token, dex=""):
        for m in scan["markets"]:
            if m["token"] == token and m.get("dex", "") == dex:
                return m
        return None
    
    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]
    
    for market in current_scan["markets"]:
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        current_contrib = market["contribution"]
        
        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)
        
        alert_reasons = []
        is_deep_climber = False
        is_immediate = False  # v3: first-jump signal, highest priority
        
        # 1. Fresh entry — wasn't in top 50 last scan
        if prev_market is None:
            if current_rank <= 20:
                alert_reasons.append(f"NEW_ENTRY_DEEP at rank #{current_rank}")
                is_deep_climber = True
                is_immediate = True
            elif current_rank <= 35:
                alert_reasons.append(f"NEW_ENTRY at rank #{current_rank}")
        
        # 2. Rank climbing (single scan) — v3: IMMEDIATE_MOVER detection
        if prev_market:
            rank_change_1 = prev_market["rank"] - current_rank
            if rank_change_1 >= 2:
                alert_reasons.append(f"RANK_UP +{rank_change_1} (#{prev_market['rank']}→#{current_rank})")
            # v3: IMMEDIATE_MOVER — big single-scan jump from deep. THIS is the entry signal.
            if rank_change_1 >= 10 and prev_market["rank"] >= 25:
                is_deep_climber = True
                is_immediate = True
                alert_reasons.append(f"IMMEDIATE_MOVER +{rank_change_1} from #{prev_market['rank']} in ONE scan")
            elif rank_change_1 >= 5 and prev_market["rank"] >= 25:
                is_deep_climber = True
                alert_reasons.append(f"DEEP_CLIMBER +{rank_change_1} from #{prev_market['rank']}")
        
        # 3. Contribution explosion (v3) — 3x+ increase in one scan
        if prev_market and prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                alert_reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x in one scan ({prev_market['contribution']*100:.2f}→{current_contrib*100:.2f})")
                if prev_market["rank"] >= 20:
                    is_immediate = True
                    is_deep_climber = True
        
        # 4. Multi-scan climb
        if old_market:
            rank_change_total = old_market["rank"] - current_rank
            if rank_change_total >= RANK_CLIMB_THRESHOLD:
                alert_reasons.append(f"CLIMBING +{rank_change_total} over {min(len(prev_scans), 5)} scans")
            # v2: massive multi-scan climb from deep
            if rank_change_total >= 10 and old_market["rank"] >= 30:
                is_deep_climber = True
                if not any("DEEP_CLIMBER" in r for r in alert_reasons) and not any("IMMEDIATE" in r for r in alert_reasons):
                    alert_reasons.append(f"DEEP_CLIMBER +{rank_change_total} from #{old_market['rank']} over {min(len(prev_scans), 5)} scans")
        
        # 5. Contribution acceleration
        if prev_market:
            contrib_delta = current_contrib - prev_market["contribution"]
            if contrib_delta >= CONTRIBUTION_ACCEL_THRESHOLD:
                alert_reasons.append(f"ACCEL +{contrib_delta:.3f} contribution")
        
        # 5. Consistent climb streak
        if len(prev_scans) >= 3:
            ranks = []
            for scan in prev_scans[-3:]:
                m = get_market_in_scan(scan, token, dex)
                if m:
                    ranks.append(m["rank"])
                else:
                    ranks.append(TOP_N + 1)
            ranks.append(current_rank)
            
            if all(ranks[i] >= ranks[i+1] for i in range(len(ranks)-1)) and ranks[0] > ranks[-1]:
                streak = ranks[0] - ranks[-1]
                if streak >= 2:
                    alert_reasons.append(f"STREAK climbing {streak} ranks over 4 checks")
        
        # Calculate contribution velocity
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i+1] - recent_contribs[i] for i in range(len(recent_contribs)-1)]
            contrib_velocity = sum(deltas) / len(deltas)
            
            if contrib_velocity > 0.002 and len(recent_contribs) >= 3 and not alert_reasons:
                alert_reasons.append(f"VELOCITY +{contrib_velocity*100:.3f}%/scan sustained")
        
        if alert_reasons:
            # Build history arrays
            contrib_history = []
            rank_history = []
            for scan in prev_scans[-5:]:
                m = get_market_in_scan(scan, token, dex)
                if m:
                    contrib_history.append(round(m["contribution"] * 100, 2))
                    rank_history.append(m["rank"])
                else:
                    contrib_history.append(None)
                    rank_history.append(None)
            contrib_history.append(round(current_contrib * 100, 2))
            rank_history.append(current_rank)
            
            dir_label = market["direction"].upper()
            alerts.append({
                "token": token,
                "dex": dex if dex else None,
                "signal": f"{token} {dir_label}",
                "direction": dir_label,
                "currentRank": current_rank,
                "contribution": round(current_contrib * 100, 3),
                "contribVelocity": round(contrib_velocity * 100, 4),
                "traders": market["traders"],
                "priceChg4h": market["price_chg_4h"],
                "reasons": alert_reasons,
                "reasonCount": len(alert_reasons),
                "rankHistory": rank_history,
                "contribHistory": contrib_history,
                "isDeepClimber": is_deep_climber,
                "isImmediate": is_immediate
            })

# ─── v3.1: Erratic rank history filter + velocity gate ───
def is_erratic_history(rank_history):
    """Detect zigzag rank patterns. A reversal > ERRATIC_REVERSAL_THRESHOLD = noise."""
    nums = [r for r in rank_history if r is not None]
    if len(nums) < 3:
        return False
    for i in range(1, len(nums) - 1):
        prev_delta = nums[i] - nums[i-1]  # negative = climbing
        next_delta = nums[i+1] - nums[i]
        # Was climbing (neg) then dropped (pos) by > threshold
        if prev_delta < 0 and next_delta > ERRATIC_REVERSAL_THRESHOLD:
            return True
        # Was dropping (pos) then climbed (neg) by > threshold
        if prev_delta > 0 and next_delta < -ERRATIC_REVERSAL_THRESHOLD:
            return True
    return False

for alert in alerts:
    rh = alert.get("rankHistory", [])
    erratic = is_erratic_history(rh)
    low_vel = alert.get("contribVelocity", 0) < (MIN_VELOCITY_FOR_IMMEDIATE * 100)  # compare in pct terms
    
    alert["erratic"] = erratic
    alert["lowVelocity"] = low_vel
    
    # Downgrade: erratic or low velocity IMMEDIATEs lose their immediate status
    if alert.get("isImmediate") and (erratic or low_vel):
        alert["isImmediate"] = False
        if erratic:
            alert["reasons"].append("⚠️ DOWNGRADED: erratic rank history (zigzag)")
        if low_vel:
            alert["reasons"].append(f"⚠️ DOWNGRADED: low velocity ({alert['contribVelocity']:.4f} < 0.03)")

# ─── Save history ───
history["scans"].append(current_scan)
if len(history["scans"]) > MAX_HISTORY:
    history["scans"] = history["scans"][-MAX_HISTORY:]

with open(HISTORY_FILE, "w") as f:
    json.dump(history, f, indent=2)

# ─── Output ───
alerts.sort(key=lambda a: (a.get("isImmediate", False), a.get("isDeepClimber", False), abs(a.get("contribVelocity", 0)), len(a["reasons"])), reverse=True)

output = {
    "status": "ok",
    "time": now,
    "totalMarkets": len(current_scan["markets"]),
    "scansInHistory": len(history["scans"]),
    "alerts": alerts,
    "immediateMovers": [a for a in alerts if a.get("isImmediate")],
    "deepClimbers": [a for a in alerts if a.get("isDeepClimber")],
    "hasImmediate": any(a.get("isImmediate") for a in alerts),
    "hasEmergingMover": len(alerts) > 0,
    "hasDeepClimber": any(a.get("isDeepClimber") for a in alerts),
    "top5": [
        {"signal": f"{m['token']} {m['direction'].upper()}", "rank": m["rank"],
         "contribution": round(m["contribution"]*100, 2), "traders": m["traders"],
         "priceChg4h": m["price_chg_4h"]}
        for m in current_scan["markets"][:5]
    ]
}

print(json.dumps(output))
