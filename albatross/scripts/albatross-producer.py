#!/usr/bin/env python3
# Senpi ALBATROSS Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""ALBATROSS v1.0.0 — Multi-week Arena conviction tracker.

Mirrors trades from Senpi Arena participants whose performance has
been consistently strong across the last 4 weekly periods + the
current monthly leaderboard. Selection criterion is a composite
score that rewards persistence and penalizes single-week luck:

    conviction = 0.3 × monthly_roe
                + 0.7 × mean(weekly_roe across last 4 weeks)
                − 0.5 × stdev(weekly_roe)

A trader with steady +30% weekly across 4 weeks AND +25% monthly
beats a trader with one +98% week and three flat weeks.

Architecture (helpers-native):
  1. Refresh leader pool every leaderRefreshHours (default 4h).
     Pull arena_leaderboard for current week + last 3 weeks + month.
     Compute composite score per senpiUserId. Filter to top-N qualified.
     Cache pool in state/leader-pool.json with TTL.
  2. Each producer tick (300s):
     - For each leader in pool, fetch their strategy wallets via
       strategy_list({userIds: [...]}).
     - For each wallet, fetch current positions via
       discovery_get_trader_state(wallet, latest=true).
     - Diff against last-tick snapshot in state/leader-positions/.
     - For each NEW position detected: emit MIRROR signal.
  3. Producer writes to state/recent-signals.json (race-window dedup
     mirrors Bison v3.0.1 pattern). DSL handles all exits.

REQUIRES USER-SCOPE AUTH TOKEN — service tokens cannot call
strategy_list, discovery_get_trader_state, or discovery_get_trader_history
which are the core data sources here.

Producer NEVER closes positions. DSL is the only exit path.
"""

import hashlib
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import albatross_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
SCANNER_NAME = "albatross_signals"
SIGNAL_TYPE = "ALBATROSS_ARENA_MIRROR"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5
DEFAULT_POOL_SIZE = 5
DEFAULT_REFRESH_HOURS = 4
DEFAULT_MIN_WEEKS_TRADED = 3
DEFAULT_MIN_WEEKLY_VOL_USD = 50000


def _resolve_wallet():
    env_val = (os.environ.get("ALBATROSS_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Leader pool refresh
# ═══════════════════════════════════════════════════════════════

def fetch_arena_week(period_number=None):
    """Pull qualified leaderboard for a specific week (or current if None)."""
    params = {"period_type": "WEEK", "qualified": True, "limit": 50}
    if period_number is not None:
        params["period_number"] = period_number
    raw = cfg.mcp_call("arena_leaderboard", **params)
    if not raw or not raw.get("success", True):
        return []
    leaderboard = raw.get("data", {}).get("leaderboard", {})
    return leaderboard.get("entries", [])


def fetch_arena_month():
    raw = cfg.mcp_call("arena_leaderboard", period_type="MONTH", qualified=True, limit=50)
    if not raw or not raw.get("success", True):
        return []
    leaderboard = raw.get("data", {}).get("leaderboard", {})
    return leaderboard.get("entries", [])


def _get_current_week_number():
    """Pull the current week number from a fresh arena_leaderboard call."""
    raw = cfg.mcp_call("arena_leaderboard", period_type="WEEK", qualified=True, limit=1)
    if not raw or not raw.get("success", True):
        return None
    leaderboard = raw.get("data", {}).get("leaderboard", {})
    return leaderboard.get("periodNumber")


def compute_conviction_pool(config):
    """Pull 4 weekly + 1 monthly leaderboards, build composite scores."""
    weights = config.get("convictionWeights", {})
    w_monthly = float(weights.get("monthlyRoe", 0.3))
    w_weekly_mean = float(weights.get("weeklyRoeMean", 0.7))
    w_stdev_penalty = float(weights.get("weeklyRoeStdevPenalty", 0.5))
    min_weeks_traded = int(config.get("minWeeksTraded", DEFAULT_MIN_WEEKS_TRADED))
    min_weekly_vol = float(config.get("minWeeklyVolumeUsd", DEFAULT_MIN_WEEKLY_VOL_USD))
    excluded_handles = {h.lower() for h in config.get("excludeXHandles", ["betashop"])}

    current_week = _get_current_week_number()
    if current_week is None:
        cfg.log("could not resolve current week number — pool refresh aborted")
        return []

    # Pull current + last 3 weeks
    week_entries = {}
    for offset in range(4):
        week_num = current_week - offset
        if week_num <= 0:
            continue
        entries = fetch_arena_week(week_num)
        for e in entries:
            uid = e.get("senpiUserId")
            if not uid:
                continue
            handle = (e.get("xHandle") or "").lower()
            if handle in excluded_handles:
                continue
            vol = float(e.get("notionalVolume", 0))
            if vol < min_weekly_vol:
                continue
            roe = float(e.get("roePct", 0))
            week_entries.setdefault(uid, {
                "senpiUserId": uid,
                "username": e.get("senpiUserName"),
                "xHandle": e.get("xHandle"),
                "weekly_roe_by_week": {},
                "totalPnl": float(e.get("totalPnl", 0)),
                "currentBalance": float(e.get("currentBalance", 0)),
            })
            week_entries[uid]["weekly_roe_by_week"][str(week_num)] = roe

    # Pull current month
    monthly_entries = fetch_arena_month()
    monthly_roe_by_uid = {
        e.get("senpiUserId"): float(e.get("roePct", 0))
        for e in monthly_entries
        if e.get("senpiUserId")
    }

    # Compose
    candidates = []
    for uid, snap in week_entries.items():
        weekly_roes = list(snap["weekly_roe_by_week"].values())
        if len(weekly_roes) < min_weeks_traded:
            continue
        weekly_mean = statistics.mean(weekly_roes)
        weekly_std = statistics.pstdev(weekly_roes) if len(weekly_roes) > 1 else 0
        monthly_roe = monthly_roe_by_uid.get(uid, 0.0)
        conviction = (
            w_monthly * monthly_roe
            + w_weekly_mean * weekly_mean
            - w_stdev_penalty * weekly_std
        )
        if conviction <= 0:
            continue
        candidates.append({
            **snap,
            "weekly_roe": weekly_roes,
            "weekly_mean_roe": round(weekly_mean, 3),
            "weekly_stdev_roe": round(weekly_std, 3),
            "monthly_roe": round(monthly_roe, 3),
            "conviction_score": round(conviction, 3),
            "weeks_traded": len(weekly_roes),
        })

    candidates.sort(key=lambda c: c["conviction_score"], reverse=True)
    pool_size = int(config.get("leaderPoolSize", DEFAULT_POOL_SIZE))
    return candidates[:pool_size]


def get_leader_pool(config):
    """Return cached pool if fresh, otherwise recompute."""
    refresh_hours = float(config.get("leaderRefreshHours", DEFAULT_REFRESH_HOURS))
    cached = cfg.load_leader_pool()
    now = time.time()
    if cached and (now - cached.get("refreshed_at", 0)) < refresh_hours * 3600:
        return cached.get("leaders", [])
    leaders = compute_conviction_pool(config)
    cfg.save_leader_pool({"refreshed_at": now, "leaders": leaders})
    return leaders


# ═══════════════════════════════════════════════════════════════
# Leader position diffing
# ═══════════════════════════════════════════════════════════════

def fetch_leader_strategy_wallets(senpi_user_id):
    """Resolve a senpiUserId → list of strategy wallet addresses."""
    raw = cfg.mcp_call("strategy_list", userIds=[senpi_user_id], status=["ACTIVE"])
    if not raw or not raw.get("success", True):
        return []
    data = raw.get("data", raw)
    strategies = data.get("strategies", data) if isinstance(data, dict) else data
    if not isinstance(strategies, list):
        return []
    return [
        s.get("strategyWalletAddress")
        for s in strategies
        if s.get("strategyWalletAddress")
    ]


def fetch_leader_positions(wallets):
    """Pull current positions across all of a leader's strategy wallets."""
    if not wallets:
        return []
    raw = cfg.mcp_call(
        "discovery_get_trader_state",
        trader_addresses=wallets,
        latest=True,
    )
    if not raw or not raw.get("success", True):
        return []
    data = raw.get("data", raw)
    states = data.get("traders", data) if isinstance(data, dict) else data
    if not isinstance(states, list):
        return []
    positions = []
    for state in states:
        for pos in state.get("openPositions", []) if isinstance(state, dict) else []:
            coin = pos.get("coin") or pos.get("asset")
            szi = float(pos.get("szi", 0))
            if not coin or szi == 0:
                continue
            positions.append({
                "wallet": state.get("traderAddress"),
                "coin": coin,
                "direction": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "entryPx": float(pos.get("entryPx", 0)),
                "leverage": float(pos.get("leverage", {}).get("value", 1)) if isinstance(pos.get("leverage"), dict) else float(pos.get("leverage", 1)),
            })
    return positions


def detect_new_positions(senpi_user_id, current_positions):
    """Returns positions present in current but not in last snapshot."""
    last_snapshot = cfg.load_leader_positions(senpi_user_id)
    last_keys = {f"{p.get('coin')}:{p.get('direction')}" for p in last_snapshot}
    new_positions = [
        p for p in current_positions
        if f"{p.get('coin')}:{p.get('direction')}" not in last_keys
    ]
    # Update snapshot regardless
    cfg.save_leader_positions(senpi_user_id, current_positions)
    return new_positions


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_mirror_signal(leader, new_pos, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = new_pos["coin"]
    direction = new_pos["direction"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    dedup_key = f"{coin}:{direction}"
    if cfg.was_recently_signaled(dedup_key):
        return False

    data_block = {
        "leaderUsername": leader.get("username"),
        "leaderXHandle": leader.get("xHandle"),
        "leaderConvictionScore": leader.get("conviction_score"),
        "leaderWeeksTraded": leader.get("weeks_traded"),
        "leaderWeeklyRoeMean": leader.get("weekly_mean_roe"),
        "leaderMonthlyRoe": leader.get("monthly_roe"),
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": direction,
        "leaderEntryPrice": new_pos.get("entryPx"),
        "leaderLeverage": new_pos.get("leverage"),
        "reason": f"ALBATROSS_MIRROR {coin} {direction} from {leader.get('username')} (conviction {leader.get('conviction_score')})",
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=direction,
            score=min(float(leader.get("conviction_score", 0)) / 50.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        cfg.record_signal(dedup_key)
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {coin}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_albatross_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_albatross_producer_version": VERSION})
        return

    # Refresh leader pool (cached for refresh_hours)
    leaders = get_leader_pool(config)
    if not leaders:
        cfg.output({
            "status": "ok",
            "note": "no qualifying leaders (composite score positive, >=3 weeks traded)",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_albatross_producer_version": VERSION,
        })
        return

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed_signals = []
    for leader in leaders:
        senpi_user_id = leader.get("senpiUserId")
        wallets = fetch_leader_strategy_wallets(senpi_user_id)
        if not wallets:
            continue
        current_positions = fetch_leader_positions(wallets)
        new_positions = detect_new_positions(senpi_user_id, current_positions)
        for pos in new_positions:
            if push_mirror_signal(leader, pos, margin_usd, leverage, held_assets):
                pushed_signals.append({
                    "from_leader": leader.get("username"),
                    "coin": pos["coin"],
                    "direction": pos["direction"],
                    "conviction": leader.get("conviction_score"),
                })

    cfg.output({
        "status": "ok",
        "leader_pool_size": len(leaders),
        "signals_pushed": len(pushed_signals),
        "pushed": pushed_signals,
        "top_leaders": [
            {
                "username": l.get("username"),
                "xHandle": l.get("xHandle"),
                "conviction_score": l.get("conviction_score"),
                "weekly_mean_roe": l.get("weekly_mean_roe"),
                "monthly_roe": l.get("monthly_roe"),
                "weeks_traded": l.get("weeks_traded"),
            }
            for l in leaders
        ],
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_albatross_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"albatross-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
