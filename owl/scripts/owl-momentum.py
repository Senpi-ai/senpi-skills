#!/usr/bin/env python3
"""OWL Momentum Scanner — Smart money concentration + technical confirmation.

Uses Senpi Hyperfeed (leaderboard_get_markets) to find where top traders are
concentrated, then confirms with technicals before signaling entry.

This is the OWL's second eye — momentum alongside contrarian.

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
MOMENTUM_STATE_PATH = STATE_DIR / "momentum-state.json"
OWL_HUNT_STATE_PATH = STATE_DIR / "owl-hunt-state.json"

# ═══ THRESHOLDS ═══
MIN_TRADER_COUNT = 30           # Min top traders holding this direction
MIN_GAIN_PCT = 2.0              # Min % of top traders' gains from this asset
MIN_PRICE_MOVE_4H = 0.5         # Min 4h price move % (confirms real movement)
MAX_PRICE_MOVE_4H = 8.0         # Max 4h move (too late, chasing)
MIN_CONTRIB_CHANGE = 1.0        # Min contribution change % in 4h (momentum building)
MIN_ENTRY_SCORE = 6             # Point threshold
MAX_SLOTS = 3                   # Shared with OWL contrarian
LOSS_COOLDOWN_HOURS = 4         # Shared cooldown

# Skip these — too correlated to trade individually
SKIP_ASSETS = set()  # Empty for now; we'll trade anything with signal

# ═══ SCORING ═══
SCORE_SM_CONCENTRATED = 3       # Smart money concentrated (base)
SCORE_SM_HEAVY = 2              # >50 traders + >5% of gains
SCORE_VOLUME_SURGE = 2          # Volume 1.5x+ trailing avg
SCORE_TREND_ALIGNED = 1         # Price above/below SMA20
SCORE_RSI_SAFE = 1              # RSI not at extremes
SCORE_CONTRIB_RISING = 2        # Contribution % rising (momentum building)
SCORE_MULTI_ASSET_CONFIRM = 1   # Other correlated assets also moving same way
SCORE_TIME_BONUS = 1            # 04-14 UTC
SCORE_TIME_PENALTY = -2         # 18-02 UTC

# 9-tier DSL (shared with OWL v4)
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
            elif isinstance(item, str):
                try:
                    return json.loads(item)
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


def run():
    owl_state = load_json(OWL_STATE_PATH)
    momentum_state = load_json(MOMENTUM_STATE_PATH)
    owl_hunt_state = load_json(OWL_HUNT_STATE_PATH)

    if not momentum_state:
        momentum_state = {"version": 1, "lastScan": None, "recentSignals": []}

    active = owl_state.get("activePositions", {})
    if len(active) >= MAX_SLOTS:
        output({"success": True, "heartbeat": "NO_REPLY", "reason": "slots_full", "scanner": "momentum"})
        return

    cooldown_assets = get_loss_cooldown_assets(owl_hunt_state)

    # 1. Get Hyperfeed market concentration data
    markets_raw = mcporter_call("leaderboard_get_markets")
    if not markets_raw:
        output({"success": False, "error": "No markets data", "scanner": "momentum"})
        return

    data = markets_raw.get("data", markets_raw)
    mkts_container = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(mkts_container, dict):
        markets_list = mkts_container.get("markets", [])
    elif isinstance(mkts_container, list):
        markets_list = mkts_container
    else:
        markets_list = []

    if not markets_list:
        output({"success": False, "error": "Empty markets list", "scanner": "momentum"})
        return

    # 2. Filter for momentum candidates
    candidates = []
    for m in markets_list:
        token = m.get("token", "")
        dex = m.get("dex", "")
        direction = m.get("direction", "").upper()
        trader_count = m.get("trader_count", 0)
        gain_pct = m.get("pct_of_top_traders_gain", 0)
        contrib_change = m.get("contribution_pct_change_4h", 0)
        price_change = abs(m.get("token_price_change_pct_4h", 0))
        max_lev = m.get("max_leverage", 10)

        asset = ("xyz:" + token) if dex == "xyz" else token

        if asset in SKIP_ASSETS:
            continue
        if trader_count < MIN_TRADER_COUNT:
            continue
        if gain_pct < MIN_GAIN_PCT:
            continue
        if price_change < MIN_PRICE_MOVE_4H:
            continue
        if price_change > MAX_PRICE_MOVE_4H:
            continue
        if max_lev < 5:
            continue

        candidates.append({
            "asset": asset,
            "direction": direction,
            "traderCount": trader_count,
            "gainPct": gain_pct,
            "contribChange": contrib_change,
            "priceChange4h": m.get("token_price_change_pct_4h", 0),
            "maxLev": max_lev,
        })

    # Sort by gain percentage (most profitable concentration first)
    candidates.sort(key=lambda x: x["gainPct"], reverse=True)

    # CORRELATION FILTER: If BTC/ETH/SOL all short, that's one bet.
    # Keep top 1 per correlated group + any independent assets.
    BTC_CORRELATED = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ADA", "DOT", "NEAR", "ATOM", "SEI", "SUI", "PEPE", "WIF"}
    seen_corr_directions = {}  # "BTC_SHORT" -> True
    filtered = []
    for c in candidates:
        clean_name = c["asset"].replace("xyz:", "")
        if clean_name in BTC_CORRELATED:
            key = "BTC_CORR_" + c["direction"]
            if key in seen_corr_directions:
                continue  # Skip — already have a correlated asset in this direction
            seen_corr_directions[key] = True
        filtered.append(c)
    candidates = filtered

    # 3. Deep scan top candidates with technicals
    signals = []
    for c in candidates[:4]:  # Max 4 deep scans
        asset = c["asset"]
        direction = c["direction"]

        if asset in active:
            continue
        if asset in cooldown_assets:
            continue

        # Fetch technical data
        dex = "xyz" if asset.startswith("xyz:") else None
        detail = mcporter_call("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h"],
            **({"dex": dex} if dex else {}),
        }, timeout=15)

        if not detail or not isinstance(detail, dict):
            continue

        asset_data = detail.get("data", detail)
        candles_1h = asset_data.get("candles", {}).get("1h", [])
        closes = extract_closes(candles_1h)
        volumes = extract_volumes(candles_1h)

        if len(closes) < 20:
            continue

        # ═══ SCORING ═══
        score = 0
        reasons = []

        # Base: SM concentrated
        score += SCORE_SM_CONCENTRATED
        reasons.append("sm_concentrated_{}_traders:+{}".format(c["traderCount"], SCORE_SM_CONCENTRATED))

        # Heavy concentration
        if c["traderCount"] >= 50 and c["gainPct"] >= 5.0:
            score += SCORE_SM_HEAVY
            reasons.append("sm_heavy_{:.1f}%_gains:+{}".format(c["gainPct"], SCORE_SM_HEAVY))

        # Contribution rising (momentum building, not fading)
        if c["contribChange"] > MIN_CONTRIB_CHANGE:
            score += SCORE_CONTRIB_RISING
            reasons.append("contrib_rising_{:.1f}%:+{}".format(c["contribChange"], SCORE_CONTRIB_RISING))

        # Volume surge
        if len(volumes) >= 6:
            recent_vol = sum(volumes[-2:]) / 2
            trailing_vol = sum(volumes[-6:-2]) / 4
            if trailing_vol > 0 and recent_vol / trailing_vol >= 1.5:
                score += SCORE_VOLUME_SURGE
                ratio = recent_vol / trailing_vol
                reasons.append("volume_surge_{:.1f}x:+{}".format(ratio, SCORE_VOLUME_SURGE))

        # Trend alignment (SMA20)
        sma20 = calc_sma(closes, 20)
        if sma20:
            current = closes[-1]
            if direction == "LONG" and current > sma20:
                score += SCORE_TREND_ALIGNED
                reasons.append("above_sma20:+{}".format(SCORE_TREND_ALIGNED))
            elif direction == "SHORT" and current < sma20:
                score += SCORE_TREND_ALIGNED
                reasons.append("below_sma20:+{}".format(SCORE_TREND_ALIGNED))

        # RSI safe (not at extremes against us)
        rsi = calc_rsi(closes, 14)
        if rsi is not None:
            if direction == "LONG" and rsi < 75:
                score += SCORE_RSI_SAFE
                reasons.append("rsi_safe_{:.0f}:+{}".format(rsi, SCORE_RSI_SAFE))
            elif direction == "SHORT" and rsi > 25:
                score += SCORE_RSI_SAFE
                reasons.append("rsi_safe_{:.0f}:+{}".format(rsi, SCORE_RSI_SAFE))

        # Time-of-day
        time_pts, time_reason = get_time_score()
        if time_pts != 0:
            score += time_pts
            reasons.append("{}:{:+d}".format(time_reason, time_pts))

        # ═══ ENTRY DECISION ═══
        if score < MIN_ENTRY_SCORE:
            continue

        # Anti-chasing: if price already moved >5% in 4h, need higher score
        if abs(c["priceChange4h"]) > 5.0 and score < 8:
            continue

        # Fading momentum: if contribution is DECLINING, skip unless very high score
        if c["contribChange"] < 0 and score < 8:
            continue

        current_price = closes[-1]
        leverage = min(8, c["maxLev"])

        # Position sizing — momentum gets standard sizing
        slot_num = len(active) + len(signals) + 1
        base_margins = {1: 380, 2: 304, 3: 253}
        margin = base_margins.get(slot_num, 200)

        # Wider floor for momentum — 0.06/leverage (6% ROE max loss)
        # 0.04 was too tight — normal noise on SOL/BTC trips SL before thesis plays out
        floor_base = 0.06
        if direction == "LONG":
            abs_floor = current_price * (1 - floor_base / leverage)
        else:
            abs_floor = current_price * (1 + floor_base / leverage)

        signals.append({
            "action": "ENTER",
            "scanner": "momentum",
            "asset": asset,
            "direction": direction,
            "leverage": leverage,
            "entryPrice": current_price,
            "marginAmount": margin,
            "entryScore": score,
            "scoreReasons": reasons,
            "traderCount": c["traderCount"],
            "gainPct": c["gainPct"],
            "contribChange": c["contribChange"],
            "priceChange4h": c["priceChange4h"],
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "ensureExecutionAsTaker": True,
            "dsl": {
                "absoluteFloor": round(abs_floor, 6),
                "floorBase": floor_base,
                "hardTimeoutMin": 45,        # Give momentum trades room
                "weakPeakCutMin": 20,
                "deadWeightCutMin": 12,
                "weakPeakRoeThreshold": 5,   # Was 3% — kills slow builders
                "greenIn10TightenPct": 50,
                "phase2RetraceThreshold": 0.012,
                "phase2ConsecutiveBreaches": 2,
                "tiers": DSL_TIERS,
                "stagnationTpEnabled": True,
                "stagnationTpRoeMin": 8,
                "stagnationTpStaleMin": 60,
                "exchangeSlDelayMin": 5,     # T1 warmup: don't set exchange SL for 5min
            },
            "wallet": WALLET,
            "strategyId": STRATEGY_ID,
        })

    now_iso = datetime.now(timezone.utc).isoformat()
    momentum_state["lastScan"] = now_iso
    momentum_state["lastCandidates"] = [{
        "asset": c["asset"], "dir": c["direction"],
        "traders": c["traderCount"], "gain%": round(c["gainPct"], 1),
    } for c in candidates[:8]]
    save_json(MOMENTUM_STATE_PATH, momentum_state)

    if signals:
        output({
            "success": True,
            "scanner": "momentum",
            "signals": signals,
            "signalCount": len(signals),
            "candidates": len(candidates),
        })
    else:
        output({
            "success": True,
            "scanner": "momentum",
            "heartbeat": "NO_REPLY",
            "candidateCount": len(candidates),
            "topCandidates": [{
                "asset": c["asset"], "dir": c["direction"],
                "traders": c["traderCount"], "gain": round(c["gainPct"], 1),
            } for c in candidates[:5]],
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        output({"success": False, "error": str(e), "scanner": "momentum"})
