#!/usr/bin/env python3
"""CROC Scanner — Funding Rate Arbitrage.

Scans all assets for extreme funding rates. Enters AGAINST the funding direction
to collect the rate while positioning for the mean-reversion snap.

Long when funding deeply negative (shorts pay you to hold).
Short when funding deeply positive (longs pay you to hold).

Runs every 15 minutes.
"""

import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import croc_config as cfg


def get_funding_rates():
    """Fetch funding rates for all instruments."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    rates = []
    for inst in instruments:
        coin = inst.get("coin") or inst.get("name", "")
        funding = inst.get("funding")
        if not coin or funding is None:
            continue
        oi = float(inst.get("openInterest", 0))
        rates.append({
            "coin": coin,
            "funding": float(funding),
            "fundingAnnualized": float(funding) * 8760,  # hourly rate * hours/year
            "openInterest": oi,
        })
    return rates


def get_candle_trend(coin, direction):
    """Check if 1h candles support the trade direction."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h"], include_funding=False)
    if not data or not data.get("success"):
        return True  # proceed if can't check
    candles = data.get("data", {}).get("candles", {}).get("1h", [])
    if len(candles) < 4:
        return True
    closes = [float(c.get("close", c.get("c", 0))) for c in candles[-4:]]
    if direction == "LONG":
        return closes[-1] > closes[-4]  # trending up
    else:
        return closes[-1] < closes[-4]  # trending down


def score_signal(rate, config):
    """Score a funding rate signal. Higher = better opportunity."""
    funding_ann = abs(rate["fundingAnnualized"])
    min_funding = config.get("minFundingAnnualizedPct", 20)

    if funding_ann < min_funding:
        return 0, []

    reasons = []
    score = 0

    # Base score from funding extremity
    if funding_ann >= 50:
        score += 4
        reasons.append(f"extreme_funding_{funding_ann:.0f}pct")
    elif funding_ann >= 35:
        score += 3
        reasons.append(f"high_funding_{funding_ann:.0f}pct")
    elif funding_ann >= min_funding:
        score += 2
        reasons.append(f"elevated_funding_{funding_ann:.0f}pct")

    # OI concentration bonus
    if rate["openInterest"] > 5_000_000:
        score += 1
        reasons.append("high_oi")

    # Funding direction bonus (consistent extreme = more likely to snap)
    if funding_ann >= 40:
        score += 1
        reasons.append("snap_likely")

    return score, reasons


def run():
    config = cfg.load_config()
    wallet, strategy_id = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "no wallet"})
        return

    # Gate check
    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"gate={tc['gate']}: {tc.get('gateReason', '')}"})
        return

    # Position check
    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", 2)
    active_coins = {p["coin"] for p in positions}

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"max positions ({len(positions)}/{max_positions})"})
        return

    # Max entries check
    risk = config.get("risk", {})
    if tc.get("entries", 0) >= risk.get("maxEntriesPerDay", 4):
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "max entries"})
        return

    # Scan funding rates
    rates = get_funding_rates()
    if not rates:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK", "note": "no funding data"})
        return

    entry_cfg = config.get("entry", {})
    signals = []

    for rate in rates:
        if rate["coin"] in active_coins:
            continue

        score, reasons = score_signal(rate, entry_cfg)
        if score < entry_cfg.get("minScore", 4):
            continue

        # Direction: opposite to funding
        direction = "LONG" if rate["funding"] > 0 else "SHORT"

        # 1h trend confirmation
        if entry_cfg.get("requireTrendConfirm", True):
            if not get_candle_trend(rate["coin"], direction):
                continue

        signals.append({
            "coin": rate["coin"],
            "direction": direction,
            "score": score,
            "reasons": reasons,
            "funding": rate["funding"],
            "fundingAnnualized": rate["fundingAnnualized"],
            "oi": rate["openInterest"],
        })

    if not signals:
        cfg.output({"success": True, "heartbeat": "HEARTBEAT_OK",
                     "note": f"scanned {len(rates)} assets, no qualifying signals"})
        return

    # Pick best signal
    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Calculate margin
    leverage = config.get("leverage", {}).get("default", 8)
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
        "scanned": len(rates),
        "candidates": len(signals),
        "all_signals": [{"coin": s["coin"], "dir": s["direction"], "score": s["score"]} for s in signals[:5]],
    })


if __name__ == "__main__":
    run()
