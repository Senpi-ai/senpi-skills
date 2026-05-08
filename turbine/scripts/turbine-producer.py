#!/usr/bin/env python3
# Senpi TURBINE Producer v3.1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/skills
"""TURBINE v3.1 Producer — two-wallet, two-runtime, single daemon.

v3.1 (2026-05-08) — two-wallet rewrite.

The runtime-phase-2 plugin enforces ONE RUNTIME PER WALLET. v3.0
attempted "two runtimes on one wallet" and got blocked at deploy
when the second runtime install failed with "A runtime for wallet
X is already running." v3.1 splits the architecture cleanly:

  Wallet A (volume):  own runtime turbine-volume-tracker
                      $3,500 funding ($500 × 7 slots)
                      Funding-fade rotation, 10-min cycle, 80/20 XYZ/main

  Wallet B (hunt):    own runtime turbine-hunt-tracker
                      $2,400 funding ($1,200 × 2 slots)
                      HYPE 4H momentum, score >= 10 floor, ratchet exit

  ONE producer daemon reads both wallets and emits to both runtimes.
  The wallet boundary IS the mode boundary — no more slot-mode
  tagging needed. audit_query splits per-wallet trivially.

VOLUME mode (Wallet A, 7 slots, 10-min rotation):
  Volume engine. Funding-fade direction. XYZ-heavy 80/20 for the
  lower fee floor. FEE_OPTIMIZED_LIMIT entries with
  ensure_execution_as_taker: false. DSL hard_timeout: 10min owns
  the exit. No Phase 2. Pure breakeven thesis — alpha is Senpi
  builder-fee recycling vs HL maker fee differential.

HUNT mode (Wallet B, 2 slots, HYPE-only momentum):
  HYPE 4H breakout rider. Score >= 10 floor on multi-axis
  confluence. 5x leverage, $1,200 margin. DSL Phase 2 ratchet
  enabled with HYPE-tuned ladder. Up to 4h hold. Distinct asset
  + distinct timescale + distinct WALLET from VOLUME = clean P&L
  attribution at the wallet level.

If TURBINE_HUNT_WALLET is unset, hunt mode is disabled gracefully
— producer only emits volume signals. Lets operators run a pure
volume engine without provisioning a second wallet.

Required env vars:
  TURBINE_VOLUME_WALLET            — volume strategy wallet (REQUIRED)
  TURBINE_HUNT_WALLET              — hunt strategy wallet (optional;
                                      omit to disable hunt mode)
  SENPI_AUTH_TOKEN                 — Bearer token for MCP + signal POST
  TURBINE_VOLUME_DECISION_MODEL    — bare model name for volume LLM gate
  TURBINE_HUNT_DECISION_MODEL      — bare model name for hunt LLM gate
                                     (only used if hunt wallet set)

Optional env vars (sensible defaults):
  SENPI_MCP_URL                    — default https://mcp.prod.senpi.ai/mcp
  SENPI_RUNTIME_API_HOST           — default 127.0.0.1
  SENPI_RUNTIME_API_PORT           — default 8787
  OPENCLAW_WORKSPACE               — default /data/workspace

Banned env vars (per v2.0.9 contamination rule):
  STRATEGY_ADDRESS  — generic env var; agent-specific vars only.
  TURBINE_WALLET    — was v3.0's single-wallet env var; v3.1 splits
                      it into TURBINE_VOLUME_WALLET + TURBINE_HUNT_WALLET.
                      If you have TURBINE_WALLET exported from v3.0
                      testing, unset it — v3.1 ignores it.
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


VERSION = "3.1.0"

# Scanner names — must match runtime-volume.yaml + runtime-hunt.yaml
VOLUME_SCANNER_NAME = "turbine_volume_signals"
HUNT_SCANNER_NAME = "turbine_hunt_signals"

# Signal types — passed explicitly to push_signal() per Rachin's review
# of Cheetah PR #209. The wrapper only forwards declared kwargs;
# defaultSignalType in scanner config isn't reliable. Distinct types
# per mode also give audit_query a free filter for per-mode P&L.
VOLUME_SIGNAL_TYPE = "TURBINE_VOLUME_ROTATION"
HUNT_SIGNAL_TYPE = "TURBINE_HUNT_HYPE_MOMENTUM"


# ═══════════════════════════════════════════════════════════════
# WALLET RESOLUTION — two wallets, hunt is optional
# ═══════════════════════════════════════════════════════════════

VOLUME_WALLET, VOLUME_STRATEGY_ID = cfg.get_volume_wallet_and_strategy()
HUNT_WALLET, HUNT_STRATEGY_ID = cfg.get_hunt_wallet_and_strategy()
HUNT_ENABLED = bool(HUNT_WALLET)


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIRS — one per wallet
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

if HUNT_ENABLED:
    _HUNT_STATE_DIR = _wallet_state_dir(HUNT_WALLET)
    _HUNT_LAST_CLOSED_FILE = _HUNT_STATE_DIR / "last-closed.json"
    _HUNT_PREV_HELD_FILE = _HUNT_STATE_DIR / "prev-held.json"
    _HUNT_HISTORY_FILE = _HUNT_STATE_DIR / "hunt-history.json"


# ═══════════════════════════════════════════════════════════════
# CONFIG (operator-tunable via turbine-config.json)
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
        "hunt_slots": int(slots.get("hunt", 2)),
        "volume_margin": float(margin.get("volume", 500)),
        "hunt_margin": float(margin.get("hunt", 1200)),
        "volume_leverage": float(leverage.get("volume", 5)),
        "hunt_leverage": float(leverage.get("hunt", 5)),
        "volume_cycle_default_min": float(cycle.get("volumeDefaultMin", 10)),
        "volume_cycle_fallback_min": float(cycle.get("volumeFallbackMin", 12)),
        "fill_rate_fallback_threshold": float(cycle.get("fillRateFallbackThreshold", 0.85)),
        "fill_rate_window_size": int(cycle.get("fillRateWindowSize", 20)),
        "hunt_cycle_min": float(cycle.get("huntMin", 240)),
        "hunt_cooldown_min": float(cycle.get("huntCooldownMin", 60)),
        "spread_main_bps": float(spread.get("mainBps", 3)),
        "spread_xyz_bps": float(spread.get("xyzBps", 10)),
        "xyz_weight": float(c.get("xyzWeight", 0.80)),
        "hunt_min_score": int(c.get("huntMinScore", 10)),
        # Hunt wallet's own balance floor — pauses hunt emission if
        # the hunt wallet itself draws down. Wallet boundary means
        # volume capital is naturally protected; this just protects
        # the hunt wallet from over-cycling after a bad streak.
        "min_hunt_wallet_balance": float(c.get("minHuntWalletBalance", 2000.0)),
    }


# ═══════════════════════════════════════════════════════════════
# UNIVERSE
# ═══════════════════════════════════════════════════════════════

VOLUME_XYZ = ["xyz:BRENTOIL", "xyz:GOLD", "xyz:SPX"]
VOLUME_MAIN = ["BTC", "ETH", "SOL", "HYPE"]
HUNT_ASSETS = ["HYPE"]


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


# Volume state
def load_volume_prev_held():
    return set(_load_json(_VOLUME_PREV_HELD_FILE, {"assets": []}).get("assets", []))


def save_volume_prev_held(held):
    cfg.atomic_write(str(_VOLUME_PREV_HELD_FILE),
                     {"assets": sorted(list(held)), "updatedAt": cfg.now_iso()})


def load_volume_last_closed():
    return _load_json(_VOLUME_LAST_CLOSED_FILE, {})


def save_volume_last_closed(d):
    cfg.atomic_write(str(_VOLUME_LAST_CLOSED_FILE), d)


def load_cycle_stats():
    return _load_json(_VOLUME_CYCLE_STATS_FILE, {"window": []})


def save_cycle_stats(d):
    cfg.atomic_write(str(_VOLUME_CYCLE_STATS_FILE), d)


def load_rotation_index():
    return _load_json(_VOLUME_ROTATION_INDEX_FILE, {"index": 0})


def save_rotation_index(d):
    cfg.atomic_write(str(_VOLUME_ROTATION_INDEX_FILE), d)


# Hunt state (only initialized if HUNT_ENABLED)
def load_hunt_prev_held():
    if not HUNT_ENABLED:
        return set()
    return set(_load_json(_HUNT_PREV_HELD_FILE, {"assets": []}).get("assets", []))


def save_hunt_prev_held(held):
    if not HUNT_ENABLED:
        return
    cfg.atomic_write(str(_HUNT_PREV_HELD_FILE),
                     {"assets": sorted(list(held)), "updatedAt": cfg.now_iso()})


def load_hunt_last_closed():
    if not HUNT_ENABLED:
        return {}
    return _load_json(_HUNT_LAST_CLOSED_FILE, {})


def save_hunt_last_closed(d):
    if not HUNT_ENABLED:
        return
    cfg.atomic_write(str(_HUNT_LAST_CLOSED_FILE), d)


def load_hunt_history():
    if not HUNT_ENABLED:
        return {"last_emitted_ts": 0, "scores": []}
    return _load_json(_HUNT_HISTORY_FILE, {"last_emitted_ts": 0, "scores": []})


def save_hunt_history(d):
    if not HUNT_ENABLED:
        return
    cfg.atomic_write(str(_HUNT_HISTORY_FILE), d)


# ═══════════════════════════════════════════════════════════════
# MARKET QUERIES
# ═══════════════════════════════════════════════════════════════

def query_asset_data(asset):
    """Spread + funding regime + candles for one asset."""
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
        "candles_4h": ad.get("candles_4h") or ad.get("candles4h") or [],
        "candles_1h": ad.get("candles_1h") or ad.get("candles1h") or [],
    }


# ═══════════════════════════════════════════════════════════════
# VOLUME MODE — funding-fade rotation
# ═══════════════════════════════════════════════════════════════

def choose_volume_direction(regime, last_direction):
    r = (regime or "").upper()
    if r in ("LONG_CROWDED", "LONG_HEAVY"):
        return "SHORT", "funding_fade_short"
    if r in ("SHORT_CROWDED", "SHORT_HEAVY"):
        return "LONG", "funding_fade_long"
    if last_direction == "LONG":
        return "SHORT", "alternate_neutral"
    return "LONG", "alternate_neutral"


def pick_volume_asset(rot_idx, xyz_weight, held_set, last_closed):
    """Pick next volume asset. Probabilistic XYZ/main weighting +
    deterministic rotation index inside each pool."""
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


def emit_volume_signal(asset, direction, thesis, ad, slot_index,
                       leverage, margin_usd, current_cycle_min):
    if not VOLUME_WALLET:
        cfg.log("TURBINE_VOLUME_WALLET not set; cannot emit volume signal")
        return False
    data_block = {
        "mode": "VOLUME",
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
            address=VOLUME_WALLET,
            scanner=VOLUME_SCANNER_NAME,
            asset=asset,
            direction=direction,
            signal_type=VOLUME_SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"volume push_signal rejected for {asset} {direction}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"volume push_signal exception for {asset} {direction}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# HUNT MODE — HYPE 4H momentum
# ═══════════════════════════════════════════════════════════════

def score_hype_momentum(ad):
    """Score HYPE 4H breakout. Returns (score, direction, reasons)."""
    candles_4h = ad.get("candles_4h") or []
    if not isinstance(candles_4h, list) or len(candles_4h) < 6:
        return 0, None, []
    candles_1h = ad.get("candles_1h") or []

    closes_4h = [cfg.safe_float(c.get("close", c.get("c", 0))) for c in candles_4h[-6:]]
    if len([c for c in closes_4h if c > 0]) < 6:
        return 0, None, []
    range_high = max(closes_4h)
    range_low = min(closes_4h)
    last_close = closes_4h[-1]

    if last_close > range_high * 0.998:
        direction = "LONG"
    elif last_close < range_low * 1.002:
        direction = "SHORT"
    else:
        return 0, None, []

    score = 0
    reasons = []

    if direction == "LONG":
        hh = sum(1 for i in range(1, 4) if closes_4h[-i] > closes_4h[-i - 1])
        if hh >= 3:
            score += 4
            reasons.append("4H_TREND_LONG_HH3")
    else:
        ll = sum(1 for i in range(1, 4) if closes_4h[-i] < closes_4h[-i - 1])
        if ll >= 3:
            score += 4
            reasons.append("4H_TREND_SHORT_LL3")

    pct_4h = ((last_close - closes_4h[-2]) / closes_4h[-2] * 100) if closes_4h[-2] > 0 else 0
    if direction == "LONG" and pct_4h >= 2.0:
        score += 3
        reasons.append(f"PRICE_4H +{pct_4h:.2f}%")
    elif direction == "SHORT" and pct_4h <= -2.0:
        score += 3
        reasons.append(f"PRICE_4H {pct_4h:.2f}%")

    if isinstance(candles_1h, list) and len(candles_1h) >= 4:
        closes_1h = [cfg.safe_float(c.get("close", c.get("c", 0))) for c in candles_1h[-4:]]
        if len([c for c in closes_1h if c > 0]) >= 4:
            pct_1h = ((closes_1h[-1] - closes_1h[-2]) / closes_1h[-2] * 100) if closes_1h[-2] > 0 else 0
            if (direction == "LONG" and pct_1h > 0) or (direction == "SHORT" and pct_1h < 0):
                score += 2
                reasons.append(f"MOMENTUM_1H {pct_1h:+.2f}%")

    vols_4h = [cfg.safe_float(c.get("volume", c.get("v", 0))) for c in candles_4h[-6:]]
    if len(vols_4h) == 6 and sum(vols_4h[:-1]) > 0:
        avg_prev = sum(vols_4h[:-1]) / 5.0
        if vols_4h[-1] >= avg_prev * 1.5:
            score += 2
            reasons.append(f"VOL {vols_4h[-1]/avg_prev:.1f}x")

    regime = ad.get("funding_regime", "UNKNOWN")
    if regime in ("NEUTRAL", "UNKNOWN"):
        score += 2
        reasons.append(f"FUNDING {regime}")
    elif (regime == "SHORT_CROWDED" and direction == "LONG") or \
         (regime == "LONG_CROWDED" and direction == "SHORT"):
        score += 2
        reasons.append(f"FUNDING {regime} (with-crowd-fade-bonus)")
    elif (regime == "LONG_CROWDED" and direction == "LONG") or \
         (regime == "SHORT_CROWDED" and direction == "SHORT"):
        score -= 1
        reasons.append(f"FUNDING {regime} FIGHT_CROWD -1")

    spread = cfg.safe_float(ad.get("spread_bps", 999))
    if spread <= 3.0:
        score += 2
        reasons.append(f"SPREAD {spread:.2f}bps DEEP")

    return max(0, score), direction, reasons


def emit_hunt_signal(asset, direction, score, reasons, ad, leverage, margin_usd):
    if not HUNT_WALLET:
        cfg.log("TURBINE_HUNT_WALLET not set; cannot emit hunt signal")
        return False
    data_block = {
        "mode": "HUNT",
        "thesis": "hype_4h_momentum",
        "score": int(score),
        "leverage": float(leverage),
        "marginUsd": float(margin_usd),
        "spreadBps": float(ad.get("spread_bps", 0)),
        "fundingRegime": ad.get("funding_regime", "UNKNOWN"),
        "reasons": " | ".join(reasons),
    }
    try:
        cfg._wrapper_client.push_signal(
            address=HUNT_WALLET,
            scanner=HUNT_SCANNER_NAME,
            asset=asset,
            direction=direction,
            signal_type=HUNT_SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"hunt push_signal rejected for {asset} {direction}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"hunt push_signal exception for {asset} {direction}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# CYCLE STATS — auto-fallback on maker fill rate
# ═══════════════════════════════════════════════════════════════

def determine_cycle_min(cfg_runtime, cycle_stats):
    window = cycle_stats.get("window", [])
    if len(window) < 5:
        return cfg_runtime["volume_cycle_default_min"]
    recent = window[-cfg_runtime["fill_rate_window_size"]:]
    fills = sum(1 for r in recent if r.get("filled_as_maker"))
    rate = fills / len(recent) if recent else 1.0
    if rate < cfg_runtime["fill_rate_fallback_threshold"]:
        return cfg_runtime["volume_cycle_fallback_min"]
    return cfg_runtime["volume_cycle_default_min"]


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
                      "STRATEGY_ADDRESS and TURBINE_WALLET are BANNED."),
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

    volume_held_keys = {cfg.normalize_coin_key(p["coin"]) for p in volume_positions}
    volume_held_keys.update(cfg.normalize_coin_key(o["coin"]) for o in volume_resting)
    free_volume = max(0, rt["volume_slots"] - len(volume_held_keys))

    # Detect closes on volume wallet
    prev_volume = load_volume_prev_held()
    closed_volume = prev_volume - volume_held_keys
    if closed_volume:
        last_closed = load_volume_last_closed()
        for k in closed_volume:
            last_closed[k] = {"ts": time.time(), "iso": cfg.now_iso()}
        save_volume_last_closed(last_closed)
    save_volume_prev_held(volume_held_keys)
    last_volume_closed = load_volume_last_closed()

    # ── HUNT WALLET state (if enabled) ──
    hunt_positions = []
    hunt_resting = []
    hunt_account_value = 0.0
    hunt_held_keys = set()
    free_hunt = 0
    closed_hunt = set()
    if HUNT_ENABLED:
        hunt_positions = cfg.get_open_positions(HUNT_WALLET)
        hunt_resting = cfg.get_resting_orders(HUNT_WALLET)
        hunt_account_value = cfg.get_account_value(HUNT_WALLET) or 0.0
        hunt_held_keys = {cfg.normalize_coin_key(p["coin"]) for p in hunt_positions}
        hunt_held_keys.update(cfg.normalize_coin_key(o["coin"]) for o in hunt_resting)
        free_hunt = max(0, rt["hunt_slots"] - len(hunt_held_keys))

        prev_hunt = load_hunt_prev_held()
        closed_hunt = prev_hunt - hunt_held_keys
        if closed_hunt:
            last_closed = load_hunt_last_closed()
            for k in closed_hunt:
                last_closed[k] = {"ts": time.time(), "iso": cfg.now_iso()}
            save_hunt_last_closed(last_closed)
        save_hunt_prev_held(hunt_held_keys)

    # ── VOLUME emission ──
    cycle_stats = load_cycle_stats()
    current_cycle_min = determine_cycle_min(rt, cycle_stats)
    rotation = load_rotation_index()
    rot_idx = rotation.get("index", 0)

    volume_emitted = []
    if free_volume > 0:
        for slot_idx in range(free_volume):
            asset, rot_idx = pick_volume_asset(
                rot_idx, rt["xyz_weight"], volume_held_keys, last_volume_closed
            )
            if asset is None:
                break
            ad = query_asset_data(asset)
            if ad is None:
                continue
            max_spread = rt["spread_xyz_bps"] if is_xyz(asset) else rt["spread_main_bps"]
            if ad["spread_bps"] > max_spread:
                continue
            direction, thesis = choose_volume_direction(ad["funding_regime"], "")
            if emit_volume_signal(
                asset, direction, thesis, ad,
                slot_idx, rt["volume_leverage"], rt["volume_margin"],
                current_cycle_min,
            ):
                volume_emitted.append({"asset": asset, "direction": direction, "thesis": thesis})
                # Mark as held for this tick to avoid double-emit
                volume_held_keys.add(cfg.normalize_coin_key(asset))

    save_rotation_index({"index": rot_idx})

    # ── HUNT emission (if enabled) ──
    hunt_emitted = []
    hunt_skipped_reason = None
    if HUNT_ENABLED and free_hunt > 0:
        if hunt_account_value < rt["min_hunt_wallet_balance"]:
            hunt_skipped_reason = (
                f"hunt wallet balance {hunt_account_value:.2f} < floor "
                f"{rt['min_hunt_wallet_balance']:.2f}"
            )
        else:
            hh = load_hunt_history()
            cooldown_sec = rt["hunt_cooldown_min"] * 60
            since_last_hunt = time.time() - hh.get("last_emitted_ts", 0)
            if since_last_hunt < cooldown_sec:
                hunt_skipped_reason = (
                    f"hunt cooldown active ({(cooldown_sec - since_last_hunt) / 60:.1f}min remaining)"
                )
            else:
                for asset in HUNT_ASSETS:
                    coin_key = cfg.normalize_coin_key(asset)
                    if coin_key in hunt_held_keys:
                        hunt_skipped_reason = f"{coin_key} already held on hunt wallet"
                        continue
                    ad = query_asset_data(asset)
                    if ad is None:
                        hunt_skipped_reason = f"{asset} asset_data fetch failed"
                        continue
                    if ad["spread_bps"] > rt["spread_main_bps"]:
                        hunt_skipped_reason = f"{asset} spread {ad['spread_bps']:.2f}bps > {rt['spread_main_bps']}"
                        continue
                    score, direction, reasons = score_hype_momentum(ad)
                    hh.setdefault("scores", []).append({
                        "asset": asset, "score": score, "direction": direction,
                        "ts": cfg.now_iso(),
                    })
                    if score < rt["hunt_min_score"] or direction is None:
                        hunt_skipped_reason = f"{asset} score {score} < floor {rt['hunt_min_score']}"
                        continue
                    if emit_hunt_signal(
                        asset, direction, score, reasons, ad,
                        rt["hunt_leverage"], rt["hunt_margin"],
                    ):
                        hunt_emitted.append({
                            "asset": asset, "direction": direction,
                            "score": score, "reasons": reasons,
                        })
                        hunt_held_keys.add(coin_key)
                        hh["last_emitted_ts"] = time.time()
                hh["scores"] = hh.get("scores", [])[-50:]
                save_hunt_history(hh)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "volume_wallet": VOLUME_WALLET[:10] + "...",
        "volume_account_value": round(volume_account_value, 2),
        "volume_positions": len(volume_positions),
        "volume_resting": len(volume_resting),
        "hunt_enabled": HUNT_ENABLED,
        "hunt_wallet": HUNT_WALLET[:10] + "..." if HUNT_ENABLED else None,
        "hunt_account_value": round(hunt_account_value, 2) if HUNT_ENABLED else None,
        "hunt_positions": len(hunt_positions),
        "hunt_resting": len(hunt_resting),
        "slots": {
            "volume": {"max": rt["volume_slots"], "held": len(volume_held_keys), "free": free_volume},
            "hunt":   {"max": rt["hunt_slots"] if HUNT_ENABLED else 0,
                       "held": len(hunt_held_keys), "free": free_hunt},
        },
        "current_cycle_min": current_cycle_min,
        "volume_emitted": volume_emitted,
        "hunt_emitted": hunt_emitted,
        "hunt_skipped_reason": hunt_skipped_reason,
        "closed_this_tick": {
            "volume": sorted(list(closed_volume)),
            "hunt": sorted(list(closed_hunt)),
        },
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
# alive_check is configured for the volume wallet's runtime — if
# turbine-volume-tracker is deleted OR the volume scanner is renamed,
# the daemon self-terminates. Hunt runtime can be deleted independently
# without affecting the daemon (operator notices via failed
# push_signal logs and decides to restart manually if needed).
#
# Do NOT add an inner scanner_lock(...) inside main() — fcntl flock
# is not reentrant; nested call raises BlockingIOError every tick.

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
        wallet=VOLUME_WALLET,                # volume runtime is alive-check'd
        scanner=VOLUME_SCANNER_NAME,
        tick_timeout=45,
    )
