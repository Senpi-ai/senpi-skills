#!/usr/bin/env python3
# Senpi KOALA Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""KOALA v1.0.0 — Set-and-Forget Trail HODL.

The simplest possible Senpi agent. Pick an asset (default BTC). Fire LONG
once on deploy. Hold with an ultra-wide DSL trail. Done.

No scoring. No multi-timeframe analysis. No SM gates. No funding regime.
Just: "is the configured asset currently held by this wallet? If not, fire
a LONG signal at fixed margin / leverage."

If `fireOnceMode` is true (default), Koala fires ONCE EVER (lifetime). The
state file tracks first_entry_at; subsequent ticks see the entry and stay
silent even after the DSL eventually exits.

If `fireOnceMode` is false, Koala will re-enter after `reEntryCooldownHours`
since the last exit — useful for operators who want a "buy / DSL-exits /
buy again" cycle.

Producer NEVER closes — the wide DSL (max_loss 30%, retrace 25, 90d
hard_timeout) is the entire exit logic. Built for operators whose entire
trading thesis is "I want to own BTC and have a safety net."
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import koala_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "koala_signals"
SIGNAL_TYPE = "KOALA_HODL"

DEFAULT_ASSET = "BTC"
DEFAULT_LEVERAGE = 2
MAX_LEVERAGE = 3              # Koala is HODL, not gambling
DEFAULT_MARGIN_PCT = 0.50     # 50% of equity — one chunky position
DEFAULT_FIRE_ONCE = True
DEFAULT_RE_ENTRY_COOLDOWN_HOURS = 168.0   # 7d if re-entry is enabled


def _resolve_wallet():
    env_val = (os.environ.get("KOALA_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Pure entry-decision logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def should_enter(state, fire_once, re_entry_cooldown_hours, now_ts):
    """Decide whether Koala should emit an entry signal RIGHT NOW.

    - state: koala-state.json contents
    - fire_once: True → only one entry ever
    - re_entry_cooldown_hours: minimum hours between exit and next entry
                              (only relevant if fire_once is False)
    - now_ts: epoch seconds (current time)

    Returns True if Koala should emit; False otherwise.
    """
    first_entry_at = state.get("first_entry_at") if state else None

    if fire_once:
        # Lifetime one-shot — fire if and only if we've never entered.
        return first_entry_at is None

    # Re-entry allowed. Two conditions for "should fire":
    #   1. Never entered → fire immediately.
    #   2. Last exit happened ≥ cooldown ago.
    if first_entry_at is None:
        return True

    last_exit_at = state.get("last_exit_at") if state else None
    if last_exit_at is None:
        # We've entered before but never recorded an exit → position must
        # still be held (or the close went undetected). Don't fire again.
        return False

    try:
        last_exit = float(last_exit_at)
    except (TypeError, ValueError):
        return False

    cooldown_sec = float(re_entry_cooldown_hours) * 3600.0
    return (now_ts - last_exit) >= cooldown_sec


def record_entry(state, now_ts):
    """Pure: return a new state dict with the entry recorded."""
    new_state = dict(state or {})
    if not new_state.get("first_entry_at"):
        new_state["first_entry_at"] = float(now_ts)
    new_state["last_entry_at"] = float(now_ts)
    new_state["total_entries"] = int(new_state.get("total_entries", 0)) + 1
    return new_state


def record_exit(state, now_ts):
    """Pure: return a new state dict with the exit recorded."""
    new_state = dict(state or {})
    new_state["last_exit_at"] = float(now_ts)
    return new_state


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(asset, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    if asset.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": 5,    # fixed — Koala has no scoring; this satisfies the schema
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": "LONG",
        "reasons": ["hodl_first_entry"],
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=asset,
            direction="LONG",
            score=0.7,
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {asset}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {asset}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — check state, fire if eligible
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    asset = str(config.get("asset", DEFAULT_ASSET)).upper()
    fire_once = bool(config.get("fireOnceMode", DEFAULT_FIRE_ONCE))
    cooldown_hours = float(config.get("reEntryCooldownHours", DEFAULT_RE_ENTRY_COOLDOWN_HOURS))

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_koala_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_koala_producer_version": VERSION})
        return

    state = cfg.read_koala_state()
    now = time.time()

    # Detect a closed position: if Koala had a first_entry but the asset is
    # no longer held, log the exit.
    if state.get("first_entry_at") and asset not in held_set and state.get("last_exit_at") is None:
        # An exit happened since the last tick — record it.
        state = record_exit(state, now)
        cfg.write_koala_state(state)

    # If currently held → do nothing (DSL is in charge)
    if asset in held_set:
        cfg.output({
            "status": "ok",
            "note": f"HOLDING — {asset} is currently in the position; DSL owns exits",
            "asset": asset,
            "first_entry_at": state.get("first_entry_at"),
            "total_entries": state.get("total_entries", 0),
            "elapsed_sec": round(time.time() - run_start, 2),
            "_koala_producer_version": VERSION,
        })
        return

    if not should_enter(state, fire_once, cooldown_hours, now):
        # Decision: not eligible. Output the reason for ops visibility.
        if fire_once:
            reason = f"fire-once mode AND already entered once (first_entry_at={state.get('first_entry_at')})"
        else:
            last_exit = state.get("last_exit_at")
            if last_exit is None:
                reason = "re-entry mode but no exit recorded yet (position state ambiguous)"
            else:
                wait_left_h = max(0, (cooldown_hours * 3600 - (now - float(last_exit))) / 3600.0)
                reason = f"re-entry cooldown active ({wait_left_h:.1f}h remaining)"
        cfg.output({
            "status": "ok",
            "note": f"WAITING — {reason}",
            "asset": asset,
            "fire_once_mode": fire_once,
            "state": state,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_koala_producer_version": VERSION,
        })
        return

    if cfg.was_recently_signaled(asset):
        cfg.output({
            "status": "ok",
            "note": "WAITING — recently signaled (race-window dedup)",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_koala_producer_version": VERSION,
        })
        return

    margin_pct = float(config.get("marginPct", DEFAULT_MARGIN_PCT))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(asset, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(asset)
        state = record_entry(state, now)
        # Clear last_exit_at — new lifecycle started
        state["last_exit_at"] = None
        cfg.write_koala_state(state)

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": asset,
            "direction": "LONG",
            "leverage": leverage,
            "margin_usd": margin_usd,
            "first_entry_at": state.get("first_entry_at"),
            "total_entries": state.get("total_entries"),
        },
        "elapsed_sec": round(time.time() - run_start, 2),
        "_koala_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=1800,   # 30min — Koala has no urgency
        name=f"koala-producer-{_wallet_lock_id}",
        tick_timeout=120,
    )
