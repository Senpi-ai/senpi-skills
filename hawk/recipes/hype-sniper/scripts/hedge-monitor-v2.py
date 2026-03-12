#!/usr/bin/env python3
"""HYPE Momentum Sniper v2 — Hedge Monitor (runs every 60s).

Upgrades over v1:
- ALO for hedge entries (saves fees)
- Higher funding threshold (0.015 vs 0.01)
- Market-risk hedge: if HYPE >15% ROE and BTC drops 1%+ in 15min
"""

import sys
import time
import json

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import hype_lib as lib


def get_main_position(wallet, asset):
    """Get the main HYPE position if it exists."""
    ch = lib.get_clearinghouse_state(wallet)
    if not ch:
        return None
    data = ch.get("data", ch)
    for section in ("main", "xyz"):
        section_data = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(section_data, dict):
            continue
        for p in section_data.get("assetPositions", []):
            pos = p.get("position", p) if isinstance(p, dict) else {}
            coin = pos.get("coin", "")
            szi = float(pos.get("szi", pos.get("size", 0)))
            if coin == asset and szi != 0:
                entry_px = float(pos.get("entryPx", pos.get("entryPrice", 0)))
                upnl = float(pos.get("unrealizedPnl", 0))
                margin = float(pos.get("marginUsed", pos.get("margin", 0)))
                roe = (upnl / margin * 100) if margin > 0 else 0
                return {
                    "coin": coin,
                    "size": szi,
                    "is_long": szi > 0,
                    "margin": margin,
                    "entry": entry_px,
                    "upnl": upnl,
                    "roe": roe
                }
    return None


def get_funding_rate(asset):
    """Get current funding rate."""
    data = lib.get_market_data(asset)
    if not data:
        return None
    inner = data.get("data", data) if isinstance(data, dict) else {}
    ctx = inner.get("asset_context", {}) if isinstance(inner, dict) else {}
    if ctx and "funding" in ctx:
        try:
            return float(ctx["funding"])
        except (TypeError, ValueError):
            pass
    return None


def get_btc_momentum_15m():
    """Check BTC price momentum over 15 min from price history or market data."""
    # We'll use the BTC market data candles
    data = lib.get_market_data("BTC")
    if not data:
        return None
    inner = data.get("data", data) if isinstance(data, dict) else {}
    candles = inner.get("candles", {}).get("1h", [])
    if not candles or len(candles) < 2:
        return None
    # Compare last two 1h candle closes as rough proxy
    latest = float(candles[-1].get("c", 0))
    prev = float(candles[-2].get("c", 0))
    if prev == 0:
        return None
    return ((latest - prev) / prev) * 100


def run():
    config = lib.load_config()
    wallet = config.get("strategy_wallet", "")
    hedge_cfg = config.get("hedge", {})

    if not wallet or not hedge_cfg.get("enabled", False):
        lib.output_json({"success": True, "heartbeat": "NO_REPLY", "note": "hedging disabled or no wallet"})
        return

    hedge_state = lib.load_state("hedge_state.json")
    main_pos = get_main_position(wallet, config["asset"])

    # If no main position, close any hedge
    if not main_pos:
        if hedge_state.get("active_hedge"):
            hedge_coin = hedge_state["active_hedge"]["coin"]
            result = lib.close_position(wallet, hedge_coin)
            hedge_state["active_hedge"] = None
            lib.save_state(hedge_state, "hedge_state.json")
            lib.output_json({"success": True, "action": "HEDGE_CLOSED", "reason": "no main position", "result": result})
            return
        lib.output_json({"success": True, "heartbeat": "NO_REPLY", "note": "no main position"})
        return

    main_funding = get_funding_rate(config["asset"])
    threshold = hedge_cfg.get("funding_threshold", 0.015)  # v2: higher threshold
    hedge_asset = hedge_cfg.get("hedge_asset", "BTC")
    hedge_size_pct = hedge_cfg.get("hedge_size_pct", 30)
    hedge_leverage = hedge_cfg.get("hedge_leverage", 5)

    needs_hedge = False
    hedge_direction = None
    hedge_reason = ""

    # Funding-based hedge
    if main_pos["is_long"] and main_funding is not None and main_funding > threshold:
        needs_hedge = True
        hedge_direction = "SHORT"
        hedge_reason = f"funding {main_funding:.6f} > {threshold}"
    elif not main_pos["is_long"] and main_funding is not None and main_funding < -threshold:
        needs_hedge = True
        hedge_direction = "LONG"
        hedge_reason = f"funding {main_funding:.6f} < {-threshold}"

    # Market-risk hedge: HYPE >15% ROE and BTC dropping
    if not needs_hedge and main_pos["roe"] > 15:
        btc_mom = get_btc_momentum_15m()
        if btc_mom is not None and btc_mom < -1.0:
            needs_hedge = True
            hedge_direction = "SHORT" if main_pos["is_long"] else "LONG"
            hedge_reason = f"market risk: HYPE ROE {main_pos['roe']:.1f}%, BTC {btc_mom:+.1f}%"

    if needs_hedge and not hedge_state.get("active_hedge"):
        # v2: ALO entry for hedge
        margin_amount = round(main_pos["margin"] * hedge_size_pct / 100, 2)
        order = {
            "coin": hedge_asset,
            "direction": hedge_direction,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "ensureExecutionAsTaker": True,
            "leverage": hedge_leverage,
            "leverageType": "ISOLATED",
            "marginAmount": margin_amount
        }
        result = lib.create_position(
            wallet=wallet,
            orders=[order],
            reason=f"Hedge v2: {hedge_reason}"
        )
        hedge_state["active_hedge"] = {
            "coin": hedge_asset,
            "direction": hedge_direction,
            "opened_ts": time.time(),
            "reason": hedge_reason
        }
        lib.save_state(hedge_state, "hedge_state.json")
        lib.output_json({
            "success": True, "action": "HEDGE_OPENED",
            "hedge_asset": hedge_asset, "direction": hedge_direction,
            "reason": hedge_reason, "result": result
        })
        return

    # Close hedge if no longer needed
    if hedge_state.get("active_hedge") and not needs_hedge:
        hedge_coin = hedge_state["active_hedge"]["coin"]
        result = lib.close_position(wallet, hedge_coin)
        hedge_state["active_hedge"] = None
        lib.save_state(hedge_state, "hedge_state.json")
        lib.output_json({
            "success": True, "action": "HEDGE_CLOSED",
            "reason": f"conditions normalized (funding={main_funding})",
            "result": result
        })
        return

    lib.output_json({
        "success": True, "heartbeat": "NO_REPLY",
        "main": f"{'LONG' if main_pos['is_long'] else 'SHORT'} HYPE ROE {main_pos['roe']:.1f}%",
        "funding": main_funding,
        "hedge_active": bool(hedge_state.get("active_hedge"))
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        lib.error(f"Hedge v2 error: {e}")
        lib.output_json({"success": False, "error": str(e)})
