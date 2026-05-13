#!/usr/bin/env python3
# Senpi OWL Producer v8.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""OWL v8.0.0 Producer — Pure Contrarian Crowding-Unwind, helpers-native.

v8.0.0 (2026-05-12) — plumbing migration. NO thesis change.
All v7.1 thesis preserved: counter-trade crowded crypto perps with
1h+ persistence + exhaustion confluence, MACRO_TREND_GATE (|BTC 4h|
> 3% blocks fades), conviction-scaled leverage (7/8/10 by score),
MARGIN_PCT 0.25, MIN_COMBINED_SCORE 12, XYZ banned, 6h post-loss
asset cooldown, dynamic daily cap by PnL.

Six-layer plumbing flip (same pattern as Wolverine/Lemon/Cheetah
v3.0+ migrations on @senpi/runtime 1.1.0):
  1. MCP calls — cfg.mcporter_call() shim routes through
     SenpiClient.mcp_call() (direct HTTPS, no mcporter subprocess).
  2. Signal emit — subprocess.run(["openclaw","senpi",
     "external-scanner","ingest"...]) replaced by
     cfg._wrapper_client.push_signal(...). signal_type passed as
     explicit kwarg ("OWL_CONTRARIAN_FADE") to avoid relying on the
     runtime YAML's defaultSignalType fallback.
  3. Reentrancy — hand-rolled fcntl flock dropped. producer_daemon
     owns per-tick scanner_lock with stale-PID auto-recovery.
  4. Tick scheduling — openclaw cron + agentTurn replaced by
     producer_daemon (long-lived process; zero per-tick LLM cost).
     15-minute cadence preserved.
  5. /state alive_check — wallet=/scanner= kwargs passed to
     producer_daemon; daemon self-terminates when the runtime is
     deleted or the scanner is renamed.
  6. State files unchanged — crowding-history.json,
     asset-cooldowns.json, trade-counter.json under
     state/<wallet-hash>/.

v7.1 thesis preserved unchanged:
  - minCrowdingScore 6, minPersistHours 1, minExhaustionSignals 2,
    minExhaustionScore 5, minCombinedScore 12
  - belowThresholdTolerance 2 (v5.3 noise-tick guard)
  - assetCooldownMinutes 360 (6h post-loss)
  - minFundingAnnualizedPct 12 (v5.2)
  - macroGateBtc4hPct 3.0 (v7.1)
  - XYZ_BANNED = True (Owl's scoring uses funding + SM long_pct
    calibrated on crypto data shape; XYZ unban deferred)

Environment variables:
  SENPI_AUTH_TOKEN  — for MCP + signal POST (required)
  OWL_WALLET        — Owl wallet (must match runtime YAML's wallet).
                      Agent-specific by design — do NOT fall back to a
                      generic STRATEGY_ADDRESS. Per Turbine v2.0.9
                      contamination fix. Also falls back to
                      config.wallet.
  OWL_MARGIN_PCT    — optional, default 0.25 (25% of account value)
  OWL_MIN_OI_USD    — optional, default 3000000 ($3M liquidity floor)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "8.0.0"
# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "owl_signals"
# Signal type passed explicitly to push_signal(). Don't rely on the
# scanner's defaultSignalType fallback.
SIGNAL_TYPE = "OWL_CONTRARIAN_FADE"


# ═══════════════════════════════════════════════════════════════
# WALLET RESOLUTION (env var > config.wallet)
# ═══════════════════════════════════════════════════════════════

def _resolve_wallet():
    """Resolve Owl strategy wallet. OWL_WALLET env var first, then
    config.json wallet. Agent-specific env var by design — do NOT
    use a generic STRATEGY_ADDRESS. Per Turbine v2.0.9 contamination
    fix."""
    env_val = (os.environ.get("OWL_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()
MARGIN_PCT = float(os.environ.get("OWL_MARGIN_PCT", "0.25"))
MIN_OI_USD = float(os.environ.get("OWL_MIN_OI_USD", "3000000"))


# Wallet-isolated state dir.
def _wallet_state_dir():
    if STRATEGY_ADDRESS:
        h = hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_CROWDING_FILE = _STATE_DIR / "crowding-history.json"
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_COUNTER_FILE = _STATE_DIR / "trade-counter.json"


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS (fleet-tuned, preserved verbatim from v7.1)
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
XYZ_BANNED = True                     # Owl's score_crowding uses funding + SM long_pct
                                       # calibrated on crypto data shape; XYZ funding/SM
                                       # dynamics are different. Lemon v1.3 lifted
                                       # XYZ_BANNED but Lemon's thesis is simpler. Revisit
                                       # Owl XYZ unban with XYZ-specific scoring.

# v7.1 — MACRO TREND GATE preserved. Fade thesis (counter-trade crowded
# positions) structurally fails when macro is trending. Block crypto
# fades when |BTC 4h move| > 3%. 3.0% threshold matches Condor + Lemon
# precedent.
MACRO_GATE_BTC_4H_PCT = 3.0

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
    if not STRATEGY_ADDRESS:
        return None, None
    ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                            strategy_wallet=STRATEGY_ADDRESS)
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


def fetch_held_assets():
    """Return list of currently-held asset symbols (any direction)."""
    if not STRATEGY_ADDRESS:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
        if not ch:
            return []
        data = ch.get("data", ch) if isinstance(ch, dict) else {}
        held = []
        for section in ("main", "xyz"):
            s = data.get(section, {}) if isinstance(data, dict) else {}
            if not isinstance(s, dict):
                continue
            for ap in s.get("assetPositions", []) or []:
                pos = ap.get("position", ap) if isinstance(ap, dict) else {}
                szi = safe_float(pos.get("szi", 0))
                if szi == 0:
                    continue
                coin = pos.get("coin", "")
                if coin:
                    held.append(coin)
        return held
    except Exception:
        return []


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
    """Returns (sm_map, btc_p4h) where sm_map = {coin: (long_pct, trader_count)}
    for crypto markets and btc_p4h is BTC's 4h price change percent (used by
    MACRO_TREND_GATE).

    v7.0: fetch ONCE per scan instead of per-asset (Pangolin v2.0 pattern).
    v7.1: extract BTC 4h price change from same call (no extra MCP cost)."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=200)
    if not raw:
        return {}, 0.0
    sm = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(sm, dict):
        sm = sm.get("markets", sm.get("leaderboard", sm))
    if isinstance(sm, dict):
        sm = sm.get("markets", [])
    if not isinstance(sm, list):
        return {}, 0.0

    out = {}
    btc_p4h = 0.0
    for m in sm:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        dex = str(m.get("dex", "")).lower()
        if not token or dex == "xyz":
            continue
        # v7.1: capture BTC's 4h move from the same scan (macro gate input)
        if token == "BTC":
            btc_p4h = safe_float(m.get("token_price_change_pct_4h",
                                        m.get("price_change_4h", 0)))
        direction = str(m.get("direction", "")).lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        trader_count = int(m.get("trader_count", m.get("traderCount", 0)))
        if direction == "long":
            out[token] = (pct * 100, trader_count)
        elif direction == "short":
            out[token] = ((1 - pct) * 100, trader_count)
    return out, btc_p4h


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
# SIGNAL EMISSION (helpers-native)
# ═══════════════════════════════════════════════════════════════

def push_signal(c, leverage, margin_usd, held_assets):
    """Push a contrarian-fade signal via senpi_runtime_helpers.

    Direct HTTP POST to runtime API on 127.0.0.1; no subprocess.
    asset/direction/signal_type at top-level kwargs per the
    SignalItem wire schema. Score normalized 0..1 for SignalItem.score
    (Owl combined score scaled), with full telemetry in `data`.
    """
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set OWL_WALLET env var "
            "or 'wallet' in owl-config.json",
            file=sys.stderr,
        )
        return False

    if c["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
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
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=c["asset"],
            direction=c["fade_direction"],
            # Combined score floor 12, max ~24 (crowding 10 + exhaustion 10
            # + slack). Normalize on 20 for headroom; cap at 1.0.
            score=min(c["combined_score"] / 20.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {c['asset']} {c['fade_direction']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        print(f"INGEST_EXCEPTION {c['asset']} {c['fade_direction']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner lock — producer_daemon owns scanner_lock."""
    run_start = time.time()

    # Fail loud if wallet not configured (Turbine v2.0.9 pattern).
    if not STRATEGY_ADDRESS:
        cfg.output({
            "status": "error",
            "error": "OWL_WALLET env var not set and config.wallet empty. Set OWL_WALLET to the Owl strategy wallet (must match runtime.yaml).",
            "_owl_producer_version": VERSION,
        })
        return

    # 1. Read account value for sizing + dynamic cap
    account_value, pos_count = get_account_value()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_owl_producer_version": VERSION,
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
            "_owl_producer_version": VERSION,
        })
        return

    # 3. Fetch universe + SM positioning map (one call each)
    assets = fetch_all_assets()
    if not assets:
        cfg.output({
            "status": "ok",
            "note": "no assets passed OI floor; market_list_instruments may be unavailable",
            "_owl_producer_version": VERSION,
        })
        return

    sm_map, btc_p4h = fetch_sm_positioning_map()

    # v7.1 — MACRO TREND GATE. Block fades when BTC's 4h move is
    # extreme. Mean reversion thesis structurally fails in trending
    # regimes — alts that look "crowded and exhausting" during macro
    # trend moves are usually consolidating before continuation.
    if abs(btc_p4h) > MACRO_GATE_BTC_4H_PCT:
        cfg.output({
            "status": "ok",
            "totalAssets": len(assets),
            "smCovered": len(sm_map),
            "note": (
                f"MACRO_GATE — BTC 4h {btc_p4h:+.2f}% > "
                f"{MACRO_GATE_BTC_4H_PCT}% threshold; fades unsafe in trending regime"
            ),
            "btc_p4h": btc_p4h,
            "_owl_producer_version": VERSION,
        })
        return

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
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "totalAssets": len(assets),
            "smCovered": len(sm_map),
            "crowding_above_floor": len(crowding_results),
            "persisted": 0,
            "scanned": len(assets),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_COMBINED_SCORE,
            "note": "no assets have persisted >=1h above crowding floor",
            "elapsed_sec": round(elapsed, 2),
            "_owl_producer_version": VERSION,
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
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "totalAssets": len(assets),
            "persisted": len(persisted),
            "scanned": len(assets),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_COMBINED_SCORE,
            "note": "persisted but no exhaustion confluence",
            "elapsed_sec": round(elapsed, 2),
            "_owl_producer_version": VERSION,
        })
        return

    # 7. Compute sizing + emit top candidate
    held_assets = fetch_held_assets()
    margin_usd = round(account_value * MARGIN_PCT, 2)

    pushed = 0
    emitted_asset = None
    emitted_score = None
    emitted_leverage = None
    for c in candidates[:1]:
        leverage = get_leverage_for_score(c["combined_score"])
        if push_signal(c, leverage, margin_usd, held_assets):
            pushed += 1
            emitted_asset = c["asset"]
            emitted_score = c["combined_score"]
            emitted_leverage = leverage
            mark_asset_emitted(c["asset"])
            tc["entries"] = tc.get("entries", 0) + 1
            save_trade_counter(tc)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(assets),
        "candidates": len(candidates),
        "signals_pushed": pushed,
        "min_score": MIN_COMBINED_SCORE,
        "totalAssets": len(assets),
        "smCovered": len(sm_map),
        "crowding_above_floor": len(crowding_results),
        "persisted": len(persisted),
        "emitted_asset": emitted_asset,
        "emitted_score": emitted_score,
        "emitted_leverage": emitted_leverage,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "dynamic_cap": dyn_cap,
        "entries_today": tc.get("entries", 0),
        "btc_p4h": btc_p4h,
        "elapsed_sec": round(elapsed, 2),
        "_owl_producer_version": VERSION,
    })


if __name__ == "__main__":
    # v8.0.0 — long-lived daemon. Replaces openclaw cron + agentTurn.
    # producer_daemon owns the per-tick scanner_lock with stale-PID
    # auto-recovery. 15-minute cadence preserved from v6/v7.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=900,                  # 15min — preserved v6/v7 cadence
        name=f"owl-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=600,                      # Owl can do many MCP calls per tick
    )
