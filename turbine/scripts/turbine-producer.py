#!/usr/bin/env python3
# Senpi TURBINE Producer v3.2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""TURBINE v3.2 Producer — two-wallet volume rotation, runners DSL on slot subset.

v3.2 (2026-05-08) — RUNNERS redesign.

v3.1's hunt-mode-as-HYPE-specialist was the wrong abstraction:
~$2,400 idle 90% of the time, no volume contribution from the second
wallet. The correct framing: "run the volume play, but allow winners
to run longer."

v3.2 keeps the two-wallet split (the senpi-trading-runtime plugin enforces one runtime
per wallet) and rewires both wallets to receive the SAME volume-rotation
alpha. The wallet boundary selects DSL behavior:

  VOLUME WALLET  ($4,000, 7 slots × $500):
    DSL hard_timeout 10min, no Phase 2.
    Pure rotation. Force-closes every position on the clock.

  RUNNERS WALLET ($1,900, 2 slots × $950):
    DSL hard_timeout 240min (4h cap), Phase 2 ratchet ENABLED.
    SAME entries as volume; ratchet locks at progressively higher
    levels (T0 5%/0, T1 10%/35, T2 20%/55, T3 35%/75, T4 50%/85).
    Most positions exit early via Phase 1 / weak_peak; ~5% land on
    a real directional move and ride to +20-50% margin ROE.

The asymmetry runners create:
  - Loser/flat positions: ~same cost as volume wallet
  - Winners that catch a real move: 5-20× bigger realized P&L

Producer emits the same signal payload to both wallets opportunistically.
Asset rotation index is per-wallet so the two wallets desync naturally
(cleaner volume distribution across the asset universe).

Auto-downsize on both wallets: if account_value drops below
slots × margin, the producer reduces effective slot count to fit
available margin. This handles the cost-of-volume bleed gracefully
without operator intervention.

Required env vars:
  TURBINE_VOLUME_WALLET            — volume strategy wallet (REQUIRED)
  TURBINE_RUNNERS_WALLET           — runners strategy wallet (optional;
                                     omit to run pure volume engine)
  SENPI_AUTH_TOKEN                 — Bearer token for MCP + signal POST
  TURBINE_VOLUME_DECISION_MODEL    — bare model name for volume LLM gate
  TURBINE_RUNNERS_DECISION_MODEL   — bare model name for runners LLM gate

Banned env vars (per v2.0.9 contamination rule):
  STRATEGY_ADDRESS, TURBINE_WALLET, TURBINE_HUNT_WALLET (legacy v3.1).
"""

import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import turbine_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "3.2.2"

# Scanner names — must match runtime YAMLs
VOLUME_SCANNER_NAME = "turbine_volume_signals"
RUNNERS_SCANNER_NAME = "turbine_runners_signals"

# Signal types — explicit kwarg to push_signal() (Rachin's review of
# Cheetah PR #209). Same scoring path on both wallets, but distinct
# signal_type tags so audit_query splits the two wallets cleanly.
VOLUME_SIGNAL_TYPE = "TURBINE_VOLUME_FAST_ROTATION"
RUNNERS_SIGNAL_TYPE = "TURBINE_VOLUME_PATIENT_ROTATION"


# ═══════════════════════════════════════════════════════════════
# WALLET RESOLUTION
# ═══════════════════════════════════════════════════════════════

VOLUME_WALLET, VOLUME_STRATEGY_ID = cfg.get_volume_wallet_and_strategy()
RUNNERS_WALLET, RUNNERS_STRATEGY_ID = cfg.get_runners_wallet_and_strategy()
RUNNERS_ENABLED = bool(RUNNERS_WALLET)


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIRS
# ═══════════════════════════════════════════════════════════════

def _wallet_state_dir(wallet):
    h = hashlib.sha256(wallet.lower().encode()).hexdigest()[:12] if wallet else "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_VOLUME_STATE_DIR = _wallet_state_dir(VOLUME_WALLET)
_VOLUME_LAST_CLOSED_FILE = _VOLUME_STATE_DIR / "last-closed.json"
_VOLUME_PREV_HELD_FILE = _VOLUME_STATE_DIR / "prev-held.json"
_VOLUME_CYCLE_STATS_FILE = _VOLUME_STATE_DIR / "cycle-stats.json"
_VOLUME_ROTATION_INDEX_FILE = _VOLUME_STATE_DIR / "rotation-index.json"

if RUNNERS_ENABLED:
    _RUNNERS_STATE_DIR = _wallet_state_dir(RUNNERS_WALLET)
    _RUNNERS_LAST_CLOSED_FILE = _RUNNERS_STATE_DIR / "last-closed.json"
    _RUNNERS_PREV_HELD_FILE = _RUNNERS_STATE_DIR / "prev-held.json"
    _RUNNERS_ROTATION_INDEX_FILE = _RUNNERS_STATE_DIR / "rotation-index.json"


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

def _runtime_config():
    c = cfg.load_config()
    slots = c.get("slots", {}) or {}
    margin = c.get("margin", {}) or {}
    leverage = c.get("leverage", {}) or {}
    cycle = c.get("cycle", {}) or {}
    spread = c.get("spread", {}) or {}
    return {
        "volume_slots": int(slots.get("volume", 7)),
        "runners_slots": int(slots.get("runners", 2)),
        "volume_margin": float(margin.get("volume", 500)),
        "runners_margin": float(margin.get("runners", 950)),
        "volume_leverage": float(leverage.get("volume", 5)),
        "runners_leverage": float(leverage.get("runners", 5)),
        "volume_cycle_default_min": float(cycle.get("volumeDefaultMin", 10)),
        "volume_cycle_fallback_min": float(cycle.get("volumeFallbackMin", 12)),
        "fill_rate_fallback_threshold": float(cycle.get("fillRateFallbackThreshold", 0.85)),
        "fill_rate_window_size": int(cycle.get("fillRateWindowSize", 20)),
        "spread_main_bps": float(spread.get("mainBps", 3)),
        "spread_xyz_bps": float(spread.get("xyzBps", 10)),
        "xyz_weight": float(c.get("xyzWeight", 0.80)),
    }


# ═══════════════════════════════════════════════════════════════
# STALE-ORDER HYGIENE
# ═══════════════════════════════════════════════════════════════
# Each runtime restart / swap (helpers migration, runtime plugin
# rollover, daemon redeploy) leaves resting ALO orders that the new
# runtime instance does not own. The runtime's own
# `execution_timeout_seconds` (180s on FEE_OPTIMIZED_LIMIT) cancels
# the orders it placed — anything still resting well past that is
# by definition abandoned. The producer's `held_keys` set treats
# every non-reduceOnly resting order as a slot occupier (v2.0.4
# ghost-trade fix), so orphans starve real fills until cleared.
#
# We sweep BEFORE computing held_keys so the fresh slot count
# reflects only live positions + actively-managed resting orders.
# Maker-vs-taker placement is unchanged — cancellation has no fee
# impact; HL only charges on fills.
STALE_ORDER_MAX_AGE_SECONDS = 300


# ═══════════════════════════════════════════════════════════════
# UNIVERSE (shared by both wallets)
# ═══════════════════════════════════════════════════════════════

VOLUME_XYZ = ["xyz:BRENTOIL", "xyz:GOLD", "xyz:SPX"]
VOLUME_MAIN = ["BTC", "ETH", "SOL", "HYPE"]


def is_xyz(asset):
    return asset.startswith("xyz:")


# ═══════════════════════════════════════════════════════════════
# STATE I/O
# ═══════════════════════════════════════════════════════════════

def _load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


# Generic per-wallet helpers — both wallets use identical state shape.
def _load_prev_held(path):
    return set(_load_json(path, {"assets": []}).get("assets", []))


def _save_prev_held(path, held):
    cfg.atomic_write(str(path),
                     {"assets": sorted(list(held)), "updatedAt": cfg.now_iso()})


def _load_last_closed(path):
    return _load_json(path, {})


def _save_last_closed(path, d):
    cfg.atomic_write(str(path), d)


def _load_rotation_index(path):
    return _load_json(path, {"index": 0}).get("index", 0)


def _save_rotation_index(path, idx):
    cfg.atomic_write(str(path), {"index": idx})


def load_cycle_stats():
    return _load_json(_VOLUME_CYCLE_STATS_FILE, {"window": []})


def save_cycle_stats(d):
    cfg.atomic_write(str(_VOLUME_CYCLE_STATS_FILE), d)


# ═══════════════════════════════════════════════════════════════
# MARKET QUERIES
# ═══════════════════════════════════════════════════════════════

def query_asset_data(asset):
    """Spread + funding regime for one asset."""
    params = {
        "asset": asset,
        "candle_intervals": [],
        "include_funding": True,
        "include_order_book": True,
    }
    if is_xyz(asset):
        params["dex"] = "xyz"
    resp = cfg.mcporter_call("market_get_asset_data", timeout=8, **params)
    if not resp or not isinstance(resp, dict):
        return None
    ad = resp.get("data", resp)
    if not isinstance(ad, dict):
        return None
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

    funding_regime = (ad.get("funding_regime") or ad.get("fundingRegime") or "UNKNOWN").upper()
    funding_annualized_pct = cfg.safe_float(
        ad.get("funding_annualized_pct") or ad.get("fundingAnnualizedPct") or 0
    )

    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": round(spread_bps, 3),
        "funding_regime": funding_regime,
        "funding_annualized_pct": funding_annualized_pct,
    }


# ═══════════════════════════════════════════════════════════════
# VOLUME ROTATION LOGIC (shared by both wallets)
# ═══════════════════════════════════════════════════════════════

def choose_direction(regime):
    """Funding fade. Crowded longs → SHORT, crowded shorts → LONG,
    flat → coin-flip (deterministic via wallet/asset combo)."""
    r = (regime or "").upper()
    if r in ("LONG_CROWDED", "LONG_HEAVY"):
        return "SHORT", "funding_fade_short"
    if r in ("SHORT_CROWDED", "SHORT_HEAVY"):
        return "LONG", "funding_fade_long"
    return random.choice(["LONG", "SHORT"]), "alternate_neutral"


def pick_rotation_asset(rot_idx, xyz_weight, held_set, last_closed):
    """Pick next rotation asset. Probabilistic XYZ/main weighting +
    deterministic rotation index inside each pool. Skips held +
    post-close cooldown."""
    use_xyz = random.random() < xyz_weight
    pool = VOLUME_XYZ if use_xyz else VOLUME_MAIN
    n = len(pool)
    for _ in range(n):
        candidate = pool[rot_idx % n]
        rot_idx = (rot_idx + 1) % n
        coin_key = cfg.normalize_coin_key(candidate)
        if coin_key in held_set:
            continue
        last = last_closed.get(coin_key, {})
        if last and (time.time() - last.get("ts", 0)) < 90:
            continue
        return candidate, rot_idx
    other = VOLUME_MAIN if use_xyz else VOLUME_XYZ
    n = len(other)
    for _ in range(n):
        candidate = other[rot_idx % n]
        rot_idx = (rot_idx + 1) % n
        coin_key = cfg.normalize_coin_key(candidate)
        if coin_key in held_set:
            continue
        last = last_closed.get(coin_key, {})
        if last and (time.time() - last.get("ts", 0)) < 90:
            continue
        return candidate, rot_idx
    return None, rot_idx


def emit_signal(wallet, scanner, signal_type, asset, direction, thesis,
                ad, slot_index, leverage, margin_usd, current_cycle_min):
    """Push a volume rotation signal via the helpers wrapper.

    Both wallets receive the same payload shape; the receiving runtime's
    DSL preset determines how the position exits."""
    if not wallet:
        cfg.log(f"wallet not set; cannot emit signal to {scanner}")
        return False
    data_block = {
        "thesis": thesis,
        "leverage": float(leverage),
        "marginUsd": float(margin_usd),
        "fundingRegime": ad.get("funding_regime", "UNKNOWN"),
        "fundingAnnualizedPct": float(ad.get("funding_annualized_pct", 0)),
        "spreadBps": float(ad.get("spread_bps", 0)),
        "slotIndex": int(slot_index),
        "isXyz": is_xyz(asset),
        "cycleMin": float(current_cycle_min),
    }
    try:
        cfg._wrapper_client.push_signal(
            address=wallet,
            scanner=scanner,
            asset=asset,
            direction=direction,
            signal_type=signal_type,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"push_signal rejected for {asset} {direction} on {scanner}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"push_signal exception for {asset} {direction} on {scanner}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# AUTO-DOWNSIZE (handles cost-of-volume bleed gracefully)
# ═══════════════════════════════════════════════════════════════

def effective_slots(account_value, max_slots, margin_per_slot):
    """How many slots can the wallet actually open right now?
    min(config max, account_value / margin_per_slot floored)."""
    if account_value <= 0 or margin_per_slot <= 0:
        return 0
    affordable = int(account_value / margin_per_slot)
    return max(0, min(max_slots, affordable))


def determine_cycle_min(rt, cycle_stats):
    window = cycle_stats.get("window", [])
    if len(window) < 5:
        return rt["volume_cycle_default_min"]
    recent = window[-rt["fill_rate_window_size"]:]
    fills = sum(1 for r in recent if r.get("filled_as_maker"))
    rate = fills / len(recent) if recent else 1.0
    if rate < rt["fill_rate_fallback_threshold"]:
        return rt["volume_cycle_fallback_min"]
    return rt["volume_cycle_default_min"]


# ═══════════════════════════════════════════════════════════════
# WALLET FILL — emit signals to a single wallet's free slots
# ═══════════════════════════════════════════════════════════════

def fill_wallet_slots(wallet_label, wallet, scanner, signal_type,
                      free_slots, rot_idx, held_keys, last_closed,
                      rt, leverage, margin, current_cycle_min):
    """Emit volume-rotation signals to fill `free_slots` on `wallet`.

    Returns (emitted_list, new_rot_idx, updated_held_keys)."""
    emitted = []
    if free_slots <= 0:
        return emitted, rot_idx, held_keys
    for slot_idx in range(free_slots):
        asset, rot_idx = pick_rotation_asset(rot_idx, rt["xyz_weight"], held_keys, last_closed)
        if asset is None:
            break
        ad = query_asset_data(asset)
        if ad is None:
            continue
        max_spread = rt["spread_xyz_bps"] if is_xyz(asset) else rt["spread_main_bps"]
        if ad["spread_bps"] > max_spread:
            continue
        direction, thesis = choose_direction(ad["funding_regime"])
        if emit_signal(wallet, scanner, signal_type, asset, direction, thesis,
                       ad, slot_idx, leverage, margin, current_cycle_min):
            emitted.append({
                "wallet": wallet_label,
                "asset": asset,
                "direction": direction,
                "thesis": thesis,
            })
            held_keys.add(cfg.normalize_coin_key(asset))
    return emitted, rot_idx, held_keys


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()

    if not VOLUME_WALLET:
        cfg.output({
            "status": "error",
            "error": ("TURBINE_VOLUME_WALLET not set. Set the env var or "
                      "populate config/turbine-config.json volume.wallet field. "
                      "STRATEGY_ADDRESS, TURBINE_WALLET, and TURBINE_HUNT_WALLET "
                      "are all BANNED in v3.2."),
            "_turbine_producer_version": VERSION,
        })
        return

    rt = _runtime_config()

    # ── VOLUME WALLET state ──
    volume_positions = cfg.get_open_positions(VOLUME_WALLET)
    volume_resting = cfg.get_resting_orders(VOLUME_WALLET)
    volume_account_value = cfg.get_account_value(VOLUME_WALLET)
    if volume_account_value is None or volume_account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read volume wallet account value; skip tick",
            "_turbine_producer_version": VERSION,
        })
        return

    # Sweep orphaned ALOs (left behind by previous runtime swaps) BEFORE
    # computing held_keys. Anything still resting > STALE_ORDER_MAX_AGE_SECONDS
    # (5 min) past the runtime's own 180s execution_timeout has been
    # abandoned — clear it so the slot is honestly counted as free.
    volume_swept = cfg.sweep_stale_resting_orders(
        VOLUME_WALLET, volume_resting, STALE_ORDER_MAX_AGE_SECONDS, label="volume",
    )
    if volume_swept:
        # Refetch so held_keys doesn't double-count just-cancelled orders.
        volume_resting = cfg.get_resting_orders(VOLUME_WALLET)

    volume_held_keys = {cfg.normalize_coin_key(p["coin"]) for p in volume_positions}
    volume_held_keys.update(cfg.normalize_coin_key(o["coin"]) for o in volume_resting)

    eff_volume = effective_slots(volume_account_value, rt["volume_slots"], rt["volume_margin"])
    free_volume = max(0, eff_volume - len(volume_held_keys))

    # Detect closes
    prev_volume = _load_prev_held(_VOLUME_PREV_HELD_FILE)
    closed_volume = prev_volume - volume_held_keys
    if closed_volume:
        last_closed = _load_last_closed(_VOLUME_LAST_CLOSED_FILE)
        for k in closed_volume:
            last_closed[k] = {"ts": time.time(), "iso": cfg.now_iso()}
        _save_last_closed(_VOLUME_LAST_CLOSED_FILE, last_closed)
    _save_prev_held(_VOLUME_PREV_HELD_FILE, volume_held_keys)
    last_volume_closed = _load_last_closed(_VOLUME_LAST_CLOSED_FILE)

    # ── RUNNERS WALLET state (if enabled) ──
    runners_positions = []
    runners_resting = []
    runners_account_value = 0.0
    runners_held_keys = set()
    eff_runners = 0
    free_runners = 0
    closed_runners = set()
    last_runners_closed = {}
    runners_swept = []
    if RUNNERS_ENABLED:
        runners_positions = cfg.get_open_positions(RUNNERS_WALLET)
        runners_resting = cfg.get_resting_orders(RUNNERS_WALLET)
        runners_account_value = cfg.get_account_value(RUNNERS_WALLET) or 0.0

        # Same orphan sweep as volume wallet — see comment above.
        runners_swept = cfg.sweep_stale_resting_orders(
            RUNNERS_WALLET, runners_resting, STALE_ORDER_MAX_AGE_SECONDS,
            label="runners",
        )
        if runners_swept:
            runners_resting = cfg.get_resting_orders(RUNNERS_WALLET)

        runners_held_keys = {cfg.normalize_coin_key(p["coin"]) for p in runners_positions}
        runners_held_keys.update(cfg.normalize_coin_key(o["coin"]) for o in runners_resting)

        eff_runners = effective_slots(runners_account_value, rt["runners_slots"], rt["runners_margin"])
        free_runners = max(0, eff_runners - len(runners_held_keys))

        prev_runners = _load_prev_held(_RUNNERS_PREV_HELD_FILE)
        closed_runners = prev_runners - runners_held_keys
        if closed_runners:
            last_closed = _load_last_closed(_RUNNERS_LAST_CLOSED_FILE)
            for k in closed_runners:
                last_closed[k] = {"ts": time.time(), "iso": cfg.now_iso()}
            _save_last_closed(_RUNNERS_LAST_CLOSED_FILE, last_closed)
        _save_prev_held(_RUNNERS_PREV_HELD_FILE, runners_held_keys)
        last_runners_closed = _load_last_closed(_RUNNERS_LAST_CLOSED_FILE)

    # ── CYCLE LENGTH (from volume wallet's fill-rate stats) ──
    cycle_stats = load_cycle_stats()
    current_cycle_min = determine_cycle_min(rt, cycle_stats)

    # ── EMIT to volume wallet ──
    volume_rot_idx = _load_rotation_index(_VOLUME_ROTATION_INDEX_FILE)
    volume_emitted, volume_rot_idx, volume_held_keys = fill_wallet_slots(
        wallet_label="volume",
        wallet=VOLUME_WALLET,
        scanner=VOLUME_SCANNER_NAME,
        signal_type=VOLUME_SIGNAL_TYPE,
        free_slots=free_volume,
        rot_idx=volume_rot_idx,
        held_keys=volume_held_keys,
        last_closed=last_volume_closed,
        rt=rt,
        leverage=rt["volume_leverage"],
        margin=rt["volume_margin"],
        current_cycle_min=current_cycle_min,
    )
    _save_rotation_index(_VOLUME_ROTATION_INDEX_FILE, volume_rot_idx)

    # ── EMIT to runners wallet (if enabled) ──
    runners_emitted = []
    if RUNNERS_ENABLED and free_runners > 0:
        runners_rot_idx = _load_rotation_index(_RUNNERS_ROTATION_INDEX_FILE)
        runners_emitted, runners_rot_idx, runners_held_keys = fill_wallet_slots(
            wallet_label="runners",
            wallet=RUNNERS_WALLET,
            scanner=RUNNERS_SCANNER_NAME,
            signal_type=RUNNERS_SIGNAL_TYPE,
            free_slots=free_runners,
            rot_idx=runners_rot_idx,
            held_keys=runners_held_keys,
            last_closed=last_runners_closed,
            rt=rt,
            leverage=rt["runners_leverage"],
            margin=rt["runners_margin"],
            current_cycle_min=current_cycle_min,
        )
        _save_rotation_index(_RUNNERS_ROTATION_INDEX_FILE, runners_rot_idx)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "volume": {
            "wallet": VOLUME_WALLET[:10] + "...",
            "account_value": round(volume_account_value, 2),
            "open_positions": len(volume_positions),
            "resting_orders": len(volume_resting),
            "stale_swept": volume_swept,
            "slots_max": rt["volume_slots"],
            "slots_effective": eff_volume,
            "slots_held": len(volume_held_keys),
            "slots_free": free_volume,
            "emitted": volume_emitted,
            "closed_this_tick": sorted(list(closed_volume)),
        },
        "runners": ({
            "wallet": RUNNERS_WALLET[:10] + "...",
            "account_value": round(runners_account_value, 2),
            "open_positions": len(runners_positions),
            "resting_orders": len(runners_resting),
            "stale_swept": runners_swept,
            "slots_max": rt["runners_slots"],
            "slots_effective": eff_runners,
            "slots_held": len(runners_held_keys),
            "slots_free": free_runners,
            "emitted": runners_emitted,
            "closed_this_tick": sorted(list(closed_runners)),
        }) if RUNNERS_ENABLED else None,
        "current_cycle_min": current_cycle_min,
        "elapsed_sec": round(elapsed, 2),
        "_turbine_producer_version": VERSION,
    })


# ═══════════════════════════════════════════════════════════════
# DAEMON ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
#
# Single daemon manages BOTH wallets. Lock name uses VOLUME_WALLET
# hash since volume is the operationally-critical mission.
#
# alive_check tracks the volume runtime — if turbine-volume-tracker
# is deleted OR the volume scanner is renamed, the daemon
# self-terminates. Runners runtime can be deleted independently
# without affecting the daemon.

if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(VOLUME_WALLET.lower().encode()).hexdigest()[:12]
        if VOLUME_WALLET
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=60,
        name=f"turbine-producer-{_wallet_lock_id}",
        wallet=VOLUME_WALLET,
        scanner=VOLUME_SCANNER_NAME,
        tick_timeout=45,
    )
