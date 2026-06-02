#!/usr/bin/env python3
# Senpi TORTOISE Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""TORTOISE v1.0.0 — DCA Scheduler.

"Slow and steady wins the race."

Tortoise buys a fixed % of budget on a strict time cadence (every
intervalHours) on a small basket — BTC alone, or BTC/ETH/SOL. No price
prediction, no scoring, no timing. Each tick:

  1. For each whitelisted asset, check how long since the last DCA buy.
  2. The asset that is MOST overdue (longest since last DCA, past the interval)
     is the candidate.
  3. If something is due → emit a LONG signal sized at marginPct of equity.
  4. Otherwise → silent. The DSL trail handles profit-taking on existing
     accumulation; Tortoise only handles entry.

Default direction: LONG. THE most accessible trade in crypto — zero prediction
skill required. Onboarding tier. Producer NEVER closes — the DSL owns exits.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tortoise_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "tortoise_signals"
SIGNAL_TYPE = "TORTOISE_DCA"

MAX_LEVERAGE = 3              # DCA is accumulation, not leverage
DEFAULT_LEVERAGE = 2
DEFAULT_DIRECTION = "LONG"    # DCA = accumulate longs

DEFAULT_ASSETS = ["BTC", "ETH", "SOL"]
DEFAULT_INTERVAL_HOURS = 24.0   # one DCA per asset per day
DEFAULT_MARGIN_PCT = 0.08       # 8% per buy → at 3 assets × 1/day, accumulates ~170%/week in MARGIN (account scales)


def _resolve_wallet():
    env_val = (os.environ.get("TORTOISE_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Pure DCA-scheduler logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def seconds_since(last_dca_ts, now_ts):
    """Seconds elapsed since the last DCA event. None for unknown
    (never-DCA'd) assets — they should be treated as MAXIMALLY overdue."""
    if last_dca_ts is None:
        return None
    try:
        return max(0.0, float(now_ts) - float(last_dca_ts))
    except (TypeError, ValueError):
        return None


def is_dca_due(elapsed_sec, interval_sec):
    """True if the DCA interval has elapsed OR this asset has never been
    DCA'd (elapsed_sec=None). Never-DCA'd assets are always due."""
    if elapsed_sec is None:
        return True
    return elapsed_sec >= interval_sec


def pick_next_dca_asset(assets, last_dca_by_asset, interval_sec, now_ts):
    """Among `assets`, pick the one that is most overdue (longest time since
    last DCA, past the interval). Never-DCA'd assets win over any DCA'd asset.
    Returns the asset symbol or None if nothing is due."""
    best_asset, best_elapsed = None, -1.0
    for asset in assets:
        key = str(asset).upper()
        last = last_dca_by_asset.get(key)
        elapsed = seconds_since(last, now_ts)
        if not is_dca_due(elapsed, interval_sec):
            continue
        # Never-DCA'd asset (elapsed is None) → treat as infinitely overdue
        # so it wins over any DCA'd asset. Use a sentinel sortable above any
        # real elapsed value.
        rank = float('inf') if elapsed is None else elapsed
        if rank > best_elapsed:
            best_elapsed = rank
            best_asset = key
    return best_asset


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(asset, margin_usd, leverage, held_assets, elapsed_sec, interval_sec):
    if not STRATEGY_ADDRESS:
        return False
    if asset.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": 5,        # producer-fixed (DCA has no scoring — every fire is "valid by cadence")
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": DEFAULT_DIRECTION,
        "reasons": [
            "dca_cadence",
            f"elapsed_{int(elapsed_sec or 0)}s",
            f"interval_{int(interval_sec)}s",
        ],
        "intervalSec": float(interval_sec),
        "elapsedSec": float(elapsed_sec or 0.0),
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=asset,
            direction=DEFAULT_DIRECTION,
            score=0.7,        # static — DCA conviction comes from cadence, not scoring
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
# MAIN — check cadence, pick the most-overdue, fire
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    assets = config.get("assets", DEFAULT_ASSETS)
    interval_hours = float(config.get("intervalHours", DEFAULT_INTERVAL_HOURS))
    interval_sec = interval_hours * 3600.0

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_tortoise_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_tortoise_producer_version": VERSION})
        return

    now = time.time()
    history = cfg.read_dca_history()
    # Filter: an asset already held is not a candidate (no duplicate stacking).
    eligible = [a for a in assets if str(a).upper() not in held_set and not cfg.was_recently_signaled(a)]

    chosen = pick_next_dca_asset(eligible, history, interval_sec, now)
    if chosen is None:
        next_due_in = next_due_seconds(eligible, history, interval_sec, now)
        cfg.output({
            "status": "ok",
            "note": "WAITING — no asset past its DCA interval (or all in cooldown/held)",
            "next_due_in_min": round(next_due_in / 60.0, 1) if next_due_in is not None else None,
            "assets": assets,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_tortoise_producer_version": VERSION,
        })
        return

    elapsed = seconds_since(history.get(chosen), now)
    margin_pct = float(config.get("marginPct", DEFAULT_MARGIN_PCT))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(chosen, margin_usd, leverage, held_assets, elapsed, interval_sec)
    if pushed:
        cfg.record_signal(chosen)
        cfg.record_dca(chosen, now)

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": chosen,
            "direction": DEFAULT_DIRECTION,
            "elapsed_hours": round((elapsed or 0) / 3600.0, 2),
            "interval_hours": interval_hours,
            "leverage": leverage,
            "margin_usd": margin_usd,
        },
        "assets": assets,
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_tortoise_producer_version": VERSION,
    })


def next_due_seconds(assets, history, interval_sec, now):
    """Seconds until the next eligible asset comes due (for the WAITING
    diagnostic). None if every asset is already past-due (then we should
    have fired)."""
    soonest = None
    for asset in assets:
        last = history.get(str(asset).upper())
        if last is None:
            return 0
        wait = (interval_sec - (now - last))
        if wait <= 0:
            return 0
        if soonest is None or wait < soonest:
            soonest = wait
    return soonest


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=1800,   # 30min — DCA is slow by design; cadence is the signal
        name=f"tortoise-producer-{_wallet_lock_id}",
        tick_timeout=120,
    )
