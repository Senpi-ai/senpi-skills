"""JACKAL Pool Maintainer — The Curation Brain.

Maintains two tiers of tracked traders:
  - Watchlist (~100-200 traders): candidates, polled lightly
  - Active Pool (~20-30 traders): qualified, Jackal acts on their trades

Emerging-mover detection: trajectory-based scoring (not rank-based).
A trader going -$500 → +$100 scores higher than one going +$500 → +$300.

Rank velocity + recent trade outcomes + PnL trajectory catch rising
stars BEFORE they hit rank 1.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import time
from collections import defaultdict

import jackal_config as cfg


# ═══════════════════════════════════════════════════════════════
# Promotion / Demotion thresholds
# ═══════════════════════════════════════════════════════════════

PROMOTE_SCORE_THRESHOLD = 70       # Watchlist → Active Pool
RETAIN_SCORE_THRESHOLD = 50        # Below this, demote from Active Pool
DRAWDOWN_HARD_DEMOTE_PCT = 10.0    # 24h drawdown >10% = instant demote
FEE_DRAG_HARD_LIMIT = 40.0         # fee_drag >40% = disqualified
DEMOTION_COOLDOWN_HOURS = 48       # can't re-promote for 48h after demote

# Pool size caps (soft — scoring decides, these just cap compute)
MAX_WATCHLIST_SIZE = 200
MAX_ACTIVE_POOL_SIZE = 30

# Minimum history requirements
MIN_TRADES_HISTORY = 10            # need at least 10 closed trades
MIN_ACCOUNT_AGE_DAYS = 7           # strategy must be 7+ days old

# Refresh intervals (seconds)
WATCHLIST_REFRESH_INTERVAL = 6 * 3600      # 6 hours — who's in Arena/points leaderboard
SCORE_REFRESH_INTERVAL = 1 * 3600          # 1 hour — re-score all tracked traders


# ═══════════════════════════════════════════════════════════════
# Scoring — the heart of the curation engine
# ═══════════════════════════════════════════════════════════════

def score_pnl_trajectory(pnl_7d, pnl_24h):
    """0-100. Reward improving trajectory, not absolute level.
    A trader recovering from losses scores higher than one cooling off."""
    if pnl_7d > 500 and pnl_24h > 0:
        return 90   # strong + still rising
    elif pnl_7d > 100 and pnl_24h > 0:
        return 75
    elif pnl_7d > 0 and pnl_24h > 0:
        return 60
    elif pnl_7d < 0 and pnl_24h > 50:
        return 55   # EMERGING: losing week but strong 24h recovery
    elif pnl_7d > 0 and pnl_24h < -50:
        return 35   # COOLING: good week but bleeding today
    elif pnl_7d > 0 and pnl_24h < 0:
        return 45   # mild cooling
    elif pnl_7d < 0 and pnl_24h > 0:
        return 40   # slight recovery
    else:
        return 15   # still losing


def score_rank_velocity(rank_delta_24h, rank_delta_7d, pnl_24h):
    """0-100. Reward rank ascent backed by real PnL.
    rank_delta_24h > 0 means moved UP (smaller rank number)."""
    if rank_delta_24h >= 15 and pnl_24h > 100:
        return 90   # EMERGING MOVER — huge ascent on real PnL
    elif rank_delta_24h >= 10 and pnl_24h > 0:
        return 80
    elif rank_delta_24h >= 5 and pnl_24h > 0:
        return 70
    elif rank_delta_24h > 0:
        return 55
    elif rank_delta_24h == 0 and rank_delta_7d >= 0:
        return 50   # stable at top
    elif rank_delta_24h >= -5:
        return 40
    elif rank_delta_24h >= -15:
        return 25   # cooling
    else:
        return 10   # dropping fast


def score_recent_outcomes(recent_trades):
    """0-100. Last 5 trades' pattern."""
    if not recent_trades or len(recent_trades) < 3:
        return 40   # not enough data
    last_5 = recent_trades[:5]
    wins = sum(1 for t in last_5 if float(t.get("realizedPnl", 0)) > 0)
    net = sum(float(t.get("realizedPnl", 0)) for t in last_5)

    if wins == 5 and net > 0:
        return 90   # hot hand
    elif wins >= 4 and net > 0:
        return 80
    elif wins >= 3 and net > 0:
        return 65
    elif wins >= 3:
        return 50   # more wins than losses but net negative (bad sizing)
    elif wins == 2 and net > 0:
        return 50
    elif wins == 2:
        return 35
    elif wins >= 1 and net > 0:
        return 45   # 1-4 record but winners big
    else:
        return 15


def score_win_loss_ratio(wins_count, losses_count, wins_sum, losses_sum):
    """0-100. Classic ratio scoring (not win rate)."""
    if wins_count == 0:
        return 10
    if losses_count == 0:
        return 90   # very small sample usually
    avg_win = wins_sum / wins_count
    avg_loss = abs(losses_sum) / losses_count
    if avg_loss == 0:
        return 90
    ratio = avg_win / avg_loss
    if ratio >= 3:
        return 90
    elif ratio >= 2:
        return 75
    elif ratio >= 1.5:
        return 60
    elif ratio >= 1:
        return 45
    elif ratio >= 0.7:
        return 25
    else:
        return 10


def score_fee_efficiency(fee_drag_pct):
    """0-100. Low fee drag = patient trader. High fee drag = overtrader."""
    if fee_drag_pct < 10:
        return 95
    elif fee_drag_pct < 20:
        return 80
    elif fee_drag_pct < 30:
        return 65
    elif fee_drag_pct < 50:
        return 40
    elif fee_drag_pct < 80:
        return 20
    else:
        return 5


def score_consistency(pnl_days_history):
    """0-100. Number of days with positive PnL in last 14 days."""
    if not pnl_days_history or len(pnl_days_history) < 5:
        return 40
    positive_days = sum(1 for p in pnl_days_history[-14:] if p > 0)
    total_days = min(len(pnl_days_history), 14)
    pct = positive_days / total_days
    if pct >= 0.7:
        return 85
    elif pct >= 0.5:
        return 65
    elif pct >= 0.35:
        return 45
    else:
        return 20


# ═══════════════════════════════════════════════════════════════
# Eligibility helpers (pre-filter before full scoring)
# ═══════════════════════════════════════════════════════════════

def compute_trader_metrics(user_id, wallet, closed_trades, pnl_history):
    """Compute the raw metrics needed for scoring.
    Returns None if trader lacks minimum data."""
    if not closed_trades or len(closed_trades) < MIN_TRADES_HISTORY:
        return None

    now = cfg.now_ts()
    day_ago_ts = now - 86400
    week_ago_ts = now - 7 * 86400

    # Filter by time windows
    trades_24h = [t for t in closed_trades if t.get("closeTime", 0) >= day_ago_ts]
    trades_7d = [t for t in closed_trades if t.get("closeTime", 0) >= week_ago_ts]

    if not trades_7d:
        return None   # no recent activity

    # PnL sums
    pnl_24h = sum(float(t.get("realizedPnl", 0)) for t in trades_24h)
    pnl_7d = sum(float(t.get("realizedPnl", 0)) for t in trades_7d)

    # Win/loss stats (all-time, for ratio stability)
    wins = [t for t in closed_trades if float(t.get("realizedPnl", 0)) > 0]
    losses = [t for t in closed_trades if float(t.get("realizedPnl", 0)) < 0]
    wins_sum = sum(float(t.get("realizedPnl", 0)) for t in wins)
    losses_sum = sum(float(t.get("realizedPnl", 0)) for t in losses)

    # Fee drag
    total_fees = sum(float(t.get("totalFees", 0)) for t in closed_trades)
    gross_pnl = abs(wins_sum) + abs(losses_sum)
    fee_drag_pct = (total_fees / gross_pnl * 100) if gross_pnl > 0 else 100

    # Average winner hold duration (filters scalpers out of pool)
    if wins:
        avg_winner_hold_hours = sum(
            (float(t.get("closeTime", 0)) - float(t.get("openTime", 0))) / 3600
            for t in wins
        ) / len(wins)
    else:
        avg_winner_hold_hours = 0

    # PnL curve for drawdown + consistency
    pnl_history_values = []
    if pnl_history:
        week_data = pnl_history.get("perpWeek", {}).get("pnlHistory", [])
        pnl_history_values = [float(v[1]) for v in week_data if len(v) >= 2]

    # 24h drawdown
    drawdown_24h_pct = 0
    if pnl_history:
        today_snap = pnl_history.get("today_snapshot", {})
        if today_snap:
            dd = today_snap.get("drawdown", {}).get("from_peak_pct", 0)
            drawdown_24h_pct = float(dd)

    return {
        "user_id": user_id,
        "wallet": wallet,
        "pnl_24h": pnl_24h,
        "pnl_7d": pnl_7d,
        "wins_count": len(wins),
        "losses_count": len(losses),
        "wins_sum": wins_sum,
        "losses_sum": losses_sum,
        "fee_drag_pct": fee_drag_pct,
        "avg_winner_hold_hours": avg_winner_hold_hours,
        "total_trades": len(closed_trades),
        "drawdown_24h_pct": drawdown_24h_pct,
        "recent_trades": closed_trades[:5],
        "pnl_history_values": pnl_history_values,
    }


def passes_hard_filters(metrics, avg_winner_hold_hours_min=2.0):
    """Hard disqualifiers before scoring."""
    if metrics is None:
        return False, "insufficient_data"
    if metrics["fee_drag_pct"] > FEE_DRAG_HARD_LIMIT:
        return False, f"fee_drag_{metrics['fee_drag_pct']:.0f}%"
    if metrics["drawdown_24h_pct"] > DRAWDOWN_HARD_DEMOTE_PCT:
        return False, f"drawdown_{metrics['drawdown_24h_pct']:.0f}%"
    if metrics["avg_winner_hold_hours"] < avg_winner_hold_hours_min:
        # Scalper — can't copy their edge at our latency
        return False, f"scalper_hold_{metrics['avg_winner_hold_hours']:.1f}h"
    # Require a 2:1+ ratio minimum
    if metrics["wins_count"] > 0 and metrics["losses_count"] > 0:
        avg_win = metrics["wins_sum"] / metrics["wins_count"]
        avg_loss = abs(metrics["losses_sum"]) / metrics["losses_count"]
        ratio = avg_win / avg_loss if avg_loss > 0 else 99
        if ratio < 1.5:
            return False, f"ratio_{ratio:.1f}"
    # Require positive 7d OR strong 24h recovery
    if metrics["pnl_7d"] < 0 and metrics["pnl_24h"] < 50:
        return False, "no_positive_trajectory"
    return True, None


def compute_quality_score(metrics, rank_info=None):
    """Composite score, 0-100."""
    pnl_score = score_pnl_trajectory(metrics["pnl_7d"], metrics["pnl_24h"])

    rank_delta_24h = rank_info.get("delta_24h", 0) if rank_info else 0
    rank_delta_7d = rank_info.get("delta_7d", 0) if rank_info else 0
    rank_score = score_rank_velocity(rank_delta_24h, rank_delta_7d, metrics["pnl_24h"])

    outcomes_score = score_recent_outcomes(metrics["recent_trades"])
    ratio_score = score_win_loss_ratio(
        metrics["wins_count"], metrics["losses_count"],
        metrics["wins_sum"], metrics["losses_sum"]
    )
    fee_score = score_fee_efficiency(metrics["fee_drag_pct"])
    consistency_score = score_consistency(metrics.get("pnl_history_values", []))

    composite = (
        0.35 * pnl_score +
        0.25 * rank_score +
        0.15 * outcomes_score +
        0.10 * ratio_score +
        0.10 * fee_score +
        0.05 * consistency_score
    )

    return {
        "composite": round(composite, 1),
        "components": {
            "pnl_trajectory": pnl_score,
            "rank_velocity": rank_score,
            "recent_outcomes": outcomes_score,
            "win_loss_ratio": ratio_score,
            "fee_efficiency": fee_score,
            "consistency": consistency_score,
        },
    }


# ═══════════════════════════════════════════════════════════════
# Data fetching
# ═══════════════════════════════════════════════════════════════

def fetch_arena_leaderboard():
    """Get current Arena participants as pool candidates."""
    data = cfg.mcporter_call("arena_leaderboard", limit=500)
    if not data:
        return []
    lb = data.get("data", {}).get("leaderboard", data)
    if isinstance(lb, dict):
        entries = lb.get("entries", [])
    else:
        entries = []
    result = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        uid = e.get("senpiUserId")
        if not uid:
            continue
        result.append({
            "user_id": uid,
            "user_name": e.get("senpiUserName", ""),
            "x_handle": e.get("xHandle", ""),
            "rank": int(e.get("rank", 999)),
            "roe_pct": float(e.get("roePct", 0)),
            "total_pnl": float(e.get("totalPnl", 0)),
            "current_balance": float(e.get("currentBalance", 0)),
            "trade_count": int(e.get("tradeCount", 0)),
        })
    return result


def fetch_trader_strategies(user_id):
    """Find a user's ACTIVE strategy wallets."""
    data = cfg.mcporter_call("strategy_list", userIds=[user_id])
    if not data:
        return []
    strategies = data.get("data", {}).get("strategies", [])
    active = [
        s for s in strategies
        if s.get("status") == "ACTIVE" and s.get("strategyWalletAddress")
    ]
    return active


def fetch_trader_trades(wallet, limit=50):
    """Recent closed trades for a wallet."""
    data = cfg.mcporter_call(
        "discovery_get_trader_history",
        trader_address=wallet, limit=limit,
        sort_by="CLOSED_TIME", sort_direction="DESC"
    )
    if not data:
        return []
    return data.get("data", {}).get("closed_positions", [])


def fetch_trader_pnl_history(wallet):
    """PnL time-series for trajectory scoring."""
    data = cfg.mcporter_call(
        "strategy_get_pnl_and_account_value_history",
        strategy_wallet=wallet
    )
    if not data:
        return {}
    return data.get("data", {})


# ═══════════════════════════════════════════════════════════════
# Pool refresh orchestration
# ═══════════════════════════════════════════════════════════════

def needs_watchlist_refresh(maint_state):
    last = maint_state.get("last_watchlist_refresh_ts", 0)
    return cfg.now_ts() - last > WATCHLIST_REFRESH_INTERVAL


def needs_score_refresh(maint_state):
    last = maint_state.get("last_score_refresh_ts", 0)
    return cfg.now_ts() - last > SCORE_REFRESH_INTERVAL


def refresh_watchlist(pool, own_user_id=None):
    """Rebuild the Watchlist from Arena leaderboard + top points users.
    Returns list of candidates with metadata."""
    arena_entries = fetch_arena_leaderboard()

    # Optional: include top Senpi points users (broader universe)
    points_lb = cfg.mcporter_call("user_get_senpi_points_leaderboard", take=100)
    points_entries = []
    if points_lb:
        for e in points_lb.get("data", {}).get("leaderboard", []):
            uid = e.get("userId") or e.get("senpiUserId")
            if not uid:
                continue
            points_entries.append({
                "user_id": uid,
                "user_name": e.get("profile", {}).get("displayName", "") if isinstance(e.get("profile"), dict) else "",
                "rank": 0,  # separate pool, not arena rank
            })

    # Merge and dedupe by user_id
    candidates = {e["user_id"]: e for e in arena_entries}
    for e in points_entries:
        if e["user_id"] not in candidates:
            candidates[e["user_id"]] = e

    # Exclude self
    if own_user_id and own_user_id in candidates:
        del candidates[own_user_id]

    # Keep existing pool members in the candidate set
    for uid in list(pool.get("active_pool", {}).keys()) + list(pool.get("watchlist", {}).keys()):
        if uid not in candidates:
            candidates[uid] = {"user_id": uid, "user_name": "", "rank": 999}

    return list(candidates.values())[:MAX_WATCHLIST_SIZE]


def rank_velocity_from_history(pool, user_id, current_rank):
    """Compute rank deltas from historical snapshots."""
    pool_entry = (pool.get("active_pool", {}).get(user_id)
                  or pool.get("watchlist", {}).get(user_id, {}))
    rank_history = pool_entry.get("rank_history", [])
    now = cfg.now_ts()

    rank_24h_ago = None
    rank_7d_ago = None
    for snap in rank_history:
        age = now - snap.get("ts", 0)
        if rank_24h_ago is None and age >= 86400:
            rank_24h_ago = snap.get("rank")
        if rank_7d_ago is None and age >= 7 * 86400:
            rank_7d_ago = snap.get("rank")

    delta_24h = (rank_24h_ago - current_rank) if rank_24h_ago else 0
    delta_7d = (rank_7d_ago - current_rank) if rank_7d_ago else 0
    return {"delta_24h": delta_24h, "delta_7d": delta_7d}


def append_rank_snapshot(pool_entry, current_rank):
    """Store a dated rank snapshot, trimmed to 14 days."""
    history = pool_entry.get("rank_history", [])
    history.append({"ts": cfg.now_ts(), "rank": current_rank})
    cutoff = cfg.now_ts() - 14 * 86400
    history = [h for h in history if h.get("ts", 0) >= cutoff]
    pool_entry["rank_history"] = history


def refresh_pool_scores(pool, candidates, own_user_id=None):
    """For each candidate, fetch metrics and score them.
    Updates pool in-place with promotions/demotions."""
    now = cfg.now_ts()
    new_active = {}
    new_watchlist = {}
    demoted_cooldowns = pool.get("demoted_cooldowns", {})

    for cand in candidates:
        user_id = cand["user_id"]
        if user_id == own_user_id:
            continue

        # Skip if in demotion cooldown
        cooldown_until = demoted_cooldowns.get(user_id, 0)
        if cooldown_until > now:
            continue

        # Find their active strategy
        strategies = fetch_trader_strategies(user_id)
        if not strategies:
            continue
        # Pick the largest (by equity) if multiple
        strategies.sort(key=lambda s: float(s.get("initialBudget", 0)), reverse=True)
        strat = strategies[0]
        wallet = strat.get("strategyWalletAddress")
        if not wallet:
            continue

        # Check strategy age
        created_at_str = strat.get("createdAt", "")
        if created_at_str:
            try:
                from datetime import datetime, timezone
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
                if age_days < MIN_ACCOUNT_AGE_DAYS:
                    continue
            except Exception:
                pass

        # Fetch trades + pnl history
        trades = fetch_trader_trades(wallet, limit=50)
        pnl_hist = fetch_trader_pnl_history(wallet)

        # Compute metrics
        metrics = compute_trader_metrics(user_id, wallet, trades, pnl_hist)
        passed, reason = passes_hard_filters(metrics)
        if not passed:
            continue

        # Get existing pool entry (preserves rank_history)
        existing = (pool.get("active_pool", {}).get(user_id)
                    or pool.get("watchlist", {}).get(user_id)
                    or {})

        # Update rank history
        current_rank = cand.get("rank", 999)
        append_rank_snapshot(existing, current_rank)
        rank_info = rank_velocity_from_history(pool, user_id, current_rank)

        # Compute composite quality score
        score_result = compute_quality_score(metrics, rank_info)

        pool_entry = {
            "user_id": user_id,
            "user_name": cand.get("user_name", existing.get("user_name", "")),
            "x_handle": cand.get("x_handle", existing.get("x_handle", "")),
            "wallet": wallet,
            "strategy_id": strat.get("id"),
            "quality_score": score_result["composite"],
            "score_components": score_result["components"],
            "pnl_24h": metrics["pnl_24h"],
            "pnl_7d": metrics["pnl_7d"],
            "fee_drag_pct": metrics["fee_drag_pct"],
            "avg_winner_hold_hours": metrics["avg_winner_hold_hours"],
            "drawdown_24h_pct": metrics["drawdown_24h_pct"],
            "current_rank": current_rank,
            "rank_velocity_24h": rank_info["delta_24h"],
            "rank_history": existing.get("rank_history", []),
            "last_scored_ts": now,
        }

        # Determine tier placement
        was_active = user_id in pool.get("active_pool", {})
        score = score_result["composite"]

        if was_active:
            # Active Pool member — retain unless score drops or drawdown fires
            if (score >= RETAIN_SCORE_THRESHOLD and
                metrics["drawdown_24h_pct"] < DRAWDOWN_HARD_DEMOTE_PCT):
                pool_entry["promoted_at"] = existing.get("promoted_at", now)
                new_active[user_id] = pool_entry
            else:
                # DEMOTE
                demoted_cooldowns[user_id] = now + DEMOTION_COOLDOWN_HOURS * 3600
                pool_entry["demoted_at"] = now
                pool_entry["demote_reason"] = (
                    f"score_{score:.0f}" if score < RETAIN_SCORE_THRESHOLD
                    else f"drawdown_{metrics['drawdown_24h_pct']:.0f}%"
                )
                new_watchlist[user_id] = pool_entry
        else:
            # Watchlist or new — check for promotion
            # For promotion: require score over threshold AND was-watchlisted > 6h
            first_seen_ts = existing.get("first_seen_ts", now)
            hours_watched = (now - first_seen_ts) / 3600

            if score >= PROMOTE_SCORE_THRESHOLD and hours_watched >= 6:
                pool_entry["promoted_at"] = now
                pool_entry["first_seen_ts"] = first_seen_ts
                new_active[user_id] = pool_entry
            else:
                pool_entry["first_seen_ts"] = first_seen_ts
                new_watchlist[user_id] = pool_entry

    # Cap active pool by score
    if len(new_active) > MAX_ACTIVE_POOL_SIZE:
        sorted_actives = sorted(
            new_active.items(),
            key=lambda x: -x[1]["quality_score"]
        )
        top = dict(sorted_actives[:MAX_ACTIVE_POOL_SIZE])
        # Overflow → watchlist
        for uid, entry in sorted_actives[MAX_ACTIVE_POOL_SIZE:]:
            new_watchlist[uid] = entry
        new_active = top

    # Cap watchlist
    if len(new_watchlist) > MAX_WATCHLIST_SIZE:
        sorted_watches = sorted(
            new_watchlist.items(),
            key=lambda x: -x[1]["quality_score"]
        )
        new_watchlist = dict(sorted_watches[:MAX_WATCHLIST_SIZE])

    # Clean up expired demotion cooldowns
    demoted_cooldowns = {
        uid: ts for uid, ts in demoted_cooldowns.items() if ts > now
    }

    pool["active_pool"] = new_active
    pool["watchlist"] = new_watchlist
    pool["demoted_cooldowns"] = demoted_cooldowns
    return pool
