#!/usr/bin/env python3
# Senpi VULTURE Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""VULTURE v3.0.0 Producer — Long-tail momentum signal emitter for v2 runtime.

v2.x was a full-agency scanner: scored signals, tracked entries, managed
asset cooldowns + dynamic slots, called create_position directly. The
cfg.set_cooldown silent crash blew out telemetry on 27 trades.

v3.0 flips to pure producer (Scorpion v4.x / Roach v2.x pattern):
  - Scan small-cap Hyperliquid perps via leaderboard_get_markets
  - Apply LONG_TAIL_MOMENTUM scoring (preserved from v2.4 — same hard
    gates, same scoring tiers, same conviction-scaled leverage)
  - Enrich with funding regime + held-positions context
  - Push qualifying signals via `openclaw senpi external-scanner ingest`

The runtime handles everything else:
  - LLM gate (decision_mode: llm) is pass-through (Roach pattern) —
    producer has applied all filters; LLM only catches malformed signals
  - risk.guard_rails enforces max_entries_per_day, per_asset_cooldown,
    daily_loss_halt, drawdown_halt, max_consecutive_losses
  - DSL manages exits via FEE_OPTIMIZED_LIMIT (~0.020% fee saving per
    maker-filled close vs v2.x MARKET orders)
  - Trade chain DB emits LIFECYCLE / DECISION_EXECUTED / ACTION_RESULT /
    DSL_CREATED / DSL_CLOSED for every trade — telemetry restored

NO execution code. NO trade counters. NO cooldown state. NO dynamic
slot bookkeeping. The runtime owns all of that.

v2.4 architectural fixes preserved:
  - 1h-alignment gate (catches "4h up + 1h rejecting + 15m spike" false
    breakouts diagnosed Apr 27)
  - FP-001 quiet hours (00-04 UTC unless apex score)
  - FP-002 (in SKILL.md) — user-conversation Claude sessions read-only

Environment / config resolution:
  Strategy wallet is read from config/vulture-config.json (the canonical
  source). VULTURE_WALLET_ADDRESS env var is supported as an optional
  override but is NOT required for routine cron runs — operators just
  set "wallet" in vulture-config.json and the cron command stays
  wallet-agnostic. This complies with the fleet rule against hardcoding
  wallet-specific values outside config files.

  SENPI_API_KEY              — MCP access (required)
  VULTURE_WALLET_ADDRESS     — optional override; defaults to config.wallet
  SENPI_MCP_URL              — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN               — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME      — optional override (default "vulture_signals")
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
import vulture_config as cfg


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD (fleet-standard fcntl pattern)
# ═══════════════════════════════════════════════════════════════
# Cron fires every 60s. If a run takes longer (many candidates clear
# MIN_SCORE and each needs a push to the runtime), the next tick would
# start a concurrent run — duplicate signals at the runtime. Skip the
# tick if a previous run holds the lock.

_LOCK_DIR = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "vulture-strategy" / "state"
_LOCK_DIR.mkdir(parents=True, exist_ok=True)
_LOCK_PATH = _LOCK_DIR / "producer.lock"


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


VERSION = "3.0.2"
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "vulture_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var VULTURE_WALLET_ADDRESS is supported as an optional override
    (useful for CI/testing) but is NOT required for routine cron runs.
    This avoids forcing operators to inline wallet addresses in crontab,
    which violates the fleet rule against hardcoding wallet-specific
    values outside config files."""
    env_val = (os.environ.get("VULTURE_WALLET_ADDRESS") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


# v3.0: agent-specific wallet (per fleet-standard memory rule — generic
# STRATEGY_ADDRESS fallback removed to prevent contamination). Read from
# config.json by default; env var override available for testing.
STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# UNIVERSE — preserved from v2.x
# ═══════════════════════════════════════════════════════════════
# 25+ small/mid-cap Hyperliquid perps. Excludes majors covered by other
# predators (BTC/ETH/SOL banned). Includes the small caps the #1 Arena
# winner actually traded (HEMI, WLD, MON, XPL, AIXBT, ARB) plus other
# liquid small caps with sufficient SM trader counts.

TRACKED_ASSETS = [
    "HYPE", "HEMI", "WLD", "MON", "XPL", "AIXBT", "ARB", "ASTER",
    "POLYX", "LDO", "APT", "DYDX", "ONDO", "SUI", "kBONK", "kPEPE",
    "TAO", "GRASS", "ZEC", "LIT", "FARTCOIN", "MORPHO", "NEAR", "INJ",
    "AVAX", "LINK", "DOGE",
]

# Banned — crypto majors handled by other predators; XYZ handled by Bald Eagle
BANNED_ASSETS = {"BTC", "ETH", "SOL"}
XYZ_BANNED = True


# ═══════════════════════════════════════════════════════════════
# SCORING CONFIG — preserved from v2.4 (proven on the +$117 ZEC trade)
# ═══════════════════════════════════════════════════════════════

MIN_SCORE = 7                   # Entry floor; scoring tiers do the real work
MIN_SM_PCT = 3.0                # Asset must have at least 3% SM concentration
MIN_SM_TRADERS = 15             # Small caps need lower threshold than majors
MIN_4H_ALIGNED_PCT = 1.0        # 4h price must be aligned ≥1% in SM direction
MIN_1H_ALIGNED_PCT = 0.1        # v2.4 fix: 1h must be aligned (catches false breakouts)
MIN_15M_VELOCITY = 0.3          # 15m velocity must be actively building

# Conviction-scaled leverage — small caps capped at 7x (low-liq slippage)
SIZING_TIERS = [
    {"min_score": 11, "leverage": 7,  "label": "apex"},
    {"min_score": 9,  "leverage": 5,  "label": "conviction"},
    {"min_score": 7,  "leverage": 3,  "label": "cautious"},
]


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def resolve_sizing_tier(score):
    """Highest-applicable sizing tier for a given score."""
    applicable = [t for t in SIZING_TIERS if score >= t["min_score"]]
    if not applicable:
        return None
    return max(applicable, key=lambda t: t["min_score"])


def in_quiet_hours(config):
    """FP-001: skip emission during low-liquidity UTC window.

    Reads `quietHours.{startUtc, endUtc, apexBypassScore}` from config.
    Defaults: 00:00-04:00 UTC, apex bypass at score 11+.
    Returns (in_quiet: bool, hour: int, apex_bypass: int).
    """
    qh = config.get("quietHours") or {}
    start = int(qh.get("startUtc", 0))
    end = int(qh.get("endUtc", 4))
    apex = int(qh.get("apexBypassScore", 11))
    if start == end:
        return False, -1, apex
    h = datetime.now(timezone.utc).hour
    if start < end:
        return (start <= h < end), h, apex
    return (h >= start or h < end), h, apex


# ═══════════════════════════════════════════════════════════════
# SCORING — LONG_TAIL_MOMENTUM (preserved from v2.4)
# ═══════════════════════════════════════════════════════════════

def score_market(m, regime, persistence_map):
    """Score a single market candidate. Returns dict or None if rejected.

    Same scoring + hard gates as v2.4's evaluate_momentum. Preserves the
    1h-alignment gate (v2.4) and move-exhaustion penalty (v2.x).
    """
    token_raw = str(m.get("token", "")).upper()
    dex = str(m.get("dex", "")).lower()

    if XYZ_BANNED and dex == "xyz":
        return None
    if token_raw in BANNED_ASSETS:
        return None

    # Match against TRACKED_ASSETS (case-preserved for kPEPE/kBONK)
    matched = None
    for tracked in TRACKED_ASSETS:
        if token_raw == tracked.upper():
            matched = tracked
            break
    if matched is None:
        return None

    direction = str(m.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                            m.get("price_change_1h", 0)))
    p15m = safe_float(m.get("token_price_change_pct_15m",
                             m.get("price_change_15m", 0)))
    c15m = safe_float(m.get("contribution_pct_change_15m", 0))
    c1h = safe_float(m.get("contribution_pct_change_1h", 0))
    c4h = safe_float(m.get("contribution_pct_change_4h", 0))

    # ─── HARD GATES ───
    if pct < MIN_SM_PCT or traders < MIN_SM_TRADERS:
        return None

    # 4h price must be ALIGNED with SM direction (momentum confirmation)
    p4h_aligned = p4h if direction == "LONG" else -p4h
    if p4h_aligned < MIN_4H_ALIGNED_PCT:
        return None

    # v2.4 1h-alignment gate — catches "4h up + 1h rejecting + 15m spike"
    # false-breakout pattern. Diagnosed 2026-04-27: 13 of 17 dead_weight
    # losses had HW ROE < 5% within 90min because 1h was already fading
    # at entry.
    p1h_aligned = p1h if direction == "LONG" else -p1h
    if p1h_aligned < MIN_1H_ALIGNED_PCT:
        return None

    # 15m velocity must be actively building in SM direction
    if c15m < MIN_15M_VELOCITY:
        return None

    score = 0
    reasons = []

    # ─── SM CONCENTRATION TIER (0-4) ───
    if pct >= 18:
        score += 4
        reasons.append(f"HEAVY_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"STRONG_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"MODERATE_FLOW {pct:.1f}% ({traders}t) {direction}")
    else:
        score += 1
        reasons.append(f"LIGHT_FLOW {pct:.1f}% ({traders}t) {direction}")

    # ─── 4H PRICE MOMENTUM (0-3) ───
    if p4h_aligned >= 8.0:
        score += 3
        reasons.append(f"TREND_RUNNING {p4h:+.1f}%")
    elif p4h_aligned >= 4.0:
        score += 2
        reasons.append(f"TREND_STRONG {p4h:+.1f}%")
    elif p4h_aligned >= 2.0:
        score += 1
        reasons.append(f"TREND_BUILDING {p4h:+.1f}%")

    # ─── 15M VELOCITY TIER (0-3) ───
    if c15m >= 3.0:
        score += 3
        reasons.append(f"15M_EXPLOSIVE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 2
        reasons.append(f"15M_STRONG +{c15m:.2f}")
    elif c15m >= 0.5:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # ─── 1H ACCELERATION (0-2) ───
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"ACCELERATING 15m({c15m:.2f})>1h({c1h:.2f})")
    elif c1h >= 1.0:
        score += 1
        reasons.append(f"1H_POSITIVE +{c1h:.2f}")

    # ─── 4H CONTRIBUTION (0-1) ───
    if c4h >= 2.0:
        score += 1
        reasons.append(f"4H_CONTINUATION +{c4h:.1f}")

    # ─── TRADER DEPTH (0-1) ───
    if traders >= 50:
        score += 1
        reasons.append(f"DEEP_DEPTH ({traders}t)")

    # ─── MOVE EXHAUSTION PENALTY ───
    if p4h_aligned >= 15.0:
        score -= 3
        reasons.append(f"LATE_ENTRY_PENALTY {p4h:+.1f}%")
    elif p4h_aligned >= 12.0:
        score -= 2
        reasons.append(f"MOVE_EXTENDED {p4h:+.1f}%")

    # ─── REGIME ALIGNMENT (-1, 0, +1) ───
    if regime == "LONG_CROWDED" and direction == "LONG":
        score += 1
        reasons.append("REGIME_LONG_CROWDED_aligned")
    elif regime == "SHORT_CROWDED" and direction == "SHORT":
        score += 1
        reasons.append("REGIME_SHORT_CROWDED_aligned")
    elif regime == "LONG_CROWDED" and direction == "SHORT":
        score -= 1
        reasons.append("REGIME_LONG_CROWDED_fighting")
    elif regime == "SHORT_CROWDED" and direction == "LONG":
        score -= 1
        reasons.append("REGIME_SHORT_CROWDED_fighting")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # ─── PERSISTENCE (0-1) ───
    ph_val = persistence_map.get(matched) if persistence_map else None
    if ph_val is not None and ph_val >= 6:
        score += 1
        reasons.append(f"TREND_PERSISTENT_{ph_val:.0f}h")

    if score < MIN_SCORE:
        return None

    tier = resolve_sizing_tier(score)
    if not tier:
        return None

    reasons.insert(0, f"LONG_TAIL_MOMENTUM {matched} {direction}")

    return {
        "asset": matched,
        "direction": direction,
        "score": score,
        "leverage": tier["leverage"],
        "tier_label": tier["label"],
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "p15m": p15m,
        "c15m": c15m,
        "c1h": c1h,
        "c4h": c4h,
        "regime": regime or "UNKNOWN",
        "persistence_hours": ph_val,
    }


# ═══════════════════════════════════════════════════════════════
# CONTEXT ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def fetch_funding_regime():
    try:
        fr = cfg.mcporter_call("market_get_funding_regime")
        if fr:
            data = fr.get("data", fr)
            if isinstance(data, dict):
                return data.get("regime")
    except Exception:
        pass
    return None


def fetch_funding_persistence(asset):
    """Fetch funding-history persistence_hours for an asset.
    Returns float hours or None on any failure."""
    try:
        fh = cfg.mcporter_call("market_get_funding_history", asset=asset)
        if not fh:
            return None
        data = fh.get("data", fh)
        if isinstance(data, dict):
            ph = data.get("persistence_hours")
            try:
                return float(ph) if ph is not None else None
            except (TypeError, ValueError):
                return None
    except Exception:
        pass
    return None


def fetch_held_assets():
    """Read current positions from the strategy wallet."""
    if not STRATEGY_ADDRESS:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
        if not ch:
            return []
        data = ch.get("data", ch)
        held = []
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            for ap in s.get("assetPositions", []):
                pos = ap.get("position", ap)
                szi = float(pos.get("szi", 0) or 0)
                if szi == 0:
                    continue
                coin = pos.get("coin", "")
                if coin:
                    held.append(coin)
        return held
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(candidate, regime, held_assets):
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "vulture-config.json (preferred) or VULTURE_WALLET_ADDRESS env var",
            file=sys.stderr,
        )
        return False

    # Skip if already holding this asset (runtime would reject anyway,
    # but cleaner to filter at producer)
    if candidate["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    payload = {
        "asset": candidate["asset"],
        "direction": candidate["direction"],
        "score": candidate["score"] / 14.0,  # normalize 0-1 (theoretical max ~14)
        "signal_type": "VULTURE_LONG_TAIL_MOMENTUM",
        "data": {
            "score": candidate["score"],
            "tier": candidate["tier_label"],
            "leverage": candidate["leverage"],
            "reasons": candidate["reasons"],
            "smPct": candidate["sm_pct"],
            "smTraders": candidate["sm_traders"],
            "priceChange4hPct": candidate["p4h"],
            "priceChange1hPct": candidate["p1h"],
            "priceChange15mPct": candidate["p15m"],
            "contribChange15m": candidate["c15m"],
            "contribChange1h": candidate["c1h"],
            "contribChange4h": candidate["c4h"],
            "fundingRegime": candidate["regime"],
            "persistenceHours": candidate["persistence_hours"],
            "heldAssets": held_assets,
        },
    }

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", STRATEGY_ADDRESS,
        "--scanner", SCANNER_NAME,
        "--payload", json.dumps(payload),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            print(f"INGEST_FAILED {candidate['asset']}: {result.stderr}", file=sys.stderr)
            return False
        response = json.loads(result.stdout) if result.stdout.strip() else {}
        if not response.get("ok", False):
            print(f"INGEST_REJECTED {candidate['asset']}: {response.get('error', {})}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"INGEST_EXCEPTION {candidate['asset']}: {e}", file=sys.stderr)
        return False


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
            "_vulture_producer_version": VERSION,
        }))
        return

    try:
        config = cfg.load_config()

        # FP-001: quiet hours gate (apex score bypasses).
        quiet, current_hour, apex_bypass = in_quiet_hours(config)

        raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
        if not raw:
            print(json.dumps({"status": "no_markets", "_vulture_producer_version": VERSION}))
            return

        markets = raw.get("data", raw)
        if isinstance(markets, dict):
            markets = markets.get("markets", markets)
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
        if not isinstance(markets, list):
            print(json.dumps({"status": "bad_shape", "_vulture_producer_version": VERSION}))
            return

        # Enrich once per run — shared context for all candidates
        regime = fetch_funding_regime()
        held_assets = fetch_held_assets()

        # Pre-fetch persistence for tracked assets we have data for
        # (lazy: only fetch for matched assets, not entire universe).
        # Skip persistence enrichment on quiet-hours-blocked sub-apex
        # candidates to reduce MCP load.
        persistence_map = {}

        # Score all markets; keep only those at/above MIN_SCORE
        candidates = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            # Pre-pass: cheap check whether asset is tracked + not banned
            token_raw = str(m.get("token", "")).upper()
            if token_raw in BANNED_ASSETS:
                continue
            matched = None
            for tracked in TRACKED_ASSETS:
                if token_raw == tracked.upper():
                    matched = tracked
                    break
            if matched is None:
                continue
            # Fetch persistence for this asset before scoring
            if matched not in persistence_map:
                persistence_map[matched] = fetch_funding_persistence(matched)

            c = score_market(m, regime, persistence_map)
            if c is not None:
                candidates.append(c)

        if not candidates:
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "scanned": len(markets),
                "candidates": 0,
                "elapsed_sec": round(elapsed, 2),
                "regime": regime,
                "_vulture_producer_version": VERSION,
            }))
            return

        # Sort by score descending (highest conviction first)
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best_score = candidates[0]["score"]

        # FP-001 quiet hours: block sub-apex during the window
        if quiet and best_score < apex_bypass:
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "scanned": len(markets),
                "candidates": len(candidates),
                "signals_pushed": 0,
                "note": f"QUIET_HOURS hour={current_hour}_UTC best_score={best_score}_below_apex_{apex_bypass}",
                "elapsed_sec": round(elapsed, 2),
                "_vulture_producer_version": VERSION,
            }))
            return

        # Push qualifying candidates. Runtime's LLM gate + risk guard
        # rails will decide which (if any) to execute.
        pushed = 0
        for c in candidates:
            if push_signal(c, regime, held_assets):
                pushed += 1

        elapsed = time.time() - run_start
        warn = "WARN_OVER_60S" if elapsed > 60 else None
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": len(candidates),
            "signals_pushed": pushed,
            "regime": regime,
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "warn": warn,
            "_vulture_producer_version": VERSION,
        }))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    main()
