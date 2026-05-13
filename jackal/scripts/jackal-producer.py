#!/usr/bin/env python3
# Senpi JACKAL Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""JACKAL v3.0.0 Producer — Smart-Stalker signal emitter, helpers-native.

Jackal v1.1 (the scanner) ran as a full-agency Python scanner that:
  - Maintained a two-tier pool (watchlist + active)
  - Detected new positions opened by pool members
  - Scored them via its own heuristics
  - Called create_position + ratchet_stop_add directly

v2.0+ splits that into two parts:
  1. This producer (long-lived daemon, 60s tick): emits candidate
     signals via client.push_signal() direct HTTP POST.
  2. Runtime (senpi-trading-runtime): receives signals at /signals,
     gates through LLM decision_prompt, executes, and manages DSL
     autonomously.

The producer's single responsibility: fetch the active trader pool,
detect new entries, enrich with consensus + TA + funding regime
context, push a signal payload to the runtime via
`SenpiClient.push_signal()` (direct HTTP POST).

NO execution code. NO DSL code. NO risk gates. The runtime owns all of that.

Environment variables (standard v2 producer):
  JACKAL_WALLET     — Jackal wallet (REQUIRED; per-agent per v2.0.9 rule)
  SENPI_AUTH_TOKEN  — Bearer token for MCP + signal POST (REQUIRED)
  JACKAL_DECISION_MODEL — bare LLM model name
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  SENPI_RUNTIME_API_HOST/PORT — optional, default 127.0.0.1:8787
  OPENCLAW_WORKSPACE — optional, default /data/workspace

  STRATEGY_ADDRESS is BANNED (v2.0.9 contamination rule). v3.0.0 emits
  a deprecation warning and falls back to it if JACKAL_WALLET unset.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jackal_config as cfg
import jackal_state as state

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


# Reentrancy guard removed in v3.0.0. producer_daemon owns the per-tick
# scanner_lock with stale-PID auto-recovery.


VERSION = "3.0.0"

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "jackal_signals"

# Signal type passed explicitly to push_signal(). Don't rely on the
# scanner's defaultSignalType fallback — runtime YAMLs commonly don't
# declare one, and missing tags break audit-log filtering.
# (Preserved from v2.x payload — don't change without coordinating
# with audit_query consumers that filter on this value.)
SIGNAL_TYPE = "JACKAL_COPY_ENTRY"


def _resolve_wallet():
    """Resolve Jackal wallet — per-agent JACKAL_WALLET (v2.0.9 rule).
    Backward-compat fallback to STRATEGY_ADDRESS with deprecation warning."""
    env_val = (os.environ.get("JACKAL_WALLET") or "").strip()
    if env_val:
        return env_val
    legacy = (os.environ.get("STRATEGY_ADDRESS") or "").strip()
    if legacy:
        sys.stderr.write(
            "[jackal v3.0.0] DEPRECATION: STRATEGY_ADDRESS env var is BANNED "
            "per Turbine v2.0.9 contamination rule. Falling back to it for "
            "compatibility. Migrate to JACKAL_WALLET ASAP.\n"
        )
        return legacy
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()

# ═══════════════════════════════════════════════════════════════
# POOL CONFIG
# ═══════════════════════════════════════════════════════════════

# Smaller, sharper pool than v1. Top-ROI monthly with win-rate + age
# filters. Pool refreshes daily (the producer rebuilds it on first
# tick of each UTC day; REFRESH_POOL=true env var forces a rebuild).
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
    """Composite quality score 0-100. Higher = better stalk target.

    v2.0.6 rewrite. Two bugs in the prior formula:

    1. `win_rate * 100` treated MCP's `winRate` field as a 0-1 fraction.
       But discovery_get_top_traders returns winRate as a 0-100 PERCENTAGE
       (e.g. 1.07 = 1.07%, 40.0 = 40%). The * 100 multiplier pegged every
       non-zero winRate at the 60 cap, yielding 60.0 composite for every
       trader → all below the 65 LLM threshold → 0 approvals.

    2. The weighting rewarded high winrate, but Jackal's alpha source is
       tail-winner trend-followers: low winrate, huge winners that pay
       for many small losers. A 115% ROI + 1% winrate trader is the IDEAL
       pool member, not disqualified. Old formula penalized this pattern.

    New weighting reflects the actual edge pattern:
      - ROI 35% (reward big-winner distributions, capped at 150%)
      - Gain-to-pain 25% (risk-adjusted pnl, the real quality signal)
      - Age 15% (survivorship buffer)
      - Win rate 10% (small weight — we don't want high-winrate scalpers)
    """
    win_rate = float(trader.get("win_rate") or trader.get("winRate") or 0)  # already 0-100 %
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
    score += min(roi_30d / 150.0 * 50, 50)            # 50 pts for 150%+ ROI
    score += min(gain_to_pain / 5.0 * 25, 25)         # 25 pts for g/p >= 5
    score += min(age_days / 90.0 * 15, 15)            # 15 pts for 90+ day age
    score += min(win_rate / 60.0 * 10, 10)            # 10 pts for 60%+ winrate (ceiling, not requirement)
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
    calling it per-candidate wastes 1 MCP call × N candidates per
    producer tick.
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
    """Push a signal via senpi_runtime_helpers wrapper (direct HTTP POST)."""
    if not STRATEGY_ADDRESS:
        print("ERROR: JACKAL_WALLET env var not set; cannot push signal", file=sys.stderr)
        return False
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=payload.get("asset"),
            direction=payload.get("direction"),
            score=payload.get("score"),       # Jackal's quality_score / 100 = 0..1 confidence
            signal_type=SIGNAL_TYPE,
            data=payload.get("data"),
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def build_signal_payload(candidate, ta, btc_macro):
    """Build the payload. push_signal extracts asset/direction/score/data
    as kwargs; signal_type is the SIGNAL_TYPE constant (passed explicitly
    to avoid the runtime YAML's defaultSignalType fallback)."""
    trader = candidate["trader"]
    return {
        "asset": candidate["coin"],
        "direction": candidate["direction"],
        "score": trader.get("quality_score", 0) / 100.0,  # 0..1 confidence
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
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()

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
            "_jackal_producer_version": VERSION,
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
        "_jackal_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v3.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=60,
        name=f"jackal-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
