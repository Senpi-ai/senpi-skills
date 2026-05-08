#!/usr/bin/env python3
# Senpi PANGOLIN Producer v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PANGOLIN v2.2.0 Producer — Funding-fade signal emitter for v2 runtime.

v2.2.0 (2026-05-04 — "DSL T0 ladder calibration"):
  Chain DB analysis of 6 ACCEPTED trades showed 4 of 4 ZEREBRO winners
  peaked at 6.7-9.7% margin ROE — never engaged Phase 2 (T0 was 12%).
  All 4 closed via dsl_breach giving back to negative ROE; only funding
  accrual saved them from being net losses. Lowered T0 from 12 to 8
  (catches 8-9.7% peaks); T1 from 20 to 16 (closes T0→T1 gap).
  No producer code changes — runtime.yaml DSL preset only.

v2.1.2 (2026-05-04 — "post-close thrash-cycle fix"):
  Workaround for runtime guard_rails.per_asset_cooldown_minutes being
  silently non-enforcing. Pangolin's 240-min cooldown was declared
  but ZEREBRO reopened within 3-25 min of close, repeatedly — 7
  reopens in 36 hours. One of those reopens landed across a runtime
  swap, where the new runtime registered the position as baseline
  but never attached local DSL — naked exposure for ~36h.

  v2.1.2 producer-side mitigation:
    - Diff held_assets across ticks; record close timestamps when an
      asset disappears from the held set (no audit-log dependency).
    - New filter: is_in_post_close_cooldown(token) — skips emission
      for POST_CLOSE_COOLDOWN_MINUTES (240) after a close.
    - State files: state/<wallet-hash>/last-closed.json,
      previously-held.json — atomic_write, persistent across cron runs.
    - Output JSON now includes skipped_post_close, post_close_skips
      with remaining_min, and closed_this_tick — operators can verify
      the guard is firing without source-reading.

  Once Senpi fixes the runtime gate, this becomes a defense layer
  instead of the primary control. The other open Senpi bug — runtime
  swap orphaning positions from local DSL — is NOT fixed by this
  patch and requires backend resolution.

v2.1.1 (observability fix — bundles into v2.1.2):
  - Surface skipped_held and held_assets in cfg.output JSON sites.
  - Fixed v2.1.0 gap where dedup counter incremented but wasn't
    visible to operators tailing producer.log.

v2.1.0 (2026-05-02 P0 hot-patch — "phantom close + duplicate emission"):
  - get_account_value() now returns held_assets set (uppercase coin
    symbols of any open position). Three-layer defense for held-
    asset dedup, mirroring Scorpion v4.1.1.
  - Producer-side filter: candidates with already-held tokens are
    DROPPED before emission. Layer 1.
  - Signal payload includes heldAssets list; LLM gate hard-skips
    on duplicate. Layer 2.
  - Runtime per_asset_cooldown_minutes 240 is layer 3 (existing).
  - Triggered by 2026-05-02 incident: MAVIA short ballooned from
    -38,808 → -79,493 size in 24h via repeated emission, while
    DSL_CLOSED telegrams fired phantom closes leaving positions
    naked. Same bug class as Scorpion v4.1.0.


Pangolin v1.x ran as a full-agency Python scanner that:
  - Fetched market_list_instruments + leaderboard_get_markets
  - Detected extreme funding extremes with persistence + regime gates
  - Scored candidates (funding extremity + persistence + trend +
    regime + SM concentration + sticky OI + price reversal)
  - Called create_position directly with FEE_OPTIMIZED_LIMIT entries

v2.0 splits that into two parts:
  1. This producer (cron, 5min): emits candidate signals only.
  2. Runtime (senpi-trading-runtime v2): receives signals via
     external_scanner ingest, LLM-gates them (pass-through),
     executes with FEE_OPTIMIZED_LIMIT (maker-first, 60s, no taker
     fallback on entries — preserve v1 patience), and manages DSL
     exits autonomously with FEE_OPTIMIZED_LIMIT (maker-first, 60s,
     taker fallback as safety floor).

The producer's single responsibility: detect funding-fade candidates
and push them to the runtime via `openclaw senpi external-scanner ingest`.

NO execution code. NO DSL code. NO position-tracking. Daily-loss /
drawdown / max-positions / consec-loss are all enforced by the
runtime's declarative guard_rails.

Funding-fade detection logic preserved verbatim from v1.5:
  - market_list_instruments → context.{funding, openInterest, markPx}
  - market_get_funding_history → list keyed by asset (v1.5 parser fix)
  - market_get_funding_regime → LONG_CROWDED / SHORT_CROWDED / NEUTRAL
  - persistence_hours >= 3 (hard gate)
  - regime confirms fade OR is neutral/unknown (hard gate)
  - score >= 9 (hard gate)
  - per-asset 240min cooldown
  - dynamic PnL-aware daily cap (12 entries at +5%, 0 entries at -25%)
  - XYZ banned

Environment variables:
  SENPI_API_KEY     — for MCP access
  PANGOLIN_WALLET   — Pangolin v2 wallet (must match runtime YAML's wallet).
                      AGENT-SPECIFIC env var by design — do NOT fall back
                      to a generic STRATEGY_ADDRESS. Per Turbine v2.0.9
                      contamination fix: a shared env var is a fleet-wide
                      vector — if two agents are deployed in the same
                      install (Railway service, container, etc.) and one
                      sets STRATEGY_ADDRESS, the other inherits it and
                      silently emits to the wrong wallet.
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  EXTERNAL_SCANNER_NAME — optional override (default "pangolin_signals")
  PANGOLIN_MARGIN_PCT — optional, default 0.25 (25% of account value)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pangolin_config as cfg


# v2.0.0: Agent-specific wallet env var. NO fallback to STRATEGY_ADDRESS
# (contamination risk per Turbine v2.0.9 fix — see feedback_mcp_auth_is_fleet_wide.md).
PANGOLIN_WALLET = os.environ.get("PANGOLIN_WALLET", "")
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "pangolin_signals")
MARGIN_PCT = float(os.environ.get("PANGOLIN_MARGIN_PCT", "0.25"))


# Wallet-isolated state dir (forward-compat for multi-wallet deployments).
def _wallet_state_dir():
    if PANGOLIN_WALLET:
        h = hashlib.sha256(PANGOLIN_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_COUNTER_FILE = _STATE_DIR / "trade-counter.json"
# v2.1.2: post-close cooldown (workaround for runtime
# per_asset_cooldown_minutes silent non-enforcement).
_LAST_CLOSED_FILE = _STATE_DIR / "last-closed.json"
_PREV_HELD_FILE = _STATE_DIR / "previously-held.json"


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD
# ═══════════════════════════════════════════════════════════════
# Reentrancy is enforced by `producer_daemon` via
# `senpi_runtime_helpers.scanner_lock(name=f"pangolin-producer-{wallet_hash}")`
# wrapping each tick. If a tick exceeds the daemon's `interval_seconds`,
# the next firing fails to acquire `scanner_lock` and the daemon records
# `tick_status="skipped_locked"`. The legacy in-script `acquire_lock` /
# `release_lock` helpers (and the `fcntl` import) were removed when the
# daemon took over scheduling — `_LOCK_PATH` is unused now too.


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS (fleet-tuned, not operator-set)
# ═══════════════════════════════════════════════════════════════
MIN_SCORE = 9                         # v1.4: raised from 7 (new persistence + regime points)
MIN_FUNDING_RATE = 0.00015            # v1.1: 20% annualized threshold
MIN_OI_USD = 1_000_000                # v1.3 floor — ensures liquidity
MIN_PERSISTENCE_HOURS = 3             # v1.4: hard gate against fresh spikes
ASSET_COOLDOWN_MINUTES = 240          # v1 — 4h. Post-EMIT cooldown.
POST_CLOSE_COOLDOWN_MINUTES = 240     # v2.1.2 — post-CLOSE cooldown.
                                      # Mirrors runtime.guard_rails.per_asset_cooldown_minutes
                                      # which is silently not enforcing in v2 runtime
                                      # (also broken on Scorpion v4.1.0). Producer-side
                                      # backstop until Senpi fixes the runtime gate.
STARTING_BUDGET = 160.0               # rebased for $160 test wallet (was 1000.0)
XYZ_BANNED = True                     # never trade equities

# Leverage tiers — preserved from v1.4
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 5},     # very high conviction
    {"min_score": 9,  "leverage": 3},     # default
]
DEFAULT_LEVERAGE = 3


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


def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """v1 carryover: PnL-aware daily entry cap.
    Producer-side gate; runtime guard_rails.max_entries_per_day is the
    upper ceiling at 12 (matching the +5% PnL bracket here)."""
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12
    elif pnl_pct >= 0:     return 8
    elif pnl_pct >= -5:    return 5
    elif pnl_pct >= -15:   return 3
    elif pnl_pct >= -25:   return 1
    else:                  return 0


# ═══════════════════════════════════════════════════════════════
# STATE I/O (wallet-isolated)
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
    """Daily counter — resets at UTC midnight."""
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
# v2.1.2 POST-CLOSE COOLDOWN STATE
# ═══════════════════════════════════════════════════════════════
# Detects close events by diffing held_assets across ticks. When an
# asset disappears from held set (held last tick, not held this
# tick), records a close timestamp. Producer skips emission for
# POST_CLOSE_COOLDOWN_MINUTES from that timestamp.
#
# Workaround for runtime guard_rails.per_asset_cooldown_minutes
# silently not enforcing in v2 runtime. Caused 7 ZEREBRO thrash
# reopens between 2026-05-03 03:55 and 2026-05-04 00:19 (incident).
# Once Senpi fixes the runtime gate, this becomes a defense layer
# instead of the primary control.

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
    payload = {
        "assets": sorted(list(held)),
        "updatedAt": cfg.now_iso(),
    }
    cfg.atomic_write(str(_PREV_HELD_FILE), payload)


def detect_closes(current_held, previously_held):
    """Return set of assets that were held last tick but are not held now.
    Updates last-closed.json with current timestamp for each detected close."""
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
    """For observability — minutes left in cooldown, or 0 if not cooled."""
    last = load_last_closed()
    if token not in last:
        return 0
    last_close = last[token].get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    remaining = cooldown_minutes - elapsed_min
    return round(max(0.0, remaining), 1)


# ═══════════════════════════════════════════════════════════════
# ACCOUNT VALUE QUERY
# ═══════════════════════════════════════════════════════════════

def get_account_value():
    """Read account value from clearinghouse for dynamic-cap math.
    Returns (value, position_count, held_assets_set) on success or
    (None, None, set()) on failure.

    v1.5: now also returns set of held asset symbols (uppercase) for
    held-asset dedup. Same fix family as Scorpion v4.1.1 — producer
    must skip emission on held assets even if runtime cooldown gates
    don't enforce, and LLM gate must hard-skip duplicates."""
    if not PANGOLIN_WALLET:
        return None, None, set()
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=PANGOLIN_WALLET)
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
# v1.4 MCP DATA TOOLS — preserved verbatim
# ═══════════════════════════════════════════════════════════════

def get_funding_regime():
    """Fetch market-wide funding regime once per scan."""
    try:
        r = cfg.mcporter_call("market_get_funding_regime")
        if not r:
            return None
        data = r.get("data", r)
        return data.get("regime")
    except Exception:
        return None


def get_funding_history(asset):
    """Fetch per-asset funding history with persistence + trend.
    v1.5 parser: MCP returns data.data=[{asset, persistence_hours, ...}, ...]
    (double-nested list keyed by asset). v1.4 read data.persistence_hours
    directly and got None, silently skipping every candidate."""
    try:
        r = cfg.mcporter_call("market_get_funding_history", asset=asset)
        if not r:
            return None
        outer = r.get("data", r)
        rows = outer.get("data") if isinstance(outer, dict) else None
        if not rows:
            return None
        row = next((x for x in rows if x.get("asset") == asset), None)
        if row is None:
            return None
        raw_trend = (row.get("funding_trend") or row.get("trend") or "").upper()
        # v1.5: normalize INTENSIFYING/DECAYING → INCREASING/DECREASING
        if raw_trend == "INTENSIFYING":
            trend = "INCREASING"
        elif raw_trend == "DECAYING":
            trend = "DECREASING"
        else:
            trend = raw_trend
        return {
            "persistence_hours": row.get("persistence_hours"),
            "funding_direction": row.get("funding_direction"),
            "trend": trend,
            "annualized_pct": row.get("funding_annualized_pct") or row.get("annualized_pct"),
        }
    except Exception:
        return None


def regime_confirms_fade(fade_direction, regime):
    """Returns True/False/None (None = regime unavailable/neutral)."""
    if regime is None or regime == "NEUTRAL":
        return None
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# SCANNING — preserved from v1.5
# ═══════════════════════════════════════════════════════════════

def scan_funding_extremes():
    """Find assets with extreme funding rates that pass v1.4 gates,
    score them, return sorted list of candidates."""
    raw = cfg.mcporter_call("market_list_instruments")
    if not raw:
        return [], None

    instruments = raw.get("data", raw)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return [], None

    # SM concentration map
    sm_raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    sm_map = {}
    if sm_raw:
        sm_markets = sm_raw.get("data", sm_raw)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", sm_markets)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", [])
        if isinstance(sm_markets, list):
            for m in sm_markets:
                if isinstance(m, dict):
                    token = str(m.get("token", "")).upper()
                    dex = str(m.get("dex", "")).lower()
                    if dex != "xyz":
                        sm_map[token] = m

    regime = get_funding_regime()
    candidates = []

    for inst in instruments:
        if not isinstance(inst, dict):
            continue

        name = str(inst.get("name", inst.get("coin", ""))).upper()
        dex = str(inst.get("dex", "")).lower()

        if XYZ_BANNED and dex == "xyz":
            continue

        # v1.3: read funding/OI/price from context (not top level)
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}

        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                              inst.get("markPx", inst.get("midPx", 0)))))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if oi_usd < MIN_OI_USD:
            continue

        funding = safe_float(ctx.get("funding", inst.get("funding", 0)))
        if abs(funding) < MIN_FUNDING_RATE:
            continue

        crowd_direction = "LONG" if funding > 0 else "SHORT"
        fade_direction = "SHORT" if funding > 0 else "LONG"

        # HARD GATE 1: regime must confirm fade OR be neutral/unavailable
        regime_confirms = regime_confirms_fade(fade_direction, regime)
        if regime_confirms is False:
            continue

        # HARD GATE 2: persistence >= 3h
        fh = get_funding_history(name)
        if fh is None:
            continue

        persistence_hours = fh.get("persistence_hours")
        if persistence_hours is None:
            continue
        try:
            persistence_hours = float(persistence_hours)
        except (TypeError, ValueError):
            continue
        if persistence_hours < MIN_PERSISTENCE_HOURS:
            continue

        # ── Scoring ──
        score = 0
        reasons = []

        # Funding extremity
        abs_funding = abs(funding)
        annualized = abs_funding * 3 * 365 * 100
        if abs_funding >= 0.001:
            score += 4
            reasons.append(f"EXTREME_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0006:
            score += 3
            reasons.append(f"HIGH_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0003:
            score += 2
            reasons.append(f"ELEVATED_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")

        # Persistence
        if persistence_hours >= 12:
            score += 3
            reasons.append(f"MATURE_CROWDING {persistence_hours:.0f}h")
        elif persistence_hours >= 6:
            score += 2
            reasons.append(f"STABLE_CROWDING {persistence_hours:.0f}h")
        else:
            score += 1
            reasons.append(f"FRESH_CROWDING {persistence_hours:.0f}h")

        # Trend
        trend = fh.get("trend", "").upper() if fh.get("trend") else ""
        if trend == "INCREASING":
            score += 1
            reasons.append("CROWDING_INCREASING")
        elif trend == "DECREASING":
            score -= 1
            reasons.append("CROWDING_DECREASING")

        # Regime confirmation
        if regime_confirms is True:
            score += 2
            reasons.append(f"REGIME_CONFIRMS_{regime}")
        elif regime_confirms is None and regime is not None:
            reasons.append(f"REGIME_{regime}")

        # SM concentration
        sm = sm_map.get(name)
        sm_pct = 0.0
        sm_traders = 0
        sm_dir = ""
        if sm:
            sm_dir = str(sm.get("direction", "")).upper()
            sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
            sm_traders = int(sm.get("trader_count", 0))

            if sm_dir == fade_direction:
                if sm_pct >= 10:
                    score += 3
                    reasons.append(f"SM_FADING_CROWD {sm_pct:.1f}% ({sm_traders}t)")
                elif sm_pct >= 5:
                    score += 2
                    reasons.append(f"SM_ALIGNED_FADE {sm_pct:.1f}% ({sm_traders}t)")
                else:
                    score += 1
                    reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
            elif sm_dir == crowd_direction:
                if sm_pct >= 10:
                    score -= 2
                    reasons.append(f"SM_WITH_CROWD {sm_pct:.1f}% (dangerous)")
                else:
                    score -= 1
                    reasons.append(f"SM_SLIGHT_CROWD {sm_pct:.1f}%")

            cc_15m = safe_float(sm.get("contribution_pct_change_15m", 0))
            if sm_dir == crowd_direction and cc_15m < -0.5:
                score += 1
                reasons.append(f"SM_MOMENTUM_FADING {cc_15m:.2f}")

        # OI turnover bonus (sticky positions)
        volume_24h = safe_float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        oi_turnover = 0.0
        if oi > 0 and volume_24h > 0:
            oi_turnover = volume_24h / oi
            if oi_turnover < 0.5:
                score += 1
                reasons.append(f"STICKY_OI (turnover {oi_turnover:.2f}x)")

        # Price already reversing
        p4h = safe_float(sm.get("token_price_change_pct_4h", 0)) if sm else 0
        if crowd_direction == "LONG" and p4h < -0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")
        elif crowd_direction == "SHORT" and p4h > 0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")

        reasons.insert(0, f"FADE_FUNDING {name} (crowd is {crowd_direction})")

        candidates.append({
            "token": name,
            "funding": funding,
            "crowd_direction": crowd_direction,
            "fade_direction": fade_direction,
            "score": score,
            "reasons": reasons,
            "annualized_pct": annualized,
            "persistence_hours": persistence_hours,
            "trend": trend,
            "regime": regime,
            "regime_confirms": bool(regime_confirms),
            "sm_pct": sm_pct,
            "sm_traders": sm_traders,
            "sm_direction": sm_dir,
            "p4h": p4h,
            "oi_usd": oi_usd,
            "oi_turnover": oi_turnover,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates, regime


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(c, leverage, margin_usd, held_assets=None):
    """Build the payload that `push_signal()` consumes.

    Returned keys are exactly what `push_signal()` extracts and forwards
    to the wrapper. The runtime's POST /signals schema declares
    `additionalProperties: false`, so any other top-level fields would
    be silently dropped or rejected — and `address`/`scanner` are sourced
    from module-level constants in `push_signal()`, not from this dict.

    Server-side fields (not sent by the producer):
      - `address` — passed to push_signal from PANGOLIN_WALLET
      - `scanner` — passed to push_signal from SCANNER_NAME
      - `timestamp` — set by the runtime receiver
      - `factors` — set by the runtime receiver to {}

    All declared scanner fields go in `data` (Turbine v2.0.11 lesson —
    do NOT use a `meta` block). The composite `score` lives there too —
    Pangolin's composite is an unbounded integer; SignalItem.score is
    bounded 0..1, different semantics.

    v1.5: heldAssets list pushed to LLM gate so it can hard-skip
    duplicates as defense layer 2 (producer dedup is layer 1).
    """
    held_list = sorted(list(held_assets)) if held_assets else []
    return {
        "signalType": "PANGOLIN_FUNDING_FADE",
        "asset": c["token"],
        "direction": c["fade_direction"],
        "data": {
            "mode": "FUNDING_FADE",
            "score": c["score"],
            "funding": c["funding"],
            "annualizedPct": round(c["annualized_pct"], 2),
            "persistenceHours": round(c["persistence_hours"], 1),
            "leverage": leverage,
            "marginUsd": margin_usd,
            "crowdDirection": c["crowd_direction"],
            "regime": c.get("regime") or "UNKNOWN",
            "regimeConfirms": bool(c.get("regime_confirms", False)),
            "trend": c.get("trend") or "STABLE",
            "smPctOfTopTraders": float(c.get("sm_pct", 0)),
            "smTraderCount": int(c.get("sm_traders", 0)),
            "smDirection": c.get("sm_direction") or "",
            "priceChg4hPct": float(c.get("p4h", 0)),
            "oiUsd": float(c.get("oi_usd", 0)),
            "oiTurnover": round(c.get("oi_turnover", 0), 3),
            "reasons": " | ".join(c.get("reasons", [])),
            "heldAssets": held_list,    # v1.5: dedup defense layer 2 (LLM gate)
        },
    }


def push_signal(payload):
    """Push a signal payload to the runtime via the wrapper.

    Direct HTTP POST to the runtime API on 127.0.0.1; no subprocess.

    Returns None on success. Raises:
      - RuntimeError if PANGOLIN_WALLET is not set (wrapper would 400 anyway)
      - SenpiClientError on schema-rejection (e.g. runtime declined the
        item with success=false)
      - urllib.error.URLError / HTTPError on transport failure
    The daemon's `_run_main_safely` catches everything and proceeds to
    the next tick — no half-written caller state.

    The producer's `build_signal_payload` returns a Signal-shaped dict.
    Map to the SignalItem schema:
      - top-level: address, scanner, asset, direction, signal_type (routing)
      - data: scanner-config-validated fields only
    `score` stays inside data (Pangolin's composite is an unbounded
    integer; SignalItem.score is bounded 0..1 — different semantics).
    `signal_type` is passed explicitly per signal so the runtime never
    relies on the scanner's defaultSignalType fallback (pangolin's
    runtime.yaml does not declare one). Keeps audit logs and the LLM
    decision context tagged correctly.
    """
    if not PANGOLIN_WALLET:
        raise RuntimeError("PANGOLIN_WALLET env var not set; cannot push signal")
    cfg._wrapper_client.push_signal(
        address=PANGOLIN_WALLET,
        scanner=SCANNER_NAME,
        asset=payload.get("asset"),
        direction=payload.get("direction"),
        signal_type=payload.get("signalType"),
        data=payload.get("data"),
    )


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    # Fail loud if wallet not configured (Turbine v2.0.9 pattern).
    if not PANGOLIN_WALLET:
        cfg.output({
            "status": "error",
            "error": "PANGOLIN_WALLET env var not set. Set it to the Pangolin strategy wallet (must match runtime.yaml).",
            "_pangolin_producer_version": "2.2.0",
        })
        return

    # Reentrancy is enforced by producer_daemon via scanner_lock — see
    # the REENTRANCY GUARD comment block. Single-call form below.
    # (Previously this body was wrapped in try/finally to release the
    # legacy fcntl lock — both the try wrapper and the lock are gone.)

    # Read account value for dynamic-cap math + sizing
    # v1.5: also pull held assets for emission-side dedup.
    account_value, pos_count, held_assets = get_account_value()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_pangolin_producer_version": "2.2.0",
        })
        return

    # v2.1.2: detect close events by diffing held_assets against
    # last tick's held set. Anything that disappeared just closed.
    # Record close timestamp into last-closed.json so the next
    # candidate filter can apply post-close cooldown.
    previously_held = load_previously_held()
    closed_this_tick = detect_closes(held_assets, previously_held)
    save_previously_held(held_assets)

    # Producer-side dynamic daily cap (v1 carryover; runtime ceiling at 12)
    tc = load_trade_counter()
    dyn_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dyn_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "status": "ok",
            "note": f"dynamic cap reached: entries {tc.get('entries')}/{dyn_cap} (PnL {pnl_pct:+.1f}%)",
            "_pangolin_producer_version": "2.2.0",
        })
        return

    # Scan funding extremes
    candidates, regime = scan_funding_extremes()
    if not candidates:
        cfg.output({
            "status": "ok",
            "note": f"no candidates passed gates (regime={regime})",
            "_pangolin_producer_version": "2.2.0",
        })
        return

    # v1.5: held-asset dedup is the FIRST filter. Producer must
    # never emit a duplicate signal on an already-held position;
    # this was the runaway-emission bug that ballooned MAVIA
    # short from -38k → -79k size in 24h on 2026-05-02.
    def _asset_already_held(token):
        if not held_assets:
            return False
        t = str(token).upper()
        if t in held_assets:
            return True
        # Tolerate "VENUE:TOKEN" or namespaced variants (defensive)
        if ":" in t and t.split(":", 1)[1] in held_assets:
            return True
        return False

    # v2.1.2: filter order — held > post-close-cooldown > emit-cooldown > score.
    # Held first (cheapest, prevents pyramiding); post-close blocks
    # the thrash-cycle bug (asset just closed, runtime per-asset
    # cooldown isn't enforcing); emit-cooldown is the v1 emission
    # debounce; min score is last.
    eligible = []
    skipped_held = 0
    skipped_post_close = 0
    post_close_skips = []   # observability detail per skipped token
    for c in candidates:
        if c["score"] < MIN_SCORE:
            continue
        if _asset_already_held(c["token"]):
            skipped_held += 1
            continue
        if is_in_post_close_cooldown(c["token"]):
            skipped_post_close += 1
            post_close_skips.append({
                "token": c["token"],
                "remaining_min": post_close_cooldown_remaining_min(c["token"]),
            })
            continue
        if is_asset_cooled_down(c["token"]):
            continue
        eligible.append(c)

    if not eligible:
        best = candidates[0]
        cfg.output({
            "status": "ok",
            "note": f"no candidates passed filters. best={best['token']} score={best['score']} regime={regime}",
            "skipped_held": skipped_held,
            "skipped_post_close": skipped_post_close,
            "post_close_skips": post_close_skips,
            "closed_this_tick": sorted(list(closed_this_tick)),
            "held_assets": sorted(list(held_assets)),
            "_pangolin_producer_version": "2.2.0",
        })
        return

    # Compute sizing (25% of account value)
    margin_usd = round(account_value * MARGIN_PCT, 2)

    # Emit signals (cap at remaining slots × dyn cap)
    # Conservative: emit only the TOP candidate per tick (v1 behavior).
    # The runtime's slot logic + per-asset cooldown handles parallelism.
    pushed = 0
    for c in eligible[:1]:    # only top candidate per tick
        leverage = get_leverage_for_score(c["score"])
        payload = build_signal_payload(c, leverage, margin_usd, held_assets)
        # push_signal raises on failure (caught by daemon._run_main_safely
        # at the tick boundary), so reaching the next line means the runtime
        # accepted the signal. State mutations stay atomic per signal.
        push_signal(payload)
        pushed += 1
        mark_asset_emitted(c["token"])
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

    elapsed = time.time() - run_start
    warn = "WARN_OVER_300S" if elapsed > 300 else None
    cfg.output({
        "status": "ok",
        "regime": regime,
        "candidates_total": len(candidates),
        "eligible": len(eligible),
        "skipped_held": skipped_held,
        "skipped_post_close": skipped_post_close,
        "post_close_skips": post_close_skips,
        "closed_this_tick": sorted(list(closed_this_tick)),
        "held_assets": sorted(list(held_assets)),
        "signals_pushed": pushed,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "dynamic_cap": dyn_cap,
        "entries_today": tc.get("entries", 0),
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_pangolin_producer_version": "2.2.0",
    })


if __name__ == "__main__":
    # Long-lived daemon: fires `main()` every 5 min. Replaces openclaw
    # cron + per-tick agentTurn for this skill — no LLM coupling, no CLI
    # cold start. producer_daemon wraps each tick in scanner_lock so
    # overlap is skipped cleanly, enforces a 6-min wall-clock per-tick
    # via SIGALRM (longer than the producer's WARN_OVER_300S budget so
    # the warn fires before the alarm), and drains gracefully on
    # SIGTERM/SIGINT.
    #
    # Lock name is per-wallet so multi-wallet hosts (one daemon per
    # wallet) don't collide on a single global lock file.
    from senpi_runtime_helpers import producer_daemon
    _wallet_lock_id = (
        hashlib.sha256(PANGOLIN_WALLET.lower().encode()).hexdigest()[:12]
        if PANGOLIN_WALLET
        else "unset"
    )
    # Pass `fn=main` directly. producer_daemon's per-tick error handler
    # catches exceptions, logs `daemon_tick_finished status=error` with the
    # error text, and continues to the next tick — the wrapper observability
    # path. A local `_run_main_safely` shim that swallowed exceptions before
    # the daemon saw them would mask failures as `status=ok` (caught by
    # bugbot on PR #208).
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"pangolin-producer-{_wallet_lock_id}",
        wallet=PANGOLIN_WALLET,        # enables default alive_check — daemon
        scanner="pangolin_signals",    # self-terminates if the runtime is deleted
        tick_timeout=360,              # OR if the scanner is dropped/renamed
    )
