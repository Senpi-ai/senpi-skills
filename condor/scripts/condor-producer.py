#!/usr/bin/env python3
# Senpi CONDOR Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CONDOR v4.0.0 Producer — One Amazing Trade per Day, senpi_runtime_helpers migration.

v4.0.0 (2026-05-12): plumbing migration from v3.4. NO thesis change.
v3.4 scoring tables, hard gates (3TF alignment, MACRO_TREND_GATE, SM
consensus, OI/traders/XYZ/stablecoin filters), score-scaled sizing
(50%/70%/80% margin), 10x leverage cap, and DSL preset all preserved
verbatim.

What v3.4 did that v4.0 does NOT do:
  - call create_position directly         → runtime opens via signal flow
  - track entries in state/trade-counter  → runtime guard_rails enforce
  - manage post-exit cooldown in Python   → runtime per_asset_cooldown +
                                             cooldown_minutes
  - has_resting_orders() stale-cancel     → runtime+exit timeout handles
  - dynamic daily cap circuit breaker     → drawdown_halt_pct + static cap

What v3.4 did that v4.0 STILL DOES (preserved verbatim):
  - Top 50 HL assets by 24h notional volume
  - HARD GATES: XYZ banned, stablecoins banned, OI >= $1M,
    trader_count >= 50, 3TF alignment (4h_price + 1h_price + 15m
    velocity all clearing magnitude floors), MACRO_TREND_GATE
    (block if |4h_move| > 10% opposite direction), SM consensus >= 70%
  - SCORING: 4h magnitude (1-4) + 1h confirmation (1-2) + 15m velocity
    (1-2) + 3TF bonus (+3) + SM tier (2-4) + trader depth (+1) +
    funding alignment (+1) + BTC macro bonus (+1) + peak session (+1)
  - MIN_SCORE 12 (v3.4 calibration)
  - Sizing tiers: score 11-12 → 50%, 13-14 → 70%, 15+ → 80% (APEX)
  - Leverage 10x (auto-clamped to asset max via strategy_get_asset_trading_limits)
  - MAX_POSITIONS=1 ("one amazing trade per day")

Environment / config resolution:
  Strategy wallet is read from config/condor-config.json (canonical
  source). CONDOR_WALLET env var is supported as an optional override
  but is NOT required for routine daemon runs.

  SENPI_AUTH_TOKEN       — REQUIRED. Bearer token for MCP + signal POST.
  CONDOR_WALLET          — optional override; defaults to config.wallet
  CONDOR_DECISION_MODEL  — bare LLM model name (no provider prefix);
                           resolved into runtime.yaml at create time
  SENPI_MCP_URL          — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import condor_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "4.0.1"
SCANNER_NAME = "condor_signals"
SIGNAL_TYPE = "CONDOR_APEX_CONTINUATION"

# v4.0.1: each push_signal() success is recorded via
# cfg.record_signal(coin). main() skips emission for any coin seen
# in the cache within RECENT_SIGNAL_TTL_SEC (default 240s). Bridges
# the race window where a freshly-pushed signal hasn't yet appeared
# in the next-tick clearinghouse pull but has already triggered a
# runtime LLM-gate fire. Audit on Condor2 (M193171) 2026-05-14 to
# 2026-05-17 showed 4 consecutive CONDOR_APEX HYPE LONG
# ENGINE_FAILUREs on already-held HYPE — all eliminated by this cache.


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v3.4
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000
MIN_TRADER_COUNT = 50
XYZ_BANNED = True
STABLECOINS_BANNED = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "BUSD"}

# 3TF alignment thresholds
MIN_4H_MAGNITUDE = 1.0
MIN_1H_MAGNITUDE = 0.3
MIN_15M_VELOCITY = 0.1            # v3.4 calibration (was 0.2 in v3.2)

# MACRO TREND GATE — Wolverine's lesson (no fighting runaway trends)
MACRO_GATE_THRESHOLD_PCT = 10.0

# SM gates
MIN_SM_CONSENSUS_PCT = 70.0       # v3.4 calibration (65 in v3.0/v3.1, 75 in v3.2)
STRONGLY_TILTED_PCT = 80.0

# Score floor
MIN_SCORE = 12                    # v3.4 calibration

# Sizing tiers (Kodiak empirical 10x cap)
MAX_LEVERAGE = 10
LEVERAGE_TIERS = [
    {"min_score": 15, "leverage": 10, "margin_pct": 0.80},   # APEX
    {"min_score": 13, "leverage": 10, "margin_pct": 0.70},   # HIGH
    {"min_score": 11, "leverage": 10, "margin_pct": 0.50},   # base
]


def _resolve_wallet():
    """Resolve strategy wallet — env var first, config.json second."""
    env_val = (os.environ.get("CONDOR_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def now_utc_hour():
    return datetime.now(timezone.utc).hour


def get_sizing_for_score(score):
    """Returns (leverage, margin_pct) based on score tier."""
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"], tier["margin_pct"]
    return 10, 0.50


def get_safe_leverage(wallet, asset, requested_leverage):
    """Clamp leverage to asset's max allowed on Hyperliquid."""
    try:
        limits = cfg.mcp_call(
            "strategy_get_asset_trading_limits",
            strategy_wallet=wallet,
            coin=asset,
        )
        if limits:
            data = limits.get("data", limits)
            if isinstance(data, dict):
                lev = data.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", MAX_LEVERAGE)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING — preserved verbatim (mcporter_call → mcp_call alias)
# ═══════════════════════════════════════════════════════════════

def fetch_universe():
    """Top 50 HL assets by 24h notional volume, context-aware read."""
    data = cfg.mcp_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    if not isinstance(instruments, list):
        return []

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = str(inst.get("coin") or inst.get("name", "")).upper()
        if not coin or coin in STABLECOINS_BANNED:
            continue
        dex = str(inst.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue

        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                                      inst.get("markPx", inst.get("midPx", 0)))))
        volume_24h = safe_float(ctx.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        funding = safe_float(ctx.get("funding", inst.get("funding", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0

        if oi_usd < MIN_OI_USD or mark_px <= 0:
            continue

        assets.append({
            "coin": coin, "oi_usd": oi_usd,
            "volume_24h": volume_24h, "price": mark_px, "funding": funding,
        })

    assets.sort(key=lambda x: x["volume_24h"], reverse=True)
    return assets[:UNIVERSE_SIZE]


def fetch_sm_map():
    """Hyperfeed SM leaderboard: direction, consensus, 4h/1h price, 15m velocity."""
    raw = cfg.mcp_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
    markets = raw
    if isinstance(markets, dict):
        data = markets.get("data", markets)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    if not isinstance(markets, list):
        return {}

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if not token:
            continue
        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "consensus_pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0) or 0),
            "p4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "p1h": safe_float(m.get("token_price_change_pct_1h",
                              m.get("price_change_1h", 0))),
            "c15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "c1h": safe_float(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


def get_btc_macro(sm_map):
    """BTC's dominant direction + 4h magnitude as macro context."""
    btc = sm_map.get("BTC")
    if not btc:
        return None
    return {"direction": btc["direction"], "p4h": btc["p4h"]}


# ═══════════════════════════════════════════════════════════════
# SIGNAL EVALUATION — preserved verbatim from v3.4
# ═══════════════════════════════════════════════════════════════

def evaluate_trend_continuation(asset_info, sm, btc_macro):
    """Score an asset for trend-continuation apex setup.

    Returns scored signal dict or None if any hard gate fails.
    """
    coin = asset_info["coin"]
    sm_dir = sm["direction"]
    if sm_dir not in ("LONG", "SHORT"):
        return None

    # HARD GATE: SM consensus
    if sm["consensus_pct"] < MIN_SM_CONSENSUS_PCT:
        return None
    # HARD GATE: trader depth
    if sm["traders"] < MIN_TRADER_COUNT:
        return None

    p4h = sm["p4h"]
    p1h = sm["p1h"]
    c15m = sm["c15m"]

    # HARD GATE: 3TF alignment (4h + 1h + 15m velocity)
    if sm_dir == "LONG":
        tf_ok = (p4h >= MIN_4H_MAGNITUDE and
                 p1h >= MIN_1H_MAGNITUDE and
                 c15m >= MIN_15M_VELOCITY)
    else:
        tf_ok = (p4h <= -MIN_4H_MAGNITUDE and
                 p1h <= -MIN_1H_MAGNITUDE and
                 c15m >= MIN_15M_VELOCITY)
    if not tf_ok:
        return None

    # HARD GATE: MACRO TREND (Wolverine's lesson)
    if sm_dir == "LONG" and p4h < -MACRO_GATE_THRESHOLD_PCT:
        return None
    if sm_dir == "SHORT" and p4h > MACRO_GATE_THRESHOLD_PCT:
        return None

    # ─── SCORING ───
    score = 0
    reasons = []

    # 4h magnitude
    p4h_abs = abs(p4h)
    if p4h_abs >= 6.0:
        score += 4
        reasons.append(f"4H_MOMENTUM_STRONG {p4h:+.1f}%")
    elif p4h_abs >= 4.0:
        score += 3
        reasons.append(f"4H_MOMENTUM {p4h:+.1f}%")
    elif p4h_abs >= 2.0:
        score += 2
        reasons.append(f"4H_TREND_BUILDING {p4h:+.1f}%")
    else:
        score += 1
        reasons.append(f"4H_TREND_LIGHT {p4h:+.1f}%")

    # 1h confirmation
    p1h_abs = abs(p1h)
    if p1h_abs >= 1.0:
        score += 2
        reasons.append(f"1H_STRONG {p1h:+.2f}%")
    elif p1h_abs >= 0.5:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # 15m SM velocity
    if c15m >= 2.0:
        score += 2
        reasons.append(f"15M_SPIKE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # 3TF alignment bonus
    score += 3
    reasons.append(f"3TF_ALIGNED_{sm_dir}")

    # SM consensus tier
    if sm["consensus_pct"] >= STRONGLY_TILTED_PCT:
        score += 4
        reasons.append(f"SM_STRONGLY_TILTED {sm['consensus_pct']:.0f}%")
    elif sm["consensus_pct"] >= 75:
        score += 3
        reasons.append(f"SM_CONVERGENT {sm['consensus_pct']:.0f}%")
    else:
        score += 2
        reasons.append(f"SM_ALIGNED {sm['consensus_pct']:.0f}%")

    # Trader depth
    if sm["traders"] >= 100:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({sm['traders']}t)")

    # Funding alignment
    funding = asset_info.get("funding", 0)
    if (sm_dir == "SHORT" and funding > 0.0002) or (sm_dir == "LONG" and funding < -0.0002):
        score += 1
        reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # BTC macro confirmation bonus
    if btc_macro and coin != "BTC":
        if btc_macro["direction"] == sm_dir and abs(btc_macro["p4h"]) >= 1.5:
            score += 1
            reasons.append(f"BTC_CONFIRMS {btc_macro['p4h']:+.1f}%")

    # Peak session bonus (13-19 UTC or 00-05 UTC)
    h = now_utc_hour()
    if (13 <= h <= 19) or (0 <= h <= 5):
        score += 1
        reasons.append(f"PEAK_SESSION_{h:02d}UTC")

    if score < MIN_SCORE:
        return None

    return {
        "coin": coin,
        "direction": sm_dir,
        "score": score,
        "reasons": reasons,
        "p4h": p4h,
        "p1h": p1h,
        "c15m": c15m,
        "sm_consensus": sm["consensus_pct"],
        "sm_traders": sm["traders"],
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit — v4.0 helpers-native (replaces create_position)
# ═══════════════════════════════════════════════════════════════

def push_signal(signal, margin_usd, leverage, held_assets):
    """Emit an APEX_CONTINUATION signal.

    asset/direction top-level routing; marginUsd + leverage in data block
    for LLM gate to pass through (preserves the score-scaled sizing tiers).
    """
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False

    # Defense in depth — runtime per_asset_cooldown is authoritative
    if signal["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": signal["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "reasons": signal["reasons"],
        "priceChange4hPct": signal["p4h"],
        "priceChange1hPct": signal["p1h"],
        "contribChange15m": signal["c15m"],
        "smConsensusPct": signal["sm_consensus"],
        "smTraders": signal["sm_traders"],
        "oiUsd": signal["oi_usd"],
        "funding": signal["funding"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=signal["coin"],
            direction=signal["direction"],
            score=min(signal["score"] / 18.0, 1.0),   # normalize to 0..1 (max ~18)
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {signal['coin']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {signal['coin']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_condor_producer_version": VERSION})
        return

    # Account state + held positions
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_condor_producer_version": VERSION})
        return

    # If a position is already open, no new signal — runtime DSL manages exit
    if held_assets:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "note": f"RIDING: {held_assets}. DSL manages exit.",
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "_condor_producer_version": VERSION,
        })
        return

    # Universe + SM
    universe = fetch_universe()
    if not universe:
        cfg.output({"status": "ok", "note": "market_list_instruments empty",
                    "_condor_producer_version": VERSION})
        return

    sm_map = fetch_sm_map()
    if not sm_map:
        cfg.output({"status": "ok", "note": "leaderboard_get_markets empty",
                    "_condor_producer_version": VERSION})
        return

    btc_macro = get_btc_macro(sm_map)

    # Evaluate every universe asset
    candidates = []
    for asset_info in universe:
        coin = asset_info["coin"]
        sm = sm_map.get(coin)
        if not sm:
            continue
        sig = evaluate_trend_continuation(asset_info, sm, btc_macro)
        if sig:
            candidates.append(sig)

    if not candidates:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(universe),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_SCORE,
            "note": f"WAITING — no apex setup >= MIN_SCORE={MIN_SCORE}",
            "elapsed_sec": round(elapsed, 2),
            "_condor_producer_version": VERSION,
        })
        return

    # Pick the highest-scoring candidate
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # v4.0.1: race-window dedup. If we just pushed this coin within
    # RECENT_SIGNAL_TTL_SEC, skip — the runtime is mid-fill and the
    # on-chain held-asset check hasn't yet caught up. Re-emitting
    # here would drive a duplicate runtime LLM-gate fire that hits
    # ENGINE_FAILURE on the held-asset bug.
    if cfg.was_recently_signaled(best["coin"]):
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(universe),
            "candidates": len(candidates),
            "signals_pushed": 0,
            "note": (
                f"DEDUP_SKIP {best['coin']} pushed within last "
                f"{cfg.RECENT_SIGNAL_TTL_SEC}s (race window) — runtime is "
                f"mid-fill, awaiting clearinghouse refresh"
            ),
            "best": {
                "coin": best["coin"],
                "direction": best["direction"],
                "score": best["score"],
            },
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "_condor_producer_version": VERSION,
        })
        return

    # Score-scaled sizing
    base_leverage, margin_pct = get_sizing_for_score(best["score"])
    leverage = get_safe_leverage(STRATEGY_ADDRESS, best["coin"], base_leverage)
    margin_usd = round(account_value * margin_pct, 2)

    # Push to runtime — LLM gate decides whether to open
    pushed = push_signal(best, margin_usd, leverage, held_assets)

    # v4.0.1: record successful push for the next tick's dedup check.
    if pushed:
        cfg.record_signal(best["coin"])

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(universe),
        "candidates": len(candidates),
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "margin_pct_of_equity": margin_pct,
            "reasons": best["reasons"][:6],
        },
        "top_5_candidates": [
            {"coin": c["coin"], "direction": c["direction"], "score": c["score"]}
            for c in candidates[:5]
        ],
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_condor_producer_version": VERSION,
    })


if __name__ == "__main__":
    # v4.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,                  # 3min — preserved from v3.4 cron cadence
        name=f"condor-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
