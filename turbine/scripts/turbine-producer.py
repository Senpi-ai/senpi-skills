#!/usr/bin/env python3
# Senpi TURBINE Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""TURBINE v3.0 Producer — two-mode signal emitter.

VOLUME mode (7 slots, 10-min rotation):
  Volume engine. Funding-fade direction. XYZ-heavy rotation (80/20)
  for the lower fee floor. FEE_OPTIMIZED_LIMIT entries with
  ensure_execution_as_taker: false. DSL hard_timeout: 10min owns
  the exit. No Phase 2. Pure breakeven thesis — profit comes from
  Senpi's builder-fee recycling vs HL maker fee differential.

HUNT mode (2 slots, HYPE-only momentum):
  HYPE 4H breakout rider. Score >= 10 floor on multi-axis confluence.
  5x leverage, $1,250 margin. DSL Phase 2 ratchet enabled with
  HYPE-tuned ladder. Up to 4h hold. Fills the gap left by Wolverine
  v3.0 spec. Distinct asset + distinct timescale from VOLUME = clean
  per-mode P&L attribution.

Architecture: ONE producer + TWO runtimes on the same wallet:
  - turbine-volume-tracker  — DSL: hard_timeout 10min, no Phase 2
  - turbine-hunt-tracker    — DSL: hard_timeout 240min, Phase 2 ratchet

v3.0 changes vs v2.0.x:
  - senpi_runtime_helpers integration (no subprocess for MCP / signals)
  - Long-lived producer_daemon replaces openclaw cron + agentTurn
  - 3 → 9 slots (7 volume + 2 hunt). $6k funding requirement.
  - Cycle 15min → 10min default; auto-fallback to 12min when realized
    maker fill rate drops below 85%.
  - Spread gates tightened: main 5→3 bps, XYZ 15→10 bps.
  - Universe tightened: dropped TSLA + NVDA (wider spreads off-hours).
  - 70/30 → 80/20 XYZ/main weighting.
  - Sentinel sunset: hunt slots take over with explicit slot accounting.
  - STRATEGY_ADDRESS env var REMOVED — only TURBINE_WALLET (per v2.0.9
    contamination rule). Fail loud when missing.

Required env vars:
  TURBINE_WALLET                   — strategy wallet (must match runtimes)
  SENPI_AUTH_TOKEN                 — Bearer token for MCP + signal POST
  TURBINE_VOLUME_DECISION_MODEL    — bare model name for volume LLM gate
  TURBINE_HUNT_DECISION_MODEL      — bare model name for hunt LLM gate

Optional env vars (sensible defaults):
  SENPI_MCP_URL                    — default https://mcp.prod.senpi.ai/mcp
  SENPI_RUNTIME_API_HOST           — default 127.0.0.1
  SENPI_RUNTIME_API_PORT           — default 8787
  OPENCLAW_WORKSPACE               — default /data/workspace
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


VERSION = "3.0.0"

# Scanner names — must match runtime-volume.yaml + runtime-hunt.yaml
VOLUME_SCANNER_NAME = "turbine_volume_signals"
HUNT_SCANNER_NAME = "turbine_hunt_signals"


# ═══════════════════════════════════════════════════════════════
# WALLET RESOLUTION (TURBINE_WALLET only — no STRATEGY_ADDRESS fallback)
# ═══════════════════════════════════════════════════════════════

def _resolve_wallet():
    env = os.environ.get("TURBINE_WALLET", "").strip()
    if env:
        return env
    return (cfg.load_config().get("wallet") or "").strip()


TURBINE_WALLET = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIR
# ═══════════════════════════════════════════════════════════════

def _wallet_state_dir():
    h = (
        hashlib.sha256(TURBINE_WALLET.lower().encode()).hexdigest()[:12]
        if TURBINE_WALLET
        else "unset"
    )
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_SLOT_MODE_FILE = _STATE_DIR / "slot-mode.json"
_PREV_HELD_FILE = _STATE_DIR / "prev-held.json"
_LAST_CLOSED_FILE = _STATE_DIR / "last-closed.json"
_CYCLE_STATS_FILE = _STATE_DIR / "cycle-stats.json"
_HUNT_HISTORY_FILE = _STATE_DIR / "hunt-history.json"
_ROTATION_INDEX_FILE = _STATE_DIR / "rotation-index.json"


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
        "hunt_margin": float(margin.get("hunt", 1250)),
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
        "min_account_value_for_hunt": float(c.get("minAccountValueForHunt", 5500.0)),
    }


# ═══════════════════════════════════════════════════════════════
# UNIVERSE (tightened from v2.0.x)
# ═══════════════════════════════════════════════════════════════

# Volume universe — only assets with deep books + tight spreads.
VOLUME_XYZ = ["xyz:BRENTOIL", "xyz:GOLD", "xyz:SPX"]
VOLUME_MAIN = ["BTC", "ETH", "SOL", "HYPE"]

# Hunt universe — HYPE only.
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


def load_slot_mode():
    data = _load_json(_SLOT_MODE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_slot_mode(d):
    cfg.atomic_write(str(_SLOT_MODE_FILE), d)


def load_prev_held():
    data = _load_json(_PREV_HELD_FILE, {"assets": []})
    return set(data.get("assets", []))


def save_prev_held(held):
    cfg.atomic_write(str(_PREV_HELD_FILE), {"assets": sorted(list(held)), "updatedAt": cfg.now_iso()})


def load_last_closed():
    return _load_json(_LAST_CLOSED_FILE, {})


def save_last_closed(d):
    cfg.atomic_write(str(_LAST_CLOSED_FILE), d)


def load_cycle_stats():
    return _load_json(_CYCLE_STATS_FILE, {"window": []})


def save_cycle_stats(d):
    cfg.atomic_write(str(_CYCLE_STATS_FILE), d)


def load_hunt_history():
    return _load_json(_HUNT_HISTORY_FILE, {"last_emitted_ts": 0, "scores": []})


def save_hunt_history(d):
    cfg.atomic_write(str(_HUNT_HISTORY_FILE), d)


def load_rotation_index():
    return _load_json(_ROTATION_INDEX_FILE, {"index": 0})


def save_rotation_index(d):
    cfg.atomic_write(str(_ROTATION_INDEX_FILE), d)


# ═══════════════════════════════════════════════════════════════
# MARKET QUERIES
# ═══════════════════════════════════════════════════════════════

def query_asset_data(asset):
    """Spread + funding regime + candles for one asset. Returns dict or None."""
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


def pick_volume_asset(rot_idx, xyz_weight, held_set, slot_mode_map, last_closed):
    """Pick next volume asset. Probabilistic XYZ/main weighting + deterministic
    rotation index inside each pool. Skips held + post-close cooldown."""
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
    if not TURBINE_WALLET:
        cfg.log("TURBINE_WALLET not set; cannot emit volume signal")
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
            address=TURBINE_WALLET,
            scanner=VOLUME_SCANNER_NAME,
            asset=asset,
            direction=direction,
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
    if not TURBINE_WALLET:
        cfg.log("TURBINE_WALLET not set; cannot emit hunt signal")
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
            address=TURBINE_WALLET,
            scanner=HUNT_SCANNER_NAME,
            asset=asset,
            direction=direction,
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
# SLOT MODE RECONCILIATION
# ═══════════════════════════════════════════════════════════════

def reconcile_slot_mode(slot_mode, current_held):
    return {k: v for k, v in slot_mode.items() if k in current_held}


def detect_closes(prev_held, current_held):
    return prev_held - current_held


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

    if not TURBINE_WALLET:
        cfg.output({
            "status": "error",
            "error": "TURBINE_WALLET not set. Set the env var or populate config/turbine-config.json wallet field. STRATEGY_ADDRESS is BANNED.",
            "_turbine_producer_version": VERSION,
        })
        return

    rt = _runtime_config()

    open_positions = cfg.get_open_positions(TURBINE_WALLET)
    resting_orders = cfg.get_resting_orders(TURBINE_WALLET)
    account_value = cfg.get_account_value(TURBINE_WALLET)
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_turbine_producer_version": VERSION,
        })
        return

    held_keys = {cfg.normalize_coin_key(p["coin"]) for p in open_positions}
    held_keys.update(cfg.normalize_coin_key(o["coin"]) for o in resting_orders)

    slot_mode = load_slot_mode()
    slot_mode = reconcile_slot_mode(slot_mode, held_keys)

    volume_held = sum(1 for v in slot_mode.values() if v.get("mode") == "VOLUME")
    hunt_held = sum(1 for v in slot_mode.values() if v.get("mode") == "HUNT")
    free_volume = max(0, rt["volume_slots"] - volume_held)
    free_hunt = max(0, rt["hunt_slots"] - hunt_held)

    prev_held = load_prev_held()
    closed_keys = detect_closes(prev_held, held_keys)
    if closed_keys:
        last_closed = load_last_closed()
        for k in closed_keys:
            last_closed[k] = {"ts": time.time(), "iso": cfg.now_iso()}
        save_last_closed(last_closed)
    save_prev_held(held_keys)
    last_closed = load_last_closed()

    # ── VOLUME emission ──
    cycle_stats = load_cycle_stats()
    current_cycle_min = determine_cycle_min(rt, cycle_stats)
    rotation = load_rotation_index()
    rot_idx = rotation.get("index", 0)

    volume_emitted = []
    if free_volume > 0:
        for slot_idx in range(free_volume):
            asset, rot_idx = pick_volume_asset(
                rot_idx, rt["xyz_weight"], held_keys, slot_mode, last_closed
            )
            if asset is None:
                break
            ad = query_asset_data(asset)
            if ad is None:
                continue
            max_spread = rt["spread_xyz_bps"] if is_xyz(asset) else rt["spread_main_bps"]
            if ad["spread_bps"] > max_spread:
                continue
            coin_key = cfg.normalize_coin_key(asset)
            last_dir = (slot_mode.get(coin_key, {}) or {}).get("last_direction", "")
            direction, thesis = choose_volume_direction(ad["funding_regime"], last_dir)
            if emit_volume_signal(
                asset, direction, thesis, ad,
                slot_idx, rt["volume_leverage"], rt["volume_margin"],
                current_cycle_min,
            ):
                volume_emitted.append({"asset": asset, "direction": direction, "thesis": thesis})
                slot_mode[coin_key] = {
                    "mode": "VOLUME",
                    "entered_at": cfg.now_iso(),
                    "last_direction": direction,
                }
                held_keys.add(coin_key)

    save_rotation_index({"index": rot_idx})

    # ── HUNT emission ──
    hunt_emitted = []
    hunt_skipped_reason = None
    if free_hunt > 0:
        if account_value < rt["min_account_value_for_hunt"]:
            hunt_skipped_reason = (
                f"account_value {account_value:.2f} < hunt_floor "
                f"{rt['min_account_value_for_hunt']:.2f}"
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
                    if coin_key in held_keys:
                        hunt_skipped_reason = f"{coin_key} already held"
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
                        slot_mode[coin_key] = {
                            "mode": "HUNT",
                            "entered_at": cfg.now_iso(),
                            "score": score,
                            "direction": direction,
                        }
                        held_keys.add(coin_key)
                        hh["last_emitted_ts"] = time.time()
                hh["scores"] = hh.get("scores", [])[-50:]
                save_hunt_history(hh)

    save_slot_mode(slot_mode)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "account_value": round(account_value, 2),
        "open_positions": len(open_positions),
        "resting_orders": len(resting_orders),
        "slots": {
            "volume": {"max": rt["volume_slots"], "held": volume_held, "free": free_volume},
            "hunt":   {"max": rt["hunt_slots"],   "held": hunt_held,   "free": free_hunt},
        },
        "current_cycle_min": current_cycle_min,
        "volume_emitted": volume_emitted,
        "hunt_emitted": hunt_emitted,
        "hunt_skipped_reason": hunt_skipped_reason,
        "closed_this_tick": sorted(list(closed_keys)),
        "elapsed_sec": round(elapsed, 2),
        "_turbine_producer_version": VERSION,
    })


# ═══════════════════════════════════════════════════════════════
# DAEMON ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(TURBINE_WALLET.lower().encode()).hexdigest()[:12]
        if TURBINE_WALLET
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=60,
        name=f"turbine-producer-{_wallet_lock_id}",
        wallet=TURBINE_WALLET,
        scanner=VOLUME_SCANNER_NAME,
        tick_timeout=45,
    )
