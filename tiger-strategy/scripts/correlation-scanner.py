#!/usr/bin/env python3
"""
correlation-scanner.py — Multi-leader correlation lag detector for TIGER.
Checks BTC AND ETH for significant moves across rolling windows (1h/4h/12h/24h),
then identifies alts lagging each leader. Runs every 3min.

Fix history:
  - 2026-03-05 c775c2c: Added multi-window detection (was single 4h candle)
  - 2026-03-05: Added ETH as second leader with ecosystem-specific alt lists

MANDATE: Run TIGER correlation scanner. Check BTC+ETH moves and find lagging alts. Report signals.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, save_state, get_all_instruments,
    get_asset_candles, get_sm_markets, output
)
from tiger_lib import (
    parse_candles, rsi, sma, atr, volume_ratio, confluence_score
)


# Leader → correlated alts mapping
# v4: Trimmed to high-liquidity, high-correlation only (was 23/20, now 9/7)
# Low-volume alts gap and get stopped out instantly
BTC_CORR_ALTS = [
    "ETH", "SOL", "DOGE", "AVAX", "LINK", "XRP", "SUI", "APT", "HYPE"
]

# ETH leads: L2s, DeFi core (high liquidity only)
ETH_CORR_ALTS = [
    "OP", "ARB", "MATIC", "AAVE", "UNI", "LDO", "PENDLE"
]

# Combined for backward compat
HIGH_CORR_ALTS = list(set(BTC_CORR_ALTS + ETH_CORR_ALTS))

LEADERS = {
    "BTC": {"alts": BTC_CORR_ALTS, "threshold_mult": 1.0},
    "ETH": {"alts": ETH_CORR_ALTS, "threshold_mult": 0.8},  # ETH is more volatile, lower bar
}


def check_leader_move(leader: str, config: dict, state: dict, threshold_mult: float = 1.0) -> dict:
    """Check if a leader asset (BTC/ETH) has made a significant move across multiple windows.
    Checks 1h, 4h, 12h, and 24h rolling windows so sustained multi-candle
    moves aren't missed (fix for the 2026-03-04 +6% miss)."""
    result = get_asset_candles(leader, ["1h"])
    if not result.get("success") and not result.get("data"):
        return {"triggered": False, "leader": leader, "error": f"Failed to fetch {leader} data"}

    data = result.get("data", result)
    candles = data.get("candles", {}).get("1h", [])
    if len(candles) < 5:
        return {"triggered": False, "leader": leader, "error": f"Insufficient {leader} candle data"}

    _, _, _, closes, volumes = parse_candles(candles)
    current_price = closes[-1]

    # Update cache
    state[f"last_{leader.lower()}_price"] = current_price
    state[f"last_{leader.lower()}_check"] = int(time.time())

    threshold = config["btc_correlation_move_pct"]

    # Multi-window checks: 1h, 4h, 12h, 24h
    def pct_move(n_candles):
        if len(closes) >= n_candles + 1:
            ref = closes[-(n_candles + 1)]
        else:
            ref = closes[0]
        return ((current_price - ref) / ref) * 100 if ref else 0

    move_1h = pct_move(1)
    move_4h = pct_move(4)
    move_12h = pct_move(12)
    move_24h = pct_move(24)

    # Apply leader-specific multiplier (ETH is more volatile → lower bar)
    t = threshold * threshold_mult
    # Trigger if ANY window exceeds its threshold:
    # - 1h:  t * 0.6 (fast spike detection)
    # - 4h:  t (original behavior)
    # - 12h: t * 1.5 (sustained move — slightly higher bar)
    # - 24h: t * 2.0 (big cumulative move)
    triggered = (
        abs(move_1h) >= t * 0.6 or
        abs(move_4h) >= t or
        abs(move_12h) >= t * 1.5 or
        abs(move_24h) >= t * 2.0
    )

    # Use the largest absolute move to determine direction
    moves = {"1h": move_1h, "4h": move_4h, "12h": move_12h, "24h": move_24h}
    dominant_window = max(moves, key=lambda k: abs(moves[k]))
    dominant_move = moves[dominant_window]
    direction = "LONG" if dominant_move > 0 else "SHORT"

    # Timeframe alignment check (Mar 5 lesson: 24h signal was stale while 4h reversed)
    # If dominant window is 12h/24h, check if shorter windows are diverging
    timeframe_aligned = True
    timeframe_warning = None
    if dominant_window in ("12h", "24h"):
        dominant_is_long = dominant_move > 0
        # 4h moving opposite by >0.5% = FADING signal
        if (dominant_is_long and move_4h < -0.5) or (not dominant_is_long and move_4h > 0.5):
            timeframe_aligned = False
            timeframe_warning = f"FADING: dominant {dominant_window} is {'LONG' if dominant_is_long else 'SHORT'} but 4h is {'+' if move_4h > 0 else ''}{move_4h:.2f}%"
        # 1h moving opposite = additional warning
        if (dominant_is_long and move_1h < -0.3) or (not dominant_is_long and move_1h > 0.3):
            if timeframe_warning:
                timeframe_warning += f"; 1h also diverging ({'+' if move_1h > 0 else ''}{move_1h:.2f}%)"
            else:
                timeframe_aligned = False
                timeframe_warning = f"WEAKENING: 1h diverging from {dominant_window} ({'+' if move_1h > 0 else ''}{move_1h:.2f}%)"

    return {
        "triggered": triggered,
        "leader": leader,
        "price": current_price,
        "move_1h_pct": round(move_1h, 2),
        "move_4h_pct": round(move_4h, 2),
        "move_12h_pct": round(move_12h, 2),
        "move_24h_pct": round(move_24h, 2),
        "dominant_window": dominant_window,
        "dominant_move_pct": round(dominant_move, 2),
        "direction": direction,
        "threshold": threshold,
        "timeframe_aligned": timeframe_aligned,
        "timeframe_warning": timeframe_warning,
    }


def check_alt_lag(asset: str, btc_direction: str, btc_move: float,
                  btc_window: str, instruments_map: dict, sm_data: dict,
                  config: dict, leader_timeframe_aligned: bool = True) -> dict:
    """Check if an alt is lagging behind BTC's move.
    btc_move: the dominant BTC move % (could be 4h, 12h, or 24h)
    btc_window: which window triggered (e.g. '12h', '24h')"""
    result = get_asset_candles(asset, ["1h", "4h"])
    if not result.get("success") and not result.get("data"):
        return None

    data = result.get("data", result)
    candles_1h = data.get("candles", {}).get("1h", [])
    candles_4h = data.get("candles", {}).get("4h", [])

    if len(candles_1h) < 5 or len(candles_4h) < 20:
        return None

    _, h1, l1, c1, v1 = parse_candles(candles_1h)
    _, h4, l4, c4, v4 = parse_candles(candles_4h)
    current_price = c1[-1]

    # Match the alt's move to the same window as BTC's dominant move
    window_candles = {"1h": 1, "4h": 4, "12h": 12, "24h": 24}
    n = window_candles.get(btc_window, 4)
    if len(c1) >= n + 1:
        alt_ref = c1[-(n + 1)]
    else:
        alt_ref = c1[0]
    alt_move = ((current_price - alt_ref) / alt_ref) * 100 if alt_ref else 0

    # The lag: BTC moved X%, alt moved Y% in same direction
    # Lag = BTC move - alt move (positive = alt is behind)
    if btc_direction == "LONG":
        lag = btc_move - alt_move
    else:
        lag = alt_move - btc_move  # For shorts, alt should be MORE negative

    # Only interested if alt is notably lagging (>50% of BTC's move)
    lag_ratio = lag / abs(btc_move) if btc_move != 0 else 0
    if lag_ratio < 0.4:  # Alt has already moved 60%+ of BTC's move — window closing/closed
        return None

    # SPEED CHECK: if lag_ratio < 0.5 AND volume is already spiking, window is closing
    # Prefer lag_ratio > 0.6 (alt has moved < 40% of BTC) for best entries
    window_quality = "STRONG" if lag_ratio > 0.7 else ("MODERATE" if lag_ratio > 0.5 else "CLOSING")

    # Direction for the trade (follow BTC)
    direction = btc_direction

    # RSI check
    rsi_values = rsi(c4, 14)
    current_rsi = rsi_values[-1]
    rsi_ok = True
    rsi_gate = config.get("rsi_entry_gate", {})
    long_max = rsi_gate.get("LONG_max", 65)
    short_min = rsi_gate.get("SHORT_min", 35)
    if current_rsi:
        if direction == "LONG" and current_rsi > long_max:
            rsi_ok = False  # Data: RSI > 65 entries consistently lose
            return None  # v4 HARD REJECT: FARTCOIN RSI 70.4 = -$333
        if direction == "SHORT" and current_rsi < short_min:
            rsi_ok = False
            return None  # v4 HARD REJECT: same logic for shorts

    # Volume: has the alt started moving? Low volume = hasn't noticed yet
    vol_r = volume_ratio(v1, short_period=2, long_period=8)
    low_volume = vol_r is not None and vol_r < 1.2  # Volume hasn't spiked yet = still lagging

    # ATR for expected catch-up move
    atr_values = atr(h4, l4, c4, 14)
    current_atr = atr_values[-1] if atr_values[-1] else 0
    atr_pct = (current_atr / current_price * 100) if current_price > 0 else 0

    # Expected catch-up in %
    expected_catchup = abs(btc_move) * lag_ratio * 0.8  # Assume 80% of lag closes

    # SM alignment
    sm_info = sm_data.get(asset, {})
    sm_aligned = sm_info.get("direction", "").upper() == direction if sm_info else False

    # Instrument context
    inst = instruments_map.get(asset, {})
    max_lev = inst.get("max_leverage", 0)
    oi = float(inst.get("context", {}).get("openInterest", 0))
    funding = float(inst.get("context", {}).get("funding", 0))

    # Confluence
    factors = {
        "leader_significant_move": (True, 0.20),
        "alt_lagging": (lag_ratio >= 0.5, 0.25),
        "volume_not_spiked": (low_volume, 0.15),  # Good: means the move hasn't started
        "rsi_safe": (rsi_ok, 0.10),
        "sm_aligned": (sm_aligned, 0.15),
        "high_correlation_alt": (asset in HIGH_CORR_ALTS, 0.10),
        "sufficient_leverage": (max_lev >= config["min_leverage"], 0.05),
        "timeframe_aligned": (leader_timeframe_aligned, 0.30),  # Mar 5: penalize stale signals heavily
    }

    # Also include alt_move for logging
    alt_move_4h = alt_move  # backward compat for output

    score = confluence_score(factors)

    return {
        "asset": asset,
        "pattern": "CORRELATION_LAG",
        "score": round(score, 2),
        "direction": direction,
        "current_price": current_price,
        "alt_move_pct": round(alt_move, 2),
        "btc_window": btc_window,
        "lag_pct": round(lag, 2),
        "lag_ratio": round(lag_ratio, 2),
        "window_quality": window_quality,
        "expected_catchup_pct": round(expected_catchup, 2),
        "expected_roe_pct": round(expected_catchup * max_lev, 1),
        "volume_ratio": round(vol_r, 2) if vol_r else None,
        "volume_quiet": low_volume,
        "rsi": round(current_rsi, 1) if current_rsi else None,
        "sm_aligned": sm_aligned,
        "atr_pct": round(atr_pct, 2),
        "max_leverage": max_lev,
        "factors": {k: v[0] for k, v in factors.items()}
    }


def main():
    config = load_config()
    state = load_state()

    if state.get("halted"):
        output({"action": "correlation_scan", "halted": True, "reason": state.get("halt_reason")})
        return

    # Step 1: Check ALL leaders for significant moves
    leader_results = {}
    triggered_leaders = []
    for leader, info in LEADERS.items():
        result = check_leader_move(leader, config, state, info["threshold_mult"])
        leader_results[leader] = result
        if result.get("triggered"):
            triggered_leaders.append(leader)

    if not triggered_leaders:
        # No leader moved significantly — report all windows for debugging
        output({
            "action": "correlation_scan",
            "leaders_triggered": [],
            "leaders": {
                name: {
                    "move_1h_pct": r.get("move_1h_pct", 0),
                    "move_4h_pct": r.get("move_4h_pct", 0),
                    "move_12h_pct": r.get("move_12h_pct", 0),
                    "move_24h_pct": r.get("move_24h_pct", 0),
                } for name, r in leader_results.items()
            },
            "threshold": config["btc_correlation_move_pct"],
            "message": "No leader (BTC/ETH) made a significant move across any window."
        })
        return

    # Step 2: Fetch instruments + SM data (shared across all leader scans)
    instruments = get_all_instruments()
    instruments_map = {i["name"]: i for i in instruments if not i.get("is_delisted")}

    sm_markets = get_sm_markets(limit=50)
    sm_data = {}
    for m in sm_markets:
        token = m.get("token", "")
        if token not in sm_data:
            sm_data[token] = m

    ap = state.get("active_positions", {})
    if isinstance(ap, list):
        active_coins = {p.get("coin", p.get("asset", "")) for p in ap}
    else:
        active_coins = set(ap.keys())

    # Step 3: For each triggered leader, scan its correlated alts
    # Optimization: if both BTC and ETH triggered in the same direction,
    # ETH is likely just following BTC — only scan BTC's alts (includes ETH-beta).
    # Only scan ETH independently if it moved opposite BTC or BTC didn't trigger.
    if len(triggered_leaders) == 2:
        btc_dir = leader_results["BTC"].get("direction")
        eth_dir = leader_results["ETH"].get("direction")
        if btc_dir == eth_dir:
            # ETH following BTC — just use BTC as leader, skip ETH scan
            triggered_leaders = ["BTC"]

    all_signals = []
    scanned_assets = set()

    for leader in triggered_leaders:
        lr = leader_results[leader]
        leader_info = LEADERS[leader]
        alt_list = list(leader_info["alts"])

        # Add other liquid assets not already in the leader's list
        other = sorted(
            [i["name"] for i in instruments
             if i["name"] not in alt_list
             and i["name"] not in ("BTC", "ETH")
             and not i.get("is_delisted")
             and i.get("max_leverage", 0) >= config["min_leverage"]
             and float(i.get("context", {}).get("dayNtlVlm", 0)) > 5_000_000],
            key=lambda n: float(instruments_map.get(n, {}).get("context", {}).get("dayNtlVlm", 0)),
            reverse=True
        )
        alt_list.extend(other[:6])

        # Deduplicate, skip leader itself and already-scanned assets
        unique_alts = []
        for a in alt_list:
            if (a not in scanned_assets and a != leader and a in instruments_map
                    and a not in active_coins):
                unique_alts.append(a)
                scanned_assets.add(a)

        # SPEED: max 4 alts total to stay within 55s timeout
        # 2 leaders (2 calls) + instruments + SM + 4 alts (4 calls) = ~8 API calls × ~6s = 48s
        remaining = 4 - len(all_signals)  # cap total alts across all leaders
        if remaining <= 0:
            break
        leader_tf_aligned = lr.get("timeframe_aligned", True)
        # v4 HARD REJECT: If leader timeframes are diverging, skip entirely
        # Mar 5 lesson: lost ~$90 on 3 trades from stale 24h signals while 4h reversed
        if not leader_tf_aligned:
            import sys
            print(f"SKIP {leader}: timeframe divergence — {lr.get('timeframe_warning', '')}", file=sys.stderr)
            continue
        for asset in unique_alts[:remaining]:
            try:
                result = check_alt_lag(
                    asset, lr["direction"], lr["dominant_move_pct"],
                    lr["dominant_window"], instruments_map, sm_data, config,
                    leader_timeframe_aligned=leader_tf_aligned,
                )
                if result:
                    result["leader"] = leader
                    all_signals.append(result)
            except Exception as e:
                import sys
                print(f"WARN: {asset} lag check failed: {e}", file=sys.stderr)
                continue

    all_signals.sort(key=lambda x: x["score"], reverse=True)

    # Step 4: Filter actionable signals
    base_from_aggression = config["min_confluence_score"].get(state.get("aggression", "NORMAL"), 2.0)
    pattern_override = config.get("pattern_confluence_overrides", {}).get("CORRELATION_LAG")
    base_min_score = max(base_from_aggression, pattern_override) if pattern_override else base_from_aggression

    WINDOW_MIN_SCORES = {
        "CLOSING": max(base_min_score, 0.80),
        "MODERATE": max(base_min_score, 0.60),
        "STRONG": base_min_score,
    }

    min_quality = config.get("correlation_window_min_quality", "MODERATE")
    quality_rank = {"CLOSING": 0, "MODERATE": 1, "STRONG": 2}
    min_rank = quality_rank.get(min_quality, 1)

    actionable = [
        s for s in all_signals
        if s["score"] >= WINDOW_MIN_SCORES.get(s.get("window_quality", "CLOSING"), 0.80)
        and quality_rank.get(s.get("window_quality", "CLOSING"), 0) >= min_rank
    ]

    save_state(state)

    # Build leader summary for output
    leader_summary = {}
    for name, r in leader_results.items():
        leader_summary[name] = {
            "triggered": r.get("triggered", False),
            "direction": r.get("direction"),
            "dominant_window": r.get("dominant_window"),
            "dominant_move_pct": r.get("dominant_move_pct"),
            "move_1h_pct": r.get("move_1h_pct", 0),
            "move_4h_pct": r.get("move_4h_pct", 0),
            "move_12h_pct": r.get("move_12h_pct", 0),
            "move_24h_pct": r.get("move_24h_pct", 0),
        }

    output({
        "action": "correlation_scan",
        "leaders_triggered": triggered_leaders,
        "leaders": leader_summary,
        "alts_scanned": len(scanned_assets),
        "lagging_found": len(all_signals),
        "actionable": len(actionable),
        "available_slots": config["max_slots"] - len(active_coins),
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "active_positions": list(active_coins)
    })


if __name__ == "__main__":
    main()
