#!/usr/bin/env python3
# Senpi SPIDER Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SPIDER v4.0.0 Producer — Single-leg patient anchor sniper.

PLUMBING-ONLY MIGRATION from v3.0.2. NO thesis change. NO scoring change.
NO threshold change. Producer ports onto senpi_runtime_helpers (in-process
SenpiClient + direct HTTP POST to runtime /signals + producer_daemon
long-lived loop replacing the openclaw cron entry).

v3.0.2 (2026-05-04 — calibration relax):
  Diagnostic of first 3 hourly scans showed arena=0.0 in every scan
  because top-10 arena traders' positions (mostly BTC/ETH/HYPE) rarely
  overlap with Spider's top-15 SM-leaderboard universe (mostly alts).
  Without arena, max achievable score is SM(3.0) + Funding(1.5) +
  RelStr(1.5) = 6.0, making MIN_SCORE 7.0 mathematically unreachable
  most of the time. Same calibration trap Kestrel v1.x had.

  v3.0.2 lowers MIN_SCORE 7.0 -> 5.5. Strong signals (full SM +
  favorable funding + mild relstr) can now trade WITHOUT arena overlap.
  Arena scoring still contributes when it does fire — making bigger
  signals score 7+. Keeps the "patience is the edge" thesis intact.



v3.0.1 (hot-patch, 2026-05-04):
  arena_leaderboard returns senpiUserId (M-prefix), NOT wallet addresses.
  v3.0.0 passed M-IDs to leaderboard_get_trader_positions which rejected
  them silently (TRADER_NOT_FOUND), making arenaScore=0 for every asset
  and capping any candidate score at ~6. With MIN_SCORE 7.0, Spider
  could never trade in v3.0.0. v3.0.1 resolves senpiUserIds to strategy
  wallets via strategy_list({userIds:[...], status:["ACTIVE"]}) before
  querying positions. Per-user asset deduping prevents over-counting
  when a user has multiple ACTIVE strategies.



v2.0 was an elaborate 2-leg portfolio operator (anchor + basket) with
custom scanner types (`composite_score`, `portfolio_snapshot`,
`predators_position_aggregator`) and custom action type
(`LLM_PORTFOLIO_DECISION`) that THE RUNTIME DOES NOT IMPLEMENT.
The operator agent was simulating the runtime in cron-fired text
prompts, never actually executing trades. Spider v2.0 contributed
$0 to the fleet.

v3.0 redesign trade-off:
  PRESERVE — the patience thesis (long horizon, low turnover, fee-aware).
  PRESERVE — the anchor-selection signal logic (arena leaders + SM
             consensus + funding favorability + relative strength).
  PRESERVE — 7-day minimum hold per anchor.
  PRESERVE — fleet-concentration overlay (cap leverage when peers
             crowded).
  DROP    — basket leg (no native portfolio runtime support; revisit
             when `dsl_portfolio` ships).
  DROP    — risk-parity multi-leg sizing (single leg now).
  DROP    — funding-harvest from coordinated shorts.
  DROP    — 7-day paper-trade warmup (gate-paused indefinitely).
  DROP    — pilot protocol (50/75/100% staged entry).

What v3.0 ACTUALLY DOES every cron tick:
  - Reads top-15 SM leaderboard markets via MCP
  - Scores each on 4-factor composite (arena, SM, funding, relstr)
  - Skips any held assets + post-close cooldowns + non-LONG candidates
  - Applies fleet-concentration leverage cap if applicable
  - Emits TOP candidate scoring >= 7.0 via external-scanner ingest
  - Runtime LLM gate evaluates regime (BTC drawdown, vol expansion,
    funding regime flipping) and either honors or rejects

Cadence: hourly cron OK; agent's own logic skips when position open.
The 7-day min-hold is enforced producer-side (skip emission) AND
DSL-side (Phase 1 max_loss is the only early exit).

Environment / config resolution:
  Strategy wallet is read from config/spider-config.json (canonical).

  SPIDER_WALLET              — optional override; defaults to config.wallet
  OPENCLAW_BIN               — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME      — optional override (default "spider_signals")
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spider_config as cfg

# senpi_runtime_helpers ships inside the senpi-trading-runtime skill.
# Global skills install under ~/.openclaw/skills/ on standard hosts
# (e.g. /data/.openclaw/skills/ on Railway). Some setups install user
# skills under ${OPENCLAW_WORKSPACE}/skills/. Probe both in order.
_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import producer_daemon  # type: ignore  # noqa: E402


VERSION = "4.0.1"
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "spider_signals")
SIGNAL_TYPE = "SPIDER_ANCHOR"


def _resolve_wallet():
    env_wallet = (os.environ.get("SPIDER_WALLET") or "").strip()
    if env_wallet:
        return env_wallet
    legacy = (os.environ.get("STRATEGY_ADDRESS") or "").strip()
    if legacy:
        sys.stderr.write(
            "[spider v4.0.0] DEPRECATION: STRATEGY_ADDRESS env var is BANNED "
            "by v2.0.9 contamination rule. Set SPIDER_WALLET instead. "
            "Honoring legacy value for this run only.\n"
        )
        return legacy
    config = cfg.load_config()
    return (config.get("wallet") or "").strip()


SPIDER_WALLET = _resolve_wallet()
STRATEGY_ADDRESS = SPIDER_WALLET  # alias used by signal payload


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIR
# ═══════════════════════════════════════════════════════════════

def _wallet_state_dir():
    if SPIDER_WALLET:
        h = hashlib.sha256(SPIDER_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_LAST_CLOSED_FILE = _STATE_DIR / "last-closed.json"
_PREV_HELD_FILE = _STATE_DIR / "previously-held.json"
_ANCHOR_HISTORY_FILE = _STATE_DIR / "anchor-history.json"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_wallet_lock_id = (hashlib.sha256(SPIDER_WALLET.lower().encode()).hexdigest()[:8]
                   if SPIDER_WALLET else "unset")


# ═══════════════════════════════════════════════════════════════
# CONSTANTS (fleet-tuned, not operator-set)
# ═══════════════════════════════════════════════════════════════

MIN_SCORE_DEFAULT = 5.5               # v3.0.2 anchor floor (was 7.0)
                                      # Diagnostic of first 3 hourly scans showed
                                      # arena=0.0 in every scan because top-10
                                      # arena traders' positions (mostly BTC/ETH/
                                      # HYPE) rarely overlap with Spider's top-15
                                      # SM-leaderboard universe (mostly alts).
                                      # Without arena, max achievable score is
                                      # SM(3.0) + Funding(1.5) + RelStr(1.5) = 6.0
                                      # — making 7.0 mathematically unreachable
                                      # most of the time. Same calibration trap
                                      # as Kestrel v1.x. 5.5 floor lets a strong
                                      # signal (full SM + favorable funding +
                                      # mild relstr) trade WITHOUT arena overlap.
ANCHOR_UNIVERSE_SIZE = 15
MIN_HOLD_DAYS = 7                     # 7-day min anchor hold
POST_CLOSE_COOLDOWN_MINUTES = 7 * 24 * 60   # 7d — match min-hold
ASSET_COOLDOWN_MINUTES = 10080        # 7d post-emit
MAX_POSITIONS = 1                     # single-leg
ANCHOR_MAX_LEVERAGE = 3
MARGIN_PCT = 1.00                     # 100% on single anchor
XYZ_BANNED = True

# Score component weights (preserved from v2.0)
W_ARENA = 0.40        # arena leaders aligned long
W_SM = 0.30           # SM consensus strength
W_FUNDING = 0.15      # funding favorability for longs (low/negative = good)
W_RELSTR = 0.15       # 30d relative strength

# Score component thresholds
SM_MIN_PCT = 8.0      # less strict than Cheetah's 10% — Spider scores by quality
SM_MIN_TRADERS = 30
ARENA_MIN_ACTIVE_TRADERS = 150


# ═══════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_anchor_leverage_for_score(score):
    """Score-scaled anchor leverage. Capped at 3x per Spider risk envelope."""
    if score >= 9.0:
        return 3
    elif score >= 8.0:
        return 2
    else:
        return 1   # score 7.0-7.9 — minimum conviction, minimum size


def get_safe_leverage(wallet, asset, requested_leverage):
    """Clamp to Hyperliquid asset max — Cheetah's leverage safety pattern."""
    try:
        limits = cfg.mcporter_call(
            "strategy_get_asset_trading_limits",
            strategy_wallet=wallet,
            coin=asset,
        )
        if limits:
            data = limits.get("data", limits)
            if isinstance(data, dict):
                lev = data.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", 20)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


# ═══════════════════════════════════════════════════════════════
# STATE I/O
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
    cooldowns = load_cooldowns()
    if token not in cooldowns:
        return False
    last_emit = cooldowns[token].get("emittedTimestamp", 0)
    elapsed_min = (time.time() - last_emit) / 60
    return elapsed_min < cooldown_minutes


def mark_asset_emitted(token):
    cooldowns = load_cooldowns()
    cooldowns[token] = {
        "emittedTimestamp": time.time(),
        "setAt": cfg.now_iso(),
    }
    save_cooldowns(cooldowns)


def load_last_closed():
    try:
        with open(_LAST_CLOSED_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_last_closed(d):
    cfg.atomic_write(str(_LAST_CLOSED_FILE), d)


def load_previously_held():
    try:
        with open(_PREV_HELD_FILE) as f:
            data = json.load(f)
        return set(data.get("assets", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_previously_held(held):
    cfg.atomic_write(str(_PREV_HELD_FILE), {
        "assets": sorted(list(held)),
        "updatedAt": cfg.now_iso(),
    })


def detect_closes(current_held, previously_held):
    closed = set(previously_held) - set(current_held)
    if not closed:
        return set()
    last = load_last_closed()
    now_ts = time.time()
    now_iso = cfg.now_iso()
    for asset in closed:
        last[asset] = {"closedTimestamp": now_ts, "closedAt": now_iso}
    save_last_closed(last)
    return closed


def is_in_post_close_cooldown(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    if token not in last:
        return False
    last_close = last[token].get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    return elapsed_min < cooldown_minutes


def post_close_cooldown_remaining_min(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    if token not in last:
        return 0
    last_close = last[token].get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    remaining = cooldown_minutes - elapsed_min
    return round(max(0.0, remaining), 1)


# ═══════════════════════════════════════════════════════════════
# ACCOUNT + HELD ASSETS + HOLD-TIME DETECTION
# ═══════════════════════════════════════════════════════════════

def get_account_state():
    """Returns (account_value, position_count, held_assets_set, oldest_position_age_seconds).
    oldest_position_age_seconds is None if no positions; otherwise seconds since the oldest open
    position was opened (used for 7-day min-hold gate)."""
    if not SPIDER_WALLET:
        return None, None, set(), None
    ch = cfg.mcporter_call(
        "strategy_get_clearinghouse_state",
        strategy_wallet=SPIDER_WALLET,
    )
    if not ch:
        return None, None, set(), None
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
    held = set()
    oldest_open_ts = None
    now_ms = time.time() * 1000
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        total_value += safe_float(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if safe_float(pos.get("szi", 0)) != 0:
                pos_count += 1
                coin = pos.get("coin")
                if coin:
                    held.add(str(coin).upper())
                # openedAt may be in pos["openedAt"], or we approximate from
                # cumFunding "sinceOpen" * funding cycle, OR just track in
                # state. For MVP we read pos["openedAt"] when available.
                opened_at = pos.get("openedAt") or pos.get("entryTime") or 0
                ts = safe_float(opened_at)
                if ts > 0:
                    if oldest_open_ts is None or ts < oldest_open_ts:
                        oldest_open_ts = ts
    if oldest_open_ts is None or oldest_open_ts == 0:
        return total_value, pos_count, held, None
    age_sec = (now_ms - oldest_open_ts) / 1000.0
    return total_value, pos_count, held, age_sec


def load_anchor_history():
    """Anchor entries for hold-time tracking (entry timestamp persistence —
    fallback if clearinghouse doesn't expose openedAt cleanly)."""
    try:
        with open(_ANCHOR_HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"anchors": []}


def save_anchor_history(history):
    if len(history.get("anchors", [])) > 50:
        history["anchors"] = history["anchors"][-50:]
    cfg.atomic_write(str(_ANCHOR_HISTORY_FILE), history)


def record_anchor_open(asset, score):
    history = load_anchor_history()
    history.setdefault("anchors", []).append({
        "asset": asset,
        "score": score,
        "openedTs": time.time(),
        "openedAt": cfg.now_iso(),
    })
    save_anchor_history(history)


def get_oldest_anchor_age_seconds(held_assets):
    """Fallback hold-time computation — find oldest anchor in our history
    whose asset is still in held_assets."""
    if not held_assets:
        return None
    history = load_anchor_history()
    oldest_ts = None
    held_upper = {a.upper() for a in held_assets}
    for entry in history.get("anchors", []):
        if entry.get("asset", "").upper() in held_upper:
            ts = safe_float(entry.get("openedTs", 0))
            if ts > 0:
                if oldest_ts is None or ts < oldest_ts:
                    oldest_ts = ts
    if oldest_ts is None:
        return None
    return time.time() - oldest_ts


# ═══════════════════════════════════════════════════════════════
# MARKET FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_sm_markets():
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=ANCHOR_UNIVERSE_SIZE)
    if not raw:
        return []
    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw

    normalized = []
    for i, m in enumerate(markets):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if not token:
            continue
        # Spider is LONG-ONLY on anchor — skip SHORT signals
        direction = str(m.get("direction", "")).upper()
        if direction != "LONG":
            continue
        normalized.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": direction,
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_24h": safe_float(m.get("token_price_change_pct_24h",
                                               m.get("price_change_24h", 0))),
            "price_chg_7d": safe_float(m.get("token_price_change_pct_7d",
                                              m.get("price_change_7d", 0))),
            "price_chg_30d": safe_float(m.get("token_price_change_pct_30d",
                                               m.get("price_change_30d", 0))),
            "funding_rate": safe_float(m.get("funding_rate",
                                              m.get("fundingRate", 0))),
        })
    return normalized


def fetch_arena_long_exposure():
    """Returns asset -> count of arena top-10 traders long that asset.

    v3.0.1 fix: arena_leaderboard returns senpiUserId (M-prefix) NOT
    wallet addresses. We must resolve userId -> strategyWalletAddress
    via strategy_list before querying positions. The original v3.0.0
    code passed M-IDs to leaderboard_get_trader_positions which
    rejected them with TRADER_NOT_FOUND, silently returning arenaScore
    0 for every asset and capping any candidate score at ~6 (below
    MIN_SCORE 7.0). Spider could never trade in v3.0.0.
    """
    # Step 1: pull arena top-10 senpiUserIds
    try:
        raw = cfg.mcporter_call("arena_leaderboard", limit=10)
    except Exception:
        return {}
    if not raw:
        return {}
    traders = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            traders = data.get("traders", data.get("leaderboard", data.get("entries", [])))
        elif isinstance(data, list):
            traders = data
    elif isinstance(raw, list):
        traders = raw

    user_ids = []
    for t in traders:
        if not isinstance(t, dict):
            continue
        uid = str(t.get("senpiUserId") or t.get("userId") or t.get("user_id") or "").strip()
        if uid:
            user_ids.append(uid)

    if not user_ids:
        return {}

    # Step 2: resolve senpiUserIds -> strategy wallets via strategy_list.
    # Each user can have multiple strategies; we take all ACTIVE strategies
    # and aggregate positions. Per-asset counts are deduplicated per user
    # (one user can't double-count for the same asset across multiple of
    # their strategies).
    try:
        strategies_raw = cfg.mcporter_call(
            "strategy_list",
            userIds=user_ids,
            status=["ACTIVE"],
        )
    except Exception:
        return {}
    if not strategies_raw:
        return {}

    strategies = []
    if isinstance(strategies_raw, dict):
        sd = strategies_raw.get("data", strategies_raw)
        if isinstance(sd, dict):
            strategies = sd.get("strategies", sd.get("results", []))
            if not isinstance(strategies, list):
                strategies = []
        elif isinstance(sd, list):
            strategies = sd
    elif isinstance(strategies_raw, list):
        strategies = strategies_raw

    # Build user_id -> [wallet, ...] map
    user_to_wallets = {}
    for s in strategies:
        if not isinstance(s, dict):
            continue
        uid = str(s.get("userId") or s.get("senpiUserId") or s.get("user_id") or "").strip()
        wallet = str(
            s.get("strategyWalletAddress")
            or s.get("walletAddress")
            or s.get("strategy_wallet_address")
            or ""
        ).lower().strip()
        if uid and wallet and wallet.startswith("0x"):
            user_to_wallets.setdefault(uid, []).append(wallet)

    if not user_to_wallets:
        return {}

    # Step 3: for each user, fetch positions across their strategies.
    # Track per-user asset-direction sets to dedupe (one count per user).
    long_count = {}
    shape_warned = False
    for uid in user_ids:
        wallets = user_to_wallets.get(uid, [])
        if not wallets:
            continue
        user_long_assets = set()
        for wallet in wallets:
            positions_data = cfg.mcporter_call(
                "leaderboard_get_trader_positions", trader_id=wallet,
            )
            if not positions_data:
                continue
            # v4.0.1: leaderboard_get_trader_positions actually returns
            #   { data: { positions: { trader_id, rank, positions: [...] } } }
            # — the per-trader positions array is nested one level deeper
            # than the schema guide documents. Pre-v4.0.1 parser expected
            # a list at `data.positions`, got a dict, silently skipped every
            # trader → arena_long_exposure empty → W_ARENA component (4pts)
            # never fired → Spider candidates capped below MIN_SCORE.
            # Same bug pattern caught and fixed in Cheetah v7.1.1.
            positions = []
            if isinstance(positions_data, dict):
                d = positions_data.get("data", positions_data)
                if isinstance(d, dict):
                    raw_positions = d.get("positions", d.get("top_positions", []))
                    if isinstance(raw_positions, list):
                        positions = raw_positions
                    elif isinstance(raw_positions, dict):
                        nested = raw_positions.get("positions", [])
                        if isinstance(nested, list):
                            positions = nested
                        elif not shape_warned:
                            cfg.log(
                                f"POSITIONS_SHAPE_WARN nested non-list for {wallet[:10]}: "
                                f"keys={list(raw_positions.keys())[:8]}"
                            )
                            shape_warned = True
                    elif not shape_warned:
                        cfg.log(
                            f"POSITIONS_SHAPE_WARN unexpected type "
                            f"{type(raw_positions).__name__} for {wallet[:10]}"
                        )
                        shape_warned = True
                elif isinstance(d, list):
                    positions = d
            if not isinstance(positions, list):
                continue
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                asset = str(
                    pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
                ).upper()
                if not asset:
                    continue
                szi = safe_float(pos.get("szi", 0))
                direction = str(pos.get("direction", pos.get("side", ""))).upper()
                is_long = (direction == "LONG") if direction in ("LONG", "SHORT") else (szi > 0)
                if is_long:
                    user_long_assets.add(asset)
        # Increment the count once per user-asset pair.
        for asset in user_long_assets:
            long_count[asset] = long_count.get(asset, 0) + 1

    return long_count


# ═══════════════════════════════════════════════════════════════
# SCORING (v3.0 — 4-factor composite, max 10)
# ═══════════════════════════════════════════════════════════════

def score_anchor(market, arena_long_exposure):
    """Score a single LONG candidate on Spider's 4-factor composite.
    Returns (score 0-10, reasons list, score_breakdown dict)."""

    reasons = []
    breakdown = {}

    sm_pct = market["pct"]
    sm_traders = market["traders"]
    if sm_pct < SM_MIN_PCT or sm_traders < SM_MIN_TRADERS:
        return 0, [], {}

    # 1. Arena leaders aligned long (max 4.0 pts at 0.40 weight x 10)
    asset = market["token"]
    arena_count = arena_long_exposure.get(asset, 0)
    arena_score_raw = min(10.0, arena_count * 2.5)   # 1 leader = 2.5, 4+ = max
    arena_pts = arena_score_raw * W_ARENA
    breakdown["arena"] = round(arena_pts, 2)
    if arena_count >= 1:
        reasons.append(f"ARENA_ALIGN +{arena_count} top-10 traders LONG")

    # 2. SM consensus strength (max 3.0 pts at 0.30 weight x 10)
    sm_score_raw = min(10.0,
                       (sm_pct / 20.0) * 5.0 +
                       (min(sm_traders, 200) / 200.0) * 5.0)
    sm_pts = sm_score_raw * W_SM
    breakdown["sm"] = round(sm_pts, 2)
    reasons.append(f"SM {sm_pct:.1f}%/{sm_traders}t")

    # 3. Funding favorability (max 1.5 pts at 0.15 weight x 10)
    # Negative funding means shorts paying longs — LONG anchor harvests carry.
    funding = market["funding_rate"]
    if funding <= -0.0002:        # very favorable (~-0.06%/8h, -22%/yr)
        funding_score_raw = 10.0
        reasons.append(f"FUNDING_DEEP {funding*100:+.4f}%")
    elif funding <= 0.0001:       # favorable (low cost to hold)
        funding_score_raw = 7.0
        reasons.append(f"FUNDING_OK {funding*100:+.4f}%")
    elif funding <= 0.0003:       # neutral
        funding_score_raw = 4.0
    else:                          # unfavorable for longs
        funding_score_raw = 0.0
    funding_pts = funding_score_raw * W_FUNDING
    breakdown["funding"] = round(funding_pts, 2)

    # 4. Relative strength 30d (max 1.5 pts at 0.15 weight x 10)
    p30 = market["price_chg_30d"]
    if p30 >= 30:        relstr_raw = 10.0
    elif p30 >= 15:      relstr_raw = 7.0
    elif p30 >= 5:       relstr_raw = 4.0
    elif p30 >= 0:       relstr_raw = 2.0
    else:                relstr_raw = 0.0
    relstr_pts = relstr_raw * W_RELSTR
    breakdown["relstr"] = round(relstr_pts, 2)
    if p30 >= 5:
        reasons.append(f"RELSTR +{p30:.1f}%/30d")
    elif p30 < 0:
        reasons.append(f"RELSTR_WEAK {p30:.1f}%/30d")

    total = round(arena_pts + sm_pts + funding_pts + relstr_pts, 2)
    return total, reasons, breakdown


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_data(candidate, leverage, margin_usd, held_assets, breakdown):
    """v4.0.0: helpers `push_signal()` takes asset/direction/signal_type/score
    as top-level kwargs. Everything else goes in `data`."""
    held_list = sorted(list(held_assets)) if held_assets else []
    return {
        "score": candidate["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "reasons": " | ".join(candidate["reasons"]),
        "smPct": float(candidate["pct"]),
        "smTraders": int(candidate["traders"]),
        "rank": int(candidate["rank"]),
        "arenaScore": breakdown.get("arena", 0),
        "smScore": breakdown.get("sm", 0),
        "fundingScore": breakdown.get("funding", 0),
        "relstrScore": breakdown.get("relstr", 0),
        "fundingRate": float(candidate["funding_rate"]),
        "priceChg30d": float(candidate["price_chg_30d"]),
        "priceChg7d": float(candidate["price_chg_7d"]),
        "priceChg24h": float(candidate["price_chg_24h"]),
        "heldAssets": held_list,
        "_spider_producer_version": VERSION,
    }


def push_signal(candidate, leverage, margin_usd, held_assets, breakdown):
    """v4.0.0: direct POST to runtime /signals via helpers SenpiClient."""
    if not STRATEGY_ADDRESS:
        cfg.log("SPIDER_WALLET not set; cannot push signal")
        return False
    data_block = build_signal_data(candidate, leverage, margin_usd, held_assets, breakdown)
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=candidate["token"],
            direction="LONG",
            signal_type=SIGNAL_TYPE,
            score=float(candidate["score"]),
            data=data_block,
        )
        return True
    except Exception as e:  # noqa: BLE001
        cfg.log(f"push_signal failed for {candidate['token']}: "
                f"{type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    if not SPIDER_WALLET:
        cfg.output({
            "status": "error",
            "error": "SPIDER_WALLET not set. Configure config/spider-config.json wallet field.",
            "_spider_producer_version": VERSION,
        })
        return

    # ── Account + held assets + oldest open position age ──
    account_value, pos_count, held_assets, oldest_age_sec = get_account_state()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_spider_producer_version": VERSION,
        })
        return

    # ── Detect closes vs last tick ──
    previously_held = load_previously_held()
    closed_this_tick = detect_closes(held_assets, previously_held)
    save_previously_held(held_assets)

    # ── Fallback hold-time check via anchor-history.json ──
    fallback_age_sec = get_oldest_anchor_age_seconds(held_assets)
    effective_age_sec = oldest_age_sec or fallback_age_sec

    # ── Single-leg max positions guard + 7-day min-hold gate ──
    if pos_count >= MAX_POSITIONS:
        held_days = (effective_age_sec / 86400.0) if effective_age_sec else None
        if held_days is not None and held_days < MIN_HOLD_DAYS:
            cfg.output({
                "status": "ok",
                "note": f"riding anchor day {held_days:.1f}/{MIN_HOLD_DAYS} min hold",
                "held_assets": sorted(list(held_assets)),
                "_spider_producer_version": VERSION,
            })
            return
        # Past min-hold — agent could rotate. v3.0 MVP: still don't emit
        # while position open. DSL Phase 1 / Phase 2 owns exits.
        cfg.output({
            "status": "ok",
            "note": (f"riding anchor (past min hold {held_days:.1f}d)"
                     if held_days is not None
                     else "riding anchor (hold-time unknown, conservative skip)"),
            "held_assets": sorted(list(held_assets)),
            "_spider_producer_version": VERSION,
        })
        return

    # ── Fetch top-15 SM markets (LONG-only, XYZ banned) ──
    markets = fetch_sm_markets()
    if not markets:
        cfg.output({
            "status": "ok",
            "note": "no LONG markets returned by leaderboard_get_markets",
            "_spider_producer_version": VERSION,
        })
        return

    # ── Fetch arena top-10 long exposure ──
    arena_long_exposure = fetch_arena_long_exposure()

    # ── Score candidates ──
    candidates = []
    all_scored = []
    skipped_held = 0
    skipped_post_close = 0
    post_close_skips = []

    for market in markets:
        token = market["token"]

        if token.upper() in held_assets:
            skipped_held += 1
            continue

        if is_in_post_close_cooldown(token):
            skipped_post_close += 1
            post_close_skips.append({
                "token": token,
                "remaining_min": post_close_cooldown_remaining_min(token),
            })
            continue

        if is_asset_cooled_down(token):
            continue

        score, reasons, breakdown = score_anchor(market, arena_long_exposure)
        if score == 0:
            continue

        all_scored.append({
            "token": token,
            "score": round(score, 2),
            "breakdown": breakdown,
        })

        if score >= MIN_SCORE_DEFAULT:
            candidate = dict(market)
            candidate["score"] = score
            candidate["reasons"] = reasons
            candidate["breakdown"] = breakdown
            candidates.append(candidate)

    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        cfg.output({
            "status": "ok",
            "note": f"0 anchor candidates at score >= {MIN_SCORE_DEFAULT} ({len(all_scored)} scored)",
            "topScored": top3,
            "skipped_held": skipped_held,
            "skipped_post_close": skipped_post_close,
            "post_close_skips": post_close_skips,
            "closed_this_tick": sorted(list(closed_this_tick)),
            "held_assets": sorted(list(held_assets)),
            "_spider_producer_version": VERSION,
        })
        return

    # ── Pick highest-scoring anchor candidate ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_usd = round(account_value * MARGIN_PCT, 2)
    requested_leverage = get_anchor_leverage_for_score(best["score"])
    leverage = get_safe_leverage(SPIDER_WALLET, best["token"], requested_leverage)

    # ── Emit signal ──
    pushed = 0
    if push_signal(best, leverage, margin_usd, held_assets, best["breakdown"]):
        pushed += 1
        mark_asset_emitted(best["token"])
        record_anchor_open(best["token"], best["score"])

    elapsed = time.time() - run_start
    warn = "WARN_OVER_120S" if elapsed > 120 else None
    cfg.output({
        "status": "ok",
        "candidates_total": len(candidates),
        "all_scored": len(all_scored),
        "skipped_held": skipped_held,
        "skipped_post_close": skipped_post_close,
        "post_close_skips": post_close_skips,
        "closed_this_tick": sorted(list(closed_this_tick)),
        "held_assets": sorted(list(held_assets)),
        "signals_pushed": pushed,
        "best_signal": {
            "asset": best["token"],
            "direction": "LONG",
            "score": round(best["score"], 2),
            "leverage": leverage,
            "marginUsd": margin_usd,
            "breakdown": best["breakdown"],
            "reasons": best["reasons"][:6],
        } if pushed else None,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_spider_producer_version": VERSION,
    })


if __name__ == "__main__":
    producer_daemon(
        fn=main,
        interval_seconds=3600,
        name=f"spider-producer-{_wallet_lock_id}",
        wallet=SPIDER_WALLET,
        scanner=SCANNER_NAME,
        tick_timeout=240,
    )
