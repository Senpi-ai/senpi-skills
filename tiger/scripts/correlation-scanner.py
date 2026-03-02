#!/usr/bin/env python3
"""
correlation-scanner.py — BTC correlation lag detector for TIGER.
When BTC makes a significant move, identifies alts that haven't moved yet.
Runs every 3min.

MANDATE: Run TIGER correlation scanner. Check BTC moves and find lagging alts. Report signals.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import resolve_dependencies
from tiger_lib import (
    parse_candles, rsi, sma, atr, volume_ratio, confluence_score
)


# ─── Constants ────────────────────────────────────────────────
SCAN_COOLDOWN_MIN = 15   # min minutes between full alt scans for same BTC event
RESCAN_MOVE_PCT = 0.5    # BTC must move this much further (%) to force rescan before cooldown

# Known high-correlation alts (BTC leads these)
HIGH_CORR_ALTS = [
    "ETH", "SOL", "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC",
    "NEAR", "ATOM", "FTM", "ARB", "OP", "INJ", "TIA", "SUI",
    "SEI", "APT", "HYPE", "JUP", "WIF", "BONK", "PEPE"
]


def check_btc_move(config: dict, state: dict, get_asset_candles_fn, now_fn=None) -> dict:
    """Check if BTC has made a significant move in the last 4 hours."""
    from datetime import datetime, timezone
    now_fn = now_fn or (lambda: datetime.now(timezone.utc).isoformat())

    result = get_asset_candles_fn("BTC", ["1h"])
    if not result.get("success") and not result.get("data"):
        return {"triggered": False, "error": "Failed to fetch BTC data"}

    data = result.get("data", result)
    candles = data.get("candles", {}).get("1h", [])
    if len(candles) < 5:
        return {"triggered": False, "error": "Insufficient BTC candle data"}

    _, _, _, closes, volumes = parse_candles(candles)
    current_price = closes[-1]
    check_ts = now_fn()

    # Check 4h move
    price_4h_ago = closes[-4] if len(closes) >= 4 else closes[0]
    move_4h = ((current_price - price_4h_ago) / price_4h_ago) * 100

    # Check 1h move (faster detection)
    price_1h_ago = closes[-2] if len(closes) >= 2 else closes[0]
    move_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100

    threshold = config["btcCorrelationMovePct"]
    triggered = abs(move_4h) >= threshold or abs(move_1h) >= threshold * 0.6

    btc_direction = "LONG" if move_4h > 0 else "SHORT"

    return {
        "triggered": triggered,
        "btc_price": current_price,
        "check_ts": check_ts,
        "move_1h_pct": round(move_1h, 2),
        "move_4h_pct": round(move_4h, 2),
        "direction": btc_direction,
        "threshold": threshold
    }


def check_alt_lag(
    asset: str,
    btc_direction: str,
    btc_move_4h: float,
    instruments_map: dict,
    sm_data: dict,
    config: dict,
    get_asset_candles_fn,
) -> dict:
    """Check if an alt is lagging behind BTC's move."""
    result = get_asset_candles_fn(asset, ["1h", "4h"])
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

    # Alt's 4h move
    alt_price_4h_ago = c1[-4] if len(c1) >= 4 else c1[0]
    alt_move_4h = ((current_price - alt_price_4h_ago) / alt_price_4h_ago) * 100

    # The lag: BTC moved X%, alt moved Y% in same direction
    # Lag = BTC move - alt move (positive = alt is behind)
    if btc_direction == "LONG":
        lag = btc_move_4h - alt_move_4h
    else:
        lag = alt_move_4h - btc_move_4h  # For shorts, alt should be MORE negative

    # Only interested if alt is notably lagging (>50% of BTC's move)
    lag_ratio = lag / abs(btc_move_4h) if btc_move_4h != 0 else 0
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
    if current_rsi:
        if direction == "LONG" and current_rsi > 75:
            rsi_ok = False
        if direction == "SHORT" and current_rsi < 25:
            rsi_ok = False

    # Volume: has the alt started moving? Low volume = hasn't noticed yet
    vol_r = volume_ratio(v1, short_period=2, long_period=8)
    low_volume = vol_r is not None and vol_r < 1.2  # Volume hasn't spiked yet = still lagging

    # ATR for expected catch-up move
    atr_values = atr(h4, l4, c4, 14)
    current_atr = atr_values[-1] if atr_values[-1] else 0
    atr_pct = (current_atr / current_price * 100) if current_price > 0 else 0

    # Expected catch-up in %
    expected_catchup = abs(btc_move_4h) * lag_ratio * 0.8  # Assume 80% of lag closes

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
        "btc_significant_move": (True, 0.20),
        "alt_lagging": (lag_ratio >= 0.5, 0.25),
        "volume_not_spiked": (low_volume, 0.15),  # Good: means the move hasn't started
        "rsi_safe": (rsi_ok, 0.10),
        "sm_aligned": (sm_aligned, 0.15),
        "high_correlation_alt": (asset in HIGH_CORR_ALTS, 0.10),
        "sufficient_leverage": (max_lev >= config["minLeverage"], 0.05),
    }

    score = confluence_score(factors)

    return {
        "asset": asset,
        "pattern": "CORRELATION_LAG",
        "score": round(score, 2),
        "direction": direction,
        "current_price": current_price,
        "alt_move_4h_pct": round(alt_move_4h, 2),
        "lag_pct": round(lag, 2),
        "lag_ratio": round(lag_ratio, 2),
        "window_quality": window_quality,
        "expected_catchup_pct": round(expected_catchup, 2),
        "volume_ratio": round(vol_r, 2) if vol_r else None,
        "volume_quiet": low_volume,
        "rsi": round(current_rsi, 1) if current_rsi else None,
        "sm_aligned": sm_aligned,
        "atr_pct": round(atr_pct, 2),
        "max_leverage": max_lev,
        "factors": {k: v[0] for k, v in factors.items()}
    }


def main(deps=None):
    deps = deps or resolve_dependencies()
    load_config = deps["load_config"]
    load_state = deps["load_state"]
    get_all_instruments = deps["get_all_instruments"]
    get_asset_candles = deps["get_asset_candles"]
    get_sm_markets = deps["get_sm_markets"]
    output = deps["output"]
    get_pattern_min_confluence = deps["get_pattern_min_confluence"]
    get_disabled_patterns = deps["get_disabled_patterns"]
    is_halted = deps["is_halted"]
    halt_reason = deps["halt_reason"]
    get_active_positions = deps["get_active_positions"]
    load_btc_cache = deps["load_btc_cache"]
    save_btc_cache = deps["save_btc_cache"]

    config = load_config()
    state = load_state(config=config)
    pattern = "CORRELATION_LAG"

    if is_halted(state):
        output({"action": "correlation_scan", "halted": True, "reason": halt_reason(state)})
        return
    if pattern in get_disabled_patterns():
        output({
            "action": "correlation_scan",
            "disabled": True,
            "disabled_pattern": pattern,
            "reason": "Pattern disabled by ROAR."
        })
        return

    # Step 1a: Check if BTC has made a significant move
    btc = check_btc_move(config, state, get_asset_candles)
    if not btc["triggered"]:
        save_btc_cache(btc["btc_price"], btc.get("check_ts"), config=config)
        output({
            "action": "correlation_scan",
            "btc_triggered": False,
            "btc_move_4h_pct": btc.get("move_4h_pct", 0),
            "btc_move_1h_pct": btc.get("move_1h_pct", 0),
            "threshold": btc.get("threshold"),
            "message": "BTC hasn't made a significant move. No lag scan needed."
        })
        return

    # Step 1b: Fast-path — skip full alt scan if BTC hasn't moved further since last scan
    cache = load_btc_cache(config=config)
    last_scan_price = cache.get("last_scan_price")
    last_scan_ts = cache.get("last_scan_ts")
    if last_scan_price and last_scan_ts:
        from datetime import datetime, timezone
        try:
            scan_dt = datetime.fromisoformat(str(last_scan_ts))
            minutes_since = (datetime.now(timezone.utc) - scan_dt).total_seconds() / 60
            price_delta_pct = abs(btc["btc_price"] - last_scan_price) / last_scan_price * 100

            if minutes_since < SCAN_COOLDOWN_MIN and price_delta_pct < RESCAN_MOVE_PCT:
                save_btc_cache(btc["btc_price"], btc.get("check_ts"), config=config)
                output({
                    "action": "correlation_scan",
                    "btc_triggered": True,
                    "cached_skip": True,
                    "btc_direction": btc["direction"],
                    "btc_move_4h_pct": btc["move_4h_pct"],
                    "btc_price": btc["btc_price"],
                    "price_delta_since_scan_pct": round(price_delta_pct, 2),
                    "minutes_since_scan": round(minutes_since, 0),
                    "message": f"BTC triggered but only +{price_delta_pct:.2f}% since last scan {minutes_since:.0f}m ago. Skipping alt scan."
                })
                return
        except (ValueError, TypeError):
            pass  # Malformed cache — proceed with full scan

    # Step 2: Fetch instruments for context
    instruments = get_all_instruments()
    instruments_map = {i["name"]: i for i in instruments if not i.get("is_delisted")}

    # Step 3: Fetch SM data
    sm_markets = get_sm_markets(limit=50)
    sm_data = {}
    for m in sm_markets:
        token = m.get("token", "")
        if token not in sm_data:
            sm_data[token] = m

    # Step 4: Scan high-correlation alts first, then other liquid assets
    active_coins = set(get_active_positions(state).keys())
    scan_list = list(HIGH_CORR_ALTS)

    # Add other liquid assets not in the corr list
    other = sorted(
        [i["name"] for i in instruments
         if i["name"] not in HIGH_CORR_ALTS
         and i["name"] != "BTC"
         and not i.get("is_delisted")
         and i.get("max_leverage", 0) >= config["minLeverage"]
         and float(i.get("context", {}).get("dayNtlVlm", 0)) > 5_000_000],
        key=lambda n: float(instruments_map.get(n, {}).get("context", {}).get("dayNtlVlm", 0)),
        reverse=True
    )
    scan_list.extend(other[:10])

    # Deduplicate and filter active positions
    seen = set()
    unique_scan = []
    for a in scan_list:
        if a not in seen and a != "BTC" and a in instruments_map and a not in active_coins:
            seen.add(a)
            unique_scan.append(a)

    signals = []
    # SPEED: Scan HIGH_CORR_ALTS first (top 10), these are most likely to lag
    # Only scan extended list if we haven't found actionable signals in top 10
    fast_scan = unique_scan[:10]
    extended_scan = unique_scan[10:]

    for asset in fast_scan:
        result = check_alt_lag(
            asset, btc["direction"], btc["move_4h_pct"],
            instruments_map, sm_data, config, get_asset_candles
        )
        if result:
            signals.append(result)

    # Only scan extended list if no strong signals found in fast scan
    min_score = get_pattern_min_confluence(config, state, pattern)
    strong_fast = [s for s in signals if s["score"] >= min_score and s.get("window_quality") != "CLOSING"]
    
    if not strong_fast:
        for asset in extended_scan[:10]:
            result = check_alt_lag(
                asset, btc["direction"], btc["move_4h_pct"],
                instruments_map, sm_data, config, get_asset_candles
            )
            if result:
                signals.append(result)

    signals.sort(key=lambda x: x["score"], reverse=True)

    min_score = get_pattern_min_confluence(config, state, pattern)
    actionable = [s for s in signals if s["score"] >= min_score]

    # Save BTC price cache with scan timestamp (enables fast-path on next run)
    save_btc_cache(
        btc["btc_price"], btc.get("check_ts"),
        scan_price=btc["btc_price"], scan_ts=btc.get("check_ts"),
        config=config
    )

    output({
        "action": "correlation_scan",
        "btc_triggered": True,
        "btc_direction": btc["direction"],
        "btc_move_4h_pct": btc["move_4h_pct"],
        "btc_move_1h_pct": btc["move_1h_pct"],
        "alts_scanned": len(unique_scan),
        "lagging_found": len(signals),
        "actionable": len(actionable),
        "available_slots": config["maxSlots"] - len(active_coins),
        "aggression": state.get("aggression", "NORMAL"),
        "top_signals": actionable[:5],
        "active_positions": list(active_coins)
    })


if __name__ == "__main__":
    main()
