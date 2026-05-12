#!/usr/bin/env python3
# Senpi OTTER Producer v2.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""OTTER v2.0.0 Producer — Open Interest Velocity Hunter (helpers-native).

PLUMBING-ONLY MIGRATION from v1.0. NO thesis change. NO scoring change.
Producer ports onto senpi_runtime_helpers (in-process SenpiClient + direct
HTTP POST to runtime /signals + producer_daemon long-lived loop).

Otter is the FIRST fleet agent to use OI velocity (delta-over-time) as
a primary trading signal. Every other agent that touches OI (Mamba,
Condor, Barracuda, Pangolin) reads it as a snapshot filter.

Thesis: when 1h OI delta is >= 5% AND price moves in the same direction
by >= 0.5%, that's the TOP-LEFT (LONGS entering) or TOP-RIGHT (SHORTS
entering) quadrant of the OI/price matrix — fresh leveraged positioning
with directional conviction. Otter rides for 1-3 hours.

Architecture (v2-native, matches Roach v2 / Pangolin v2 / Jackal v2):
  1. This producer (cron, 5min): emits flow-detected signals only.
  2. Runtime (senpi-trading-runtime): receives signals via
     external_scanner ingest, LLM-gates them (pass-through), executes
     with FEE_OPTIMIZED_LIMIT (entries: maker-only; exits: maker-first
     + taker fallback), and manages DSL exits autonomously.

The producer's responsibility:
  1. Maintain rolling OI history per asset (state/<wallet-hash>/oi-history.json,
     last 60 samples = 5h at 5min cadence)
  2. Compute 1h and 4h OI deltas + price deltas
  3. Apply 4-quadrant filter (TOP quadrants only — fresh flow)
  4. Score + rank candidates with multi-signal confluence
  5. Push top candidate to runtime via openclaw CLI

NO execution code. NO DSL code. NO position-tracking. NO risk gates
beyond per-asset cooldown.

Bootstrap behavior: first 12 cron ticks (1 hour) build the rolling
window with no signals emitted — output reports "bootstrapping_history".
After that, 1h delta is computable. After 48 ticks (4h), 4h
confirmation also active.

Environment variables:
  SENPI_AUTH_TOKEN  — required for MCP + signal POST (helpers SenpiClient)
  OTTER_WALLET      — Otter wallet (must match runtime YAML's wallet).
                      AGENT-SPECIFIC env var by design — do NOT fall back
                      to a generic STRATEGY_ADDRESS. Per Turbine v2.0.9
                      contamination fix. (STRATEGY_ADDRESS is honored
                      with deprecation warning for transition only.)
  EXTERNAL_SCANNER_NAME — optional override (default "otter_signals")
  OTTER_MARGIN_PCT  — optional, default 0.25 (25% of account value)
  OTTER_MIN_OI_DELTA_PCT — optional, default 5.0 (1h OI delta floor)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import otter_config as cfg

_sdk_path = str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))
                    / "skills" / "senpi-trading-runtime")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import producer_daemon  # type: ignore  # noqa: E402


# v2.0.0: Agent-specific wallet env var (Turbine v2.0.9 contamination fix).
def _resolve_wallet():
    env_val = (os.environ.get("OTTER_WALLET") or "").strip()
    if env_val:
        return env_val
    legacy = (os.environ.get("STRATEGY_ADDRESS") or "").strip()
    if legacy:
        sys.stderr.write(
            "[otter v2.0.0] DEPRECATION: STRATEGY_ADDRESS env var is BANNED "
            "by v2.0.9 contamination rule. Set OTTER_WALLET instead. "
            "Honoring legacy value for this run only.\n"
        )
        return legacy
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


OTTER_WALLET = _resolve_wallet()
STRATEGY_ADDRESS = OTTER_WALLET  # alias used by signal payload
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "otter_signals")
SIGNAL_TYPE = "OTTER_OI_VELOCITY"
MARGIN_PCT = float(os.environ.get("OTTER_MARGIN_PCT", "0.25"))
MIN_OI_DELTA_1H_PCT = float(os.environ.get("OTTER_MIN_OI_DELTA_PCT", "5.0"))


# Wallet-isolated state dir.
def _wallet_state_dir():
    if OTTER_WALLET:
        h = hashlib.sha256(OTTER_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_OI_HISTORY_FILE = _STATE_DIR / "oi-history.json"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_wallet_lock_id = (hashlib.sha256(OTTER_WALLET.lower().encode()).hexdigest()[:8]
                   if OTTER_WALLET else "unset")


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS (fleet-tuned)
# ═══════════════════════════════════════════════════════════════
MIN_SCORE = 9                          # high bar (Polar v2.4 / Cheetah v5.2 / Roach v1.1 pattern)
MIN_PRICE_ALIGN_PCT = 0.5              # 1h price must move at least 0.5% in same direction as OI
MIN_OI_USD = 1_000_000                 # liquidity floor
MAX_SPREAD_BPS = 5                     # entry quality gate
ASSET_COOLDOWN_MINUTES = 240
XYZ_BANNED = True                      # different OI dynamics; Bald Eagle's territory

# Rolling history config — 5min cadence, 60 samples = 5h window
HISTORY_MAX_SAMPLES = 60
SAMPLES_FOR_1H = 12                    # 12 samples × 5 min = 60 min
SAMPLES_FOR_4H = 48                    # 48 samples × 5 min = 240 min
MIN_SAMPLES_TO_FIRE = SAMPLES_FOR_1H   # need at least 1h of history to compute 1h delta

# Conviction-scaled leverage (Polar v2.4 / Bald Eagle v3.0 pattern)
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 10},
    {"min_score": 11, "leverage": 7},
    {"min_score": 9,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5


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


def time_of_day_modifier():
    """UTC time-of-day adjustment. Same logic as Roach/Bloodhound."""
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return 1, "tod_active_window"
    elif hour >= 18 or hour < 2:
        return -2, "tod_chop_zone"
    return 0, None


def is_xyz_asset(asset, dex=""):
    if not asset:
        return False
    if dex and str(dex).lower() == "xyz":
        return True
    return str(asset).lower().startswith("xyz:")


# ═══════════════════════════════════════════════════════════════
# STATE I/O
# ═══════════════════════════════════════════════════════════════

def load_oi_history():
    """Returns dict { asset: [{ts, oi, mark_px}, ...] }."""
    try:
        with open(_OI_HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_oi_history(history):
    cfg.atomic_write(str(_OI_HISTORY_FILE), history)


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


# ═══════════════════════════════════════════════════════════════
# ACCOUNT VALUE QUERY (for sizing)
# ═══════════════════════════════════════════════════════════════

def get_account_value():
    if not OTTER_WALLET:
        return None, None
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=OTTER_WALLET)
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
# UNIVERSE FETCH + HISTORY UPDATE
# ═══════════════════════════════════════════════════════════════

def fetch_instruments():
    """Pull market_list_instruments. Returns list of dicts with name,
    dex, openInterest, markPx (read from context-nested fields per
    Pangolin v1.3 fix)."""
    raw = cfg.mcporter_call("market_list_instruments")
    if not raw:
        return []
    instruments = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    parsed = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = str(inst.get("name", inst.get("coin", ""))).upper()
        dex = str(inst.get("dex", "")).lower()
        if not name:
            continue
        if XYZ_BANNED and is_xyz_asset(name, dex):
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                              inst.get("markPx", inst.get("midPx", 0)))))
        if oi <= 0 or mark_px <= 0:
            continue
        parsed.append({
            "asset": name,
            "dex": dex,
            "oi": oi,
            "mark_px": mark_px,
            "oi_usd": oi * mark_px,
        })
    return parsed


def update_history(history, instruments):
    """Append current sample for each instrument to its history.
    Trim each asset's list to HISTORY_MAX_SAMPLES."""
    now = time.time()
    for inst in instruments:
        asset = inst["asset"]
        sample = {
            "ts": now,
            "oi": inst["oi"],
            "mark_px": inst["mark_px"],
        }
        if asset not in history:
            history[asset] = []
        history[asset].append(sample)
        if len(history[asset]) > HISTORY_MAX_SAMPLES:
            history[asset] = history[asset][-HISTORY_MAX_SAMPLES:]
    # Note: assets that drop out of the universe (delisted, low liquidity)
    # keep their existing history but stop accumulating. Not pruned here
    # because the asset might come back next tick.
    return history


# ═══════════════════════════════════════════════════════════════
# DELTA COMPUTATION
# ═══════════════════════════════════════════════════════════════

def compute_deltas(samples):
    """Compute 1h and 4h OI delta % + price delta % from a list of
    samples (oldest first). Returns dict with computed deltas or None
    if insufficient history."""
    n = len(samples)
    if n < SAMPLES_FOR_1H + 1:
        return None  # need at least 1h + current

    current = samples[-1]
    sample_1h_ago = samples[-(SAMPLES_FOR_1H + 1)] if n >= SAMPLES_FOR_1H + 1 else None
    sample_4h_ago = samples[-(SAMPLES_FOR_4H + 1)] if n >= SAMPLES_FOR_4H + 1 else None

    out = {
        "samples": n,
        "current_oi": current["oi"],
        "current_px": current["mark_px"],
        "oi_delta_1h_pct": None,
        "price_delta_1h_pct": None,
        "oi_delta_4h_pct": None,
        "price_delta_4h_pct": None,
    }

    if sample_1h_ago and sample_1h_ago["oi"] > 0 and sample_1h_ago["mark_px"] > 0:
        out["oi_delta_1h_pct"] = (current["oi"] - sample_1h_ago["oi"]) / sample_1h_ago["oi"] * 100
        out["price_delta_1h_pct"] = (current["mark_px"] - sample_1h_ago["mark_px"]) / sample_1h_ago["mark_px"] * 100

    if sample_4h_ago and sample_4h_ago["oi"] > 0 and sample_4h_ago["mark_px"] > 0:
        out["oi_delta_4h_pct"] = (current["oi"] - sample_4h_ago["oi"]) / sample_4h_ago["oi"] * 100
        out["price_delta_4h_pct"] = (current["mark_px"] - sample_4h_ago["mark_px"]) / sample_4h_ago["mark_px"] * 100

    return out


# ═══════════════════════════════════════════════════════════════
# SM CONCENTRATION (optional bonus, not a hard gate)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_map():
    """Returns {asset: {direction, pct}} for the SM concentration bonus."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
    sm = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm, dict):
        sm = sm.get("markets", sm)
    if isinstance(sm, dict):
        sm = sm.get("markets", [])
    if not isinstance(sm, list):
        return {}
    out = {}
    for m in sm:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if dex == "xyz":
            continue
        if not token:
            continue
        out[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)) * 100,
            "traders": int(m.get("trader_count", 0)),
        }
    return out


# ═══════════════════════════════════════════════════════════════
# SPREAD CHECK (required before emit)
# ═══════════════════════════════════════════════════════════════

def fetch_spread_bps(asset):
    """Pull orderbook spread for a single asset. Returns None on failure."""
    try:
        r = cfg.mcporter_call(
            "market_get_asset_data",
            asset=asset,
            candle_intervals=[],
            include_funding=False,
            include_order_book=True,
        )
        if not r:
            return None
        data = r.get("data", r) if isinstance(r, dict) else {}
        if not isinstance(data, dict):
            return None
        ob = data.get("order_book") or data.get("orderBook") or {}
        if not isinstance(ob, dict):
            return None
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        if not bids or not asks:
            return None
        best_bid = safe_float((bids[0] or {}).get("price")
                               or (bids[0] or {}).get("px"))
        best_ask = safe_float((asks[0] or {}).get("price")
                               or (asks[0] or {}).get("px"))
        if best_bid <= 0 or best_ask <= 0:
            return None
        mid = (best_bid + best_ask) / 2
        if mid <= 0:
            return None
        return (best_ask - best_bid) / mid * 10000
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# CANDIDATE BUILDING + SCORING (4-quadrant filter)
# ═══════════════════════════════════════════════════════════════

def build_candidates(instruments, history, sm_map):
    """Apply 4-quadrant filter + scoring. Returns sorted list of
    candidates. Skips bootstrapping assets (insufficient history)."""
    candidates = []
    bootstrapping = 0

    for inst in instruments:
        asset = inst["asset"]
        oi_usd = inst["oi_usd"]

        # Liquidity floor
        if oi_usd < MIN_OI_USD:
            continue

        samples = history.get(asset, [])
        if len(samples) < MIN_SAMPLES_TO_FIRE + 1:
            bootstrapping += 1
            continue

        deltas = compute_deltas(samples)
        if not deltas or deltas["oi_delta_1h_pct"] is None:
            bootstrapping += 1
            continue

        oi_d_1h = deltas["oi_delta_1h_pct"]
        px_d_1h = deltas["price_delta_1h_pct"]
        oi_d_4h = deltas.get("oi_delta_4h_pct")
        px_d_4h = deltas.get("price_delta_4h_pct")

        # ── HARD GATE 1: 1h OI delta floor ──
        if abs(oi_d_1h) < MIN_OI_DELTA_1H_PCT:
            continue

        # ── HARD GATE 2: top-quadrant only (OI ↑) ──
        # Bottom quadrants (OI ↓) are unwinding signals → Pangolin/Owl territory.
        if oi_d_1h <= 0:
            continue

        # ── HARD GATE 3: 1h price aligned with conviction direction ──
        # If OI is growing but price is flat or moves the wrong way, this is
        # ambiguous flow (could be hedging) — skip.
        if px_d_1h is None:
            continue
        if abs(px_d_1h) < MIN_PRICE_ALIGN_PCT:
            continue
        # Flow direction = price direction (since OI is growing, the flow is
        # adding liquidity to whichever side the price is going).
        flow_direction = "LONG" if px_d_1h > 0 else "SHORT"

        # ── HARD GATE 4: 4h OI not net unwinding ──
        # If 1h OI is up but 4h OI is down, the recent 1h is an inversion of
        # a longer unwind. Skip — the trend is against us.
        if oi_d_4h is not None and oi_d_4h < 0:
            continue

        # ── SCORING ──
        score = 0.0
        reasons = [
            f"OI_DELTA_1H {oi_d_1h:+.1f}%",
            f"PRICE_DELTA_1H {px_d_1h:+.2f}%",
        ]

        # 1h OI delta tier (4-6 points)
        abs_oi_d = abs(oi_d_1h)
        if abs_oi_d > 20:
            score += 6
            reasons.append("OI_TIER_EXTREME")
        elif abs_oi_d > 10:
            score += 5
            reasons.append("OI_TIER_HIGH")
        else:
            score += 4
            reasons.append("OI_TIER_BASE")

        # 4h confirmation (+2) or contradiction (-2)
        if oi_d_4h is not None:
            if oi_d_4h >= 10:
                score += 2
                reasons.append(f"OI_4H_CONFIRMS {oi_d_4h:+.1f}%")
            elif oi_d_4h < 0:
                score -= 2
                reasons.append(f"OI_4H_CONTRADICTS {oi_d_4h:+.1f}%")

        # 1h price magnitude
        abs_px_d = abs(px_d_1h)
        if abs_px_d > 2:
            score += 2
            reasons.append("PRICE_STRONG")
        elif abs_px_d > 1:
            score += 1
            reasons.append("PRICE_MODERATE")

        # SM concentration alignment
        sm = sm_map.get(asset)
        sm_aligned = False
        sm_pct = 0.0
        if sm:
            sm_dir = sm.get("direction", "")
            sm_pct = sm.get("pct", 0)
            if sm_dir == flow_direction and sm_pct >= 5:
                score += 2
                sm_aligned = True
                reasons.append(f"SM_ALIGNED {sm_pct:.1f}%")

        # Time-of-day modifier
        tod_mod, tod_reason = time_of_day_modifier()
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        candidates.append({
            "asset": asset,
            "direction": flow_direction,
            "score": round(score, 2),
            "oi_delta_1h_pct": oi_d_1h,
            "oi_delta_4h_pct": oi_d_4h,
            "price_delta_1h_pct": px_d_1h,
            "price_delta_4h_pct": px_d_4h,
            "oi_usd": oi_usd,
            "mark_px": deltas["current_px"],
            "samples": deltas["samples"],
            "sm_aligned": sm_aligned,
            "sm_pct": sm_pct,
            "reasons": reasons,
            # spread filled in later (only for top scorer)
            "spread_bps": None,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates, bootstrapping


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_data(c, leverage, margin_usd):
    """v2.0.0: helpers `push_signal()` takes asset/direction/signal_type/score
    as top-level kwargs. Everything else goes in `data`."""
    return {
        "score": c["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "oiDelta1hPct": round(c["oi_delta_1h_pct"], 3),
        "priceDelta1hPct": round(c["price_delta_1h_pct"], 3),
        "oiUsd": round(c["oi_usd"], 2),
        "oiDelta4hPct": round(c["oi_delta_4h_pct"], 3) if c["oi_delta_4h_pct"] is not None else 0,
        "priceDelta4hPct": round(c["price_delta_4h_pct"], 3) if c["price_delta_4h_pct"] is not None else 0,
        "spreadBps": round(c["spread_bps"], 2) if c["spread_bps"] is not None else 0,
        "smAligned": bool(c["sm_aligned"]),
        "smPctOfTopTraders": round(c["sm_pct"], 2),
        "markPx": round(c["mark_px"], 6),
        "samplesInHistory": int(c["samples"]),
        "reasons": " | ".join(c.get("reasons", [])),
        "_otter_producer_version": "2.0.0",
    }


def push_signal(c, leverage, margin_usd):
    """v2.0.0: direct POST to runtime /signals via helpers SenpiClient."""
    if not STRATEGY_ADDRESS:
        cfg.log("OTTER_WALLET env var not set; cannot push signal")
        return False
    data_block = build_signal_data(c, leverage, margin_usd)
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=c["asset"],
            direction=c["direction"],
            signal_type=SIGNAL_TYPE,
            score=float(c["score"]),
            data=data_block,
        )
        return True
    except Exception as e:  # noqa: BLE001
        cfg.log(f"push_signal failed for {c['asset']} {c['direction']}: "
                f"{type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    # Fail loud if wallet not configured (Turbine v2.0.9 pattern).
    if not OTTER_WALLET:
        cfg.output({
            "status": "error",
            "error": "OTTER_WALLET env var not set. Set it to the Otter strategy wallet (must match runtime.yaml).",
            "_otter_producer_version": "2.0.0",
        })
        return

    # 1. Read account value for sizing
    account_value, pos_count = get_account_value()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_otter_producer_version": "2.0.0",
        })
        return

    # 2. Fetch instrument universe
    instruments = fetch_instruments()
    if not instruments:
        cfg.output({
            "status": "error",
            "error": "market_list_instruments fetch failed or empty",
            "_otter_producer_version": "2.0.0",
        })
        return

    # 3. Update OI history
    history = load_oi_history()
    history = update_history(history, instruments)
    save_oi_history(history)

    # 4. Compute deltas + apply quadrant filter + score
    sm_map = fetch_sm_map()
    candidates, bootstrapping = build_candidates(instruments, history, sm_map)

    # 5. Filter cooldown + min score
    eligible = [
        c for c in candidates
        if c["score"] >= MIN_SCORE
        and not is_asset_cooled_down(c["asset"])
    ]

    total_assets_tracked = len(instruments)
    if not eligible and bootstrapping > 0:
        cfg.output({
            "status": "ok",
            "note": f"bootstrapping_history ({bootstrapping}/{total_assets_tracked} assets need more samples)",
            "candidates_total": len(candidates),
            "samples_per_asset_avg": round(sum(len(s) for s in history.values()) / max(len(history), 1), 1),
            "_otter_producer_version": "2.0.0",
        })
        return

    if not eligible:
        best = candidates[0] if candidates else None
        note = "no candidates passed score+cooldown filter"
        if best:
            note = f"best {best['asset']} {best['direction']} score={best['score']} (need >= {MIN_SCORE})"
        cfg.output({
            "status": "ok",
            "note": note,
            "candidates_total": len(candidates),
            "_otter_producer_version": "2.0.0",
        })
        return

    # 6. Spread check on the top candidate (gate before emit)
    margin_usd = round(account_value * MARGIN_PCT, 2)

    emitted = None
    for c in eligible[:3]:
        spread_bps = fetch_spread_bps(c["asset"])
        c["spread_bps"] = spread_bps
        if spread_bps is None or spread_bps > MAX_SPREAD_BPS:
            c["reasons"].append(f"SKIP_SPREAD {spread_bps}")
            continue
        if spread_bps <= 2:
            c["score"] += 2
            c["reasons"].append(f"SPREAD_TIGHT {spread_bps:.1f}bps")
        else:
            c["score"] += 1
            c["reasons"].append(f"SPREAD_OK {spread_bps:.1f}bps")
        if c["score"] < MIN_SCORE:
            continue
        emitted = c
        break

    if not emitted:
        cfg.output({
            "status": "ok",
            "note": "all top candidates failed spread gate",
            "candidates_total": len(candidates),
            "eligible": len(eligible),
            "_otter_producer_version": "2.0.0",
        })
        return

    # 7. Emit
    leverage = get_leverage_for_score(emitted["score"])
    pushed = 1 if push_signal(emitted, leverage, margin_usd) else 0
    if pushed:
        mark_asset_emitted(emitted["asset"])

    elapsed = time.time() - run_start
    warn = "WARN_OVER_300S" if elapsed > 300 else None
    cfg.output({
        "status": "ok",
        "candidates_total": len(candidates),
        "eligible": len(eligible),
        "signals_pushed": pushed,
        "emitted_asset": emitted["asset"] if pushed else None,
        "emitted_score": emitted["score"] if pushed else None,
        "emitted_leverage": leverage if pushed else None,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "samples_per_asset_avg": round(sum(len(s) for s in history.values()) / max(len(history), 1), 1),
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_otter_producer_version": "2.0.0",
    })


if __name__ == "__main__":
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"otter-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
