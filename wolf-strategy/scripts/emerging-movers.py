#!/usr/bin/env python3
"""Emerging Movers Detector v4
Tracks SM market concentration rank changes over time.
Flags assets accelerating up the ranks EARLY — catch at #50→#20, not #5.

v4 changes (WOLF v5 — 2026-02-24):
- FIRST_JUMP signal: highest priority. Asset jumps 10+ ranks from #25+ AND was not
  in previous scan's top 50 (or was >= #30). Enter before confirmation.
- Erratic logic reworked: only check history BEFORE the current jump. A 10+ rank
  jump THIS scan is the signal, not noise. CONTRIB_EXPLOSION never downgraded.
- Velocity gate lowered for IMMEDIATEs: vel > 0 is enough (velocity hasn't built yet).
  Keep vel >= 0.03 for DEEP_CLIMBER signals only.
- Scanner interval changed to 90s (from 60s) to reduce token burn.

v3.1 changes (2026-02-23):
- Erratic rank history filter (now reworked in v4)
- Minimum velocity gate (now split by signal type in v4)

v3 changes:
- IMMEDIATE_MOVER signal: trigger on FIRST big jump (10+ ranks from #25+ in ONE scan)
- NEW_ENTRY_DEEP: assets appearing in top 20 for first time get highest priority
- Contribution explosion detection: 3x+ contrib increase in one scan

v2 changes:
- Track top 50 markets (was 25)
- DEEP_CLIMBER signal: assets jumping 10+ ranks from #30-50 range

Uses: leaderboard_get_markets (single API call)
"""
import json, subprocess, sys, os, time
from datetime import datetime, timezone

HISTORY_FILE = os.environ.get("EMERGING_HISTORY", "/data/workspace/emerging-movers-history.json")
MAX_HISTORY = 60
TOP_N = 50
RANK_CLIMB_THRESHOLD = 3
CONTRIBUTION_ACCEL_THRESHOLD = 0.003
MIN_SCANS_FOR_TREND = 2
MIN_VELOCITY_FOR_DEEP_CLIMBER = 0.0003  # 0.03% — only applies to DEEP_CLIMBER
ERRATIC_REVERSAL_THRESHOLD = 5

# ─── Load history ───
try:
    with open(HISTORY_FILE) as f:
        history = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    history = {"scans": []}

# ─── Fetch current market concentration (3 retries) ───
raw_markets = None
for _attempt in range(3):
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "leaderboard_get_markets", "limit=100"],
            capture_output=True, text=True, timeout=30
        )
        result = json.loads(r.stdout)
        if not result.get("success"):
            raise ValueError(f"API call failed: {r.stdout[:500]}")
        raw_markets = result["data"]["markets"]["markets"]
        break
    except Exception as e:
        if _attempt < 2:
            time.sleep(3)
        else:
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

# ─── Helper: previous scan token set ───
prev_scans = history["scans"]
prev_top50_tokens = set()
if prev_scans:
    for m in prev_scans[-1]["markets"]:
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

def get_market_in_scan(scan, token, dex=""):
    for m in scan["markets"]:
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None

# ─── Analyze trends ───
alerts = []

if len(prev_scans) >= MIN_SCANS_FOR_TREND:
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
        is_immediate = False
        is_first_jump = False
        is_contrib_explosion = False
        rank_jump_this_scan = 0  # how many ranks jumped THIS scan

        # 1. Fresh entry — wasn't in top 50 last scan
        if prev_market is None:
            if current_rank <= 20:
                alert_reasons.append(f"NEW_ENTRY_DEEP at rank #{current_rank}")
                is_deep_climber = True
                is_immediate = True
            elif current_rank <= 35:
                alert_reasons.append(f"NEW_ENTRY at rank #{current_rank}")

        # 2. Rank climbing (single scan) — IMMEDIATE_MOVER / FIRST_JUMP detection
        if prev_market:
            rank_change_1 = prev_market["rank"] - current_rank
            rank_jump_this_scan = rank_change_1
            if rank_change_1 >= 2:
                alert_reasons.append(f"RANK_UP +{rank_change_1} (#{prev_market['rank']}→#{current_rank})")
            # IMMEDIATE_MOVER — big single-scan jump from deep
            if rank_change_1 >= 10 and prev_market["rank"] >= 25:
                is_deep_climber = True
                is_immediate = True
                alert_reasons.append(f"IMMEDIATE_MOVER +{rank_change_1} from #{prev_market['rank']} in ONE scan")
                # FIRST_JUMP: was not in previous top 50 or was >= #30
                was_in_prev = (token, dex) in prev_top50_tokens
                if not was_in_prev or prev_market["rank"] >= 30:
                    is_first_jump = True
                    alert_reasons.append(f"FIRST_JUMP from #{prev_market['rank']}→#{current_rank} — highest priority")
            elif rank_change_1 >= 5 and prev_market["rank"] >= 25:
                is_deep_climber = True
                alert_reasons.append(f"DEEP_CLIMBER +{rank_change_1} from #{prev_market['rank']}")

        # 3. Contribution explosion — 3x+ increase in one scan
        if prev_market and prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                alert_reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x in one scan ({prev_market['contribution']*100:.2f}→{current_contrib*100:.2f})")
                is_contrib_explosion = True
                if prev_market["rank"] >= 20:
                    is_immediate = True
                    is_deep_climber = True

        # 4. Multi-scan climb
        if old_market:
            rank_change_total = old_market["rank"] - current_rank
            if rank_change_total >= RANK_CLIMB_THRESHOLD:
                alert_reasons.append(f"CLIMBING +{rank_change_total} over {min(len(prev_scans), 5)} scans")
            if rank_change_total >= 10 and old_market["rank"] >= 30:
                is_deep_climber = True
                if not any("DEEP_CLIMBER" in r for r in alert_reasons) and not any("IMMEDIATE" in r for r in alert_reasons):
                    alert_reasons.append(f"DEEP_CLIMBER +{rank_change_total} from #{old_market['rank']} over {min(len(prev_scans), 5)} scans")

        # 5. Contribution acceleration
        if prev_market:
            contrib_delta = current_contrib - prev_market["contribution"]
            if contrib_delta >= CONTRIBUTION_ACCEL_THRESHOLD:
                alert_reasons.append(f"ACCEL +{contrib_delta:.3f} contribution")

        # 6. Consistent climb streak
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
                "isImmediate": is_immediate,
                "isFirstJump": is_first_jump,
                "isContribExplosion": is_contrib_explosion,
                "rankJumpThisScan": rank_jump_this_scan
            })

# ─── v4: Reworked erratic + velocity filters ───
def is_erratic_history(rank_history, exclude_last=False):
    """Detect zigzag rank patterns. A reversal > ERRATIC_REVERSAL_THRESHOLD = noise.
    If exclude_last=True, only check history BEFORE the final entry (current scan)."""
    nums = [r for r in rank_history if r is not None]
    if exclude_last and len(nums) > 1:
        nums = nums[:-1]  # exclude the current jump from erratic check
    if len(nums) < 3:
        return False
    for i in range(1, len(nums) - 1):
        prev_delta = nums[i] - nums[i-1]
        next_delta = nums[i+1] - nums[i]
        if prev_delta < 0 and next_delta > ERRATIC_REVERSAL_THRESHOLD:
            return True
        if prev_delta > 0 and next_delta < -ERRATIC_REVERSAL_THRESHOLD:
            return True
    return False

for alert in alerts:
    rh = alert.get("rankHistory", [])
    vel = alert.get("contribVelocity", 0)
    is_imm = alert.get("isImmediate", False)
    is_fj = alert.get("isFirstJump", False)
    is_ce = alert.get("isContribExplosion", False)
    big_jump = alert.get("rankJumpThisScan", 0) >= 10

    # --- Erratic check ---
    # CONTRIB_EXPLOSION: NEVER downgrade for erratic
    # IMMEDIATE with big jump this scan: only check history BEFORE the jump
    # Others: check full history
    if is_ce:
        erratic = False  # contrib spike IS the signal
    elif big_jump or is_fj:
        erratic = is_erratic_history(rh, exclude_last=True)  # only pre-jump history
    else:
        erratic = is_erratic_history(rh, exclude_last=False)

    # --- Velocity gate ---
    # IMMEDIATE / FIRST_JUMP: vel > 0 is enough (hasn't had time to build)
    # DEEP_CLIMBER (non-immediate): vel >= 0.03 required
    if is_imm or is_fj:
        low_vel = vel <= 0  # just needs to be positive
    else:
        low_vel = vel < (MIN_VELOCITY_FOR_DEEP_CLIMBER * 100)  # 0.03%

    alert["erratic"] = erratic
    alert["lowVelocity"] = low_vel

    # Downgrade logic — only for non-FIRST_JUMP, non-CONTRIB_EXPLOSION
    if is_fj:
        pass  # NEVER downgrade FIRST_JUMP — this is THE signal
    elif is_ce:
        pass  # NEVER downgrade CONTRIB_EXPLOSION
    elif is_imm and (erratic or low_vel):
        alert["isImmediate"] = False
        if erratic:
            alert["reasons"].append("⚠️ DOWNGRADED: erratic rank history (zigzag in pre-jump history)")
        if low_vel:
            alert["reasons"].append(f"⚠️ DOWNGRADED: non-positive velocity ({alert['contribVelocity']:.4f})")
    elif not is_imm and alert.get("isDeepClimber") and low_vel:
        alert["reasons"].append(f"⚠️ LOW_VEL: velocity {alert['contribVelocity']:.4f} < 0.03 for DEEP_CLIMBER")

# ─── Save history ───
history["scans"].append(current_scan)
if len(history["scans"]) > MAX_HISTORY:
    history["scans"] = history["scans"][-MAX_HISTORY:]

with open(HISTORY_FILE, "w") as f:
    json.dump(history, f, indent=2)

# ─── Output ───
# Sort: first_jump > immediate > deep climber > velocity > reason count
alerts.sort(key=lambda a: (
    a.get("isFirstJump", False),
    a.get("isImmediate", False),
    a.get("isContribExplosion", False),
    a.get("isDeepClimber", False),
    abs(a.get("contribVelocity", 0)),
    len(a["reasons"])
), reverse=True)

output = {
    "status": "ok",
    "time": now,
    "totalMarkets": len(current_scan["markets"]),
    "scansInHistory": len(history["scans"]),
    "alerts": alerts,
    "firstJumps": [a for a in alerts if a.get("isFirstJump")],
    "immediateMovers": [a for a in alerts if a.get("isImmediate")],
    "contribExplosions": [a for a in alerts if a.get("isContribExplosion")],
    "deepClimbers": [a for a in alerts if a.get("isDeepClimber")],
    "hasFirstJump": any(a.get("isFirstJump") for a in alerts),
    "hasImmediate": any(a.get("isImmediate") for a in alerts),
    "hasContribExplosion": any(a.get("isContribExplosion") for a in alerts),
    "hasEmergingMover": len(alerts) > 0,
    "hasDeepClimber": any(a.get("isDeepClimber") for a in alerts),
}

print(json.dumps(output))
