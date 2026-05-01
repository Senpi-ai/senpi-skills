#!/usr/bin/env python3
# Senpi SCORPION Producer v4.1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SCORPION v4.1.1 Producer — Multi-market signal emitter for v2 runtime.

v4.1.1 (2026-05-01) — held-asset dedup hot-patch (P0):
  After v4.1.0 ferry, Scorpion accumulated 154+ create_position calls
  in 2.5h all firing ZEC LONG at score 11. Three layers of defense
  silently failed simultaneously:
    (1) LLM prompt's HARD SKIP CONDITIONS list missed "held_assets
        contains this asset → SKIP" — only mentioned correlation risk.
    (2) Runtime per_asset_cooldown_minutes: 120 was not enforcing
        (separate Erik investigation).
    (3) recent_entry_count stub returned 0, making the >=2 over-traded
        rule mathematically unreachable.
  Each LLM-approved create_position became an HL "add to existing
  position" — the live ZEC LONG accumulated to $1,484 notional /
  $742 margin (essentially maxed out, $44 withdrawable).

v4.1.1 fixes ALL THREE LAYERS defensively:
  (A) Producer skips emission if signal asset is already held (the
      strongest fix — never even sends the signal to the LLM)
  (B) recent_entry_count returns 99 if asset is held — makes the
      LLM's existing >=2 rule trip
  (C) LLM prompt updated with explicit held-asset hard skip
  Defense in depth — any one of these would prevent the runaway. With
  all three, only a coordinated three-way silent failure could leak.

v4.1.0 (2026-04-30) — XYZ-fixed:
  - Asymmetric MIN_SCORE: crypto raised 9 → 11 (score-9 crypto bled
    -$10.07 across 11 trades in v4.0.2 sample); XYZ held at 9 (no
    accept data yet — let the new LLM rubric prove out)
  - btcMacroDirection set to NOT_APPLICABLE for XYZ signals (v4.0
    was injecting irrelevant crypto-macro context into oil/brent
    decisions; LLM was citing BTC macro to skip XYZ trades)
  - xyzPeerMomentum field added — counts other XYZ assets trending
    same direction; macro tailwind signal for the LLM rubric

v4.0 history:

v3.x was a full-agency scanner: scored signals, counted daily entries,
managed asset cooldowns + scalp re-entry windows, called create_position.
The scalp-reentry bypass meant MAX_DAILY_ENTRIES=3 silently leaked to
43 fills / 18h in Week 5.

v4.0 flips to pure producer:
  - Scan crypto + XYZ markets via leaderboard_get_markets
  - Apply the multi-factor score gate (MIN_SCORE >= 9)
  - Enrich with BTC macro + funding regime + current-position context
  - Push to the runtime via `openclaw senpi external-scanner ingest`

The runtime handles everything else:
  - LLM gate (decision_mode: llm) filters each signal
  - risk.guard_rails enforces max_entries_per_day, per_asset_cooldown,
    drawdown_halt — no Python counters that can drift
  - DSL manages exits (maker-preferred on v2)

NO execution code. NO trade counters. NO cooldown state. NO scalp
re-entry logic. The runtime owns all of that.

Environment:
  SENPI_API_KEY     — MCP access
  STRATEGY_ADDRESS  — Scorpion wallet (must match runtime YAML)
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "scorpion_signals")
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
import scorpion_config as cfg


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD (v4.0.1 — inherited from Jackal's Daniel-review fix)
# ═══════════════════════════════════════════════════════════════
# Cron fires every 60s. If a run takes longer (can happen when many
# candidates clear MIN_SCORE=9 and each needs a push to the runtime),
# the next tick would start a concurrent run. We don't share state
# between runs the way Jackal does, but concurrent `openclaw senpi
# external-scanner ingest` calls could pile up at the runtime. Skip
# the tick if a previous run holds the lock.

_LOCK_DIR = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "scorpion-tracker" / "state"
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


SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "scorpion_signals")
STRATEGY_ADDRESS = os.environ.get("STRATEGY_ADDRESS", "")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")


# ═══════════════════════════════════════════════════════════════
# UNIVERSE + SCORING CONFIG (preserved from v3.2)
# ═══════════════════════════════════════════════════════════════

CRYPTO_ASSETS = {
    "BTC", "ETH", "SOL", "HYPE", "ZEC", "LIT", "GRASS", "FARTCOIN",
    "TAO", "ONDO", "SUI", "ARB", "WLD", "DOGE", "AVAX",
}
XYZ_ASSETS = {"CL", "BRENTOIL", "GOLD", "SPX"}

# v4.1.0 — asymmetric score floor by asset class.
# Crypto at score 9 demonstrably bleeds in the v4.0.2 sample
# (11 trades, -$10.07 realized, all weak_peak / dead_weight exits).
# XYZ had zero acceptances in the sample because the LLM rubric
# was gating it out — we have no data on whether XYZ at score 9
# wins or loses. Keep XYZ permissive until we collect that data.
MIN_SCORE_CRYPTO = 11   # raised from 9 — score-9 crypto loses
MIN_SCORE_XYZ    = 9    # held — no data yet; let the new rubric prove out

# 4H price-alignment thresholds — XYZ moves less than crypto
MIN_4H_ALIGNED_PCT_CRYPTO = 1.0
MIN_4H_ALIGNED_PCT_XYZ = 0.5


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(token):
    return token in XYZ_ASSETS


def coin_label(token, is_xyz_asset):
    return f"xyz:{token}" if is_xyz_asset else token


# ═══════════════════════════════════════════════════════════════
# SIGNAL SCORING (preserved from v3.2 — unchanged logic)
# ═══════════════════════════════════════════════════════════════

def score_market(m):
    """Score a single market candidate. Returns dict or None if below threshold."""
    token = str(m.get("token", "")).upper()
    dex = str(m.get("dex", "")).lower()

    is_xyz_asset = (dex == "xyz" and token in XYZ_ASSETS)
    is_crypto_asset = (dex != "xyz" and token in CRYPTO_ASSETS)
    if not is_xyz_asset and not is_crypto_asset:
        return None

    sm_direction = str(m.get("direction", "")).upper()
    if sm_direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                           m.get("price_change_1h", 0)))
    cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
    cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

    if traders < 5:
        return None

    # 4H price alignment gate — SM direction must match price trend
    min_4h = MIN_4H_ALIGNED_PCT_XYZ if is_xyz_asset else MIN_4H_ALIGNED_PCT_CRYPTO
    price_aligned = (sm_direction == "LONG" and p4h >= min_4h) or \
                    (sm_direction == "SHORT" and p4h <= -min_4h)
    if not price_aligned:
        return None

    score = 0
    reasons = []

    # SM concentration (0-3)
    if pct >= 15:
        score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

    # 4H price alignment (0-3)
    big_move = 3.0 if is_xyz_asset else 5.0
    med_move = 1.5 if is_xyz_asset else 3.0
    if abs(p4h) >= big_move:
        score += 3; reasons.append(f"STRONG_TREND {p4h:+.1f}%")
    elif abs(p4h) >= med_move:
        score += 2; reasons.append(f"TREND {p4h:+.1f}%")
    elif abs(p4h) >= min_4h:
        score += 1; reasons.append(f"ALIGNED {p4h:+.1f}%")

    # 15m SM velocity (0-2, penalty -1 on fade)
    if cc_15m > 1.0:
        score += 2; reasons.append(f"15M_SM_BUILDING {cc_15m:+.2f}")
    elif cc_15m > 0.3:
        score += 1; reasons.append(f"15M_SM_FRESH {cc_15m:+.2f}")
    elif cc_15m < -0.5:
        score -= 1; reasons.append(f"15M_SM_FADING {cc_15m:+.2f}")

    # 1H acceleration
    if sm_direction == "LONG" and p1h > 0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")
    elif sm_direction == "SHORT" and p1h < -0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")

    # Trader depth
    if traders >= 50:
        score += 1; reasons.append(f"DEEP_SM ({traders}t)")

    # 4H contribution shift
    if abs(cc_4h) >= 5.0:
        score += 1; reasons.append(f"4H_CONVICTION {cc_4h:+.1f}")

    min_score = MIN_SCORE_XYZ if is_xyz_asset else MIN_SCORE_CRYPTO
    if score < min_score:
        return None

    reasons.insert(0, f"TREND_FOLLOW {coin_label(token, is_xyz_asset)} {sm_direction}")

    return {
        "asset": coin_label(token, is_xyz_asset),
        "token": token,
        "is_xyz": is_xyz_asset,
        "direction": sm_direction,
        "score": score,
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
    }


# ═══════════════════════════════════════════════════════════════
# CONTEXT ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def fetch_btc_macro():
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
        return {"direction": "UP" if pct > 0 else "DOWN", "pct": round(pct, 2)}
    except Exception:
        return {"direction": None, "pct": None}


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


def fetch_held_assets():
    """Read current positions from the strategy wallet. Returns list of asset labels."""
    wallet = STRATEGY_ADDRESS or os.environ.get("WALLET_ADDRESS", "")
    if not wallet:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=wallet)
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


def recent_entry_count(asset, held_assets=None):
    """v4.1.1: return 99 if asset is in held_assets so the LLM's
    'recentEntryCountThisAsset >= 2 within 24h' rule trips on duplicates.

    The runtime per_asset_cooldown was supposed to backstop this but isn't
    enforcing reliably (separate Erik investigation post-v4.1.0 runaway).
    Tripping the LLM's existing rule is a defense-in-depth backstop;
    the producer-level held_assets skip in main() is the primary fix.
    """
    if held_assets:
        held_norm = {h.upper() for h in held_assets}
        if asset.upper() in held_norm:
            return 99
        # XYZ-prefixed signals: also check token portion (e.g. xyz:BRENTOIL)
        if ":" in asset:
            token = asset.split(":", 1)[1].upper()
            if token in held_norm:
                return 99
    return 0


# ═══════════════════════════════════════════════════════════════
# v4.1.0 — XYZ ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def build_macro_context(candidate, btc_macro):
    """BTC macro is structurally irrelevant to XYZ assets — don't anchor
    the LLM on it. v4.0 was injecting btcMacroDirection into XYZ signal
    payloads; the LLM then cited BTC macro as a reason to skip oil/brent
    trades. v4.1 sets NOT_APPLICABLE for XYZ so the rubric ignores it."""
    if candidate["is_xyz"]:
        return {"direction": "NOT_APPLICABLE", "pct": 0.0}
    return {
        "direction": btc_macro["direction"] or "UNKNOWN",
        "pct": btc_macro["pct"] or 0,
    }


def compute_xyz_peer_momentum(candidates):
    """For each XYZ direction, count peer XYZ assets trending the same
    way in the same scan. Oil + Brent + SPX up together = macro
    tailwind; single-asset XYZ move on noise = no peer support.
    Returns dict: {(token, direction): peer_count}."""
    xyz_signals = [c for c in candidates if c["is_xyz"]]
    result = {}
    for c in xyz_signals:
        peers = sum(
            1 for p in xyz_signals
            if p["token"] != c["token"] and p["direction"] == c["direction"]
        )
        result[(c["token"], c["direction"])] = peers
    return result


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(candidate, btc_macro, funding_regime, held_assets, xyz_peer_count=0):
    if not STRATEGY_ADDRESS:
        print("ERROR: STRATEGY_ADDRESS env var not set", file=sys.stderr)
        return False

    macro_ctx = build_macro_context(candidate, btc_macro)
    payload = {
        "asset": candidate["asset"],
        "direction": candidate["direction"],
        "score": candidate["score"] / 20.0,  # normalize 0-1 (theoretical max ~13)
        "signal_type": "SCORPION_TREND_FOLLOW",
        "data": {
            "score": candidate["score"],
            "isXyz": candidate["is_xyz"],
            "reasons": candidate["reasons"],
            "smPct": candidate["sm_pct"],
            "smTraders": candidate["sm_traders"],
            "priceChange4hPct": candidate["p4h"],
            "priceChange1hPct": candidate["p1h"],
            "contribChange15m": candidate["cc_15m"],
            "contribChange1h": candidate["cc_1h"],
            "contribChange4h": candidate["cc_4h"],
            "btcMacroDirection": macro_ctx["direction"],
            "btcMacro24hPct": macro_ctx["pct"],
            "fundingRegime": funding_regime or "UNKNOWN",
            "heldAssets": held_assets,
            "recentEntryCountThisAsset": recent_entry_count(candidate["asset"], held_assets),
            "xyzPeerMomentum": xyz_peer_count,
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
            "_scorpion_producer_version": "4.1.1",
        }))
        return

    try:
        raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
        if not raw:
            print(json.dumps({"status": "no_markets", "_scorpion_producer_version": "4.1.1"}))
            return

        markets = raw.get("data", raw)
        if isinstance(markets, dict):
            markets = markets.get("markets", markets)
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
        if not isinstance(markets, list):
            print(json.dumps({"status": "bad_shape", "_scorpion_producer_version": "4.1.1"}))
            return

        # Score all markets; keep only those at/above MIN_SCORE
        candidates = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            c = score_market(m)
            if c is not None:
                candidates.append(c)

        if not candidates:
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "scanned": len(markets),
                "candidates": 0,
                "elapsed_sec": round(elapsed, 2),
                "_scorpion_producer_version": "4.1.1",
            }))
            return

        # Enrich once per run (shared context for all candidates)
        btc_macro = fetch_btc_macro()
        funding_regime = fetch_funding_regime()
        held_assets = fetch_held_assets()

        # v4.1.1: PRIMARY held-asset dedup fix. Skip emission entirely
        # if the candidate's asset is already in held_assets. This is
        # the strongest layer of the three-layer fix — a candidate that
        # never reaches the LLM can't possibly trigger a duplicate add.
        held_norm = {h.upper() for h in held_assets}

        def _asset_already_held(candidate):
            asset = candidate["asset"]
            if asset.upper() in held_norm:
                return True
            # XYZ-prefixed: also check token portion (e.g. xyz:BRENTOIL → BRENTOIL)
            if ":" in asset:
                token = asset.split(":", 1)[1].upper()
                if token in held_norm:
                    return True
            return False

        # Push all qualifying candidates. Runtime's LLM gate + risk guard
        # rails will decide which (if any) to execute.
        candidates.sort(key=lambda c: c["score"], reverse=True)
        xyz_peer_map = compute_xyz_peer_momentum(candidates)
        pushed = 0
        skipped_held = 0
        for c in candidates:
            if _asset_already_held(c):
                skipped_held += 1
                continue
            peer_count = xyz_peer_map.get((c["token"], c["direction"]), 0) if c["is_xyz"] else 0
            if push_signal(c, btc_macro, funding_regime, held_assets, peer_count):
                pushed += 1

        elapsed = time.time() - run_start
        warn = "WARN_OVER_60S" if elapsed > 60 else None
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": len(candidates),
            "signals_pushed": pushed,
            "skipped_held_assets": skipped_held,
            "btc_macro": btc_macro,
            "funding_regime": funding_regime,
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "warn": warn,
            "_scorpion_producer_version": "4.1.1",
        }))
    finally:
        release_lock(lock)


if __name__ == "__main__":
    main()
