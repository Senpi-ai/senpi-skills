#!/usr/bin/env python3
"""EAGLE Scanner — Macro Event / Correlation Break Detector.

Monitors BTC + top alts simultaneously. When BTC dumps but one asset doesn't follow
(correlation break), that asset has either an independent catalyst (LONG it) or is
about to catch up (SHORT it). Also detects sudden cross-asset volume explosions
(macro event) and positions in the direction of first movers.

Runs every 3 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eagle_config as cfg


# ─── Asset Groups ─────────────────────────────────────────────

DEFAULT_LEADERS = ["BTC", "ETH"]
DEFAULT_ALTS = [
    "SOL", "DOGE", "AVAX", "LINK", "ADA", "DOT", "NEAR", "ATOM",
    "SEI", "SUI", "PEPE", "WIF", "RENDER", "HYPE", "ARB", "OP",
]


def fetch_multi_asset_candles(assets, interval="1h"):
    """Fetch candles for multiple assets. Returns {asset: candles}."""
    results = {}
    for asset in assets:
        data = cfg.mcporter_call("market_get_asset_data", asset=asset,
                                  candle_intervals=[interval],
                                  include_funding=False, include_order_book=False)
        if data and data.get("success"):
            candles = data.get("data", {}).get("candles", {}).get(interval, [])
            if candles:
                results[asset] = candles
    return results


def price_change_pct(candles, n_bars=1):
    """Calculate price change % over last n bars."""
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def volume_spike(candles, lookback=4):
    """Check if latest bar volume is a spike vs average."""
    if len(candles) < lookback + 1:
        return 1.0
    vols = []
    for c in candles[-(lookback + 1):-1]:
        v = float(c.get("volume", c.get("v", c.get("vlm", 0))))
        vols.append(v)
    avg = sum(vols) / len(vols) if vols else 1
    latest = float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


def detect_correlation_breaks(leaders_data, alts_data, entry_cfg):
    """Find alts that diverge from leader moves."""
    min_leader_move = entry_cfg.get("minLeaderMovePct", 1.5)
    max_alt_move = entry_cfg.get("maxAltMovePct", 0.5)
    signals = []

    for leader, candles in leaders_data.items():
        leader_move_1h = price_change_pct(candles, 1)
        leader_move_4h = price_change_pct(candles, 4)

        # Need significant leader move
        if abs(leader_move_1h) < min_leader_move and abs(leader_move_4h) < min_leader_move * 1.5:
            continue

        leader_dir = "down" if leader_move_1h < 0 else "up"

        for alt, alt_candles in alts_data.items():
            alt_move_1h = price_change_pct(alt_candles, 1)

            # Alt didn't follow leader
            if abs(alt_move_1h) > max_alt_move:
                continue

            # Score the divergence
            score = 0
            reasons = []
            divergence = abs(leader_move_1h) - abs(alt_move_1h)

            # Strong divergence
            if divergence > 2.0:
                score += 3
                reasons.append(f"strong_divergence_{divergence:.1f}pct")
            elif divergence > 1.0:
                score += 2
                reasons.append(f"divergence_{divergence:.1f}pct")

            # Volume on alt (if low volume = hasn't reacted yet = catch-up trade)
            alt_vol = volume_spike(alt_candles)
            if alt_vol < 0.7:
                score += 1
                reasons.append("alt_low_volume")
                # Catch-up trade: alt will follow leader
                direction = "SHORT" if leader_dir == "down" else "LONG"
                reasons.append(f"catch_up_{leader}")
            else:
                # Independent catalyst: alt is resisting
                direction = "LONG" if leader_dir == "down" else "SHORT"
                score += 1
                reasons.append(f"independent_catalyst_vs_{leader}")

            # Leader move strength bonus
            if abs(leader_move_1h) > 3.0:
                score += 1
                reasons.append(f"{leader}_{leader_move_1h:+.1f}pct")

            if score >= entry_cfg.get("minScore", 5):
                signals.append({
                    "coin": alt,
                    "direction": direction,
                    "score": score,
                    "reasons": reasons,
                    "leader": leader,
                    "leaderMove1h": leader_move_1h,
                    "altMove1h": alt_move_1h,
                    "divergence": divergence,
                    "price": float(alt_candles[-1].get("close", alt_candles[-1].get("c", 0))),
                })

    return signals


def detect_macro_event(all_data, entry_cfg):
    """Detect simultaneous volume explosion across multiple assets."""
    min_assets_spiking = entry_cfg.get("minAssetsSpiking", 4)
    min_vol_ratio = entry_cfg.get("minVolSpikeRatio", 2.0)

    spiking = []
    for asset, candles in all_data.items():
        ratio = volume_spike(candles)
        move = price_change_pct(candles, 1)
        if ratio >= min_vol_ratio:
            spiking.append({"asset": asset, "vol_ratio": ratio, "move_1h": move})

    if len(spiking) < min_assets_spiking:
        return []

    # Macro event detected — find the first movers (biggest moves)
    spiking.sort(key=lambda x: abs(x["move_1h"]), reverse=True)
    dominant_dir = "LONG" if sum(s["move_1h"] for s in spiking) > 0 else "SHORT"

    signals = []
    # Enter the strongest movers in the dominant direction
    for s in spiking[:3]:
        if (s["move_1h"] > 0 and dominant_dir == "LONG") or (s["move_1h"] < 0 and dominant_dir == "SHORT"):
            signals.append({
                "coin": s["asset"],
                "direction": dominant_dir,
                "score": 6 + int(s["vol_ratio"]),
                "reasons": [
                    f"macro_event_{len(spiking)}_assets",
                    f"vol_spike_{s['vol_ratio']:.1f}x",
                    f"first_mover_{s['move_1h']:+.1f}pct",
                ],
                "price": 0,  # filled by caller
            })

    return signals


def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", 2)
    active_coins = {p["coin"] for p in positions}

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"max positions"})
        return

    entry_cfg = config.get("entry", {})
    leaders = config.get("leaders", DEFAULT_LEADERS)
    alts = config.get("alts", DEFAULT_ALTS)

    # Fetch all candles
    all_assets = leaders + alts
    all_data = fetch_multi_asset_candles(all_assets)

    leaders_data = {k: v for k, v in all_data.items() if k in leaders}
    alts_data = {k: v for k, v in all_data.items() if k in alts and k not in active_coins}

    signals = []

    # Check correlation breaks
    corr_signals = detect_correlation_breaks(leaders_data, alts_data, entry_cfg)
    signals.extend(corr_signals)

    # Check macro events
    macro_signals = detect_macro_event(all_data, entry_cfg)
    for s in macro_signals:
        if s["coin"] not in active_coins:
            signals.append(s)

    if not signals:
        leader_moves = {k: price_change_pct(v, 1) for k, v in leaders_data.items()}
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": "no breaks detected",
                     "leaders": leader_moves})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    leverage = config.get("leverage", {}).get("default", 10)
    margin_pct = entry_cfg.get("marginPct", 0.15)
    margin = round(account_value * margin_pct, 2)

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["coin"],
            "direction": best["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "type": "correlation_break" if "divergence" in best else "macro_event",
        "candidates": len(signals),
    })


if __name__ == "__main__":
    run()
