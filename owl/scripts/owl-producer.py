#!/usr/bin/env python3
# Senpi OWL Producer v7.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""OWL v7.0 Producer — Pure Contrarian Crowding-Unwind (v2-runtime-native).

Owl v6.x was a v1-architecture self-executing scanner that:
  - Polled market_list_instruments + leaderboard_get_markets
  - Per-asset: scored crowding (funding extremity + SM tilt + OI concentration)
  - Maintained persistence timers in state file (1h+ required to fire)
  - Detected exhaustion (volume declining, price stalling, RSI divergence)
  - Called create_position directly for entries opposite to the crowd
  - Used MARKET orders for DSL exits

v7.0 splits that into two parts:
  1. This producer (cron, 15min): emits contrarian signals only.
  2. Runtime (senpi-trading-runtime v2): receives signals via
     external_scanner ingest, LLM-gates them (pass-through), executes
     with FEE_OPTIMIZED_LIMIT (entries: maker-only; exits: maker-first
     + taker fallback as safety), and manages DSL exits autonomously.

The producer's responsibility:
  1. Maintain per-asset crowding history (state/<wallet-hash>/crowding-history.json)
     with persistence timers + peak score + below-threshold tolerance counter
  2. Score crowding per asset (funding extremity + SM tilt + OI concentration)
  3. Score exhaustion per asset (volume decline + price stall + RSI divergence)
  4. Apply persistence gate (>= 1h above minCrowdingScore)
  5. Apply combined score gate (>= 12)
  6. Apply per-asset cooldown + dynamic daily cap
  7. Push top contrarian candidate per tick to runtime via openclaw CLI

NO execution code. NO DSL code. NO position-tracking. Daily-loss /
drawdown / max-positions / consec-loss enforced by runtime guard_rails.

Entry direction is OPPOSITE of crowd direction (this is the whole edge —
crowded trades unwind violently; we trade the unwind).

Crowding/exhaustion logic preserved verbatim from v6.2:
  - minCrowdingScore: 6 (was 8 in v6.1; lowered v6.2 to unblock persistence)
  - minPersistHours: 1 (was 4 in v5.x; lowered v6.0)
  - minExhaustionSignals: 2 distinct, score >= 5
  - entry.minScore: 12 (combined crowding + exhaustion)
  - persistence tolerance: 2 consecutive below-threshold scans before clear
    (v5.3 — prevents single-noise-tick reset)

Environment variables:
  SENPI_API_KEY     — for MCP access
  OWL_WALLET        — Owl wallet (must match runtime YAML's wallet).
                      AGENT-SPECIFIC env var by design — do NOT fall back
                      to a generic STRATEGY_ADDRESS. Per Turbine v2.0.9
                      contamination fix.
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  OPENCLAW_BIN      — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME — optional override (default "owl_signals")
  OWL_MARGIN_PCT    — optional, default 0.25 (25% of account value)
  OWL_MIN_OI_USD    — optional, default 3000000 ($3M liquidity floor)
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as cfg


# v7.0: Agent-specific wallet env var (Turbine v2.0.9 contamination fix).
OWL_WALLET = os.environ.get("OWL_WALLET", "")
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "owl_signals")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
MARGIN_PCT = float(os.environ.get("OWL_MARGIN_PCT", "0.25"))
MIN_OI_USD = float(os.environ.get("OWL_MIN_OI_USD", "3000000"))


# Wallet-isolated state dir.
def _wallet_state_dir():
    if OWL_WALLET:
        h = hashlib.sha256(OWL_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_LOCK_PATH = _STATE_DIR / "producer.lock"
_CROWDING_FILE = _STATE_DIR / "crowding-history.json"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_COUNTER_FILE = _STATE_DIR / "trade-counter.json"


# ═══════════════════════════════════════════════════════════════
# REENTRANCY GUARD
# ═══════════════════════════════════════════════════════════════
# Cron fires every 15 min. Owl makes 1× market_list_instruments +
# 1× leaderboard_get_markets + N× market_get_asset_data (per
# qualifying asset). At ~10-20 qualifying assets and 25s per call
# this can run multiple minutes. Reentrancy guard handles overlap.

def acquire_lock():
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


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS (fleet-tuned, preserved from v6.2)
# ═══════════════════════════════════════════════════════════════
MIN_CROWDING_SCORE = 6                # v6.2: lowered 8→6 to unblock persistence
MIN_PERSIST_HOURS = 1                 # v6.0: lowered 4→1
MIN_EXHAUSTION_SIGNALS = 2
MIN_EXHAUSTION_SCORE = 5
MIN_COMBINED_SCORE = 12               # v6.0: lowered 14→12
BELOW_THRESHOLD_TOLERANCE = 2         # v5.3: 2 consecutive below-threshold scans before clear
ASSET_COOLDOWN_MINUTES = 360          # 6h post-loss
MIN_FUNDING_ANNUALIZED_PCT = 12       # v5.2 (was 20)
STARTING_BUDGET = 1000.0
XYZ_BANNED = True

# Conviction-scaled leverage (Polar v2.4 / Bald Eagle v3.0 pattern)
LEVERAGE_TIERS = [
    {"min_score": 16, "leverage": 10},
    {"min_score": 14, "leverage": 8},
    {"min_score": 12, "leverage": 7},
]
DEFAULT_LEVERAGE = 7


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


def get_dynamic_daily_cap(account_value, day_realized_pnl=0):
    """v6.x dynamicSlots: 2 base, 3 at +$150 day PnL, 4 at +$400.
    Drawdown circuit breaker: 0 entries below -25% (matches runtime
    guard_rails.drawdown_halt_pct), 1 below -15%, 2 below -5%."""
    pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
    if pnl_pct < -25:
        return 0  # HARD STOP — also enforced by runtime guard_rails
    if pnl_pct < -15:
        return 1
    if pnl_pct < -5:
        return 2
    if day_realized_pnl >= 400:
        return 4
    if day_realized_pnl >= 150:
        return 3
    return 2


def is_xyz_asset(asset, dex=""):
    if not asset:
        return False
    if dex and str(dex).lower() == "xyz":
        return True
    return str(asset).lower().startswith("xyz:")


# ═══════════════════════════════════════════════════════════════
# STATE I/O (wallet-isolated)
# ═══════════════════════════════════════════════════════════════

def load_crowding_history():
    try:
        with open(_CROWDING_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_crowding_history(h):
    cfg.atomic_write(str(_CROWDING_FILE), h)


def load_cooldowns():
    try:
        with open(_COOLDOWN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cooldowns(cooldowns):
    cfg.atomic_write(str(_COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(asset, cooldown_minutes=ASSET_COOLDOWN_MINUTES):
    cooldowns = load_cooldowns()
    if asset not in cooldowns:
        return False
    last_emit = cooldowns[asset].get("emittedTimestamp", 0)
    return ((time.time() - last_emit) / 60) < cooldown_minutes


def mark_asset_emitted(asset):
    cooldowns = load_cooldowns()
    cooldowns[asset] = {
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
            tc = {"date": today, "entries": 0, "realizedPnl": 0}
        return tc
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": today, "entries": 0, "realizedPnl": 0}


def save_trade_counter(tc):
    tc["updatedAt"] = cfg.now_iso()
    cfg.atomic_write(str(_COUNTER_FILE), tc)


# ═══════════════════════════════════════════════════════════════
# ACCOUNT VALUE QUERY
# ═══════════════════════════════════════════════════════════════

def get_account_value():
    if not OWL_WALLET:
        return None, None
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=OWL_WALLET)
    if not ch:
        return None, None
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
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
    return total_value, pos_count


# ═══════════════════════════════════════════════════════════════
# UNIVERSE FETCH (v6.1: ALL assets with OI > $3M, no top-N truncation)
# ═══════════════════════════════════════════════════════════════

def fetch_all_assets():
    """Returns list of {coin, oi, oi_usd, price, funding, dex} for all
    assets with OI > MIN_OI_USD. v6.1 universe expansion preserved —
    NO top-30 truncation."""
    raw = cfg.mcporter_call("market_list_instruments")
    if not raw:
        return []
    instruments = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        coin = str(coin).upper() if coin else ""
        dex = str(inst.get("dex", "")).lower()
        if not coin:
            continue
        if XYZ_BANNED and is_xyz_asset(coin, dex):
            continue
        # v1.3 fix: funding/OI/price are nested in `context`, not top-level
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                                      inst.get("markPx", inst.get("midPx", 0)))))
        funding = safe_float(ctx.get("funding", inst.get("funding", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if oi_usd >= MIN_OI_USD:
            assets.append({
                "coin": coin,
                "oi": oi,
                "oi_usd": oi_usd,
                "price": mark_px,
                "funding": funding,
                "dex": dex,
            })
    assets.sort(key=lambda x: x["oi_usd"], reverse=True)
    return assets


# ═══════════════════════════════════════════════════════════════
# SM POSITIONING MAP (one MCP call shared across all assets)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_positioning_map():
    """Returns {coin: (long_pct, trader_count)} for crypto markets.
    v7.0: fetch ONCE per scan instead of per-asset (Pangolin v2.0 pattern)."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=200)
    if not raw:
        return {}
    sm = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm, dict):
        sm = sm.get("markets", sm.get("leaderboard", sm))
    if isinstance(sm, dict):
        sm = sm.get("markets", [])
    if not isinstance(sm, list):
        return {}

    out = {}
    for m in sm:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        dex = str(m.get("dex", "")).lower()
        if not token or dex == "xyz":
            continue
        direction = str(m.get("direction", "")).lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        trader_count = int(m.get("trader_count", m.get("traderCount", 0)))
        if direction == "long":
            out[token] = (pct * 100, trader_count)
        elif direction == "short":
            out[token] = ((1 - pct) * 100, trader_count)
    return out


# ═══════════════════════════════════════════════════════════════
# CROWDING SCORING (preserved verbatim from v6.2)
# ═══════════════════════════════════════════════════════════════

def score_crowding(asset, sm_long_pct, sm_count):
    """Score how crowded an asset is. Higher = more one-sided.
    Returns (score, crowd_direction, details)."""
    funding = asset["funding"]
    funding_ann = abs(funding) * 8760  # hourly funding × 24 × 365 = annualized %

    score = 0
    details = []
    crowd_direction = None

    # Funding extremity (biggest signal)
    if funding_ann >= 60:
        score += 4
        details.append(f"funding_extreme_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= 40:
        score += 3
        details.append(f"funding_high_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= MIN_FUNDING_ANNUALIZED_PCT:
        score += 2
        details.append(f"funding_elevated_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    else:
        details.append(f"funding_below_floor_{funding_ann:.0f}%ann")
        if funding != 0:
            crowd_direction = "LONG" if funding > 0 else "SHORT"

    # SM concentration (top traders tilted one way)
    sm_tilt = abs(sm_long_pct - 50)
    if sm_tilt > 20:
        score += 3
        sm_dir = "LONG" if sm_long_pct > 50 else "SHORT"
        details.append(f"sm_tilted_{sm_dir}_{sm_long_pct:.0f}%")
        if (funding > 0 and sm_long_pct > 50) or (funding < 0 and sm_long_pct < 50):
            score += 1
            details.append("sm_confirms_funding")
    elif sm_tilt > 12:
        score += 1
        details.append(f"sm_leaning_{sm_long_pct:.0f}%")

    # OI concentration (positions building, not churning)
    if asset["oi_usd"] > 20_000_000:
        score += 2
        details.append(f"oi_concentrated_{asset['oi_usd']/1e6:.0f}M")
    elif asset["oi_usd"] > 10_000_000:
        score += 1
        details.append(f"oi_moderate_{asset['oi_usd']/1e6:.0f}M")

    return score, crowd_direction, details


# ═══════════════════════════════════════════════════════════════
# EXHAUSTION DETECTION (preserved verbatim from v6.2)
# ═══════════════════════════════════════════════════════════════

def detect_exhaustion(coin, crowd_direction):
    """Check if the crowded trade is showing exhaustion signals.
    Returns (score, signals_list, price_chg_4h, rsi)."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data:
        return 0, [], 0, None

    inner = data.get("data", data) if isinstance(data, dict) else {}
    candles_1h = (inner.get("candles", {}) or {}).get("1h", []) or []
    candles_4h = (inner.get("candles", {}) or {}).get("4h", []) or []

    if len(candles_1h) < 12 or len(candles_4h) < 6:
        return 0, [], 0, None

    score = 0
    signals = []

    # SIGNAL 1: Volume declining while funding stays extreme = exhaustion building
    if len(candles_1h) >= 8:
        recent_vol = sum(safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]) / 3
        earlier_vol = sum(safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-8:-3]) / 5
        if earlier_vol > 0 and recent_vol < earlier_vol * 0.7:
            score += 3
            signals.append(f"volume_declining_{recent_vol/earlier_vol:.0%}")

    # SIGNAL 2: Price stalling despite extreme positioning
    closes_4h = [safe_float(c.get("close", c.get("c", 0))) for c in candles_4h[-4:]]
    price_change_4h = 0
    if len(closes_4h) >= 4 and closes_4h[-4] > 0:
        price_change_4h = ((closes_4h[-1] - closes_4h[-4]) / closes_4h[-4]) * 100
        if crowd_direction == "LONG" and price_change_4h < 0.5:
            score += 3
            signals.append(f"price_stalled_crowd_long_{price_change_4h:+.1f}%")
        elif crowd_direction == "SHORT" and price_change_4h > -0.5:
            score += 3
            signals.append(f"price_stalled_crowd_short_{price_change_4h:+.1f}%")

    # SIGNAL 3: Volume spike without price follow-through (capitulation wick)
    if len(candles_1h) >= 6:
        latest_vol = safe_float(candles_1h[-1].get("volume", candles_1h[-1].get("v", candles_1h[-1].get("vlm", 0))))
        avg_vol = sum(safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-1]) / 5
        latest_close = safe_float(candles_1h[-1].get("close", candles_1h[-1].get("c", 0)))
        prev_close = safe_float(candles_1h[-2].get("close", candles_1h[-2].get("c", 0)))
        price_move = ((latest_close - prev_close) / prev_close * 100) if prev_close > 0 else 0
        if avg_vol > 0 and latest_vol > avg_vol * 2.0 and abs(price_move) < 1.0:
            score += 2
            signals.append(f"vol_spike_{latest_vol/avg_vol:.1f}x_no_follow_through")

    # SIGNAL 4: 4h RSI divergence (price flat/up but RSI declining = momentum dying)
    closes_4h_full = [safe_float(c.get("close", c.get("c", 0))) for c in candles_4h]
    rsi = None
    if len(closes_4h_full) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes_4h_full)):
            d = closes_4h_full[i] - closes_4h_full[i - 1]
            gains.append(max(0, d))
            losses.append(max(0, -d))
        period = 14
        if len(gains) >= period:
            avg_g = sum(gains[-period:]) / period
            avg_l = sum(losses[-period:]) / period
            rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100

            if crowd_direction == "LONG" and rsi < 55:
                score += 2
                signals.append(f"rsi_divergence_crowd_long_rsi_{rsi:.0f}")
            elif crowd_direction == "SHORT" and rsi > 45:
                score += 2
                signals.append(f"rsi_divergence_crowd_short_rsi_{rsi:.0f}")

    return score, signals, price_change_4h, rsi


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE TRACKING (preserved verbatim from v6.2)
# ═══════════════════════════════════════════════════════════════

def check_persistence(history, coin, crowd_score):
    """Track how long crowding has been elevated. Returns (persisted, hours, peak)."""
    now_ts = time.time()

    if coin not in history:
        history[coin] = {
            "firstSeen": cfg.now_iso(),
            "ts": now_ts,
            "peakScore": crowd_score,
            "belowThresholdCount": 0,
        }
        return False, 0, crowd_score

    entry = history[coin]
    hours = (now_ts - entry.get("ts", now_ts)) / 3600
    if crowd_score > entry.get("peakScore", 0):
        entry["peakScore"] = crowd_score
    entry["belowThresholdCount"] = 0
    return hours >= MIN_PERSIST_HOURS, hours, entry.get("peakScore", crowd_score)


def mark_below_threshold(history, coin):
    """Mark a coin as below threshold. Returns True if persistence should
    be cleared (exceeded tolerance), False to keep tracking.
    v5.3 tolerance: 2 consecutive below-threshold scans before clear."""
    if coin not in history:
        return True  # nothing to track
    entry = history[coin]
    below_count = entry.get("belowThresholdCount", 0) + 1
    entry["belowThresholdCount"] = below_count
    return below_count > BELOW_THRESHOLD_TOLERANCE


def clear_persistence(history, coin):
    history.pop(coin, None)


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_payload(c, leverage, margin_usd):
    """All declared scanner fields go in `data`, NOT `meta`. (Turbine v2.0.11.)"""
    return {
        "address": OWL_WALLET,
        "scannerId": SCANNER_NAME,
        "signalType": "OWL_CONTRARIAN_UNWIND",
        "asset": c["asset"],
        "direction": c["fade_direction"],
        "score": float(c["combined_score"]),
        "timestamp": int(time.time() * 1000),
        "factors": {},
        "data": {
            "score": c["combined_score"],
            "leverage": leverage,
            "marginUsd": margin_usd,
            "crowdDirection": c["crowd_direction"],
            "crowdingScore": c["crowding_score"],
            "exhaustionScore": c["exhaustion_score"],
            "persistenceHours": round(c["persistence_hours"], 2),
            "fundingAnnualizedPct": round(c["funding_ann"], 2),
            "smTilt": round(c["sm_tilt"], 2),
            "smLongPct": round(c["sm_long_pct"], 2),
            "oiUsd": round(c["oi_usd"], 2),
            "priceChg4hPct": round(c["price_chg_4h"], 3),
            "rsi4h": round(c["rsi"], 1) if c.get("rsi") is not None else 0,
            "peakCrowdingScore": c["peak_crowding_score"],
            "exhaustionSignals": " | ".join(c.get("exhaustion_signals", [])),
            "reasons": " | ".join(c.get("reasons", [])),
        },
        "meta": {
            "_owl_producer_version": "7.0.0",
        },
    }


def push_signal(payload):
    """Push to runtime via openclaw CLI (Turbine v2.0.8 invocation shape)."""
    if not OWL_WALLET:
        cfg.log("OWL_WALLET env var not set; cannot push signal")
        return False

    cmd = [
        OPENCLAW_BIN, "senpi", "external-scanner", "ingest",
        "--address", OWL_WALLET,
        "--scanner", SCANNER_NAME,
        "--payload", json.dumps(payload),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            cfg.log(f"ingest failed for {payload['asset']} {payload['direction']}: {r.stderr.strip()}")
            return False
        if r.stdout.strip():
            try:
                response = json.loads(r.stdout)
                if isinstance(response, dict) and response.get("ok") is False:
                    cfg.log(f"ingest rejected for {payload['asset']}: {response.get('error')}")
                    return False
            except (json.JSONDecodeError, TypeError):
                pass
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        cfg.log(f"ingest exception for {payload['asset']}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    # Fail loud if wallet not configured (Turbine v2.0.9 pattern).
    if not OWL_WALLET:
        cfg.output({
            "status": "error",
            "error": "OWL_WALLET env var not set. Set it to the Owl strategy wallet (must match runtime.yaml).",
            "_owl_producer_version": "7.0.0",
        })
        return

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({
            "status": "skip",
            "reason": "previous run still active — cron reentrancy guard",
            "_owl_producer_version": "7.0.0",
        }))
        return

    try:
        # 1. Read account value for sizing + dynamic cap
        account_value, pos_count = get_account_value()
        if account_value is None or account_value <= 0:
            cfg.output({
                "status": "ok",
                "note": "cannot read account value; skip tick",
                "_owl_producer_version": "7.0.0",
            })
            return

        # 2. Producer-side dynamic daily cap (defense-in-depth alongside runtime)
        tc = load_trade_counter()
        dyn_cap = get_dynamic_daily_cap(account_value, tc.get("realizedPnl", 0))
        if tc.get("entries", 0) >= dyn_cap:
            pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
            cfg.output({
                "status": "ok",
                "note": f"dynamic cap reached: entries {tc.get('entries')}/{dyn_cap} (PnL {pnl_pct:+.1f}%)",
                "_owl_producer_version": "7.0.0",
            })
            return

        # 3. Fetch universe + SM positioning map (one call each)
        assets = fetch_all_assets()
        if not assets:
            cfg.output({
                "status": "ok",
                "note": "no assets passed OI floor; market_list_instruments may be unavailable",
                "_owl_producer_version": "7.0.0",
            })
            return

        sm_map = fetch_sm_positioning_map()

        # 4. Score crowding for each asset, update persistence history
        history = load_crowding_history()
        crowding_results = []

        for asset in assets:
            coin = asset["coin"]
            sm_long_pct, sm_count = sm_map.get(coin, (50, 0))
            crowd_score, crowd_direction, details = score_crowding(
                asset, sm_long_pct, sm_count
            )

            if crowd_score >= MIN_CROWDING_SCORE and crowd_direction:
                # Above threshold — start/continue persistence timer
                persisted, hours, peak = check_persistence(history, coin, crowd_score)
                crowding_results.append({
                    "asset": coin,
                    "crowd_score": crowd_score,
                    "crowd_direction": crowd_direction,
                    "details": details,
                    "asset_data": asset,
                    "sm_long_pct": sm_long_pct,
                    "sm_tilt": abs(sm_long_pct - 50),
                    "persisted": persisted,
                    "hours": hours,
                    "peak_score": peak,
                })
            else:
                # Below threshold — mark and possibly clear
                if mark_below_threshold(history, coin):
                    clear_persistence(history, coin)

        # Persist history immediately (always, regardless of signal emission)
        save_crowding_history(history)

        # 5. Filter to persisted candidates only
        persisted = [c for c in crowding_results if c["persisted"]]

        if not persisted:
            cfg.output({
                "status": "ok",
                "totalAssets": len(assets),
                "smCovered": len(sm_map),
                "crowding_above_floor": len(crowding_results),
                "persisted": 0,
                "note": "no assets have persisted >=1h above crowding floor",
                "_owl_producer_version": "7.0.0",
            })
            return

        # 6. Detect exhaustion only for persisted candidates (saves MCP calls)
        candidates = []
        for c in persisted:
            asset = c["asset_data"]
            coin = c["asset"]
            crowd_dir = c["crowd_direction"]

            ex_score, ex_signals, p4h, rsi = detect_exhaustion(coin, crowd_dir)
            if ex_score < MIN_EXHAUSTION_SCORE or len(ex_signals) < MIN_EXHAUSTION_SIGNALS:
                continue

            combined = c["crowd_score"] + ex_score
            if combined < MIN_COMBINED_SCORE:
                continue

            # Per-asset cooldown check (defense-in-depth)
            if is_asset_cooled_down(coin):
                continue

            funding = asset["funding"]
            funding_ann = abs(funding) * 8760
            fade_direction = "SHORT" if crowd_dir == "LONG" else "LONG"

            reasons = list(c["details"]) + ex_signals + [f"persistence_{c['hours']:.1f}h"]

            candidates.append({
                "asset": coin,
                "crowd_direction": crowd_dir,
                "fade_direction": fade_direction,
                "crowding_score": c["crowd_score"],
                "exhaustion_score": ex_score,
                "combined_score": combined,
                "persistence_hours": c["hours"],
                "peak_crowding_score": c["peak_score"],
                "funding_ann": funding_ann,
                "sm_long_pct": c["sm_long_pct"],
                "sm_tilt": c["sm_tilt"],
                "oi_usd": asset["oi_usd"],
                "price_chg_4h": p4h,
                "rsi": rsi,
                "exhaustion_signals": ex_signals,
                "reasons": reasons,
            })

        candidates.sort(key=lambda c: c["combined_score"], reverse=True)

        if not candidates:
            cfg.output({
                "status": "ok",
                "totalAssets": len(assets),
                "persisted": len(persisted),
                "candidates": 0,
                "note": "persisted but no exhaustion confluence",
                "_owl_producer_version": "7.0.0",
            })
            return

        # 7. Compute sizing + emit top candidate (conservative, like Pangolin)
        margin_usd = round(account_value * MARGIN_PCT, 2)
        pushed = 0
        for c in candidates[:1]:
            leverage = get_leverage_for_score(c["combined_score"])
            payload = build_signal_payload(c, leverage, margin_usd)
            if push_signal(payload):
                pushed += 1
                mark_asset_emitted(c["asset"])
                tc["entries"] = tc.get("entries", 0) + 1
                save_trade_counter(tc)

        elapsed = time.time() - run_start
        warn = "WARN_OVER_900S" if elapsed > 900 else None  # 15min cron
        cfg.output({
            "status": "ok",
            "totalAssets": len(assets),
            "smCovered": len(sm_map),
            "crowding_above_floor": len(crowding_results),
            "persisted": len(persisted),
            "candidates": len(candidates),
            "signals_pushed": pushed,
            "emitted_asset": candidates[0]["asset"] if pushed else None,
            "emitted_score": candidates[0]["combined_score"] if pushed else None,
            "emitted_leverage": get_leverage_for_score(candidates[0]["combined_score"]) if pushed else None,
            "account_value": round(account_value, 2),
            "open_positions": pos_count,
            "dynamic_cap": dyn_cap,
            "entries_today": tc.get("entries", 0),
            "elapsed_sec": round(elapsed, 2),
            "warn": warn,
            "_owl_producer_version": "7.0.0",
        })
    finally:
        release_lock(lock)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
