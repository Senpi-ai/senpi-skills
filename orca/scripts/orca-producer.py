#!/usr/bin/env python3
# Senpi ORCA Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""ORCA v4.0.0 Producer — Gen-1 Vanilla Striker, helpers-native.

v4.0.0 (2026-05-12): plumbing migration from v3.0. NO thesis change.
v3.0 Gen-1 vanilla Striker logic (FIRST_JUMP + base scoring + volume
confirmation), MAX_LEVERAGE 7, MIN_SCORE 9, 4-reason floor, MAX_POSITIONS
3, 120-min cooldown all preserved verbatim.

Six-layer plumbing flip:
1. MCP transport: mcporter subprocess → SenpiClient.mcp_call() HTTPS.
2. Signal emit: scanner called create_position directly; producer
   now emits via push_signal() to runtime /signals. Runtime opens
   via LLM-gated orca_entry action with FEE_OPTIMIZED_LIMIT.
3. Reentrancy: producer_daemon owns scanner_lock with stale-PID
   auto-recovery.
4. Scheduler: openclaw cron → producer_daemon(interval_seconds=90).
5. Risk gates: Python MAX_DAILY_ENTRIES + dynamic daily cap →
   declarative risk.guard_rails. State files for daily counter
   now vestigial.
6. Exit fee: DSL exits MARKET → FEE_OPTIMIZED_LIMIT (maker-first).

Environment:
  SENPI_AUTH_TOKEN      — REQUIRED.
  ORCA_WALLET           — strategy wallet (or config.json fallback)
  ORCA_DECISION_MODEL   — bare LLM model name for the entry gate
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "4.0.0"
SCANNER_NAME = "orca_signals"
SIGNAL_TYPE = "ORCA_STRIKER_FIRST_JUMP"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v3.0
# ═══════════════════════════════════════════════════════════════

TOP_N = 50
MIN_LEVERAGE = 7
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MARGIN_PCT = 0.18
XYZ_BANNED = True

# Striker thresholds
STRIKER_MIN_SCORE = 9
STRIKER_MIN_REASONS = 4
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25
STRIKER_MIN_VOL_RATIO = 1.5


def _resolve_wallet():
    env_val = (os.environ.get("ORCA_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_4h_alignment(direction, price_chg_4h):
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def time_of_day_modifier():
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        return 1, "US_SESSION"
    return 0, None


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# FETCH + PARSE — preserved verbatim
# ═══════════════════════════════════════════════════════════════

def fetch_markets():
    try:
        data = cfg.mcp_call("leaderboard_get_markets", limit=100)
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
    markets = []
    for i, m in enumerate(raw_markets):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = m.get("dex", "")
        if XYZ_BANNED and (dex == "xyz" or token.lower().startswith("xyz:")):
            continue
        if not token:
            continue

        markets.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "cc_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
        })
    return {"markets": markets[:TOP_N], "time": now_iso()}


def get_safe_leverage(wallet, asset, requested_leverage):
    try:
        limits = cfg.mcp_call(
            "strategy_get_asset_trading_limits",
            strategy_wallet=wallet,
            coin=asset,
        )
        if limits:
            data = limits.get("data", limits)
            if isinstance(data, dict):
                lev = data.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", MAX_LEVERAGE)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


def check_asset_volume(token, dex):
    try:
        data = cfg.mcp_call("market_get_asset_data",
                            asset=token, candle_intervals=["1h"],
                            include_funding=False)
        if not data:
            return 0, True
        ad = data.get("data", data)
        if not isinstance(ad, dict):
            return 0, True
        ac = ad.get("asset_context", ad.get("assetContext", {}))
        if not isinstance(ac, dict):
            return 0, True
        vol = safe_float(ac.get("dayNtlVlm", 0))
        prev = safe_float(ac.get("prevDayNtlVlm", 0))
        if prev > 0:
            ratio = vol / prev
            return ratio, ratio >= STRIKER_MIN_VOL_RATIO
        return 0, True
    except Exception:
        return 0, True


# ═══════════════════════════════════════════════════════════════
# SIGNAL DETECTION — preserved verbatim from v3.0
# ═══════════════════════════════════════════════════════════════

def detect_signals(current_scan, history):
    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = market["direction"]
        current_contrib = market["contribution"]

        if current_rank <= 10:
            continue
        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)
        if not prev_market:
            continue

        rank_jump = prev_market["rank"] - current_rank

        is_first_jump = False
        is_immediate = False
        is_contrib_explosion = False
        reasons = []

        if rank_jump >= 10 and prev_market["rank"] >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_market['rank']}")
            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_market["rank"] >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_market['rank']}->#{current_rank}")

        if prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                is_contrib_explosion = True
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        if not is_first_jump and not is_immediate:
            continue
        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        # Contribution velocity
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i]
                      for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        # ── Base Striker scoring ──
        score = 0
        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs(contrib_velocity):.1f}")
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

        if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # 15m velocity freshness gate
        cc_15m = safe_float(market.get("cc_15m", 0))
        if cc_15m <= 0:
            continue

        # Volume confirmation
        vol_ratio, vol_strong = check_asset_volume(token, dex)
        if not vol_strong:
            continue
        reasons.append(f"VOL_CONFIRMED {vol_ratio:.1f}x")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "isContribExplosion": is_contrib_explosion,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": market["traders"],
            "priceChg4h": market.get("price_chg_4h", 0),
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(signal, leverage, margin_usd, held_assets):
    """Emit a STRIKER signal. asset/direction top-level routing,
    leverage + marginUsd in data block for LLM gate to pass through."""
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False
    if signal["token"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": signal["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "mode": signal["mode"],
        "currentRank": signal["currentRank"],
        "rankJump": signal["rankJump"],
        "isFirstJump": signal["isFirstJump"],
        "isContribExplosion": signal["isContribExplosion"],
        "contribVelocity": signal["contribVelocity"],
        "volRatio": signal["volRatio"],
        "contribution": signal["contribution"],
        "traders": signal["traders"],
        "priceChg4h": signal["priceChg4h"],
        "reasons": " | ".join(signal["reasons"]),
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=signal["token"],
            direction=signal["direction"],
            score=min(signal["score"] / 14.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {signal['token']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {signal['token']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_orca_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_orca_producer_version": VERSION})
        return

    raw_markets = fetch_markets()
    if raw_markets is None:
        cfg.output({"status": "error", "error": "failed to fetch markets",
                    "_orca_producer_version": VERSION})
        return

    current_scan = parse_scan(raw_markets)
    history = cfg.load_scan_history()
    signals = detect_signals(current_scan, history)

    history["scans"].append(current_scan)
    cfg.save_scan_history(history)

    # Filter cooldowns + held assets
    signals = [s for s in signals
               if not cfg.is_asset_cooled_down(s["token"], 120)]
    held_upper = {h.upper() for h in held_assets}
    signals = [s for s in signals if s["token"] not in held_upper]

    if not signals:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(current_scan.get("markets", [])),
            "scansInHistory": len(history["scans"]),
            "candidates": 0,
            "signals_pushed": 0,
            "note": "No Striker signals.",
            "elapsed_sec": round(elapsed, 2),
            "_orca_producer_version": VERSION,
        })
        return

    best = signals[0]
    margin_usd = round(account_value * MARGIN_PCT, 2)
    safe_leverage = get_safe_leverage(STRATEGY_ADDRESS, best["token"], DEFAULT_LEVERAGE)

    pushed = push_signal(best, safe_leverage, margin_usd, held_assets)
    elapsed = time.time() - run_start

    cfg.output({
        "status": "ok",
        "scanned": len(current_scan.get("markets", [])),
        "candidates": len(signals),
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "asset": best["token"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": safe_leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:6],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_orca_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=90,                    # 90s — preserved from v3.0 cron cadence
        name=f"orca-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
