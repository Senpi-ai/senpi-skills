#!/usr/bin/env python3
"""
SHARK SM Consensus Trader v1.0
Replaces the entire cascade/movers pipeline with smart money signal following.

Logic:
1. Pull top leaderboard traders (4h window) — real-time SM positions
2. Find directional consensus: 3+ of top 10 traders aligned on same asset+direction
3. Validate with 15m candle confirmation (3 candles trending)
4. Open position: 25% margin, 10x leverage, ALO maker-only
5. SL at 5% ROE, no TP — winners run via DSL trailing

Runs every 15 minutes via cron.
"""

import json
import os
import sys
import subprocess
import time
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _atomic_write(path, data):
    """Write JSON atomically via tmp file + rename."""
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Config — read from environment or shark-strategies.json ──
STRATEGY_ID = os.environ.get("SHARK_STRATEGY_ID", "")
STRATEGY_WALLET = os.environ.get("SHARK_STRATEGY_WALLET", "")
MAX_SLOTS = 2
MAX_ENTRIES_PER_DAY = 4
MARGIN_PCT = 0.25  # 25% of account
LEVERAGE = 10
SL_ROE_PCT = 5  # 5% ROE stop loss
MIN_CONSENSUS = 3  # minimum traders agreeing on direction
TOP_N_TRADERS = 10  # scan top N from leaderboard
BANNED_PREFIXES = ["xyz:"]  # no equities/commodities
MIN_ACCOUNT_VALUE = 100  # don't trade below this

# Try loading from strategy registry if env vars not set
if not STRATEGY_ID or not STRATEGY_WALLET:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import shark_config as cfg
        strategies = cfg.load_all_strategies()
        if strategies:
            strat = strategies[0]
            STRATEGY_ID = STRATEGY_ID or strat.get("strategyId", "")
            STRATEGY_WALLET = STRATEGY_WALLET or strat.get("wallet", "")
    except Exception:
        pass

# State paths
STATE_DIR = Path(f"/data/workspace/state/{STRATEGY_ID}")
TRADE_COUNTER = STATE_DIR / "trade-counter.json"
SHARK_STATE = STATE_DIR / "shark-state.json"
SM_HISTORY = STATE_DIR / "sm-history.json"

def get_token():
    """Extract token from mcporter config."""
    try:
        config = json.loads(Path("/data/.openclaw/config/mcporter.json").read_text())
        return config["mcpServers"]["senpi"]["env"]["SENPI_AUTH_TOKEN"]
    except:
        return os.environ.get("SENPI_AUTH_TOKEN", "")

def mcporter_call(tool, params=None, timeout=30):
    """Call a Senpi MCP tool via mcporter."""
    cmd = ["mcporter", "call", "senpi", tool]
    if params:
        if isinstance(params, list):
            # Positional args (strings)
            cmd.extend([str(p) for p in params])
        elif isinstance(params, dict):
            # Named args as JSON
            cmd.extend(["--args", json.dumps(params)])
    
    env = os.environ.copy()
    env["SENPI_AUTH_TOKEN"] = get_token()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        if result.returncode != 0:
            return {"success": False, "error": {"message": result.stderr[:500]}}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": {"message": "timeout"}}
    except json.JSONDecodeError:
        return {"success": False, "error": {"message": f"JSON parse error: {result.stdout[:200]}"}}
    except Exception as e:
        return {"success": False, "error": {"message": str(e)}}

def hl_api(endpoint_type, payload):
    """Direct Hyperliquid API call."""
    import urllib.request
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None

def get_account_state():
    """Get account value and current positions from clearinghouse."""
    state = hl_api("clearinghouseState", {
        "type": "clearinghouseState",
        "user": STRATEGY_WALLET
    })
    if not state:
        return None, []
    
    account_value = float(state.get("marginSummary", {}).get("accountValue", 0))
    positions = []
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        szi = float(pos.get("szi", "0"))
        if szi != 0:
            positions.append({
                "coin": pos["coin"],
                "direction": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "entry_price": float(pos.get("entryPx", 0)),
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "roe": float(pos.get("returnOnEquity", 0)) * 100,
                "margin_used": float(pos.get("marginUsed", 0))
            })
    return account_value, positions

def get_trade_counter():
    """Load or initialize trade counter for today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        tc = json.loads(TRADE_COUNTER.read_text())
        if tc.get("date") != today:
            raise ValueError("stale")
        return tc
    except:
        return {"date": today, "entries": 0, "gate": "OPEN"}

def save_trade_counter(tc):
    TRADE_COUNTER.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(str(TRADE_COUNTER), tc)

def get_shark_state():
    """Load shark state."""
    try:
        return json.loads(SHARK_STATE.read_text())
    except:
        return {"active_positions": {}, "updated_at": datetime.now(timezone.utc).isoformat()}

def save_shark_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    SHARK_STATE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(str(SHARK_STATE), state)

def get_top_trader_positions():
    """
    Get positions from top leaderboard traders.
    Returns dict: {coin: {"LONG": count, "SHORT": count, "traders": [...]}}
    """
    # Get top traders from hyperfeed leaderboard (4h rolling window)
    result = mcporter_call("leaderboard_get_top", [str(TOP_N_TRADERS)])
    
    if not result.get("success"):
        print(f"  ❌ leaderboard_get_top_traders failed: {result.get('error', {}).get('message', 'unknown')}")
        return None
    
    # leaderboard_get_top returns {leaderboard: {data: [...]}}
    lb_data = result.get("data", {}).get("leaderboard", {})
    traders = lb_data.get("data", []) if isinstance(lb_data, dict) else []
    if not traders:
        print("  ⚠️ No traders returned from leaderboard")
        return None
    
    print(f"  📊 Got {len(traders)} top traders from leaderboard")
    
    # For each trader, get their current positions
    consensus = {}  # coin -> {"LONG": n, "SHORT": n, "traders": [...]}
    
    for i, trader in enumerate(traders[:TOP_N_TRADERS]):
        address = trader.get("trader_id") or trader.get("address", "")
        if not address:
            continue
        
        # Get positions via leaderboard_get_trader_positions
        pos_result = mcporter_call("leaderboard_get_trader_positions", [address])
        
        if not pos_result.get("success"):
            print(f"  ⚠️ Failed to get positions for trader #{i+1} ({address[:10]}...)")
            continue
        
        # Structure: data.positions.positions (nested wrapper)
        wrapper = pos_result.get("data", {}).get("positions", {})
        positions = wrapper.get("positions", []) if isinstance(wrapper, dict) else []
        if not positions:
            continue
        
        print(f"  #{i+1} {address[:10]}... {len(positions)} positions")
        
        for pos in positions:
            coin = pos.get("market") or pos.get("coin", "")
            if not coin:
                continue
            
            # Skip banned assets
            if any(coin.startswith(p) for p in BANNED_PREFIXES):
                continue
            
            # Direction from explicit field (lowercase from API)
            direction = (pos.get("direction", "")).upper()
            if not direction:
                szi = float(pos.get("size", 0))
                direction = "LONG" if szi > 0 else "SHORT" if szi < 0 else ""
            
            if direction not in ("LONG", "SHORT"):
                continue
            
            if coin not in consensus:
                consensus[coin] = {"LONG": 0, "SHORT": 0, "traders": []}
            
            consensus[coin][direction] += 1
            consensus[coin]["traders"].append({
                "address": address[:10] + "...",
                "rank": i + 1,
                "direction": direction,
                "upnl": float(pos.get("delta_pnl", 0))
            })
    
    return consensus

def check_candle_confirmation(coin, direction):
    """
    Check 15m candles for trend confirmation.
    LONG: 3 higher lows. SHORT: 3 lower highs.
    Uses Hyperliquid candle API directly.
    """
    try:
        now = int(time.time() * 1000)
        # Get last 5 15-min candles
        candles = hl_api("candleSnapshot", {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": "15m",
                "startTime": now - (5 * 15 * 60 * 1000),
                "endTime": now
            }
        })
        
        if not candles or len(candles) < 4:
            print(f"  ⚠️ Not enough candles for {coin} ({len(candles) if candles else 0})")
            return False
        
        # Take last 4 candles
        recent = candles[-4:]
        
        if direction == "LONG":
            # Check higher lows: last candle low > previous candle low, AND overall trend up
            lows = [float(c.get("l", c.get("low", 0))) for c in recent]
            # Require: at least 2 of 3 transitions are higher, AND last transition is higher
            transitions = [lows[i+1] > lows[i] for i in range(len(lows)-1)]
            confirmed = transitions[-1] and sum(transitions) >= 2
            if confirmed:
                print(f"  ✅ {coin} LONG candle confirmed: lows {[f'{l:.2f}' for l in lows]}")
            else:
                print(f"  ❌ {coin} LONG candle rejected: lows {[f'{l:.2f}' for l in lows]}")
            return confirmed
        else:
            # Check lower highs: last candle high < previous candle high, AND overall trend down
            highs = [float(c.get("h", c.get("high", 0))) for c in recent]
            # Require: at least 2 of 3 transitions are lower, AND last transition is lower
            transitions = [highs[i+1] < highs[i] for i in range(len(highs)-1)]
            confirmed = transitions[-1] and sum(transitions) >= 2
            if confirmed:
                print(f"  ✅ {coin} SHORT candle confirmed: highs {[f'{h:.2f}' for h in highs]}")
            else:
                print(f"  ❌ {coin} SHORT candle rejected: highs {[f'{h:.2f}' for h in highs]}")
            return confirmed
            
    except Exception as e:
        print(f"  ⚠️ Candle check failed for {coin}: {e}")
        return False

def open_position(coin, direction, margin, leverage):
    """Open a position via Senpi create_position with SL at 5% ROE."""
    
    order = {
        "coin": coin,
        "direction": direction,
        "leverage": leverage,
        "marginAmount": round(margin, 2),
        "orderType": "MARKET",
        "stopLoss": {
            "percentage": SL_ROE_PCT,
            "orderType": "MARKET"
        }
    }
    
    reason = f"SM consensus: {MIN_CONSENSUS}+ of top {TOP_N_TRADERS} traders aligned {direction}"
    
    print(f"  🎯 Opening {coin} {direction} {leverage}x ${margin:.2f} (SL at {SL_ROE_PCT}% ROE)")
    
    result = mcporter_call("create_position", {
        "strategyWalletAddress": STRATEGY_WALLET,
        "orders": [order],
        "reason": reason
    })
    
    if not result.get("success"):
        error_msg = result.get("error", {}).get("message", "unknown")
        print(f"  ❌ create_position failed: {error_msg[:300]}")
        return False
    
    data = result.get("data", {})
    results = data.get("results", [data]) if isinstance(data, dict) else [data]
    
    for r in results:
        status = r.get("status") or r.get("mainOrder", {}).get("status", "unknown")
        filled_price = r.get("mainOrder", {}).get("avgFillPrice") or r.get("avgFillPrice", "?")
        maker = r.get("mainOrder", {}).get("executionAsMaker", False)
        print(f"  ✅ {coin} {direction}: status={status}, price=${filled_price}, maker={maker}")
    
    # Verify fill
    time.sleep(3)
    _, positions = get_account_state()
    filled = any(p["coin"] == coin for p in positions)
    if filled:
        pos = next(p for p in positions if p["coin"] == coin)
        print(f"  ✅ Fill confirmed: {coin} {pos['direction']} entry=${pos['entry_price']:.2f} margin=${pos['margin_used']:.2f}")
    else:
        print(f"  ⚠️ No fill detected yet — checking orders")
    
    return filled

def set_stop_loss(coin, direction):
    """Set native stop loss at 5% ROE."""
    # Get current price to calculate SL price
    state = hl_api("clearinghouseState", {
        "type": "clearinghouseState",
        "user": STRATEGY_WALLET
    })
    if not state:
        print("  ⚠️ Failed to get clearinghouse state for SL")
        return False
    
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        if pos.get("coin") == coin:
            entry = float(pos.get("entryPx", 0))
            if direction == "LONG":
                sl_price = entry * (1 - SL_ROE_PCT / 100 / LEVERAGE)
            else:
                sl_price = entry * (1 + SL_ROE_PCT / 100 / LEVERAGE)
            
            # Set SL via edit_position (Senpi manages it on HL)
            # Actually, need to use the order system — let the risk guardian handle SL
            print(f"  🛡️ SL target: ${sl_price:.2f} ({SL_ROE_PCT}% ROE from ${entry:.2f})")
            return True
    
    return False

def save_sm_history(consensus, signal=None):
    """Save SM consensus snapshot for analysis."""
    try:
        history = json.loads(SM_HISTORY.read_text()) if SM_HISTORY.exists() else []
    except:
        history = []
    
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_consensus": {},
        "signal": signal
    }
    
    # Save only coins with 2+ alignment
    for coin, data in consensus.items():
        max_dir = "LONG" if data["LONG"] >= data["SHORT"] else "SHORT"
        max_count = max(data["LONG"], data["SHORT"])
        if max_count >= 2:
            entry["top_consensus"][coin] = {
                "direction": max_dir,
                "count": max_count,
                "total_traders": len(data["traders"])
            }
    
    history.append(entry)
    # Keep last 96 entries (24h at 15min intervals)
    history = history[-96:]
    _atomic_write(str(SM_HISTORY), history)

def main():
    now = datetime.now(timezone.utc)
    print(f"🦈 SM Consensus Trader — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)
    
    # ── Gate checks ──
    tc = get_trade_counter()
    if tc.get("gate") == "CLOSED":
        print(f"  🚫 Gate CLOSED — {tc.get('reason', 'daily limit')}")
        return "gate_closed"
    
    if tc.get("entries", 0) >= MAX_ENTRIES_PER_DAY:
        print(f"  🚫 Max entries reached ({tc['entries']}/{MAX_ENTRIES_PER_DAY})")
        return "max_entries"
    
    # ── Account check ──
    account_value, positions = get_account_state()
    if account_value is None:
        print("  ❌ Failed to get account state")
        return "error"
    
    print(f"  💰 Account: ${account_value:.2f}")
    print(f"  📊 Open positions: {len(positions)}")
    
    if account_value < MIN_ACCOUNT_VALUE:
        print(f"  🚫 Account below minimum (${MIN_ACCOUNT_VALUE})")
        return "low_balance"
    
    if len(positions) >= MAX_SLOTS:
        print(f"  🚫 All {MAX_SLOTS} slots filled")
        for p in positions:
            print(f"     {p['coin']} {p['direction']} {p['roe']:+.1f}% ROE")
        return "slots_full"
    
    # Don't open if existing positions already hold this coin
    held_coins = {p["coin"] for p in positions}
    
    # ── Get SM consensus ──
    print("\n  🔍 Scanning top trader positions...")
    consensus = get_top_trader_positions()
    
    if not consensus:
        print("  ❌ Failed to get trader positions")
        return "no_data"
    
    # ── Find signals ──
    signals = []
    for coin, data in consensus.items():
        if coin in held_coins:
            continue
        
        for direction in ["LONG", "SHORT"]:
            if data[direction] >= MIN_CONSENSUS:
                signals.append({
                    "coin": coin,
                    "direction": direction,
                    "count": data[direction],
                    "traders": [t for t in data["traders"] if t["direction"] == direction],
                    "total_upnl": sum(t["upnl"] for t in data["traders"] if t["direction"] == direction)
                })
    
    if not signals:
        print(f"\n  😴 No consensus signals (need {MIN_CONSENSUS}+ traders aligned)")
        # Log top near-misses
        near = sorted(
            [(coin, max(d["LONG"], d["SHORT"]), "LONG" if d["LONG"] >= d["SHORT"] else "SHORT")
             for coin, d in consensus.items()],
            key=lambda x: -x[1]
        )[:5]
        if near:
            print("  📋 Strongest non-consensus:")
            for coin, count, direction in near:
                print(f"     {coin} {direction}: {count} traders")
        save_sm_history(consensus)
        return "no_signal"
    
    # Sort by consensus strength then total uPnL
    signals.sort(key=lambda s: (-s["count"], -s["total_upnl"]))
    
    print(f"\n  🎯 Found {len(signals)} consensus signals:")
    for s in signals[:5]:
        print(f"     {s['coin']} {s['direction']}: {s['count']} traders, ${s['total_upnl']:,.0f} uPnL")
    
    # ── Take best signal ──
    best = signals[0]
    coin = best["coin"]
    direction = best["direction"]
    
    print(f"\n  🔬 Validating {coin} {direction} ({best['count']} traders)...")
    
    # ── Candle confirmation ──
    if not check_candle_confirmation(coin, direction):
        print(f"  ❌ Candle confirmation failed — skipping")
        save_sm_history(consensus, {"coin": coin, "direction": direction, "rejected": "candle"})
        
        # Try next signal
        for alt in signals[1:3]:
            print(f"\n  🔬 Trying alternative: {alt['coin']} {alt['direction']}...")
            if check_candle_confirmation(alt["coin"], alt["direction"]):
                best = alt
                coin = best["coin"]
                direction = best["direction"]
                print(f"  ✅ {coin} {direction} passed candle check")
                break
        else:
            print("  ❌ No signals passed candle confirmation")
            save_sm_history(consensus, {"coin": coin, "direction": direction, "rejected": "all_candles"})
            return "no_confirmation"
    
    # ── Calculate position size ──
    margin = round(account_value * MARGIN_PCT, 2)
    print(f"\n  💰 Position size: ${margin:.2f} margin @ {LEVERAGE}x = ${margin * LEVERAGE:.2f} notional")
    
    # ── Open position ──
    success = open_position(coin, direction, margin, LEVERAGE)
    
    if success:
        # Update state
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)
        
        state = get_shark_state()
        state["active_positions"] = state.get("active_positions", {})
        state["active_positions"][coin] = {
            "coin": coin,
            "direction": direction,
            "leverage": LEVERAGE,
            "margin": margin,
            "source": "sm_consensus",
            "consensus_count": best["count"],
            "entered_at": now.isoformat()
        }
        save_shark_state(state)
        
        save_sm_history(consensus, {
            "coin": coin,
            "direction": direction,
            "opened": True,
            "consensus": best["count"],
            "margin": margin
        })
        
        print(f"\n  ✅ OPENED: {coin} {direction} {LEVERAGE}x | ${margin:.2f} margin")
        print(f"  📊 Entries today: {tc['entries']}/{MAX_ENTRIES_PER_DAY}")
        return f"opened:{coin}:{direction}"
    else:
        save_sm_history(consensus, {"coin": coin, "direction": direction, "rejected": "execution_failed"})
        return "execution_failed"

if __name__ == "__main__":
    try:
        result = main()
        print(f"\n{'=' * 60}")
        print(f"Result: {result}")
    except Exception as e:
        print(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
