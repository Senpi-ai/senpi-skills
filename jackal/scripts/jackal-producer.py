#!/usr/bin/env python3
# Senpi JACKAL Producer v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""JACKAL v2.0 Producer — Smart-Stalker signal emitter for v2 runtime.

Jackal v1.1 (the scanner) ran as a full-agency Python scanner that:
  - Maintained a two-tier pool (watchlist + active)
  - Detected new positions opened by pool members
  - Scored them via its own heuristics
  - Called create_position + ratchet_stop_add directly

v2.0 splits that into two parts:
  1. This producer (runs on cron, 60s): emits candidate signals only
  2. Runtime (senpi-trading-runtime v2): receives signals via
     external_scanner ingest, gates through LLM decision_prompt,
     executes, and manages DSL autonomously.

The producer's single responsibility: fetch the active trader pool,
detect new entries, enrich with consensus + TA + funding regime
context, push a signal payload to the runtime via
`openclaw senpi external-scanner ingest`.

NO execution code. NO DSL code. NO risk gates. The runtime owns all of that.

Environment variables (standard v2 producer):
  SENPI_API_KEY     — for MCP access
  STRATEGY_ADDRESS  — Jackal v2 wallet (must match runtime YAML)
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "jackal_signals")
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jackal_config as cfg
import jackal_state as state


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD (v2.0.3 — Daniel's review)
# ═══════════════════════════════════════════════════════════════
# Cron fires every 60s. If a run takes longer (MCP latency × candidate
# count can push past 60s when many new entries appear), the next tick
# would start a second concurrent run. That races on last-seen.json —
# run B reads the pre-run-A state, re-detects the SAME entries, and
# pushes duplicate signals to the runtime.
# fcntl.LOCK_EX | LOCK_NB is non-blocking: if another run holds the
# lock, acquire_lock() returns None and we skip this tick cleanly.
# fcntl locks auto-release when the process dies, so crashes self-heal.

_LOCK_PATH = state.STATE_DIR / "producer.lock"


def acquire_lock():
    """Non-blocking exclusive lock. Returns file handle or None if held."""
    try:
        f = open(_LOCK_PATH, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()} {int(time.time())}\n")
        f.flush()
        return f
    except (IOError, OSError, BlockingIOError):
        return None


def release_lock(lock_file):
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_file.close()
    except Exception:
        pass


SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "jackal_signals")
STRATEGY_ADDRESS = os.environ.get("STRATEGY_ADDRESS", "")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")

# ═══════════════════════════════════════════════════════════════
# POOL CONFIG
# ═══════════════════════════════════════════════════════════════

# Smaller, sharper pool than v1. Top-ROI monthly with win-rate + age
# filters. Pool refreshes daily (run cron once at 00:00 UTC with
# REFRESH_POOL=true env var, or it rebuilds on first run of the day).
POOL_SIZE = 25
POOL_MIN_WIN_RATE = 0.50
POOL_MIN_TRADER_AGE_DAYS = 14
POOL_MIN_ROI_30D = 10.0  # %

# Only emit signals for entries that happened within this window.
# Beyond this, the alpha is already public and Jackal is late.
MAX_ENTRY_AGE_SECONDS = 600  # 10 min


# ═══════════════════════════════════════════════════════════════
# POOL MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def refresh_pool(force=False):
    """Refresh the active trader pool via discovery_get_top_traders.

    Caches in state/pool.json. Refreshes daily or when force=True.
    """
    pool_data = state.load_pool()
    now = time.time()
    last_refresh = pool_data.get("refreshed_at", 0)
    age_hours = (now - last_refresh) / 3600

    if not force and age_hours < 24 and pool_data.get("traders"):
        return pool_data["traders"]

    resp = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame="MONTHLY",
        sort_by="RETURN_ON_INVESTMENT",
        limit=60,  # overfetch then filter
    )
    if not resp:
        # Fall back to cached pool if refresh fails
        return pool_data.get("traders", [])

    raw = resp.get("data", resp)
    if isinstance(raw, dict):
        raw = raw.get("traders", raw.get("data", []))
    if not isinstance(raw, list):
        return pool_data.get("traders", [])

    filtered = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        win_rate = float(t.get("win_rate") or t.get("winRate") or 0)
        # v2.0.4: MCP returns returnOnInvestment (camelCase), not
        # return_on_investment. Also traderAgeSeconds not traderAgeDays.
        # Without these fallbacks, every trader scored ROI=0 and age=0,
        # producing a pool full of "0%-ROI 0-day-old" traders that
        # silently filtered out. Jackal found this live 2026-04-23.
        roi_30d = float(
            t.get("return_on_investment")
            or t.get("roi")
            or t.get("returnOnInvestment")
            or 0
        )
        age_days = float(
            t.get("trader_age_days")
            or t.get("traderAgeDays")
            or (t.get("traderAgeSeconds") or 0) / 86400
            or 0
        )
        address = t.get("address") or t.get("trader_address")
        if not address:
            continue
        if win_rate < POOL_MIN_WIN_RATE:
            continue
        if roi_30d < POOL_MIN_ROI_30D:
            continue
        if age_days < POOL_MIN_TRADER_AGE_DAYS:
            continue
        filtered.append({
            "address": address.lower(),
            "user_id": t.get("user_id") or t.get("userId"),
            "username": t.get("username") or t.get("userName"),
            "quality_score": _compute_quality_score(t),
            "win_rate": win_rate,
            "roi_30d": roi_30d,
            "trader_age_days": age_days,
            "consecutive_wins": int(t.get("consecutive_wins") or 0),
        })

    filtered.sort(key=lambda x: x["quality_score"], reverse=True)
    top = filtered[:POOL_SIZE]

    state.save_pool({
        "refreshed_at": now,
        "refreshed_iso": datetime.now(timezone.utc).isoformat(),
        "size": len(top),
        "traders": top,
    })
    return top


def _compute_quality_score(trader):
    """Composite quality score 0-100. Higher = better stalk target."""
    win_rate = float(trader.get("win_rate") or trader.get("winRate") or 0)
    roi_30d = float(
        trader.get("return_on_investment")
        or trader.get("roi")
        or trader.get("returnOnInvestment")
        or 0
    )
    age_days = float(
        trader.get("trader_age_days")
        or trader.get("traderAgeDays")
        or (trader.get("traderAgeSeconds") or 0) / 86400
        or 0
    )
    gain_to_pain = float(trader.get("gain_to_pain_ratio") or trader.get("gainToPainRatio") or 0)

    score = 0.0
    score += min(win_rate * 100, 60) * 0.4           # win rate max 60 pts weighted 40%
    score += min(roi_30d, 100) * 0.3                  # roi max 100 pts weighted 30%
    score += min(age_days / 90.0 * 20, 20) * 0.15    # age max 20 pts (caps at 90 days) weighted 15%
    score += min(gain_to_pain * 10, 20) * 0.15       # g/p max 20 pts weighted 15%
    return round(score, 2)


# ═══════════════════════════════════════════════════════════════
# POSITION DIFF DETECTION
# ═══════════════════════════════════════════════════════════════

def fetch_pool_positions(pool):
    """Fetch current open positions for every pool member in one MCP call."""
    addresses = [t["address"] for t in pool]
    if not addresses:
        return {}
    resp = cfg.mcporter_call(
        "discovery_get_trader_state",
        trader_addresses=addresses,
    )
    if not resp:
        return {}
    data = resp.get("data", resp)
    traders = data.get("traders", []) if isinstance(data, dict) else []
    by_addr = {}
    for t in traders:
        if not isinstance(t, dict):
            continue
        addr = (t.get("address") or "").lower()
        open_positions = t.get("openPositions") or []
        by_addr[addr] = open_positions
    return by_addr


def detect_new_entries(pool, current_positions, last_seen):
    """Diff current vs last-seen positions. Emit candidates for anything
    that appeared newly or flipped direction in the last MAX_ENTRY_AGE_SECONDS.
    """
    now = time.time()
    candidates = []
    for trader in pool:
        addr = trader["address"]
        cur = current_positions.get(addr, [])
        prev = last_seen.get(addr, [])
        # v2.0.4: MCP positions don't carry an explicit "direction" key;
        # derive it from the sign of szi. Without this, prev_keys always
        # contained (coin, None) which never matched current positions,
        # causing every current position to be re-detected as "new" on
        # every run.
        def _derive_key(p):
            coin = p.get("coin") or p.get("asset")
            szi = float(p.get("szi") or p.get("size") or 0)
            direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
            return (coin, direction)
        prev_keys = {_derive_key(p) for p in prev if isinstance(p, dict)}

        for pos in cur:
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            if not coin:
                continue
            # Normalize direction (LONG/SHORT)
            szi = float(pos.get("szi") or pos.get("size") or 0)
            direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
            if not direction:
                continue
            key = (coin, direction)
            if key in prev_keys:
                continue  # not new

            # Freshness gate: entry must be recent
            # v2.0.4: MCP returns startTime, not openedAtTs/openTime.
            entry_ts = float(
                pos.get("openedAtTs")
                or pos.get("openTime")
                or pos.get("startTime")
                or 0
            )
            if entry_ts > 0 and (now - entry_ts) > MAX_ENTRY_AGE_SECONDS:
                continue

            candidates.append({
                "trader": trader,
                "coin": coin,
                "direction": direction,
                "entry_price": float(pos.get("entryPx") or pos.get("entry_price") or 0),
                "leverage": float(
                    (pos.get("leverage") or {}).get("value")
                    if isinstance(pos.get("leverage"), dict)
                    else pos.get("leverage") or 0
                ),
                "size_usd": abs(float(pos.get("positionValue") or pos.get("notional") or 0)),
                "entry_ts": entry_ts or now,
            })
    return candidates


# ═══════════════════════════════════════════════════════════════
# SIGNAL ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def enrich_with_consensus(candidates, current_positions):
    """How many other pool members are in the SAME coin+direction?"""
    by_key = {}
    for addr, positions in current_positions.items():
        for pos in positions or []:
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            szi = float(pos.get("szi") or pos.get("size") or 0)
            direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
            if not coin or not direction:
                continue
            by_key.setdefault((coin, direction), set()).add(addr)

    for c in candidates:
        key = (c["coin"], c["direction"])
        # Exclude the candidate's own trader from consensus count
        consensus_set = by_key.get(key, set()) - {c["trader"]["address"]}
        c["pool_consensus_count"] = len(consensus_set)

        # Count same-asset, any-direction (conflicting consensus indicator)
        asset_addrs = set()
        for (coin, _), addrs in by_key.items():
            if coin == c["coin"]:
                asset_addrs.update(addrs)
        asset_addrs.discard(c["trader"]["address"])
        c["pool_consensus_asset_count"] = len(asset_addrs)
    return candidates


def fetch_funding_regime():
    """Fetch market-wide funding regime once per run (NOT per candidate)."""
    try:
        fr = cfg.mcporter_call("market_get_funding_regime")
        if fr:
            data = fr.get("data", fr)
            if isinstance(data, dict):
                return data.get("regime")
    except Exception:
        pass
    return None


def enrich_with_ta(candidate, funding_regime):
    """Pull 4h/1h/15m trend + asset funding for the candidate asset.

    Takes pre-fetched funding_regime as arg — do NOT call
    market_get_funding_regime here; it's a global per-run state and
    calling it per-candidate wastes 1 MCP call × N candidates per cron
    tick (Daniel's review, 2026-04-23).
    """
    coin = candidate["coin"]
    out = {
        "trend_4h": None,
        "trend_1h": None,
        "trend_15m": None,
        "price_change_4h_pct": None,
        "funding_regime": funding_regime,
        "funding_annualized_pct": None,
    }
    try:
        ad = cfg.mcporter_call(
            "market_get_asset_data",
            asset=coin,
            candle_intervals=["15m", "1h", "4h"],
            include_funding=True,
            include_order_book=False,
        )
        if ad:
            data = ad.get("data", ad)
            candles = data.get("candles", {}) if isinstance(data, dict) else {}
            out["price_change_4h_pct"] = _trend_pct(candles.get("4h"))
            out["trend_4h"] = _trend_label(out["price_change_4h_pct"])
            out["trend_1h"] = _trend_label(_trend_pct(candles.get("1h")))
            out["trend_15m"] = _trend_label(_trend_pct(candles.get("15m")))
            ac = data.get("asset_context") or data.get("assetContext") or {}
            if isinstance(ac, dict):
                funding = ac.get("funding")
                if funding is not None:
                    try:
                        f = float(funding)
                        out["funding_annualized_pct"] = round(f * 3 * 365 * 100, 2)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    return out


def _trend_pct(candles):
    if not candles or not isinstance(candles, list) or len(candles) < 2:
        return None
    try:
        open_price = float(candles[0].get("open") or candles[0].get("o") or 0)
        close_price = float(candles[-1].get("close") or candles[-1].get("c") or 0)
        if open_price <= 0:
            return None
        return round((close_price - open_price) / open_price * 100, 3)
    except (TypeError, ValueError):
        return None


def _trend_label(pct):
    if pct is None:
        return None
    if pct >= 0.3:
        return "BULLISH"
    if pct <= -0.3:
        return "BEARISH"
    return "NEUTRAL"


def fetch_btc_macro():
    """Context signal: where is BTC at 24h?"""
    try:
        ad = cfg.mcporter_call(
            "market_get_asset_data",
            asset="BTC",
            candle_intervals=["1h"],
            include_funding=False,
            include_order_book=False,
        )
        if not ad:
            return {"direction": None, "pct": None}
        data = ad.get("data", ad)
        candles_1h = (data.get("candles", {}) or {}).get("1h", [])
        if len(candles_1h) < 24:
            return {"direction": None, "pct": None}
        opens = [float(c.get("open") or c.get("o") or 0) for c in candles_1h[-24:]]
        closes = [float(c.get("close") or c.get("c") or 0) for c in candles_1h[-24:]]
        if opens[0] <= 0:
            return {"direction": None, "pct": None}
        pct = (closes[-1] - opens[0]) / opens[0] * 100
        direction = "UP" if pct > 0 else "DOWN"
        return {"direction": direction, "pct": round(pct, 2)}
    except Exception:
        return {"direction": None, "pct": None}


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(payload):
    """Push a signal payload to the runtime via CLI."""
    if not STRATEGY_ADDRESS:
        print("ERROR: STRATEGY_ADDRESS env var not set; cannot push signal", file=sys.stderr)
        return False

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", STRATEGY_ADDRESS,
        "--scanner", SCANNER_NAME,
        "--payload", json.dumps(payload),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            print(f"INGEST_FAILED: {result.stderr}", file=sys.stderr)
            return False
        response = json.loads(result.stdout) if result.stdout.strip() else {}
        if not response.get("ok", False):
            print(f"INGEST_REJECTED: {response.get('error', {})}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"INGEST_EXCEPTION: {e}", file=sys.stderr)
        return False


def build_signal_payload(candidate, ta, btc_macro):
    """Build the payload matching runtime.yaml's jackal_signals.config.fields schema."""
    trader = candidate["trader"]
    return {
        "asset": candidate["coin"],
        "direction": candidate["direction"],
        "score": trader.get("quality_score", 0) / 100.0,  # normalized 0-1
        "signal_type": "JACKAL_COPY_ENTRY",
        "data": {
            "sourceTraderAddress": trader["address"],
            "sourceTraderUserId": trader.get("user_id") or "",
            "sourceTraderUsername": trader.get("username") or "",
            "sourceQualityScore": trader.get("quality_score", 0),
            "sourceWinRate": trader.get("win_rate", 0),
            "sourceRoi30d": trader.get("roi_30d", 0),
            "sourceConsecutiveWins": trader.get("consecutive_wins", 0),
            "entryTimestamp": candidate["entry_ts"],
            "leverage": candidate["leverage"],
            "sizeUsd": candidate["size_usd"],
            "entryPrice": candidate["entry_price"],
            "poolConsensusCount": candidate["pool_consensus_count"],
            "poolConsensusAssetCount": candidate["pool_consensus_asset_count"],
            "trend4h": ta["trend_4h"] or "UNKNOWN",
            "trend1h": ta["trend_1h"] or "UNKNOWN",
            "trend15m": ta["trend_15m"] or "UNKNOWN",
            "priceChange4hPct": ta["price_change_4h_pct"] or 0,
            "fundingRegime": ta["funding_regime"] or "UNKNOWN",
            "fundingAnnualizedPct": ta["funding_annualized_pct"] or 0,
            "btcMacroDirection": btc_macro["direction"] or "UNKNOWN",
            "btcMacro24hPct": btc_macro["pct"] or 0,
        },
    }


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    lock = acquire_lock()
    if lock is None:
        print(json.dumps({
            "status": "skip",
            "reason": "previous run still active — cron reentrancy guard",
            "_jackal_producer_version": "2.0.3",
        }))
        return

    try:
        force_refresh = os.environ.get("REFRESH_POOL", "").lower() in ("1", "true", "yes")
        pool = refresh_pool(force=force_refresh)
        if not pool:
            print(json.dumps({"status": "no_pool", "reason": "pool refresh returned empty"}))
            return

        current_positions = fetch_pool_positions(pool)
        last_seen = state.load_last_seen()

        # v2.0.4: CRITICAL — baseline-seed guard.
        # If last_seen is empty (first run ever, or state file deleted),
        # DO NOT treat every current pool-member position as "new." That
        # would emit signals for all existing positions → LLM approves
        # a few → unintended entries on startup. Observed live 2026-04-23:
        # Jackal v2.0.3 opened 2 positions (ETH SHORT + SOL SHORT)
        # immediately on first install before the baseline settled.
        # On empty baseline, emit 0 signals, just save the baseline. The
        # NEXT run has a populated baseline and can safely detect diffs.
        if not last_seen:
            candidates = []
        else:
            candidates = detect_new_entries(pool, current_positions, last_seen)
            candidates = enrich_with_consensus(candidates, current_positions)

        # Always persist current positions as new baseline — regardless of whether
        # we emitted signals. If we don't, dropped signals resurface forever.
        state.save_last_seen({addr: pos for addr, pos in current_positions.items()})

        if not candidates:
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "pool_size": len(pool),
                "candidates": 0,
                "elapsed_sec": round(elapsed, 2),
                "_jackal_producer_version": "2.0.3",
            }))
            return

        # v2.0.3: fetch BTC macro + funding regime ONCE per run (shared
        # across all candidates). Previously market_get_funding_regime
        # was called per-candidate inside enrich_with_ta — 1 MCP call
        # per candidate × N candidates = wasted MCP budget.
        btc_macro = fetch_btc_macro()
        funding_regime = fetch_funding_regime()

        pushed = 0
        for c in candidates:
            ta = enrich_with_ta(c, funding_regime)
            payload = build_signal_payload(c, ta, btc_macro)
            if push_signal(payload):
                pushed += 1

        elapsed = time.time() - run_start
        warn = "WARN_OVER_60S" if elapsed > 60 else None
        print(json.dumps({
            "status": "ok",
            "pool_size": len(pool),
            "candidates_detected": len(candidates),
            "signals_pushed": pushed,
            "btc_macro": btc_macro,
            "funding_regime": funding_regime,
            "elapsed_sec": round(elapsed, 2),
            "warn": warn,
            "_jackal_producer_version": "2.0.3",
        }))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    main()
