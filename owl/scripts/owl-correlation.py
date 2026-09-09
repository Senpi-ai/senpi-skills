#!/usr/bin/env python3
"""OWL Correlation Lag Scanner — BTC/ETH move, alts lag behind.

When BTC or ETH makes a significant move (>1.5% in 1-4h), scan high-correlation
alts for those that haven't caught up yet. Enter the lagging alt in the leader's
direction.

This is a proven, repeatable edge on Hyperliquid — TIGER's most reliable scanner.

Cron: every 5 min, main session.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as ocfg

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
WALLET, STRATEGY_ID = ocfg.get_wallet_and_strategy()
STATE_DIR = ocfg.get_state_dir()
OWL_STATE_PATH = STATE_DIR / "owl-state.json"
CORRELATION_STATE_PATH = STATE_DIR / "correlation-state.json"
OWL_HUNT_STATE_PATH = STATE_DIR / "owl-hunt-state.json"

# ═══ LEADER/ALT MAPPINGS ═══
BTC_ALTS = ["SOL", "DOGE", "AVAX", "LINK", "ADA", "DOT", "NEAR", "ATOM", "SEI", "SUI", "PEPE", "WIF", "RENDER"]
ETH_ALTS = ["OP", "ARB", "AAVE", "UNI", "LDO", "SNX", "PENDLE", "ENA", "ETHFI", "CRV"]

# ═══ THRESHOLDS ═══
LEADER_MIN_MOVE_1H = 1.5        # Min 1h leader move %
LEADER_MIN_MOVE_4H = 2.0        # Min 4h leader move %
LAG_RATIO_MIN = 0.4             # Alt moved < 40% of leader's move = lagging
LAG_RATIO_MAX = 0.85            # Alt moved < 85% = still opportunity
MIN_ENTRY_SCORE = 6
MAX_SLOTS = 3
LOSS_COOLDOWN_HOURS = 4
MAX_DEEP_SCANS = 4              # Max alts to deep-scan per leader

# ═══ SCORING ═══
SCORE_LAG_STRONG = 3            # Lag ratio < 0.3 (barely moved)
SCORE_LAG_MODERATE = 2          # Lag ratio 0.3-0.5
SCORE_LAG_MILD = 1              # Lag ratio 0.5-0.85
SCORE_VOLUME_QUIET = 2          # Alt volume not spiked yet (room to run)
SCORE_RSI_SAFE = 1              # RSI not extreme
SCORE_TREND_ALIGNED = 1         # Alt's own trend matches leader direction
SCORE_SM_ALIGNED = 2            # Hyperfeed shows SM on same side
SCORE_TIME_BONUS = 1
SCORE_TIME_PENALTY = -2

DSL_TIERS = [
    {"triggerPct": 5,   "lockPct": 2},
    {"triggerPct": 10,  "lockPct": 5},
    {"triggerPct": 20,  "lockPct": 14},
    {"triggerPct": 30,  "lockPct": 24},
    {"triggerPct": 40,  "lockPct": 34},
    {"triggerPct": 50,  "lockPct": 44},
    {"triggerPct": 65,  "lockPct": 56},
    {"triggerPct": 80,  "lockPct": 72},
    {"triggerPct": 100, "lockPct": 90},
]


def mcporter_call(tool, args=None, timeout=15):
    if args:
        cmd = ["mcporter", "call", "senpi." + tool, "--args", json.dumps(args)]
    else:
        cmd = ["mcporter", "call", "senpi." + tool]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        raw = json.loads(r.stdout)
        content = raw.get("content")
        if isinstance(content, list) and content:
            item = content[0]
            if isinstance(item, dict):
                text = item.get("text", "")
                if text:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        pass
        return raw
    except Exception:
        return None


def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    ocfg.atomic_write(path, data)


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def extract_closes(candles):
    return [float(c.get("close") or c.get("c") or 0) for c in candles if (c.get("close") or c.get("c"))]


def extract_volumes(candles):
    return [float(c.get("volume") or c.get("v") or c.get("vlm") or 0) for c in candles if (c.get("volume") or c.get("v") or c.get("vlm"))]


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g = gains[-period:]
    l = losses[-period:]
    ag = sum(g) / period
    al = sum(l) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def get_time_score():
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return SCORE_TIME_BONUS, "time_bonus_04_14"
    elif hour >= 18 or hour < 2:
        return SCORE_TIME_PENALTY, "time_penalty_18_02"
    return 0, None


def get_loss_cooldown_assets(owl_hunt_state):
    recent_losses = owl_hunt_state.get("recentLosses", {})
    now = datetime.now(timezone.utc)
    cooldown = set()
    for asset, ts in recent_losses.items():
        try:
            loss_time = datetime.fromisoformat(ts)
            if now - loss_time < timedelta(hours=LOSS_COOLDOWN_HOURS):
                cooldown.add(asset)
        except (ValueError, TypeError):
            pass
    return cooldown


def get_leader_move(leader, candles_1h):
    """Calculate leader's 1h and 4h moves."""
    closes = extract_closes(candles_1h)
    if len(closes) < 5:
        return None, None, None

    current = closes[-1]
    move_1h = ((current - closes[-2]) / closes[-2] * 100) if closes[-2] > 0 else 0
    if len(closes) >= 5:
        move_4h = ((current - closes[-5]) / closes[-5] * 100) if closes[-5] > 0 else 0
    else:
        move_4h = move_1h

    direction = "LONG" if move_1h > 0 else "SHORT"
    return abs(move_1h), abs(move_4h), direction


def run():
    owl_state = load_json(OWL_STATE_PATH)
    corr_state = load_json(CORRELATION_STATE_PATH)
    owl_hunt_state = load_json(OWL_HUNT_STATE_PATH)

    if not corr_state:
        corr_state = {"version": 1, "lastScan": None}

    active = owl_state.get("activePositions", {})
    if len(active) >= MAX_SLOTS:
        output({"success": True, "heartbeat": "NO_REPLY", "reason": "slots_full", "scanner": "correlation"})
        return

    cooldown_assets = get_loss_cooldown_assets(owl_hunt_state)

    # 1. Check BTC and ETH for significant moves
    leaders_to_check = []

    for leader in ["BTC", "ETH"]:
        data = mcporter_call("market_get_asset_data", {
            "asset": leader,
            "candle_intervals": ["1h"],
        }, timeout=15)

        if not data or not isinstance(data, dict):
            continue

        asset_data = data.get("data", data)
        candles = asset_data.get("candles", {}).get("1h", [])
        move_1h, move_4h, direction = get_leader_move(leader, candles)

        if move_1h is None:
            continue

        # Check if leader made a significant move
        significant = False
        if move_1h >= LEADER_MIN_MOVE_1H:
            significant = True
        if move_4h >= LEADER_MIN_MOVE_4H:
            significant = True

        if significant:
            leaders_to_check.append({
                "leader": leader,
                "move_1h": move_1h,
                "move_4h": move_4h,
                "direction": direction,
                "alts": BTC_ALTS if leader == "BTC" else ETH_ALTS,
            })

    if not leaders_to_check:
        now_iso = datetime.now(timezone.utc).isoformat()
        corr_state["lastScan"] = now_iso
        corr_state["status"] = "no_leader_move"
        save_json(CORRELATION_STATE_PATH, corr_state)
        output({"success": True, "heartbeat": "NO_REPLY", "scanner": "correlation", "reason": "no_leader_move"})
        return

    # If BTC and ETH both moved same direction, only scan BTC's alts (avoid duplication)
    if len(leaders_to_check) == 2 and leaders_to_check[0]["direction"] == leaders_to_check[1]["direction"]:
        leaders_to_check = [leaders_to_check[0]]  # BTC takes priority

    # 2. Get Hyperfeed data for SM alignment check
    markets_raw = mcporter_call("leaderboard_get_markets")
    sm_direction = {}  # asset -> direction
    if markets_raw:
        data = markets_raw.get("data", markets_raw)
        mkts_container = data.get("markets", data) if isinstance(data, dict) else data
        if isinstance(mkts_container, dict):
            markets_list = mkts_container.get("markets", [])
        elif isinstance(mkts_container, list):
            markets_list = mkts_container
        else:
            markets_list = []
        for m in markets_list:
            token = m.get("token", "")
            if m.get("trader_count", 0) >= 10:
                sm_direction[token] = m.get("direction", "").upper()

    # 3. Scan lagging alts
    signals = []
    scanned = 0

    for leader_info in leaders_to_check:
        leader = leader_info["leader"]
        leader_move = max(leader_info["move_1h"], leader_info["move_4h"])
        direction = leader_info["direction"]

        for alt in leader_info["alts"]:
            if scanned >= MAX_DEEP_SCANS:
                break
            if alt in active or alt in cooldown_assets:
                continue

            data = mcporter_call("market_get_asset_data", {
                "asset": alt,
                "candle_intervals": ["1h"],
            }, timeout=15)

            if not data or not isinstance(data, dict):
                continue

            scanned += 1
            asset_data = data.get("data", data)
            candles = asset_data.get("candles", {}).get("1h", [])
            closes = extract_closes(candles)
            volumes = extract_volumes(candles)

            if len(closes) < 20:
                continue

            # Calculate alt's move
            current = closes[-1]
            alt_move_1h = abs((current - closes[-2]) / closes[-2] * 100) if closes[-2] > 0 else 0
            alt_move_4h = abs((current - closes[-5]) / closes[-5] * 100) if len(closes) >= 5 and closes[-5] > 0 else alt_move_1h

            # Check direction matches (alt should be moving same way as leader, just less)
            alt_dir_1h = "LONG" if closes[-1] > closes[-2] else "SHORT"
            alt_dir_4h = "LONG" if (len(closes) >= 5 and closes[-1] > closes[-5]) else "SHORT"

            # Use the matching timeframe
            if leader_info["move_1h"] >= LEADER_MIN_MOVE_1H:
                alt_move = alt_move_1h
                leader_ref_move = leader_info["move_1h"]
            else:
                alt_move = alt_move_4h
                leader_ref_move = leader_info["move_4h"]

            if leader_ref_move <= 0:
                continue

            lag_ratio = alt_move / leader_ref_move

            # Must be lagging
            if lag_ratio > LAG_RATIO_MAX or lag_ratio < 0:
                continue

            # ═══ SCORING ═══
            score = 0
            reasons = []

            # Lag score
            if lag_ratio < 0.3:
                score += SCORE_LAG_STRONG
                reasons.append("strong_lag_{:.0f}%:+{}".format(lag_ratio*100, SCORE_LAG_STRONG))
            elif lag_ratio < 0.5:
                score += SCORE_LAG_MODERATE
                reasons.append("moderate_lag_{:.0f}%:+{}".format(lag_ratio*100, SCORE_LAG_MODERATE))
            else:
                score += SCORE_LAG_MILD
                reasons.append("mild_lag_{:.0f}%:+{}".format(lag_ratio*100, SCORE_LAG_MILD))

            # Volume quiet (alt hasn't spiked yet = room to run)
            if len(volumes) >= 6:
                recent_vol = sum(volumes[-2:]) / 2
                trailing_vol = sum(volumes[-6:-2]) / 4
                if trailing_vol > 0 and recent_vol / trailing_vol < 1.3:
                    score += SCORE_VOLUME_QUIET
                    reasons.append("volume_quiet:+{}".format(SCORE_VOLUME_QUIET))

            # RSI safe
            rsi = calc_rsi(closes, 14)
            if rsi is not None:
                if direction == "LONG" and rsi < 75:
                    score += SCORE_RSI_SAFE
                    reasons.append("rsi_safe_{:.0f}:+{}".format(rsi, SCORE_RSI_SAFE))
                elif direction == "SHORT" and rsi > 25:
                    score += SCORE_RSI_SAFE
                    reasons.append("rsi_safe_{:.0f}:+{}".format(rsi, SCORE_RSI_SAFE))

            # Trend aligned (alt's own SMA)
            sma20 = calc_sma(closes, 20)
            if sma20:
                if direction == "LONG" and current > sma20:
                    score += SCORE_TREND_ALIGNED
                    reasons.append("trend_aligned:+{}".format(SCORE_TREND_ALIGNED))
                elif direction == "SHORT" and current < sma20:
                    score += SCORE_TREND_ALIGNED
                    reasons.append("trend_aligned:+{}".format(SCORE_TREND_ALIGNED))

            # SM aligned
            if alt in sm_direction and sm_direction[alt] == direction:
                score += SCORE_SM_ALIGNED
                reasons.append("sm_aligned:+{}".format(SCORE_SM_ALIGNED))

            # Time
            time_pts, time_reason = get_time_score()
            if time_pts != 0:
                score += time_pts
                reasons.append("{}:{:+d}".format(time_reason, time_pts))

            if score < MIN_ENTRY_SCORE:
                continue

            # ═══ BUILD SIGNAL ═══
            leverage = min(8, 10)  # Most alts support 5-10x

            slot_num = len(active) + len(signals) + 1
            base_margins = {1: 380, 2: 304, 3: 253}
            margin = base_margins.get(slot_num, 200)

            # Correlation lag: use standard floor — 0.04 was too tight
            floor_base = 0.06
            if direction == "LONG":
                abs_floor = current * (1 - floor_base / leverage)
            else:
                abs_floor = current * (1 + floor_base / leverage)

            signals.append({
                "action": "ENTER",
                "scanner": "correlation",
                "asset": alt,
                "direction": direction,
                "leverage": leverage,
                "entryPrice": current,
                "marginAmount": margin,
                "entryScore": score,
                "scoreReasons": reasons,
                "leader": leader,
                "leaderMove": round(leader_ref_move, 2),
                "altMove": round(alt_move, 2),
                "lagRatio": round(lag_ratio, 2),
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": True,
                "dsl": {
                    "absoluteFloor": round(abs_floor, 6),
                    "floorBase": floor_base,
                    "hardTimeoutMin": 45,
                    "weakPeakCutMin": 20,
                    "deadWeightCutMin": 12,
                    "weakPeakRoeThreshold": 5,   # Was 3% — too aggressive
                    "greenIn10TightenPct": 50,
                    "phase2RetraceThreshold": 0.012,
                    "phase2ConsecutiveBreaches": 2,
                    "tiers": DSL_TIERS,
                    "stagnationTpEnabled": True,
                    "stagnationTpRoeMin": 8,
                    "stagnationTpStaleMin": 60,
                    "exchangeSlDelayMin": 5,     # T1 warmup delay
                },
                "wallet": WALLET,
                "strategyId": STRATEGY_ID,
            })

    now_iso = datetime.now(timezone.utc).isoformat()
    corr_state["lastScan"] = now_iso
    corr_state["leadersChecked"] = [{
        "leader": l["leader"], "move_1h": round(l["move_1h"], 2),
        "move_4h": round(l["move_4h"], 2), "dir": l["direction"],
    } for l in leaders_to_check]
    save_json(CORRELATION_STATE_PATH, corr_state)

    if signals:
        output({
            "success": True,
            "scanner": "correlation",
            "signals": signals,
            "signalCount": len(signals),
        })
    else:
        output({
            "success": True,
            "scanner": "correlation",
            "heartbeat": "NO_REPLY",
            "leaders": [{
                "leader": l["leader"], "move_1h": round(l["move_1h"], 2),
                "dir": l["direction"],
            } for l in leaders_to_check],
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        output({"success": False, "error": str(e), "scanner": "correlation"})
