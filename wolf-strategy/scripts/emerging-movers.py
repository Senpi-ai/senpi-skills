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
- Scanner interval changed to 3min (from 60s) to reduce token burn.

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
import json, sys, os
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import atomic_write, mcporter_call, mcporter_call_safe, load_all_strategies, dsl_state_glob, heartbeat, check_gate, SIGNAL_CONVICTION, WORKSPACE, ROTATION_COOLDOWN_MINUTES

heartbeat("emerging_movers")

# ─── Trend Guard integration (optional — degrades gracefully if not installed) ───
_TREND_GUARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'trend-guard', 'scripts')
try:
    sys.path.insert(0, os.path.abspath(_TREND_GUARD_DIR))
    from trend_guard import classify_trend as _classify_trend
    TREND_GUARD_AVAILABLE = True
except ImportError:
    TREND_GUARD_AVAILABLE = False

# Load trend cache from disk once at startup (written hourly by trend-guard batch cron)
TREND_CACHE_FILE = os.environ.get("TREND_CACHE", os.path.join(WORKSPACE, "trend-cache.json"))
try:
    with open(TREND_CACHE_FILE) as _f:
        TREND_CACHE = json.load(_f)
except (FileNotFoundError, json.JSONDecodeError):
    TREND_CACHE = {}

def _now_utc():
    return datetime.now(timezone.utc)

def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def check_trend_alignment(asset, direction):
    """
    Three-layer trend check for an asset + direction.

    Returns (aligned: bool, trend_meta: dict)

    Layer 1 — fresh cache (≤62 min): 0ms, use cached value.
    Layer 2 — stale/missing cache: live MCP fetch for this one asset (~2-3s).
    Layer 3 — MCP completely down: block the entry (return False), never trade blind.
    """
    cached = TREND_CACHE.get(asset)
    if cached and "computedAt" in cached:
        try:
            age_sec = (_now_utc() - _parse_iso(cached["computedAt"])).total_seconds()
            if age_sec <= 3720:  # 62 minutes
                return _evaluate_alignment(cached, direction), cached
        except (ValueError, TypeError):
            pass  # bad timestamp — fall through to live fetch

    # Cache stale or missing — live fetch for this asset
    if TREND_GUARD_AVAILABLE:
        try:
            fresh = _classify_trend(asset)
            return _evaluate_alignment(fresh, direction), fresh
        except Exception:
            pass  # MCP down — fall through to block

    # MCP down or trend-guard not installed and cache stale — block entry
    return False, {"error": "trend_unavailable", "asset": asset}

def _evaluate_alignment(trend_data, direction):
    """Return True if trend is aligned with direction, or NEUTRAL (don't block uncertainty)."""
    trend = trend_data.get("trend", "NEUTRAL")
    if trend == "NEUTRAL":
        return True
    d = (direction or "").upper()
    if d == "LONG":
        return trend == "UP"
    if d == "SHORT":
        return trend == "DOWN"
    return True  # unknown direction — don't block

# ─── Sector cluster tags (4c) ───
_CLUSTERS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'emerging-movers', 'references', 'sector-clusters.json'
)
try:
    with open(os.path.abspath(_CLUSTERS_FILE)) as _cf:
        _CLUSTERS_RAW = json.load(_cf)
except (FileNotFoundError, json.JSONDecodeError):
    _CLUSTERS_RAW = {}

# Build reverse lookup: token -> cluster
_TOKEN_CLUSTER = {}
for _cluster, _tokens in _CLUSTERS_RAW.items():
    if _cluster == "_unclustered":
        continue
    for _t in _tokens:
        _TOKEN_CLUSTER[_t] = _cluster

def get_cluster(asset):
    """Return the sector cluster for an asset token."""
    token = asset.replace("xyz:", "").upper()
    return _TOKEN_CLUSTER.get(token, "_unclustered")

HISTORY_FILE = os.environ.get("EMERGING_HISTORY", "/data/workspace/emerging-movers-history.json")
MAX_LEV_FILE = os.path.join(WORKSPACE, "max-leverage.json")

# Load max-leverage data (file-based, no API call — speed critical for 3min scanner)
try:
    with open(MAX_LEV_FILE) as f:
        MAX_LEV_DATA = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    MAX_LEV_DATA = {}
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

# ─── Fetch current market concentration ───
try:
    data = mcporter_call("leaderboard_get_markets", limit=100)
    raw_markets = data["markets"]["markets"]
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

# ─── SM Exit Detection (4b) ───
exit_alerts = []
if prev_scans:
    prev_scan = prev_scans[-1]
    # Build lookup dicts: (token, dex) -> market entry
    prev_by_key = {(m["token"], m.get("dex", "")): m for m in prev_scan["markets"]}
    curr_by_key = {(m["token"], m.get("dex", "")): m for m in current_scan["markets"]}

    # Assets that dropped out of top 50 entirely
    for key, prev_m in prev_by_key.items():
        if key not in curr_by_key:
            token, dex = key
            exit_alerts.append({
                "asset": f"xyz:{token}" if dex == "xyz" else token,
                "token": token,
                "dex": dex if dex else None,
                "signal": "SM_EXIT",
                "exitType": "DROPPED_OUT",
                "prevRank": prev_m["rank"],
                "prevTraders": prev_m.get("traders", 0),
            })

    # Assets still in top 50 but with 50%+ trader exodus
    for key, prev_m in prev_by_key.items():
        if key not in curr_by_key:
            continue  # already captured above
        curr_m = curr_by_key[key]
        prev_traders = prev_m.get("traders", 0)
        curr_traders = curr_m.get("traders", 0)
        if prev_traders > 200 and curr_traders < prev_traders * 0.5:
            token, dex = key
            exit_alerts.append({
                "asset": f"xyz:{token}" if dex == "xyz" else token,
                "token": token,
                "dex": dex if dex else None,
                "signal": "SM_EXIT",
                "exitType": "TRADER_EXODUS",
                "currRank": curr_m["rank"],
                "prevTraders": prev_traders,
                "currTraders": curr_traders,
                "dropPct": round((1 - curr_traders / prev_traders) * 100, 1),
            })

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

            # Determine max leverage from file-based cache
            lev_key = f"xyz:{token}" if dex else token
            alert_max_lev = MAX_LEV_DATA.get(lev_key) or MAX_LEV_DATA.get(token)

            # Map signal type to conviction
            if is_first_jump:
                alert_conviction = SIGNAL_CONVICTION["FIRST_JUMP"]
            elif is_contrib_explosion:
                alert_conviction = SIGNAL_CONVICTION["CONTRIB_EXPLOSION"]
            elif is_immediate:
                alert_conviction = SIGNAL_CONVICTION["IMMEDIATE_MOVER"]
            elif is_deep_climber and any("NEW_ENTRY_DEEP" in r for r in alert_reasons):
                alert_conviction = SIGNAL_CONVICTION["NEW_ENTRY_DEEP"]
            elif is_deep_climber:
                alert_conviction = SIGNAL_CONVICTION["DEEP_CLIMBER"]
            else:
                alert_conviction = 0.5

            # Signal type label + numeric priority (1=highest)
            if is_first_jump:
                signal_type, signal_priority = "FIRST_JUMP", 1
            elif is_contrib_explosion:
                signal_type, signal_priority = "CONTRIB_EXPLOSION", 2
            elif is_immediate:
                signal_type, signal_priority = "IMMEDIATE_MOVER", 3
            elif is_deep_climber and any("NEW_ENTRY_DEEP" in r for r in alert_reasons):
                signal_type, signal_priority = "NEW_ENTRY_DEEP", 4
            else:
                signal_type, signal_priority = "DEEP_CLIMBER", 5

            qualified_asset = f"xyz:{token}" if dex == "xyz" else token
            alerts.append({
                "token": token,
                "dex": dex if dex else None,
                "qualifiedAsset": qualified_asset,
                "signal": f"{token} {dir_label}",
                "direction": dir_label,
                "currentRank": current_rank,
                "contribution": round(current_contrib * 100, 3),
                "contribVelocity": round(contrib_velocity * 100, 4),
                "traders": market["traders"],
                "priceChg4h": market["price_chg_4h"],
                "maxLeverage": alert_max_lev,
                "conviction": alert_conviction,
                "signalType": signal_type,
                "signalPriority": signal_priority,
                "reasons": alert_reasons,
                "reasonCount": len(alert_reasons),
                "rankHistory": rank_history,
                "contribHistory": contrib_history,
                "isDeepClimber": is_deep_climber,
                "isImmediate": is_immediate,
                "isFirstJump": is_first_jump,
                "isContribExplosion": is_contrib_explosion,
                "rankJumpThisScan": rank_jump_this_scan,
                "cluster": get_cluster(qualified_asset),
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
        alert["signalType"] = "DEEP_CLIMBER"
        alert["signalPriority"] = 5
        alert["conviction"] = SIGNAL_CONVICTION["DEEP_CLIMBER"]
        if erratic:
            alert["reasons"].append("⚠️ DOWNGRADED: erratic rank history (zigzag in pre-jump history)")
        if low_vel:
            alert["reasons"].append(f"⚠️ DOWNGRADED: non-positive velocity ({alert['contribVelocity']:.4f})")
    elif not is_imm and alert.get("isDeepClimber") and low_vel:
        alert["reasons"].append(f"⚠️ LOW_VEL: velocity {alert['contribVelocity']:.4f} < 0.03 for DEEP_CLIMBER")

# ─── Trend alignment check (runs after erratic/velocity filter) ───
for alert in alerts:
    if not alert.get("isImmediate") and not alert.get("isFirstJump"):
        continue  # only gate immediate signals — DEEP_CLIMBER slow signals skip trend check

    asset     = alert.get("qualifiedAsset") or alert.get("token", "")
    direction = alert.get("direction", "")

    aligned, trend_meta = check_trend_alignment(asset, direction)
    alert["hourlyTrend"]  = trend_meta.get("trend", "UNKNOWN")
    alert["trendStrength"] = trend_meta.get("strength", 0)
    alert["trendAligned"] = aligned

    if trend_meta.get("error"):
        # MCP down or trend unavailable — block entry
        alert["isImmediate"] = False
        alert["isFirstJump"] = False
        alert["signalType"]  = "DEEP_CLIMBER"
        alert["signalPriority"] = 5
        alert["conviction"]  = SIGNAL_CONVICTION["DEEP_CLIMBER"]
        alert["reasons"].append(f"⚠️ BLOCKED: {trend_meta['error']} — cannot verify trend")
    elif not aligned and trend_meta.get("strength", 0) > 50:
        # Counter-trend with conviction — downgrade to log-only
        alert["isImmediate"] = False
        alert["isFirstJump"] = False
        alert["signalType"]  = "DEEP_CLIMBER"
        alert["signalPriority"] = 5
        alert["conviction"]  = SIGNAL_CONVICTION["DEEP_CLIMBER"]
        alert["reasons"].append(
            f"⚠️ DOWNGRADED: counter-trend on hourly ({alert['hourlyTrend']}, strength {trend_meta.get('strength', 0)})"
        )

# ─── Cluster concentration (4c) ───
_cluster_counts = Counter(a["cluster"] for a in alerts)
for alert in alerts:
    cluster = alert.get("cluster", "_unclustered")
    if cluster != "_unclustered" and _cluster_counts[cluster] >= 2:
        alert["clusterConcentration"] = True
        alert["clusterPeers"] = [
            a["qualifiedAsset"] for a in alerts
            if a.get("cluster") == cluster and a["qualifiedAsset"] != alert["qualifiedAsset"]
        ]

# ─── Save history ───
history["scans"].append(current_scan)
if len(history["scans"]) > MAX_HISTORY:
    history["scans"] = history["scans"][-MAX_HISTORY:]

atomic_write(HISTORY_FILE, history)

# ─── Save full output for agent reference (prevents re-run signal loss) ───
OUTPUT_FILE = os.path.join(os.path.dirname(HISTORY_FILE), "emerging-movers-output.json")

# ─── Output ───
# Sort: priority number (1=highest) > velocity > reason count
alerts.sort(key=lambda a: (
    a.get("signalPriority", 99),
    -abs(a.get("contribVelocity", 0)),
    -len(a["reasons"])
))

for idx, alert in enumerate(alerts):
    alert["signalIndex"] = idx

# ─── Slot availability per strategy (clearinghouse-backed) ───
import glob as globmod

APPROX_GRACE_MINUTES = 10  # approximate DSLs older than this don't count toward slots

strategy_slots = {}
try:
    all_strategies = load_all_strategies()
    for key, cfg in all_strategies.items():
        max_slots = cfg.get("slots", 2)
        wallet = cfg.get("wallet", "")

        # Count DSL state files with active=True, excluding stale approximate DSLs
        dsl_active_count = 0
        slot_ages = []
        rotation_eligible_coins = []
        scan_now = datetime.now(timezone.utc)
        for sf in globmod.glob(dsl_state_glob(key)):
            try:
                with open(sf) as f:
                    s = json.load(f)
                if not s.get("active"):
                    continue
                # Skip stale approximate DSLs from slot count
                if s.get("approximate") and s.get("createdAt"):
                    try:
                        created = datetime.fromisoformat(s["createdAt"].replace("Z", "+00:00"))
                        age_min = (scan_now - created).total_seconds() / 60
                        if age_min > APPROX_GRACE_MINUTES:
                            continue
                    except (ValueError, TypeError):
                        pass
                dsl_active_count += 1

                # Track per-slot age for rotation eligibility
                coin_name = s.get("asset", os.path.basename(sf).replace("dsl-", "").replace(".json", ""))
                slot_age_min = None
                if s.get("createdAt"):
                    try:
                        created = datetime.fromisoformat(s["createdAt"].replace("Z", "+00:00"))
                        slot_age_min = round((scan_now - created).total_seconds() / 60, 1)
                    except (ValueError, TypeError):
                        pass
                slot_ages.append({"coin": coin_name, "ageMinutes": slot_age_min})
                if slot_age_min is None or slot_age_min >= ROTATION_COOLDOWN_MINUTES:
                    rotation_eligible_coins.append(coin_name)
            except (json.JSONDecodeError, IOError, KeyError, AttributeError):
                continue

        # Cross-check against on-chain positions
        on_chain_count = 0
        on_chain_coins = []
        if wallet:
            ch_data = mcporter_call_safe("strategy_get_clearinghouse_state",
                                          strategy_wallet=wallet)
            if ch_data:
                for section_key in ("main", "xyz"):
                    section = ch_data.get(section_key, {})
                    for p in section.get("assetPositions", []):
                        if not isinstance(p, dict):
                            continue
                        pos = p.get("position", {})
                        coin = pos.get("coin", "")
                        szi = float(pos.get("szi", 0))
                        if coin and szi != 0:
                            on_chain_count += 1
                            on_chain_coins.append(coin)

        # Use max of both counts — handles both desync directions
        used = max(dsl_active_count, on_chain_count)

        strategy_slots[key] = {
            "name": cfg.get("name", key),
            "slots": max_slots,
            "used": used,
            "available": max(0, max_slots - used),
            "dslActive": dsl_active_count,
            "onChain": on_chain_count,
            "onChainCoins": sorted(on_chain_coins) if on_chain_coins else [],
            "slotAges": slot_ages,
            "rotationEligibleCoins": sorted(rotation_eligible_coins),
            "rotationCooldownMinutes": ROTATION_COOLDOWN_MINUTES,
            "hasRotationCandidate": len(rotation_eligible_coins) > 0,
        }

        # Guard rail gate status
        try:
            gate_status, gate_reason = check_gate(key)
        except Exception:
            gate_status, gate_reason = "OPEN", None
        strategy_slots[key]["gate"] = gate_status
        strategy_slots[key]["gateReason"] = gate_reason
        if gate_status != "OPEN":
            strategy_slots[key]["available"] = 0
except Exception:
    pass

any_slots_available = any(s["available"] > 0 for s in strategy_slots.values()) if strategy_slots else True

# Pre-filter: if no slots available and no FIRST_JUMP signals (rotation candidates),
# skip outputting alerts entirely — saves LLM reasoning time within the cron timeout.
has_first_jump = any(a.get("isFirstJump") for a in alerts)
any_rotation_candidate = any(
    s.get("hasRotationCandidate", True)
    for s in strategy_slots.values()
) if strategy_slots else True

if not any_slots_available and not has_first_jump:
    alerts = []  # clear signals since agent can't act on them
elif not any_slots_available and has_first_jump and not any_rotation_candidate:
    pass  # keep alerts visible but agent will see hasRotationCandidate=false and skip

# Top picks: pre-selected priority-ordered signals for the LLM to act on
total_available_slots = sum(s.get("available", 0) for s in strategy_slots.values())
first_jump_count = len([a for a in alerts if a.get("isFirstJump")])
pick_count = max(total_available_slots, first_jump_count)
top_picks = alerts[:pick_count] if pick_count > 0 else []

output = {
    "status": "ok",
    "time": now,
    "totalMarkets": len(current_scan["markets"]),
    "scansInHistory": len(history["scans"]),
    "strategySlots": strategy_slots,
    "anySlotsAvailable": any_slots_available,
    "totalAvailableSlots": total_available_slots,
    "topPicks": top_picks,
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
    "exitAlerts": exit_alerts,
    "hasExitAlert": len(exit_alerts) > 0,
}

print(json.dumps(output))
atomic_write(OUTPUT_FILE, output)
