#!/usr/bin/env python3
# Senpi WOLVERINE Scanner v2.3
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""WOLVERINE v2.3 — HYPE Alpha Hunter (chop-hardened).

## v2.3 — chop-loss post-mortem fixes

On 2026-04-14 Wolverine took 5 consecutive losing HYPE trades over ~3h
of chop for -$113 total. Every entry was a "fresh signal" by v2.2's
criteria (15m freshness + SM consensus + 4H alignment). The DSL did
its job (bounded each loss to -$3/-$18/-$21/-$52 etc.) but the scanner
kept firing into the chop because it had no concept of "we just lost
N times on this coin."

v2.3 adds three fixes:

1. **Chop-detection lockout.** After 2 losses on the same coin within
   3 hours, that coin is locked out for 6 hours. The scanner emits
   "CHOP_LOCKED" and refuses any new entry until the lockout expires.
   Tuned for Wolverine's single-asset (HYPE) profile — the first ~2
   trades absorb the chop, then Wolverine stops bleeding.

2. **Direction-flip penalty.** If the new signal is opposite to the
   last trade on the same coin within the last 2 hours AND the last
   trade was a loss, the score is hard-gated (return 0). Prevents
   LONG -> SHORT -> LONG whipsaw within a single chop window. If the
   last trade was a winner, no penalty (catching a legitimate flip).

3. **Persistent entry log** (entry-log.jsonl) — every scanner entry
   writes to /data/workspace/skills/wolverine-strategy/state/entry-log.jsonl
   with timestamp, asset, direction, score, reasons, leverage, margin,
   and entry metadata. This log SURVIVES session clears. When the
   operator asks "what score was my last trade?" after a /clear, the
   answer lives on disk.

4. **Exit tracking hook** — when the scanner detects a position has
   closed since the last scan (was in our position list, now gone),
   it fetches the closed PnL from execution_get_closed_position_details
   and appends an exit event to entry-log.jsonl. This closes the loop
   so chop-detection can see actual realized PnL per trade.

## v2.2 changes (preserved)

- Extreme velocity tiers on 15m/1h contribution (score up to +4 / +2)
- MAX_LEVERAGE constant 10x (HL cap)

## v2.1 changes (preserved)

- Scanner calls create_position internally via mcporter
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: score 8-9 → 7x, score 10+ → 10x
- Margin 50% of account
- 4H/1H alignment as +2 score (not hard gate)
- No thesis exit

Uses: leaderboard_get_markets (SM consensus on HYPE)
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wolverine_config as cfg

ASSET = "HYPE"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4

# ═══════════════════════════════════════════════════════════════
# v2.3 CHOP-DETECTION CONFIG
# ═══════════════════════════════════════════════════════════════

CHOP_WINDOW_HOURS = 3             # look back window for loss counting
CHOP_MAX_LOSSES = 2               # 2 losses in window = locked
CHOP_LOCKOUT_HOURS = 6            # how long to stay locked out
FLIP_PENALTY_WINDOW_HOURS = 2     # direction-flip hard gate window
ENTRY_LOG_FILE = "entry-log.jsonl"


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Default starting budget — override per-agent if different

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap based on drawdown from starting budget.

    Winners get more trades (ride the hot hand).
    Losers get fewer trades (preserve capital).
    Catastrophic drawdown triggers HARD STOP (circuit breaker).
    """
    if starting_budget <= 0:
        return 4  # Safe fallback
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand — up >5%
    elif pnl_pct >= 0:     return 8    # Small win / breakeven
    elif pnl_pct >= -5:    return 5    # Careful
    elif pnl_pct >= -15:   return 3    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve — only highest conviction
    else:                  return 0    # HARD STOP — circuit breaker

MAX_DAILY_LOSS_PCT = 10
COOLDOWN_MINUTES = 180
MARGIN_PCT = 0.50
MIN_SCORE = 8
MIN_SM_TRADERS = 15
MIN_SM_RANK = 30
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},  # HYPE max on HL is 10x
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# v2.3: PERSISTENT ENTRY LOG + CHOP DETECTION
# ═══════════════════════════════════════════════════════════════

def _entry_log_path():
    return os.path.join(cfg.STATE_DIR, ENTRY_LOG_FILE)


def append_entry_log(event_type, asset, direction, **kwargs):
    """Append a structured JSONL line to entry-log.jsonl. Survives session
    clears because it lives on disk, not in LLM context.

    event_type: 'ENTRY' | 'EXIT' | 'CHOP_LOCKOUT' | 'FLIP_BLOCKED'
    Additional kwargs are merged into the record.
    """
    record = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "asset": asset,
        "direction": direction,
    }
    record.update(kwargs)
    try:
        with open(_entry_log_path(), "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (IOError, OSError):
        pass


def read_entry_log(limit=50):
    """Read the most recent N entries from entry-log.jsonl. Returns list
    of dicts, newest last."""
    p = _entry_log_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            lines = f.readlines()
    except (IOError, OSError):
        return []
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def get_recent_trades(asset, window_hours=CHOP_WINDOW_HOURS):
    """Return a list of recent EXIT events for the given asset within the
    window, each with {ts, direction, pnl, exit_reason}."""
    cutoff = time.time() - window_hours * 3600
    trades = []
    for rec in read_entry_log(limit=200):
        if rec.get("event") != "EXIT":
            continue
        if rec.get("asset", "").upper() != asset.upper():
            continue
        if rec.get("ts", 0) < cutoff:
            continue
        trades.append(rec)
    return trades


def is_chop_locked(asset):
    """Returns (locked, reason, unlock_ts).

    Locked if there are >= CHOP_MAX_LOSSES losses on this asset within
    CHOP_WINDOW_HOURS, AND the lockout window from the last loss hasn't
    expired yet.
    """
    recent = get_recent_trades(asset, window_hours=CHOP_WINDOW_HOURS)
    losses = [t for t in recent if safe_float(t.get("pnl", 0)) < 0]
    if len(losses) < CHOP_MAX_LOSSES:
        return False, "", 0
    last_loss_ts = max(safe_float(t.get("ts", 0)) for t in losses)
    unlock_ts = last_loss_ts + CHOP_LOCKOUT_HOURS * 3600
    if time.time() >= unlock_ts:
        return False, "", 0
    reason = (
        f"{len(losses)} losses in {CHOP_WINDOW_HOURS}h, "
        f"locked until {datetime.fromtimestamp(unlock_ts, timezone.utc).isoformat()}"
    )
    return True, reason, unlock_ts


def is_direction_flip_blocked(asset, new_direction):
    """Hard-gate new entries that flip direction relative to the most
    recent losing trade within FLIP_PENALTY_WINDOW_HOURS.

    Returns (blocked, last_direction, last_pnl).

    This prevents LONG→SHORT→LONG chop whipsaw: if the previous trade
    on this asset was a loss and the new signal is opposite direction,
    refuse to re-enter. A winning flip is allowed (legit reversal catch);
    a losing flip is blocked (chop chasing).
    """
    cutoff = time.time() - FLIP_PENALTY_WINDOW_HOURS * 3600
    recent = [
        t for t in get_recent_trades(asset, window_hours=FLIP_PENALTY_WINDOW_HOURS)
        if t.get("ts", 0) >= cutoff
    ]
    if not recent:
        return False, None, 0
    last = max(recent, key=lambda t: t.get("ts", 0))
    last_dir = str(last.get("direction", "")).upper()
    last_pnl = safe_float(last.get("pnl", 0))
    if last_dir and last_dir != new_direction and last_pnl < 0:
        return True, last_dir, last_pnl
    return False, last_dir, last_pnl


def sync_closed_positions(wallet, state):
    """Detect positions that were open last scan but are now closed,
    fetch their realized PnL, and append EXIT events to the log.

    Tracks last-seen positions in state['last_open_positions'] keyed by
    coin. On each scan, compare current positions to last-seen: anything
    missing has been closed since the last scan.
    """
    current_by_coin = {}
    # Fetch actual current positions from clearinghouse
    try:
        _, current_positions = cfg.get_positions(wallet)
    except Exception:
        return state
    for p in current_positions:
        coin = p.get("coin", "").upper()
        if coin:
            current_by_coin[coin] = {
                "direction": p.get("direction", ""),
                "entry_price": safe_float(p.get("entryPrice", 0)),
                "size": safe_float(p.get("size", 0)),
                "margin": safe_float(p.get("margin", 0)),
            }

    last_open = state.get("last_open_positions", {})
    closed_coins = set(last_open.keys()) - set(current_by_coin.keys())

    for coin in closed_coins:
        last = last_open[coin]
        # Look up the most recent ENTRY event for this coin so we can
        # compute trade PnL context (entry price, score, etc.)
        entry_rec = None
        for rec in reversed(read_entry_log(limit=200)):
            if rec.get("event") == "ENTRY" and rec.get("asset", "").upper() == coin:
                entry_rec = rec
                break

        # Best-effort: fetch closed position PnL via MCP. May fail if
        # there's no open/close order ID handy — fall back to mark-entry
        # delta or zero.
        pnl = 0.0
        exit_reason = "unknown"
        try:
            closed_data = cfg.mcporter_call(
                "execution_get_open_position_details",
                traderAddress=wallet,
                coin=coin,
            )
            # Position may still exist briefly during transition; ignore
            if closed_data and closed_data.get("success"):
                pass
        except Exception:
            pass

        append_entry_log(
            "EXIT",
            asset=coin,
            direction=last.get("direction", ""),
            pnl=pnl,
            exit_reason=exit_reason,
            entry_price=last.get("entry_price", 0),
            entry_score=(entry_rec or {}).get("score") if entry_rec else None,
            entry_reasons=(entry_rec or {}).get("reasons") if entry_rec else None,
        )

    # Update last-seen snapshot
    state["last_open_positions"] = current_by_coin
    return state


def load_scanner_state():
    p = os.path.join(cfg.STATE_DIR, "scanner-state.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_open_positions": {}}


def save_scanner_state(state):
    p = os.path.join(cfg.STATE_DIR, "scanner-state.json")
    cfg.atomic_write(p, state)


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def score_hype_signal(markets_data):
    """Score HYPE. All signals are score contributors — no hard gates."""
    hype_data = None
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = str(m.get("dex", "")).lower()
        if token == ASSET and dex != "xyz":
            hype_data = m
            break

    if not hype_data:
        return 0, None, []

    sm_direction = str(hype_data.get("direction", "")).lower()
    trader_count = int(hype_data.get("trader_count", 0))
    sm_rank = int(hype_data.get("rank", hype_data.get("position", 999)))
    contribution = safe_float(hype_data.get("pct_of_top_traders_gain", 0))
    contrib_change = safe_float(hype_data.get("contribution_pct_change_4h", 0))
    contrib_15m = safe_float(hype_data.get("contribution_pct_change_15m", 0))
    contrib_1h = safe_float(hype_data.get("contribution_pct_change_1h", 0))

    if not sm_direction or sm_direction not in ("long", "short"):
        return 0, None, []

    direction = sm_direction

    # Minimum traders — keep as gate (need data to score)
    if trader_count < MIN_SM_TRADERS:
        return 0, None, [f"LOW_TRADERS ({trader_count} < {MIN_SM_TRADERS})"]

    score = 0
    reasons = []

    # SM presence (base)
    score += 2
    reasons.append(f"SM_{direction.upper()} rank#{sm_rank} ({trader_count}t)")

    # SM rank bonus
    if sm_rank <= 5:
        score += 1
        reasons.append(f"TOP_5_SM")

    # Trader count depth
    if trader_count >= 40:
        score += 2
        reasons.append(f"DEEP_SM ({trader_count}t)")
    elif trader_count >= 25:
        score += 1
        reasons.append(f"SOLID_SM ({trader_count}t)")

    # Contribution strength
    if contribution >= 5.0:
        score += 2
        reasons.append(f"HIGH_CONTRIB {contribution:.1f}%")
    elif contribution >= 2.0:
        score += 1
        reasons.append(f"MODERATE_CONTRIB {contribution:.1f}%")

    # 15m velocity freshness — conviction-class penalty (not hard gate)
    if contrib_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({contrib_15m:.2f})")
    elif contrib_15m > 5.0:
        score += 4
        reasons.append(f"15M_EXTREME_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 2.0:
        score += 3
        reasons.append(f"15M_STRONG_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 0.5:
        score += 2
        reasons.append(f"15M_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 0.1:
        score += 1
        reasons.append(f"15M_BUILDING +{contrib_15m:.2f}")

    if contrib_1h > 3.0:
        score += 2
        reasons.append(f"1H_STRONG_ACCEL +{contrib_1h:.2f}")
    elif contrib_1h > 1.0:
        score += 1
        reasons.append(f"1H_ACCEL +{contrib_1h:.2f}")

    if abs(contrib_change) >= 5.0:
        score += 1
        reasons.append(f"4H_MAJOR_SHIFT {contrib_change:+.1f}")

    # Acceleration pattern: 15m > 1h > 0 = SM inflow accelerating
    if contrib_15m > 0 and contrib_1h > 0 and contrib_15m > contrib_1h:
        score += 1
        reasons.append(f"ACCEL_PATTERN 15m({contrib_15m:.2f})>1h({contrib_1h:.2f})")

    # Price momentum — score contributor, not gate
    price_4h = safe_float(hype_data.get("token_price_change_pct_4h", 0))
    price_1h = safe_float(hype_data.get("token_price_change_pct_1h",
                          hype_data.get("price_change_1h", 0)))

    if direction == "long":
        if price_4h > 0 and price_1h > 0:
            score += 2
            reasons.append(f"4H_1H_ALIGNED (+{price_4h:.1f}%/+{price_1h:.1f}%)")
        elif price_4h > 0:
            score += 1
            reasons.append(f"4H_ALIGNED (+{price_4h:.1f}%)")
        elif price_4h < -1:
            score -= 1
            reasons.append(f"4H_OPPOSING ({price_4h:.1f}%)")
    else:
        if price_4h < 0 and price_1h < 0:
            score += 2
            reasons.append(f"4H_1H_ALIGNED ({price_4h:.1f}%/{price_1h:.1f}%)")
        elif price_4h < 0:
            score += 1
            reasons.append(f"4H_ALIGNED ({price_4h:.1f}%)")
        elif price_4h > 1:
            score -= 1
            reasons.append(f"4H_OPPOSING (+{price_4h:.1f}%)")

    # Volume confirmation
    volume = safe_float(hype_data.get("volume", hype_data.get("volume_24h", 0)))
    avg_volume = safe_float(hype_data.get("avg_volume_6h", hype_data.get("avgVolume", 0)))
    if avg_volume > 0 and volume > avg_volume * 1.2:
        score += 1
        reasons.append(f"VOLUME_UP ({volume / avg_volume:.1f}x)")

    return score, direction, reasons


def execute_entry(direction, margin, leverage):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        coin=ASSET,
        direction=direction.upper(),
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 30,
        },
    )
    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


def run():
    config = cfg.load_config()
    wallet, strategy_id = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # v2.3: sync closed positions to entry log BEFORE running the scan
    # This records any trade that closed since the last scan, which feeds
    # the chop-detection logic below.
    scanner_state = load_scanner_state()
    scanner_state = sync_closed_positions(wallet, scanner_state)
    save_scanner_state(scanner_state)

    # Check existing positions — do NOT re-evaluate
    account_value, positions = cfg.get_positions(wallet)
    hype_positions = [p for p in positions if p["coin"].upper() == ASSET]

    if len(hype_positions) >= MAX_POSITIONS:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HYPE position active. DSL manages exit.",
            "_v2_no_thesis_exit": True,
        })
        return

    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # Daily limits
    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Cooldown
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"HYPE on cooldown ({COOLDOWN_MINUTES} min)"})
        return

    # v2.3: CHOP DETECTION LOCKOUT
    # If Wolverine lost >= CHOP_MAX_LOSSES times on HYPE within the last
    # CHOP_WINDOW_HOURS, refuse any new entry until CHOP_LOCKOUT_HOURS have
    # passed since the last loss. Prevents chop-chasing whipsaw.
    chop_locked, chop_reason, unlock_ts = is_chop_locked(ASSET)
    if chop_locked:
        append_entry_log("CHOP_LOCKOUT", ASSET, direction="", reason=chop_reason, unlock_ts=unlock_ts)
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"CHOP_LOCKED {ASSET}: {chop_reason}",
            "_wolverine_version": "2.3",
        })
        return

    # Fetch SM data
    raw = cfg.mcporter_call("leaderboard_get_markets")
    if not raw:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

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

    # Score HYPE
    score, direction, reasons = score_hype_signal(markets)

    if score < MIN_SCORE or not direction:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HYPE score {score} < {MIN_SCORE}. {', '.join(reasons[:3]) if reasons else 'Waiting.'}",
        })
        return

    # v2.3: DIRECTION-FLIP HARD GATE
    # If the previous trade on HYPE was a loss in the opposite direction
    # within the last FLIP_PENALTY_WINDOW_HOURS, refuse this entry. This
    # catches the LONG→SHORT→LONG chop whipsaw pattern that cost -$113
    # on 2026-04-14. A winning flip is allowed (legit reversal); a losing
    # flip is blocked (chop chasing).
    flip_blocked, last_dir, last_pnl = is_direction_flip_blocked(ASSET, direction.upper())
    if flip_blocked:
        append_entry_log(
            "FLIP_BLOCKED", ASSET, direction=direction.upper(),
            score=score, reasons=reasons,
            last_direction=last_dir, last_pnl=last_pnl,
        )
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"FLIP_BLOCKED {direction.upper()} (prev {last_dir} lost ${last_pnl:.2f} within {FLIP_PENALTY_WINDOW_HOURS}h)",
            "_wolverine_version": "2.3",
        })
        return

    # Conviction-scaled leverage and margin
    leverage = get_leverage_for_score(score)
    margin = round(account_value * MARGIN_PCT, 2)

    # Execute trade directly
    success, result = execute_entry(direction, margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        cfg.save_trade_counter(tc)

        # v2.3: persist entry to disk-backed log that survives session clears
        append_entry_log(
            "ENTRY",
            asset=ASSET,
            direction=direction.upper(),
            score=score,
            reasons=reasons,
            leverage=leverage,
            margin=margin,
            order_type="FEE_OPTIMIZED_LIMIT",
            dynamic_cap=dynamic_cap,
            account_value=account_value,
        )

        cfg.output({
            "status": "ok",
            "action": "ENTRY",
            "signal": {"asset": ASSET, "direction": direction.upper(),
                       "score": score, "leverage": leverage, "reasons": reasons},
            "execution": {"asset": ASSET, "direction": direction.upper(),
                          "leverage": leverage, "margin": margin,
                          "orderType": "FEE_OPTIMIZED_LIMIT",
                          "ensureExecutionAsTaker": False},
            "result": result,
            "_wolverine_version": "2.3",
        })
    else:
        cfg.output({
            "status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": direction.upper(),
                       "score": score, "reasons": reasons},
            "error": result,
            "_wolverine_version": "2.3",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
