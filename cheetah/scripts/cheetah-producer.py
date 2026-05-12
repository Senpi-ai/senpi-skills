#!/usr/bin/env python3
# Senpi CHEETAH Producer v7.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CHEETAH v7.0.0 Producer — senpi_runtime_helpers migration.

v7.0.0 (2026-05-08) — plumbing migration from openclaw-CLI subprocess
+ mcporter subprocess to senpi_runtime_helpers in-process wrapper.
NO thesis change. Scoring tables, scoring components, leverage tiers,
margin %, asset cooldowns, post-close cooldown, dedup logic are all
preserved verbatim from v6.1.0. The runtime itself is now on
senpi-trading-runtime >= 1.1.0 which speaks
the new {success,data,error} envelope on /signals + /audit and
exposes GET /state for daemon liveness probes.

Six-layer plumbing flip (per migration-cookbook):
  1. MCP calls: cfg.mcporter_call(...) now delegates to
     senpi_runtime_helpers.SenpiClient.mcp_call() — direct HTTPS,
     kills the 6-process tree of mcporter cold-start. Existing call
     sites continue to call cfg.mcporter_call (backward-compat shim
     in cheetah_config.py).
  2. Signal emit: subprocess.run(["openclaw","senpi","external-scanner",
     "ingest", ...]) replaced by cfg._wrapper_client.push_signal(...).
     Direct HTTP POST to runtime API on 127.0.0.1:8787/signals.
     CRITICAL: asset and direction are top-level routing fields;
     score stays inside data (Cheetah's score is unbounded int 0-16,
     not 0..1 confidence — same Pangolin reference rationale).
  3. Reentrancy: hand-rolled fcntl flock dropped. producer_daemon
     owns the lock per tick via scanner_lock(name) — adds stale-PID
     auto-recovery via os.kill(pid, 0). Do NOT add an inner
     scanner_lock inside main() — the daemon already wraps it.
  4. Tick scheduling: openclaw cron + agentTurn replaced by
     producer_daemon(fn=main, interval_seconds=300, ...). Long-lived
     Python process, no per-tick LLM cost.
  5. Per-tick cache + parallel fan-out NOT adopted in v7.0.0 to match
     Pangolin reference minimum-change pattern. Future opt-in.
  6. /state alive_check: producer_daemon self-terminates if the
     runtime for CHEETAH_WALLET is deleted OR the cheetah_signals
     scanner is renamed. Default behavior — alive_check left unset.

CHEETAH v6.1 thesis preserved unchanged:

v6.1 (2026-05-05) — Phase 2 T0 ladder calibration. No producer code
changes. Runtime.yaml only: T0 trigger 5→3, T0 lock 35→40, T1 7/55,
T2 15/70, T3 30/80, T4 50/90. First post-v6.0 trade (ZEC LONG score
10, SM 22.85%/210t) peaked at +3.05% margin ROE — old T0 at 5%
never armed; closed via Phase 1 retrace at -4.09% / -$9.68. Same
Pangolin v2.2 fix. T0 at 3 captures the typical Cheetah small
winner at 3x leverage.

v6.0 — full v1 → v2 architecture migration:
v5.x was a v1 full-agency scanner: scored signals, tracked counters,
called create_position directly, maintained Python-side cooldowns and
resting-order guards. v6.0 flips to producer + v2 runtime (Polar v4.0
/ Pangolin v2.1 / Vulture v3.0 pattern).

ARCHITECTURE CHANGE:
  - Producer (cheetah-producer.py) emits signals via
    `SenpiClient.push_signal()` (direct HTTP POST). NO execution code.
  - Runtime LLM gate (decision_mode: llm) is pass-through (Roach
    pattern) — producer has applied every filter; LLM only catches
    malformed signals.
  - risk.guard_rails ENFORCES daily caps, drawdown halt, consecutive-
    loss halt, per-asset cooldown (no Python state to drift / crash).
  - DSL uses FEE_OPTIMIZED_LIMIT on entries AND exits — saves
    ~0.020-0.030% per maker-filled close vs v5.x MARKET orders.
  - Trade chain DB emits LIFECYCLE / DECISION_EXECUTED / ACTION_RESULT
    / DSL_CREATED / DSL_CLOSED for every trade — telemetry restored.

WHAT'S PRESERVED FROM v5.2:
  - Multi-signal confluence scoring (max 15 points across 7 components):
      +4 SM_STRONG (sm pct ≥ 10%, traders ≥ 25)
      +2 VELOCITY (15m ≥ 1.0 OR 1h ≥ 3.0)
      +2 ACCELERATING (15m > 1h > 0)
      +2 DUAL_PRICE (4h ≥ 2% AND 1h aligned)
      +1 VOLUME (current ≥ 2x 6h avg)
      +3 QUALITY_TRADER (≥ 1 ELITE/RELIABLE aligned)
      +1 RANK_CLIMB (≥ 5 positions in last 2 scans, top 15)
  - Hard gates (XYZ banned, SM consensus minimum, velocity floor)
  - Top-100 SM leaderboard universe (leaderboard_get_markets)
  - Quality-trader cache (15 min)
  - Scan history (rank tracking across 20 scans)
  - Score-scaled leverage tiers
  - get_safe_leverage() — clamps to Hyperliquid asset max

v6.0 CHANGES vs v5.2:
  - MIN_SCORE 11 → 10 (restore trade flow that produced +$182 in
    v5.0/v5.1 era; v5.2's 11-floor caused 8 days of zero trades)
  - Architecture v1 → v2 (producer + runtime, not full-agency scanner)
  - FEE_OPTIMIZED_LIMIT on EXITS too (v5.2 only used it on entries)
  - Held-asset dedup (3-layer: producer + LLM gate + runtime cooldown)
  - Post-close cooldown (Pangolin v2.1.2 pattern; backstop for runtime
    per_asset_cooldown silent enforcement bug)
  - Reentrancy lockfile (fcntl)
  - Wallet from config/cheetah-config.json (canonical source)

Environment / config resolution:
  Strategy wallet is read from config/cheetah-config.json (canonical
  source). CHEETAH_WALLET env var supported as optional override.

  CHEETAH_WALLET             — REQUIRED. Strategy wallet (must match runtime.yaml).
                              Per Turbine v2.0.9 contamination rule, agent-specific
                              env var only — do NOT fall back to STRATEGY_ADDRESS.
  SENPI_MCP_URL              — defaults to https://mcp.prod.senpi.ai/mcp
  SENPI_AUTH_TOKEN           — REQUIRED. Bearer token for MCP + signal push.
  SENPI_RUNTIME_API_HOST     — defaults to 127.0.0.1
  SENPI_RUNTIME_API_PORT     — defaults to 8787
  OPENCLAW_WORKSPACE         — defaults to /data/workspace
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheetah_config as cfg

# senpi_runtime_helpers (loaded via cfg's sys.path shim) — used here only
# for SenpiClientError type; the wrapper client lives on cfg.
from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "7.1.1"
SCANNER_NAME = "cheetah_signals"  # must match runtime.yaml external_scanner.name


def _resolve_wallet():
    """Resolve strategy wallet — config.json is canonical, env var optional."""
    env_wallet = os.environ.get("CHEETAH_WALLET", "").strip()
    if env_wallet:
        return env_wallet
    config = cfg.load_config()
    return (config.get("wallet") or "").strip()


CHEETAH_WALLET = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIR
# ═══════════════════════════════════════════════════════════════

def _wallet_state_dir():
    if CHEETAH_WALLET:
        h = hashlib.sha256(CHEETAH_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_COUNTER_FILE = _STATE_DIR / "trade-counter.json"
_LAST_CLOSED_FILE = _STATE_DIR / "last-closed.json"
_PREV_HELD_FILE = _STATE_DIR / "previously-held.json"
_SCAN_HISTORY_FILE = _STATE_DIR / "scan-history.json"
_QUALITY_CACHE_FILE = _STATE_DIR / "quality-cache.json"


# Reentrancy guard removed in v7.0.0. producer_daemon owns the per-tick
# scanner_lock with stale-PID auto-recovery via os.kill(pid, 0). Do NOT
# add an inner scanner_lock(...) inside main() — fcntl flock is not
# reentrant; nested call raises BlockingIOError and the daemon logs
# tick_status=skipped_locked every tick. See migration-cookbook §Step 5.


# ═══════════════════════════════════════════════════════════════
# CONSTANTS (fleet-tuned, not operator-set)
# ═══════════════════════════════════════════════════════════════

MIN_SCORE_DEFAULT = 10                # v6.0 — restored from v5.2's 11
ASSET_COOLDOWN_MINUTES = 240          # v5.2 carryover, post-emit
POST_CLOSE_COOLDOWN_MINUTES = 240     # v6.0 — Pangolin v2.1.2 pattern;
                                      # backstop for runtime per_asset_
                                      # cooldown_minutes silent non-
                                      # enforcement bug.
MAX_POSITIONS = 1                     # v5.x carryover — sniper, not scalper
XYZ_BANNED = True                     # v5.x carryover

# Score-scaled leverage tiers (v5.2 carryover, v6.0 floor at score 10).
LEVERAGE_TIERS = [
    {"min_score": 14, "leverage": 8},
    {"min_score": 12, "leverage": 7},
    {"min_score": 11, "leverage": 5},
    {"min_score": 10, "leverage": 3},
]
DEFAULT_LEVERAGE = 5

# Sizing
MARGIN_PCT = 0.30                     # v5.2 — 30% per slot, survivable

# Quality trader pool config
#
# v7.1.0 (2026-05-11): WIDENED. The v5.2-era filter
# (WEEKLY + ELITE/RELIABLE + open_position_filter=True, limit=8)
# returned ZERO traders in the post-migration regime, leaving
# positions_map empty for the entire helpers-migration era. Without
# the +3 QUALITY_TRADER bonus, candidate scores cap at 8 (needs 4-of-4
# secondary confluence to reach the MIN_SCORE=10 floor), which only
# fires in strong trending regimes — not the BTC/ETH consolidation
# we've been in since 5/8. Filter changes (verified live:
# WEEKLY+ELITE/RELIABLE+open_position → 0 traders returned):
#   - TIMEFRAME WEEKLY → MONTHLY (broader history, more labels accumulate)
#   - CONSISTENCY ELITE/RELIABLE → ELITE/RELIABLE/STREAKY
#   - POOL_SIZE 8 → 25 (wider sample lets per-asset overlap surface)
#   - drop open_position_filter (was True): trader pool stays
#     stable across regimes where elite traders go briefly flat;
#     per-trader leaderboard_get_trader_positions still determines
#     whether each contributes to positions_map.
# Scoring components, weights, and MIN_SCORE unchanged.
QT_POOL_SIZE = 25
QT_TIMEFRAME = "MONTHLY"
QT_CONSISTENCY = ["ELITE", "RELIABLE", "STREAKY"]
QT_CACHE_MINUTES = 15

# Score component thresholds (preserved from v5.2)
SM_MIN_PCT = 10.0
SM_MIN_TRADERS = 25
VEL_MIN_15M = 1.0
VEL_MIN_1H = 3.0
PRICE_MIN_4H = 2.0
VOL_MIN_RATIO = 2.0
RANK_CLIMB_MIN = 5
RANK_CLIMB_TOPN = 15


# ═══════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_safe_leverage(wallet, asset, requested_leverage):
    """Query Hyperliquid's max leverage for this asset and clamp.
    v5.1.1 leverage safety fix preserved verbatim — prevents
    CREATE_INVALID_LEVERAGE rejections on assets whose Hyperliquid max
    is below the scanner's requested tier."""
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
# STATE I/O — wallet-isolated
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


def load_trade_counter():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(_COUNTER_FILE) as f:
            tc = json.load(f)
        if tc.get("date") != today:
            tc = {"date": today, "entries": 0}
        return tc
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": today, "entries": 0}


def save_trade_counter(tc):
    tc["updatedAt"] = cfg.now_iso()
    cfg.atomic_write(str(_COUNTER_FILE), tc)


# ═══════════════════════════════════════════════════════════════
# v6.0 POST-CLOSE COOLDOWN (Pangolin v2.1.2 pattern)
# ═══════════════════════════════════════════════════════════════

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
    payload = {"assets": sorted(list(held)), "updatedAt": cfg.now_iso()}
    cfg.atomic_write(str(_PREV_HELD_FILE), payload)


def detect_closes(current_held, previously_held):
    """Diff held_assets across ticks. Anything that disappeared just
    closed — record close timestamp into last-closed.json."""
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
# ACCOUNT + HELD ASSETS
# ═══════════════════════════════════════════════════════════════

def get_account_state():
    """Returns (account_value, position_count, held_assets_set).
    held_assets is uppercase coin symbols of any open position — used
    for v6.0 dedup defense layer 1."""
    if not CHEETAH_WALLET:
        return None, None, set()
    ch = cfg.mcporter_call(
        "strategy_get_clearinghouse_state",
        strategy_wallet=CHEETAH_WALLET,
    )
    if not ch:
        return None, None, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
    held = set()
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
    return total_value, pos_count, held


# ═══════════════════════════════════════════════════════════════
# MARKET FETCHING (preserved from v5.2)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_markets():
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
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
        normalized.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                             m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume_6h": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return normalized


def load_scan_history():
    if _SCAN_HISTORY_FILE.exists():
        try:
            with open(_SCAN_HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > 20:
        history["scans"] = scans[-20:]
    cfg.atomic_write(str(_SCAN_HISTORY_FILE), history)


def build_scan_snapshot(markets):
    return {
        "ts": time.time(),
        "markets": [
            {"token": m["token"], "dex": m["dex"], "rank": m["rank"], "direction": m["direction"]}
            for m in markets
        ],
    }


def get_rank_climb(history, token, dex):
    scans = history.get("scans", [])
    if len(scans) < 2:
        return 0
    prev = scans[-2]
    current = scans[-1]
    prev_rank = None
    current_rank = None
    for m in prev.get("markets", []):
        if m.get("token") == token and m.get("dex", "") == dex:
            prev_rank = m.get("rank", 999)
            break
    for m in current.get("markets", []):
        if m.get("token") == token and m.get("dex", "") == dex:
            current_rank = m.get("rank", 999)
            break
    if prev_rank is None or current_rank is None:
        return 0
    return max(0, prev_rank - current_rank)


def fetch_quality_trader_positions():
    """Cached for QT_CACHE_MINUTES. Returns (asset:direction) -> [addr...]."""
    if _QUALITY_CACHE_FILE.exists():
        try:
            with open(_QUALITY_CACHE_FILE) as f:
                cache = json.load(f)
            if time.time() - cache.get("ts", 0) < QT_CACHE_MINUTES * 60:
                return cache.get("positions_map", {})
        except (json.JSONDecodeError, IOError):
            pass

    raw = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame=QT_TIMEFRAME,
        sort_by="PROFIT_AND_LOSS_UNREALIZED",
        consistency=QT_CONSISTENCY,
        # v7.1.0: open_position_filter removed — pool stayed empty when
        # the platform's WEEKLY classification combined with currently-flat
        # elite traders produced 0 results. The per-trader positions fetch
        # below already filters out flat traders implicitly (empty positions
        # array contributes nothing to positions_map).
        limit=QT_POOL_SIZE,
    )
    if not raw:
        return {}

    traders = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            traders = data.get("traders", [])
        elif isinstance(data, list):
            traders = data

    addresses = []
    for t in traders:
        if isinstance(t, dict):
            addr = str(t.get("address", "")).lower()
            if addr:
                addresses.append(addr)

    positions_map = {}
    shape_warned = False  # log unexpected response shapes once per refresh
    for addr in addresses:
        data = cfg.mcporter_call("leaderboard_get_trader_positions", trader_id=addr)
        if not data:
            continue
        # v7.1.1: leaderboard_get_trader_positions actually returns
        #   { data: { positions: { trader_id, rank, positions: [...] } } }
        # — the per-trader positions array is nested one level deeper than the
        # schema guide documents. v7.0/v7.1 parser looked at `data.positions`
        # expecting a list, got a dict, hit `if not isinstance(positions, list):
        # continue` and silently skipped every trader → positions_map empty
        # → +3 QUALITY_TRADER bonus never fires → Cheetah scores cap at 8.
        # Handle BOTH shapes (list-direct AND nested-dict) so we're resilient
        # to future schema changes either direction.
        positions = []
        if isinstance(data, dict):
            d = data.get("data", data)
            if isinstance(d, dict):
                raw_positions = d.get("positions", d.get("top_positions", []))
                if isinstance(raw_positions, list):
                    # Old shape (matches the schema guide): direct list
                    positions = raw_positions
                elif isinstance(raw_positions, dict):
                    # Actual shape on prod: { trader_id, rank, positions: [...] }
                    nested = raw_positions.get("positions", [])
                    if isinstance(nested, list):
                        positions = nested
                    elif not shape_warned:
                        cfg.log(
                            f"POSITIONS_SHAPE_WARN unexpected nested dict for {addr[:10]}: "
                            f"keys={list(raw_positions.keys())[:8]}; treating as empty."
                        )
                        shape_warned = True
                elif not shape_warned:
                    cfg.log(
                        f"POSITIONS_SHAPE_WARN unexpected type {type(raw_positions).__name__} "
                        f"for {addr[:10]}: treating as empty."
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
            direction = str(pos.get("direction", pos.get("side", ""))).upper()
            if direction not in ("LONG", "SHORT"):
                szi = safe_float(pos.get("szi", 0))
                if szi != 0:
                    direction = "LONG" if szi > 0 else "SHORT"
                else:
                    direction = "LONG"
            key = f"{asset}:{direction}"
            positions_map.setdefault(key, []).append(addr)

    # v7.1.0 telemetry: surface the trader-pool shape so cache-empty
    # regressions are visible in the tick log without log archaeology.
    # If pool size is < QT_POOL_SIZE / 2 OR positions_map is empty, log
    # a warning event the operator can grep for.
    if len(addresses) < max(2, QT_POOL_SIZE // 2) or not positions_map:
        cfg.log(
            f"QT_POOL_WARN traders={len(addresses)} (configured {QT_POOL_SIZE}), "
            f"positions_map_size={len(positions_map)}, "
            f"filter=time_frame={QT_TIMEFRAME} consistency={QT_CONSISTENCY}. "
            f"+3 QUALITY_TRADER bonus may not fire reliably this tick."
        )

    try:
        cfg.atomic_write(str(_QUALITY_CACHE_FILE), {"ts": time.time(), "positions_map": positions_map})
    except Exception:
        pass

    return positions_map


# ═══════════════════════════════════════════════════════════════
# SCORING (preserved verbatim from v5.2)
# ═══════════════════════════════════════════════════════════════

def score_confluence(market, quality_positions, history):
    """Score a single market for APEX confluence. Returns (score, reasons).
    Hard gates reject with score=0 and an empty reasons list."""

    direction = market["direction"]
    if not direction:
        return 0, []

    reasons = []
    score = 0

    # Hard gate: SM consensus must meet minimum
    sm_pct = market["pct"]
    sm_traders = market["traders"]
    if sm_pct < SM_MIN_PCT or sm_traders < SM_MIN_TRADERS:
        return 0, []
    score += 4
    reasons.append(f"SM_STRONG {sm_pct:.1f}%/{sm_traders}t")

    # Hard gate: at least one velocity axis must pass
    c15m = market["contrib_15m"]
    c1h = market["contrib_1h"]
    if c15m < VEL_MIN_15M and c1h < VEL_MIN_1H:
        return 0, []
    if c15m >= VEL_MIN_15M or c1h >= VEL_MIN_1H:
        score += 2
        reasons.append(f"VELOCITY 15m={c15m:.2f}/1h={c1h:.2f}")

    # Accelerating: 15m > 1h > 0
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"ACCEL 15m({c15m:.2f})>1h({c1h:.2f})")

    # Dual price confirmation (4h + 1h aligned)
    p4h = market["price_chg_4h"]
    p1h = market["price_chg_1h"]
    if direction == "LONG":
        if p4h >= PRICE_MIN_4H and p1h > 0:
            score += 2
            reasons.append(f"DUAL_PRICE +{p4h:.1f}/+{p1h:.2f}%")
    else:
        if p4h <= -PRICE_MIN_4H and p1h < 0:
            score += 2
            reasons.append(f"DUAL_PRICE {p4h:.1f}/{p1h:.2f}%")

    # Volume spike
    vol = market["volume"]
    avg_vol = market["avg_volume_6h"]
    if avg_vol > 0 and vol >= avg_vol * VOL_MIN_RATIO:
        score += 1
        reasons.append(f"VOL {vol/avg_vol:.1f}x")

    # Quality trader alignment
    key = f"{market['token']}:{direction}"
    aligned_traders = quality_positions.get(key, [])
    if aligned_traders:
        score += 3
        reasons.append(f"QUALITY_ALIGN {len(aligned_traders)}_traders")

    # Rank climb
    climb = get_rank_climb(history, market["token"], market["dex"])
    if climb >= RANK_CLIMB_MIN and market["rank"] <= RANK_CLIMB_TOPN:
        score += 1
        reasons.append(f"RANK_CLIMB +{climb}->#{market['rank']}")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(candidate, leverage, margin_usd, held_assets):
    """Build the payload that `push_signal()` consumes.

    Returned keys are exactly what `push_signal()` extracts and forwards
    to the wrapper. The runtime's POST /signals schema declares
    `additionalProperties: false`, so any other top-level fields would
    be silently dropped or rejected — and `address`/`scanner` are sourced
    from module-level constants in `push_signal()`, not from this dict.

    Server-side fields (not sent by the producer):
      - `address` — passed to push_signal from CHEETAH_WALLET
      - `scanner` — passed to push_signal from SCANNER_NAME
      - `timestamp` — set by the runtime receiver
      - `factors` — set by the runtime receiver to {}

    All scanner-config-validated fields go in `data`. The composite
    `score` lives there too — it is an unbounded int 0-16, not the
    schema's 0..1 confidence.
    """
    held_list = sorted(list(held_assets)) if held_assets else []
    return {
        "signalType": "CHEETAH_CONFLUENCE",
        "asset": candidate["token"],
        "direction": candidate["direction"],
        "data": {
            "score": candidate["score"],
            "leverage": leverage,
            "marginUsd": margin_usd,
            "reasons": " | ".join(candidate["reasons"]),
            "smPct": float(candidate["pct"]),
            "smTraders": int(candidate["traders"]),
            "rank": int(candidate["rank"]),
            "contrib15m": float(candidate["contrib_15m"]),
            "contrib1h": float(candidate["contrib_1h"]),
            "priceChg4hPct": float(candidate["price_chg_4h"]),
            "priceChg1hPct": float(candidate["price_chg_1h"]),
            "volRatio": round(
                candidate["volume"] / candidate["avg_volume_6h"]
                if candidate["avg_volume_6h"] > 0 else 0,
                2
            ),
            "qualityAlign": int(candidate.get("quality_align", 0)),
            "rankClimb": int(candidate.get("rank_climb", 0)),
            "heldAssets": held_list,
        },
    }


def push_signal(payload):
    """Push a signal payload to the runtime via senpi_runtime_helpers.

    Direct HTTP POST to runtime API on 127.0.0.1; no subprocess.

    SignalItem field mapping (per references/signal-schema.md and the
    Pangolin TST 2026-05-05 incident):
      - top-level routing fields: address, scanner, asset, direction, signal_type
      - data block: scanner-config-validated fields only
      - score STAYS inside data — Cheetah's composite is unbounded int
        0-16, not 0..1 confidence (different semantics; same Pangolin
        v2.2 reference rationale).
      - signal_type passed explicitly per signal so the runtime never
        relies on the scanner's defaultSignalType fallback (cheetah's
        runtime.yaml does not declare one). Keeps audit logs and the
        LLM decision context tagged correctly.

    Returns True on accepted ingest, False on transport / schema rejection.
    The daemon's per-tick error handler catches anything unhandled here
    and continues to the next tick — no half-written caller state.
    """
    if not CHEETAH_WALLET:
        cfg.log("CHEETAH_WALLET not set; cannot push signal")
        return False
    try:
        cfg._wrapper_client.push_signal(
            address=CHEETAH_WALLET,
            scanner=SCANNER_NAME,
            asset=payload.get("asset"),
            direction=payload.get("direction"),
            signal_type=payload.get("signalType"),
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

    # Fail loud if wallet not configured (Turbine v2.0.9 contamination rule).
    if not CHEETAH_WALLET:
        cfg.output({
            "status": "error",
            "error": "CHEETAH_WALLET not set. Set the env var or populate config/cheetah-config.json wallet field.",
            "_cheetah_producer_version": VERSION,
        })
        return

    # NB: no scanner_lock here. producer_daemon owns the per-tick lock.

    # ── Account state + held assets ──
    account_value, pos_count, held_assets = get_account_state()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_cheetah_producer_version": VERSION,
        })
        return

    # v6.0 close detection: diff held_assets against last tick's held set
    previously_held = load_previously_held()
    closed_this_tick = detect_closes(held_assets, previously_held)
    save_previously_held(held_assets)

    # Max positions guard (Cheetah is 1-slot sniper)
    if pos_count >= MAX_POSITIONS:
        cfg.output({
            "status": "ok",
            "note": f"riding open position(s): {sorted(list(held_assets))}",
            "open_positions": pos_count,
            "_cheetah_producer_version": VERSION,
        })
        return

    # ── Fetch SM markets ──
    markets = fetch_sm_markets()
    if not markets:
        cfg.output({
            "status": "error",
            "error": "failed to fetch leaderboard_get_markets",
            "_cheetah_producer_version": VERSION,
        })
        return

    # ── Update scan history (for rank-climb scoring) ──
    history = load_scan_history()
    history["scans"].append(build_scan_snapshot(markets))
    save_scan_history(history)

    # ── Quality trader positions (cached) ──
    quality_positions = fetch_quality_trader_positions()

    # ── Score all markets ──
    # Filter order: held > post-close-cooldown > emit-cooldown > score.
    candidates = []
    all_scored = []
    skipped_held = 0
    skipped_post_close = 0
    post_close_skips = []

    for market in markets:
        token = market["token"]

        # v6.0 dedup defense layer 1: never emit on held assets
        if token.upper() in held_assets:
            skipped_held += 1
            continue

        # v6.0 post-close cooldown (Pangolin v2.1.2 pattern)
        if is_in_post_close_cooldown(token):
            skipped_post_close += 1
            post_close_skips.append({
                "token": token,
                "remaining_min": post_close_cooldown_remaining_min(token),
            })
            continue

        # Emit cooldown (post-emit, prevents spam re-fires)
        if is_asset_cooled_down(token):
            continue

        score, reasons = score_confluence(market, quality_positions, history)
        if score == 0:
            continue

        all_scored.append({
            "token": token,
            "direction": market["direction"],
            "score": score,
        })

        if score >= MIN_SCORE_DEFAULT:
            # Annotate with extras for telemetry
            key = f"{token}:{market['direction']}"
            quality_count = len(quality_positions.get(key, []))
            rank_climb = get_rank_climb(history, token, market["dex"])
            candidate = dict(market)
            candidate["score"] = score
            candidate["reasons"] = reasons
            candidate["quality_align"] = quality_count
            candidate["rank_climb"] = rank_climb
            candidates.append(candidate)

    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        cfg.output({
            "status": "ok",
            "note": f"0 candidates at score >= {MIN_SCORE_DEFAULT} ({len(all_scored)} scored)",
            "topScored": top3,
            "skipped_held": skipped_held,
            "skipped_post_close": skipped_post_close,
            "post_close_skips": post_close_skips,
            "closed_this_tick": sorted(list(closed_this_tick)),
            "held_assets": sorted(list(held_assets)),
            "_cheetah_producer_version": VERSION,
        })
        return

    # ── Pick top candidate, compute sizing + leverage ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_usd = round(account_value * MARGIN_PCT, 2)
    requested_leverage = get_leverage_for_score(best["score"])
    # v5.1.1 leverage clamp preserved
    leverage = get_safe_leverage(CHEETAH_WALLET, best["token"], requested_leverage)

    # ── Daily counter (best-effort observability; runtime guard_rails
    #    is the authoritative max_entries_per_day enforcement) ──
    tc = load_trade_counter()

    # ── Emit signal (only top candidate per tick) ──
    payload = build_signal_payload(best, leverage, margin_usd, held_assets)
    pushed = 0
    if push_signal(payload):
        pushed += 1
        mark_asset_emitted(best["token"])
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

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
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "marginUsd": margin_usd,
            "reasons": best["reasons"][:5],
        } if pushed else None,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "entries_today": tc.get("entries", 0),
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_cheetah_producer_version": VERSION,
    })


if __name__ == "__main__":
    # v7.0.0 — long-lived daemon. Replaces openclaw cron + agentTurn for
    # this skill — no LLM coupling, no per-tick CLI cold start.
    # producer_daemon wraps each tick in scanner_lock so overlap is
    # skipped cleanly, enforces a 6-min wall-clock per-tick via SIGALRM
    # (longer than the producer's WARN_OVER_120S budget so the warn
    # fires before the alarm), and drains gracefully on SIGTERM/SIGINT.
    #
    # Lock name is per-wallet so multi-wallet hosts (one daemon per
    # wallet) don't collide on a single global lock file.
    #
    # alive_check is left UNSET (default behavior) — producer_daemon
    # self-terminates if the runtime for CHEETAH_WALLET is deleted OR
    # the cheetah_signals scanner is dropped/renamed. Do NOT pass
    # alive_check=None when wallet= is set.
    _wallet_lock_id = (
        hashlib.sha256(CHEETAH_WALLET.lower().encode()).hexdigest()[:12]
        if CHEETAH_WALLET
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"cheetah-producer-{_wallet_lock_id}",
        wallet=CHEETAH_WALLET,
        scanner=SCANNER_NAME,
        tick_timeout=360,
    )
