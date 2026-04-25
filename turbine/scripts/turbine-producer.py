#!/usr/bin/env python3
# Senpi TURBINE Producer v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""TURBINE v2.0 Producer — Multi-asset rotation signal emitter.

Each cron tick:
  1. Acquire reentrancy lock (fcntl) — skip if prior run still alive
  2. Load session state (rotation index, last-direction per asset)
  3. Query open positions on strategy wallet via MCP clearinghouse
  4. For each empty slot (up to max configured):
       a. Advance rotation index, pick next asset (XYZ-weighted RR)
       b. Query funding regime + current spread via MCP
       c. Choose direction:
            - LONG_CROWDED → SHORT (collect funding)
            - SHORT_CROWDED → LONG (collect funding)
            - FLAT/NEUTRAL → alternate vs last_direction for this asset
       d. Skip if spread > threshold (main 5 bps, xyz 15 bps)
       e. Emit signal via `openclaw senpi external-scanner ingest`
  5. Save session state

NO execution code. NO exit logic. NO position management.
The v2 runtime's DSL engine handles all of that, with
ensure_execution_as_taker: false on both entry AND exit.

Environment:
  SENPI_API_KEY               — MCP access
  STRATEGY_ADDRESS / TURBINE_WALLET — Turbine wallet (must match runtime)
  STRATEGY_ID / TURBINE_STRATEGY_ID — strategy id (for signal scope)
  OPENCLAW_BIN                — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME       — optional, default "turbine_signals"

Runs every 60s via cron. Single tick = one rotation decision per empty slot.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# WALL-CLOCK TIMEOUT (v2.0.3 — 2026-04-24)
# ═══════════════════════════════════════════════════════════════
# Hard guarantee the producer doesn't exceed the cron interval.
# Without this, a hanging MCP subprocess (any stalled market data
# call) holds the fcntl lock forever. Subsequent ticks silently
# skip with "prior run holds lock" and the producer is effectively
# dead while appearing active.
#
# At 60s cron + 3 slots × 14 rotation entries × 30s MCP retry,
# worst case was >10 minutes per tick. This caps it.

_PRODUCER_MAX_SECONDS = int(os.environ.get("TURBINE_PRODUCER_MAX_SECONDS", 45))


class ProducerTimeout(Exception):
    """Raised when producer exceeds its wall-clock budget."""


def _alarm_handler(signum, frame):
    raise ProducerTimeout(f"producer exceeded {_PRODUCER_MAX_SECONDS}s budget")


def install_timeout():
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(_PRODUCER_MAX_SECONDS)


def clear_timeout():
    signal.alarm(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import turbine_config as cfg


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD (inherited from Jackal/Scorpion's Daniel-review fix)
# ═══════════════════════════════════════════════════════════════

# v2.0.6: per-wallet lock isolation. STATE_DIR is now wallet-scoped
# (derived from TURBINE_WALLET / STRATEGY_ADDRESS env var) so multiple
# concurrent producers — one per wallet — don't share a single lockfile.
# Without this, only 1 of N wallets would run per cron tick.
_LOCK_DIR = cfg.STATE_DIR
_LOCK_DIR.mkdir(parents=True, exist_ok=True)
_LOCK_PATH = _LOCK_DIR / "producer.lock"


def acquire_lock():
    try:
        f = open(_LOCK_PATH, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()} {int(time.time())}\n")
        f.flush()
        return f
    except (IOError, OSError, BlockingIOError):
        return None


def release_lock(lock_file):
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    except (IOError, OSError):
        pass


# ═══════════════════════════════════════════════════════════════
# ROTATION UNIVERSE
# ═══════════════════════════════════════════════════════════════
# XYZ-weighted 70 / HL main 30. The weighting is implicit in the
# rotation list construction: we include XYZ assets 2.33× to bias
# selection probability under simple round-robin.
#
# XYZ fee schedule is ~0.006% both sides (confirmed empirically from
# Scorpion BRENTOIL fills). HL main is 0.015% maker / 0.045% taker.
# Rotating through XYZ gets Turbine closer to the <1 bp/RT budget.

ROTATION_LIST = [
    # XYZ DEX (70% weight)
    "xyz:BRENTOIL",
    "xyz:GOLD",
    "xyz:SPX",
    "xyz:TSLA",
    "xyz:NVDA",
    "xyz:BRENTOIL",  # duplicates increase selection probability for XYZ-heavy rotation
    "xyz:GOLD",
    "xyz:SPX",
    "xyz:TSLA",
    "xyz:NVDA",
    # HL main (30% weight)
    "BTC",
    "ETH",
    "SOL",
    "HYPE",
]

# Per-asset spread thresholds. Wider for XYZ (less liquid during off-hours).
MAX_SPREAD_BPS_MAIN = 5
MAX_SPREAD_BPS_XYZ = 15


# ═══════════════════════════════════════════════════════════════
# CONFIG (load from env + config.json, with sensible defaults)
# ═══════════════════════════════════════════════════════════════

def load_runtime_params():
    c = cfg.load_config()
    return {
        "max_slots": int(os.environ.get("TURBINE_MAX_SLOTS", c.get("maxSlots", 3))),
        "margin_per_slot_usd": float(os.environ.get("TURBINE_MARGIN_USD", c.get("marginPerSlotUsd", 500))),
        "leverage": float(os.environ.get("TURBINE_LEVERAGE", c.get("leverage", 5))),
        "max_spread_bps_main": float(os.environ.get("TURBINE_MAX_SPREAD_MAIN", c.get("maxSpreadBpsMain", MAX_SPREAD_BPS_MAIN))),
        "max_spread_bps_xyz": float(os.environ.get("TURBINE_MAX_SPREAD_XYZ", c.get("maxSpreadBpsXyz", MAX_SPREAD_BPS_XYZ))),
    }


# ═══════════════════════════════════════════════════════════════
# MARKET + FUNDING QUERIES
# ═══════════════════════════════════════════════════════════════

def is_xyz(asset):
    return asset.startswith("xyz:")


def query_asset_data(asset):
    """Pull spread + funding regime for an asset in one MCP call.
    Returns dict with: bid, ask, spread_bps, funding_regime, funding_annualized_pct.
    None if query fails."""
    # market_get_asset_data returns order_book + funding_history if asked.
    # 2026-04-24 fix: XYZ assets require dex="xyz" passed as an explicit
    # parameter in addition to the "xyz:" asset prefix. Without it, the
    # orderbook silently returns None. Bald Eagle was spread-gate-blocked
    # for an unknown number of days due to this same bug in its scanner.
    params = {
        "asset": asset,
        "candle_intervals": [],
        "include_funding": True,
        "include_order_book": True,
    }
    if is_xyz(asset):
        params["dex"] = "xyz"
    # Tight per-call timeout: 8s × 2 retries = 16s max per asset.
    # Producer tick budget is 45s (PRODUCER_MAX_SECONDS). Three slots
    # × ~2 candidates per slot = ~6 MCP calls per tick in common case.
    # Any slow MCP call gets abandoned before it can hang the whole tick.
    resp = cfg.mcporter_call("market_get_asset_data", timeout=8, retries=2, **params)
    if not resp or not isinstance(resp, dict):
        return None

    ad = resp.get("data", resp)
    if not isinstance(ad, dict):
        return None

    # Spread from order book.
    # v2.0.2 (2026-04-24): same silent-zero bug as Bald Eagle's scanner.
    # Live API returns order_book.levels = [bids_array, asks_array].
    # Prior code looked for order_book.bids / order_book.asks which
    # don't exist. Verified against live XYZ assets.
    ob = ad.get("order_book") or ad.get("orderBook") or {}
    levels = ob.get("levels", [])
    if not isinstance(levels, list) or len(levels) < 2:
        return None
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        return None

    def _lvl_px(lvl):
        if isinstance(lvl, dict):
            return cfg.safe_float(lvl.get("px", lvl.get("price", 0)))
        if isinstance(lvl, list) and lvl:
            return cfg.safe_float(lvl[0])
        return 0.0

    bid = _lvl_px(bids[0])
    ask = _lvl_px(asks[0])
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / mid) * 10000 if mid > 0 else 999

    # Funding regime (optional — not all XYZ symbols have funding info)
    funding_regime = (ad.get("funding_regime") or ad.get("fundingRegime") or "UNKNOWN").upper()
    funding_annualized_pct = cfg.safe_float(
        ad.get("funding_annualized_pct") or ad.get("fundingAnnualizedPct") or 0
    )

    return {
        "bid": bid,
        "ask": ask,
        "spread_bps": round(spread_bps, 3),
        "funding_regime": funding_regime,
        "funding_annualized_pct": funding_annualized_pct,
    }


def choose_direction(asset, regime, last_direction):
    """Funding-fade first, alternate as fallback.
    LONG_CROWDED → SHORT (collect funding)
    SHORT_CROWDED → LONG (collect funding)
    everything else → alternate based on last_direction for this asset
    """
    r = (regime or "").upper()
    if r in ("LONG_CROWDED", "LONG_HEAVY"):
        return "SHORT", "funding_fade_short"
    if r in ("SHORT_CROWDED", "SHORT_HEAVY"):
        return "LONG", "funding_fade_long"
    # Flat / neutral / unknown
    if last_direction == "LONG":
        return "SHORT", "alternate_neutral"
    return "LONG", "alternate_neutral"


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def emit_signal(asset, direction, thesis, spread_bps, funding_regime,
                funding_annualized_pct, slot_index, leverage, margin_usd,
                wallet, scanner_name, openclaw_bin):
    """Push a signal to the runtime's external_scanner via openclaw CLI."""
    signal = {
        "address": wallet,
        "scannerId": scanner_name,
        "signalType": "TURBINE_CYCLE_OPEN",
        "asset": asset,
        "direction": direction,
        "score": 10.0,
        "timestamp": int(cfg.now_ts() * 1000),
        "factors": {},
        "meta": {
            "thesis": thesis,
            "leverage": leverage,
            "marginUsd": margin_usd,
            "fundingRegime": funding_regime,
            "fundingAnnualizedPct": funding_annualized_pct,
            "spreadBps": spread_bps,
            "slotIndex": slot_index,
            "isXyz": is_xyz(asset),
            "_turbine_producer_version": "2.0.8",
        },
    }
    # v2.0.8 (2026-04-25): correct CLI invocation shape. Previous code
    # passed scanner_name as a positional argument and the JSON payload
    # via stdin — the openclaw CLI rejected this with "too many arguments
    # for 'ingest'. Expected 0 arguments but got 1." Jackal's working
    # producer uses named flags (--address, --scanner, --payload).
    # Matching that pattern, plus widening timeout 6s → 20s to match
    # Jackal's tested value (the 6s was too tight for some MCP routes
    # and caused intermittent timeout exceptions in the 1-hour log).
    cmd = [
        openclaw_bin, "senpi", "external-scanner", "ingest",
        "--address", wallet,
        "--scanner", scanner_name,
        "--payload", json.dumps(signal),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            cfg.log(f"ingest failed for {asset} {direction}: {r.stderr.strip()}")
            return False
        # Jackal pattern: validate response.ok if present
        if r.stdout.strip():
            try:
                response = json.loads(r.stdout)
                if isinstance(response, dict) and response.get("ok") is False:
                    cfg.log(f"ingest rejected for {asset} {direction}: {response.get('error')}")
                    return False
            except (json.JSONDecodeError, TypeError):
                pass  # Tolerate non-JSON success responses
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        cfg.log(f"ingest exception for {asset} {direction}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def run():
    # Reentrancy guard
    lock = acquire_lock()
    if lock is None:
        cfg.output({"status": "ok", "note": "prior run holds lock; skipping tick"})
        return

    try:
        wallet, strategy_id = cfg.get_wallet_and_strategy()
        if not wallet:
            cfg.output({"status": "error", "error": "no wallet configured. Set TURBINE_WALLET or STRATEGY_ADDRESS."})
            return

        params = load_runtime_params()
        openclaw_bin = os.environ.get("OPENCLAW_BIN", "openclaw")
        scanner_name = os.environ.get("EXTERNAL_SCANNER_NAME", "turbine_signals")

        ss = cfg.load_session_state()
        open_positions = cfg.get_open_positions(wallet)
        resting_orders = cfg.get_resting_orders(wallet)

        # v2.0.4: count BOTH filled positions AND resting ALO orders against
        # max_slots. Without this, producer thinks slot is empty during the
        # 120s ALO rest window and emits an opposing-direction signal on the
        # same asset → ghost trade when market crosses both orders. Also
        # normalize coin keys (strip xyz: prefix) so XYZ asset comparisons
        # work correctly between held_assets and the rotation_list candidates.
        active_count = len(open_positions) + len(resting_orders)
        empty_slots = params["max_slots"] - active_count

        if empty_slots <= 0:
            cfg.output({
                "status": "ok",
                "note": f"all {params['max_slots']} slots occupied (positions + resting orders)",
                "positions": [{"coin": p["coin"], "direction": p["direction"], "upnl": round(p["upnl"], 2)} for p in open_positions],
                "resting_orders": [{"coin": o["coin"], "direction": o["direction"], "limit_price": o["limit_price"]} for o in resting_orders],
                "session": {
                    "cycles_opened_today": ss["cycles_opened_today"],
                    "signals_emitted_today": ss["signals_emitted_today"],
                },
            })
            return

        # Avoid emitting a signal for an asset already held OR pending fill.
        # Both lookups go through normalize_coin_key so 'xyz:GOLD' and 'GOLD'
        # canonicalize to the same key.
        held_assets = {cfg.normalize_coin_key(p["coin"]) for p in open_positions}
        held_assets.update(cfg.normalize_coin_key(o["coin"]) for o in resting_orders)

        emitted = []
        rotation_len = len(ROTATION_LIST)
        attempts = 0
        max_attempts = rotation_len * 2  # don't spin forever on a bad universe

        for slot_idx in range(empty_slots):
            asset = None
            asset_data = None
            direction = None
            thesis = None

            while attempts < max_attempts:
                attempts += 1
                candidate = ROTATION_LIST[ss["rotation_index"] % rotation_len]
                ss["rotation_index"] = (ss["rotation_index"] + 1) % rotation_len

                coin_key = candidate.split(":")[-1].upper()
                if coin_key in held_assets:
                    continue

                asset_data = query_asset_data(candidate)
                if asset_data is None:
                    continue

                max_spread = params["max_spread_bps_xyz"] if is_xyz(candidate) else params["max_spread_bps_main"]
                if asset_data["spread_bps"] > max_spread:
                    continue

                last_dir = ss["last_direction_by_asset"].get(coin_key)
                direction, thesis = choose_direction(candidate, asset_data["funding_regime"], last_dir)
                asset = candidate
                break

            if asset is None:
                # Rotation exhausted without finding a tradable asset this tick
                break

            ok = emit_signal(
                asset=asset,
                direction=direction,
                thesis=thesis,
                spread_bps=asset_data["spread_bps"],
                funding_regime=asset_data["funding_regime"],
                funding_annualized_pct=asset_data["funding_annualized_pct"],
                slot_index=slot_idx,
                leverage=params["leverage"],
                margin_usd=params["margin_per_slot_usd"],
                wallet=wallet,
                scanner_name=scanner_name,
                openclaw_bin=openclaw_bin,
            )

            if ok:
                coin_key = asset.split(":")[-1].upper()
                ss["last_direction_by_asset"][coin_key] = direction
                ss["signals_emitted_today"] += 1
                # v2.0.8: cycles_opened_today now tracks signal emission
                # success (matching the original intent — every successful
                # signal becomes an attempted cycle). Was always 0 before
                # because nothing incremented it. Note: this is "emitted
                # cycle attempts," not "filled cycles." The runtime's
                # ingest acknowledges receipt; whether it converts to a
                # filled position depends on the LLM gate + ALO fill.
                ss["cycles_opened_today"] = ss.get("cycles_opened_today", 0) + 1
                held_assets.add(coin_key)
                emitted.append({
                    "asset": asset,
                    "direction": direction,
                    "thesis": thesis,
                    "spread_bps": asset_data["spread_bps"],
                    "funding_regime": asset_data["funding_regime"],
                })

        cfg.save_session_state(ss)
        cfg.output({
            "status": "ok",
            "empty_slots_before": empty_slots,
            "signals_emitted": len(emitted),
            "signals": emitted,
            "open_positions": len(open_positions),
            "session": {
                "cycles_opened_today": ss["cycles_opened_today"],
                "signals_emitted_today": ss["signals_emitted_today"],
                "rotation_index": ss["rotation_index"],
            },
            "_turbine_producer_version": "2.0.8",
        })

    finally:
        release_lock(lock)


if __name__ == "__main__":
    install_timeout()
    try:
        run()
    except ProducerTimeout as e:
        # Release the lock BEFORE exiting so next tick isn't blocked.
        # No tool call can outlive this process; killing the producer
        # ends any child subprocesses and unblocks the fcntl lock.
        cfg.log(f"TIMEOUT: {e}. Exiting so next cron tick can run.")
        cfg.output({"status": "timeout", "max_seconds": _PRODUCER_MAX_SECONDS})
        sys.exit(0)
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
    finally:
        clear_timeout()
