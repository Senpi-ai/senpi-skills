#!/usr/bin/env python3
# Senpi ROACH Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""ROACH v3.0.0 Producer — Striker-only signal emitter for v2 runtime.

Roach v1.x ran as a full-agency Python scanner that:
  - Fetched market concentration + scan history
  - Detected Striker signals (FIRST_JUMP / IMMEDIATE_MOVER + volume)
  - Returned them as JSON for the agent to act on via create_position

v2.0 splits that into two parts:
  1. This producer (cron, 90s): emits candidate signals only.
  2. Runtime (senpi-trading-runtime): receives signals via
     external_scanner ingest, LLM-gates them (pass-through), executes
     with FEE_OPTIMIZED_LIMIT (maker-first + 60s + taker fallback),
     and manages DSL exits autonomously.

The producer's single responsibility: detect Striker signals and push
them to the runtime via `SenpiClient.push_signal()` (direct HTTP POST).

NO execution code. NO DSL code. NO daily/loss/cooldown gates beyond
the per-asset cooldown that gates the SAME asset re-entering. Daily-
loss / max-positions / consec-loss / drawdown are all enforced by
the runtime's declarative guard_rails.

Striker detection logic preserved verbatim from v1.2 scanner:
  - FIRST_JUMP from #25+ with rank jump >= 10 OR IMMEDIATE_MOVER
  - rank jump >= 15 OR contribVelocity >= 15
  - Score >= 10 with min 4 reasons (v1.1 tightened from 9)
  - Volume >= 1.5x of 6h average (v1.1 loosened from 2.0x)
  - cc_15m >= 0.5 (v1.2 — reject barely-positive velocity)
  - 1h price aligned with direction by >= 0.1% (v1.2)
  - 4h trend aligned with direction (hard block)
  - XYZ banned at scan level
  - Per-asset 120min cooldown

Environment variables (standard v2 producer):
  SENPI_API_KEY     — for MCP access
  ROACH_WALLET      — Roach v2 wallet (must match runtime YAML's wallet).
                      AGENT-SPECIFIC env var by design — do NOT fall back
                      to a generic STRATEGY_ADDRESS. Per Turbine v2.0.9
                      contamination fix: a shared env var is a fleet-wide
                      vector — if two agents are deployed in the same
                      install (Railway service, container, etc.) and one
                      sets STRATEGY_ADDRESS, the other inherits it and
                      silently emits to the wrong wallet.
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "roach_signals")
  ROACH_LEVERAGE    — optional override of default 7
  ROACH_MARGIN_USD  — optional override of default 250
  SENPI_AUTH_TOKEN  — REQUIRED. Bearer token for MCP + signal POST.
  ROACH_DECISION_MODEL — bare LLM model name (no provider prefix)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roach_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


# Reentrancy guard removed in v3.0.0. producer_daemon owns the per-tick
# scanner_lock with stale-PID auto-recovery. Do NOT add an inner
# scanner_lock(...) inside main().

# v2.0.0: Agent-specific wallet env var. NO fallback to STRATEGY_ADDRESS.
ROACH_WALLET = os.environ.get("ROACH_WALLET", "")

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "roach_signals"

# Signal type passed explicitly to push_signal() per Rachin's review
# of Cheetah PR #209.
SIGNAL_TYPE = "ROACH_STRIKER"

# Leverage + margin — operator-tunable, runtime defaults match runtime.yaml
DEFAULT_LEVERAGE = int(os.environ.get("ROACH_LEVERAGE", "7"))
DEFAULT_MARGIN_USD = float(os.environ.get("ROACH_MARGIN_USD", "250"))


# Wallet-isolated state (forward-compat for multi-wallet deployments).
# Falls back to "unset" if ROACH_WALLET not configured, so the module
# loads cleanly. main() refuses to run without ROACH_WALLET — see below.
def _wallet_state_dir():
    if ROACH_WALLET:
        # Lowercase for stable hashing — ETH addresses are case-insensitive
        # but checksum casing varies; we don't want different state paths
        # for "0xEf51..." vs "0xef51..." pointing to the same wallet.
        h = hashlib.sha256(ROACH_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_HISTORY_FILE = _STATE_DIR / "scan-history.json"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_EMITTED_FILE = _STATE_DIR / "last-emitted.json"


# ═══════════════════════════════════════════════════════════════
# STRIKER CONSTANTS (HARDCODED — fleet-tuned, not operator-set)
# ═══════════════════════════════════════════════════════════════
TOP_N = 50
ERRATIC_REVERSAL_THRESHOLD = 5
MIN_SCORE = 10                  # v1.1: tightened from 9
MIN_REASONS = 4
MIN_RANK_JUMP = 15
MIN_VELOCITY_OVERRIDE = 15
MIN_VELOCITY_FLOOR = 15         # v1.1: tightened from 10
MIN_VOL_RATIO = 1.5             # v1.1: loosened from 2.0
MIN_CC_15M = 0.5                # v1.2: tightened from 0
MIN_PRICE_1H_ALIGN_PCT = 0.1    # v1.2
ASSET_COOLDOWN_MINUTES = 120
MAX_HISTORY_SCANS = 60


# ═══════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════

def fetch_markets():
    """Fetch current SM market concentration."""
    try:
        data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
        if not data:
            return None
        data = data.get("data", data)
        raw = data.get("markets", data)
        if isinstance(raw, dict):
            raw = raw.get("markets", [])
        return raw
    except Exception:
        return None


def parse_scan(raw_markets):
    """Parse raw markets into a scan snapshot.
    HARDCODED: xyz: assets filtered at scan level — never enter the signal pipeline."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scan = {"time": now, "markets": []}
    for i, m in enumerate(raw_markets[:TOP_N]):
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        dex = m.get("dex", "")
        # XYZ ban (Fox data: SNDK -$57, GOLD -$8, CRCL -$9, MU -$7)
        if dex and dex.lower() == "xyz":
            continue
        if token.lower().startswith("xyz:"):
            continue
        scan["markets"].append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": m.get("direction", ""),
            "contribution": round(m.get("pct_of_top_traders_gain", 0), 6),
            "traders": m.get("trader_count", 0),
            "price_chg_4h": round(m.get("token_price_change_pct_4h", 0) or 0, 4),
            "price_chg_1h": round(m.get("token_price_change_pct_1h", m.get("price_change_1h", 0)) or 0, 4),
            "cc_15m": float(m.get("contribution_pct_change_15m", 0) or 0),
        })
    return scan


def get_market_in_scan(scan, token, dex=""):
    for m in scan["markets"]:
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# VOLUME CONFIRMATION
# ═══════════════════════════════════════════════════════════════

def check_asset_volume(token, dex=""):
    """Check if raw asset volume is alive. Returns (ratio, is_strong)."""
    asset_name = f"{dex}:{token}" if dex else token
    data = cfg.mcporter_call("market_get_asset_data", asset=asset_name,
                             candle_intervals=["1h"],
                             include_funding=False, include_order_book=False)
    if not data:
        return 0, False

    candle_data = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(candle_data, dict):
        candles = candle_data.get("candles", {}).get("1h", [])
    else:
        return 0, False

    if len(candles) < 6:
        return 0, False

    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-6:]]
    avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
    latest_vol = vols[-1] if vols else 0
    ratio = latest_vol / avg_vol if avg_vol > 0 else 0
    return ratio, ratio >= MIN_VOL_RATIO


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def is_erratic_history(rank_history, exclude_last=False):
    """Detect zigzag rank patterns."""
    nums = [r for r in rank_history if r is not None]
    if exclude_last and len(nums) > 1:
        nums = nums[:-1]
    if len(nums) < 3:
        return False
    for i in range(1, len(nums) - 1):
        prev_delta = nums[i] - nums[i - 1]
        next_delta = nums[i + 1] - nums[i]
        if prev_delta < 0 and next_delta > ERRATIC_REVERSAL_THRESHOLD:
            return True
        if prev_delta > 0 and next_delta < -ERRATIC_REVERSAL_THRESHOLD:
            return True
    return False


def time_of_day_modifier():
    """UTC time-of-day scoring adjustment."""
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return 1, "time_bonus_optimal_window"
    elif hour >= 18 or hour < 2:
        return -2, "time_penalty_chop_zone"
    return 0, None


def check_4h_alignment(direction, price_chg_4h):
    """4H trend must agree with signal direction. Hard block."""
    if direction == "LONG" and price_chg_4h < 0:
        return False
    if direction == "SHORT" and price_chg_4h > 0:
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# SCAN HISTORY (wallet-isolated)
# ═══════════════════════════════════════════════════════════════

def load_scan_history():
    try:
        with open(_HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"scans": []}


def save_scan_history(history):
    if len(history.get("scans", [])) > MAX_HISTORY_SCANS:
        history["scans"] = history["scans"][-MAX_HISTORY_SCANS:]
    cfg.atomic_write(str(_HISTORY_FILE), history)


# ═══════════════════════════════════════════════════════════════
# ASSET COOLDOWNS (producer-side; runtime also has per-asset gate)
# ═══════════════════════════════════════════════════════════════

def load_cooldowns():
    try:
        with open(_COOLDOWN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cooldowns(cooldowns):
    cfg.atomic_write(str(_COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(token, cooldown_minutes=ASSET_COOLDOWN_MINUTES):
    """Producer-side cooldown — emit-suppression. Runtime also enforces
    per_asset_cooldown_minutes independently as a second line of defense."""
    cooldowns = load_cooldowns()
    if token not in cooldowns:
        return False
    last_emit_ts = cooldowns[token].get("emittedTimestamp", 0)
    elapsed_min = (time.time() - last_emit_ts) / 60
    return elapsed_min < cooldown_minutes


def mark_asset_emitted(token):
    cooldowns = load_cooldowns()
    cooldowns[token] = {
        "emittedTimestamp": time.time(),
        "setAt": cfg.now_iso(),
    }
    save_cooldowns(cooldowns)


# ═══════════════════════════════════════════════════════════════
# STRIKER DETECTION (v1.2 logic, unchanged)
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP / IMMEDIATE_MOVER signals.
    Logic preserved verbatim from roach-scanner.py v1.2."""

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]

    prev_top50_tokens = set()
    for m in latest_prev["markets"]:
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

    signals = []

    for market in current_scan["markets"]:
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = market["direction"].upper()
        current_contrib = market["contribution"]

        # Destination ceiling: reject if in top 10
        if current_rank <= 10:
            continue

        # 4H trend alignment (hard block)
        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)

        if not prev_market:
            continue

        rank_jump = prev_market["rank"] - current_rank

        # ── FIRST_JUMP detection ──
        is_first_jump = False
        is_immediate = False
        is_contrib_explosion = False
        reasons = []

        if rank_jump >= 10 and prev_market["rank"] >= 25:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_market['rank']}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_market["rank"] >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_market['rank']}->#{current_rank}")

        # Contribution explosion
        if prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                is_contrib_explosion = True
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        if not is_first_jump and not is_immediate:
            continue

        # Velocity computation
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        abs_velocity = abs(contrib_velocity)

        if rank_jump < MIN_RANK_JUMP and abs_velocity < MIN_VELOCITY_OVERRIDE:
            continue

        # Velocity floor
        if abs_velocity < MIN_VELOCITY_FLOOR:
            if is_first_jump and contrib_velocity > 0:
                pass
            else:
                continue

        # ── SCORING ──
        score = 0
        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs_velocity > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs_velocity:.1f}")

        if prev_market["rank"] >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        if old_market:
            total_climb = old_market["rank"] - current_rank
            if total_climb >= 10:
                score += 1
                reasons.append(f"CLIMBING +{total_climb} over scans")

        tod_mod, tod_reason = time_of_day_modifier()
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        if score < MIN_SCORE or len(reasons) < MIN_REASONS:
            continue

        # 15m velocity freshness gate
        cc_15m = float(market.get("cc_15m", 0))
        if cc_15m < MIN_CC_15M:
            continue

        # 1h price confirmation of direction
        p1h = float(market.get("price_chg_1h", 0))
        if direction == "LONG" and p1h < MIN_PRICE_1H_ALIGN_PCT:
            continue
        if direction == "SHORT" and p1h > -MIN_PRICE_1H_ALIGN_PCT:
            continue
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

        # Volume confirmation
        vol_ratio, vol_strong = check_asset_volume(token, dex)
        if not vol_strong:
            continue
        reasons.append(f"VOL_CONFIRMED {vol_ratio:.1f}x")

        # Build canonical asset id (no XYZ — already filtered out)
        asset_id = token

        signals.append({
            "asset": asset_id,
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "prevRank": prev_market["rank"],
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "isContribExplosion": is_contrib_explosion,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": market["traders"],
            "priceChg4h": market.get("price_chg_4h", 0),
            "priceChg1h": p1h,
            "cc15m": cc_15m,
        })

    return signals


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(s):
    """v3.0.0: returns only fields push_signal() forwards (asset, direction, data)."""
    return {
        "asset": s["asset"],
        "direction": s["direction"],
        "data": {
            "mode": s["mode"],
            "score": s["score"],
            "rankJump": s["rankJump"],
            "currentRank": s["currentRank"],
            "prevRank": s.get("prevRank", 0),
            "contribVelocity": s.get("contribVelocity", 0),
            "volRatio": s.get("volRatio", 0),
            "priceChg4h": s.get("priceChg4h", 0),
            "priceChg1h": s.get("priceChg1h", 0),
            "cc15m": s.get("cc15m", 0),
            "traders": s.get("traders", 0),
            "contribution": s.get("contribution", 0),
            "isFirstJump": bool(s.get("isFirstJump", False)),
            "isContribExplosion": bool(s.get("isContribExplosion", False)),
            "reasons": " | ".join(s.get("reasons", [])),
            "leverage": DEFAULT_LEVERAGE,
            "marginUsd": DEFAULT_MARGIN_USD,
        },
    }


def push_signal(payload):
    """Push a signal via senpi_runtime_helpers wrapper (direct HTTP POST)."""
    if not ROACH_WALLET:
        cfg.log("ROACH_WALLET env var not set; cannot push signal")
        return False
    try:
        cfg._wrapper_client.push_signal(
            address=ROACH_WALLET,
            scanner=SCANNER_NAME,
            asset=payload.get("asset"),
            direction=payload.get("direction"),
            signal_type=SIGNAL_TYPE,
            data=payload.get("data"),
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"push_signal rejected for {payload.get('asset')} {payload.get('direction')}: {e}")
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        cfg.log(f"push_signal exception for {payload.get('asset')}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    # Fail loud if wallet not configured (Turbine v2.0.9 pattern).
    # Don't silently process scan history when we couldn't push a signal
    # if we tried — that just builds up state in the "unset" hash dir.
    if not ROACH_WALLET:
        cfg.output({
            "status": "error",
            "error": "ROACH_WALLET env var not set. Set it to the Roach strategy wallet (must match runtime.yaml).",
            "_roach_producer_version": "3.0.0",
        })
        return

    # Fetch & parse current scan
    raw = fetch_markets()
    if raw is None:
        cfg.output({
            "status": "error",
            "error": "failed to fetch markets",
            "_roach_producer_version": "3.0.0",
        })
        return

    current_scan = parse_scan(raw)

    # Load history (rank-jump detection requires it)
    history = load_scan_history()

    # Detect striker signals
    striker_signals = detect_striker_signals(current_scan, history)

    # Persist scan history (always, even if no signals)
    history["scans"].append(current_scan)
    save_scan_history(history)

    # Apply per-asset cooldown (defense-in-depth alongside runtime guard)
    striker_signals = [s for s in striker_signals
                       if not is_asset_cooled_down(s["token"])]

    # Sort highest score first
    striker_signals.sort(key=lambda s: s["score"], reverse=True)

    if not striker_signals:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "totalMarkets": len(current_scan["markets"]),
            "scansInHistory": len(history["scans"]),
            "candidates": 0,
            "elapsed_sec": round(elapsed, 2),
            "_roach_producer_version": "3.0.0",
        })
        return

    # Emit signals
    pushed = 0
    for s in striker_signals:
        payload = build_signal_payload(s)
        if push_signal(payload):
            pushed += 1
            mark_asset_emitted(s["token"])

    elapsed = time.time() - run_start
    warn = "WARN_OVER_90S" if elapsed > 90 else None
    cfg.output({
        "status": "ok",
        "totalMarkets": len(current_scan["markets"]),
        "scansInHistory": len(history["scans"]),
        "candidates_detected": len(striker_signals),
        "signals_pushed": pushed,
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_roach_producer_version": "3.0.0",
    })


if __name__ == "__main__":
    # v3.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(ROACH_WALLET.lower().encode()).hexdigest()[:12]
        if ROACH_WALLET
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=90,           # Roach's v2.x cron was every 90s
        name=f"roach-producer-{_wallet_lock_id}",
        wallet=ROACH_WALLET,
        scanner=SCANNER_NAME,
        tick_timeout=180,
    )
