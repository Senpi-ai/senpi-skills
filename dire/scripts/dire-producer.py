#!/usr/bin/env python3
# Senpi DIRE Producer v2.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""DIRE v2.0.0 Producer — senpi_runtime_helpers migration.

v2.0.0 (2026-05-12) — plumbing migration. NO thesis change. BRENTOIL
XYZ single-asset specialist. v1.7.0 scoring preserved verbatim:
  - 4TF alignment hard gate (5m / 15m / 1h / 4h)
  - SM HARD BLOCK (markPx vs oraclePx premium proxy)
  - SM conviction scoring (tiered premium magnitude)
  - OI velocity scoring (flat-path extraction)
  - Volume spike scoring (15m / 1h ratio, tiered)
  - Price cleanliness check (adverse-wick filter)
  - MIN_SCORE 11
  - FP-001 quiet hours (00-04 UTC unless apex 12+)
  - FP-003 require-all-confirmations gate
  - Conviction-scaled sizing tiers

Six-layer plumbing flip:
  1. MCP calls — cfg.mcporter_call() shim now routes through
     SenpiClient.mcp_call() (direct HTTPS).
  2. Signal emit — direct create_position / ratchet_stop_add replaced
     by cfg._wrapper_client.push_signal(...). signal_type passed as
     explicit kwarg ("DIRE_BRENTOIL_TREND").
  3. Reentrancy — hand-rolled state guards dropped. producer_daemon
     owns per-tick scanner_lock with stale-PID auto-recovery.
  4. Tick scheduling — openclaw cron replaced by producer_daemon
     (long-lived process).
  5. Drawdown / dynamic-daily-cap / consecutive-loss state files
     dropped. runtime.guard_rails owns them.
  6. /state alive_check — wallet=/scanner= kwargs passed to
     producer_daemon; daemon self-terminates when the runtime is
     deleted or the scanner is renamed.

v1.7.0 thesis preserved unchanged.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dire_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "2.0.1"

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "dire_signals"

# Signal type passed explicitly to push_signal(). Don't rely on the
# scanner's defaultSignalType fallback — runtime YAMLs commonly don't
# declare one, and missing tags break audit-log filtering.
SIGNAL_TYPE = "DIRE_BRENTOIL_TREND"


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var DIRE_WALLET is supported as an optional override."""
    env_val = (os.environ.get("DIRE_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved from v1.7.0 (BRENTOIL-tuned)
# ═══════════════════════════════════════════════════════════════

ASSET = "xyz:BRENTOIL"
MIN_SCORE_DEFAULT = 11             # v1.6 floor — "hit fewer, win bigger"

# Default sizing tiers (overridable via config.sizingTiers). v1.1: leverage
# and margin scale with score. Higher conviction = bigger position. Hard-
# capped at maxLeverage (default 10x = 50% of HL's 20x BRENTOIL max).
_DEFAULT_SIZING_TIERS = [
    {"minScore": 9,  "leverage": 3,  "marginPct": 0.20, "label": "cautious"},
    {"minScore": 10, "leverage": 5,  "marginPct": 0.25, "label": "standard"},
    {"minScore": 11, "leverage": 7,  "marginPct": 0.30, "label": "conviction"},
    {"minScore": 12, "leverage": 10, "marginPct": 0.30, "label": "apex"},
]


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def candle_close(c):
    return safe_float(c.get("close", c.get("c", 0)))


def candle_open(c):
    return safe_float(c.get("open", c.get("o", 0)))


def candle_high(c):
    return safe_float(c.get("high", c.get("h", 0)))


def candle_low(c):
    return safe_float(c.get("low", c.get("l", 0)))


def candle_volume(c):
    return safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))


def trend_direction(candles, n=5):
    """Determine trend direction from last n candles.

    Returns "BULLISH", "BEARISH", or "FLAT", and the % change over the window.
    """
    if not candles or len(candles) < n:
        return "FLAT", 0.0
    recent = candles[-n:]
    first = candle_close(recent[0])
    last = candle_close(recent[-1])
    if first <= 0:
        return "FLAT", 0.0
    pct = (last - first) / first * 100
    if pct > 0.15:
        return "BULLISH", pct
    if pct < -0.15:
        return "BEARISH", pct
    return "FLAT", pct


def check_4tf_alignment(candles_by_tf):
    """4TF alignment hard gate.

    Returns (direction, aligned, trend_by_tf, details).
    aligned=True only if 5m, 15m, 1h, 4h all agree on direction.
    """
    required_tfs = ["5m", "15m", "1h", "4h"]
    trends = {}
    for tf in required_tfs:
        candles = candles_by_tf.get(tf, [])
        n = 6 if tf in ("5m", "15m") else 4
        trend, pct = trend_direction(candles, n=n)
        trends[tf] = {"trend": trend, "pct": pct}
    directions = {trends[tf]["trend"] for tf in required_tfs}
    if "BULLISH" in directions and "BEARISH" not in directions and "FLAT" not in directions:
        return "LONG", True, trends, "all_bullish"
    if "BEARISH" in directions and "BULLISH" not in directions and "FLAT" not in directions:
        return "SHORT", True, trends, "all_bearish"
    return None, False, trends, f"mixed:{directions}"


def extract_oi_velocity_1h(asset_data):
    """Flat-path extraction of oi_velocity.oi_change_pct_1h.

    NOTE: Do not use nested oi_velocity["1h"]["change_pct"] — that path does
    not exist in the MCP response. Silent-None bug documented in
    reference_cobra_antipattern.md.
    """
    oi_vel = asset_data.get("oi_velocity")
    if not isinstance(oi_vel, dict):
        return None
    val = oi_vel.get("oi_change_pct_1h")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_oi_velocity(oi_vel_change):
    """OI velocity scoring.

    > +5%  → +2 (accelerating)
    > +2%  → +1 (rising)
    < -3%  → -1 (draining)
    null   → pass (0)
    """
    if oi_vel_change is None:
        return 0, None
    if oi_vel_change > 5:
        return 2, f"OI_ACCELERATING_{oi_vel_change:+.1f}%"
    if oi_vel_change > 2:
        return 1, f"OI_rising_{oi_vel_change:+.1f}%"
    if oi_vel_change < -3:
        return -1, f"OI_draining_{oi_vel_change:+.1f}%"
    return 0, None


def volume_spike_score(candles_15m, candles_1h, threshold=2.5, strong_threshold=5.0):
    """News-impact proxy: is the latest 15m volume > threshold × 1h average?

    Tiered scoring:
      > strong_threshold (5x default) → +2 (extreme news spike)
      > threshold (2.5x default)      → +1 (moderate news activity)
      otherwise                        → 0

    Returns (score, reason).
    """
    if not candles_15m or not candles_1h:
        return 0, None
    last_15m_vol = candle_volume(candles_15m[-1])
    recent_1h = candles_1h[-4:] if len(candles_1h) >= 4 else candles_1h
    vols = [candle_volume(c) for c in recent_1h if candle_volume(c) > 0]
    if not vols:
        return 0, None
    avg_15m_equiv = (sum(vols) / len(vols)) / 4
    if avg_15m_equiv <= 0:
        return 0, None
    ratio = last_15m_vol / avg_15m_equiv
    if ratio > strong_threshold:
        return 2, f"VOL_EXTREME_{ratio:.1f}x"
    if ratio > threshold:
        return 1, f"VOL_SPIKE_{ratio:.1f}x"
    return 0, None


def sm_conviction_score(premium_pct_abs, moderate_threshold=0.001, strong_threshold=0.003):
    """Score Smart Money conviction strength based on absolute premium magnitude.

    Premium is (markPx - oraclePx) / oraclePx — positive means longs aggressive,
    negative means shorts aggressive. Absolute magnitude measures conviction.

    Tiered scoring:
      |premium| > strong_threshold (0.3% default)   → +2 (extreme tilt)
      |premium| > moderate_threshold (0.1% default) → +1 (meaningful tilt)
      otherwise                                      → 0 (direction detected but weak)

    Returns (score, reason).
    """
    if premium_pct_abs is None:
        return 0, None
    if premium_pct_abs > strong_threshold:
        return 2, f"SM_EXTREME_{premium_pct_abs * 100:.3f}%"
    if premium_pct_abs > moderate_threshold:
        return 1, f"SM_STRONG_{premium_pct_abs * 100:.3f}%"
    return 0, None


def price_cleanliness_score(candles_5m, direction, max_wick_pct=1.5, lookback_minutes=30):
    """Check last 30 min of 5m candles for adverse wicks > 1.5%.

    Returns (score, reason).
    A "clean" approach (no significant wicks against direction) scores +1.
    """
    if not candles_5m or not direction:
        return 0, None
    n_candles = max(6, lookback_minutes // 5)
    recent = candles_5m[-n_candles:]
    if not recent:
        return 0, None
    for c in recent:
        o = candle_open(c)
        h = candle_high(c)
        l = candle_low(c)
        cl = candle_close(c)
        if o <= 0:
            continue
        if direction == "LONG":
            wick = (min(o, cl) - l) / o * 100
        else:  # SHORT
            wick = (h - max(o, cl)) / o * 100
        if wick > max_wick_pct:
            return 0, f"DIRTY_wick_{wick:.2f}%"
    return 1, "CLEAN_PX"


# ─── SM Direction (Smart Money) ──────────────────────────────

def get_sm_direction(asset_data, config=None):
    """Derive Smart Money direction from market data.

    For XYZ assets we use a proxy: markPx vs oraclePx premium from asset_context.
    Positive premium → mark > oracle → longs aggressive → SM LONG.
    Negative premium → mark < oracle → shorts aggressive → SM SHORT.

    Returns (direction, premium_abs, reason).
    - direction: "LONG", "SHORT", or None (ambiguous → HARD BLOCK)

    Ambiguous-premium threshold is configurable via
    `smAmbiguousPremiumAbsPct` (default 0.0001 = 0.01%). XYZ (HIP-3)
    markets structurally have smaller mark-oracle premiums than crypto
    perps because the oracle tracks mark closely by design.
    """
    ctx = asset_data.get("asset_context") or {}
    try:
        premium = float(ctx.get("premium", 0) or 0)
        mark_px = float(ctx.get("markPx", 0) or 0)
    except (TypeError, ValueError):
        return None, 0.0, "parse_error"

    if mark_px <= 0:
        return None, 0.0, "no_mark_px"

    ambiguous_threshold = 0.0001
    if config is not None:
        try:
            ambiguous_threshold = float(config.get("smAmbiguousPremiumAbsPct", 0.0001))
        except (TypeError, ValueError):
            pass

    if abs(premium) < ambiguous_threshold:
        return None, abs(premium), f"sm_ambiguous_premium_{premium * 100:+.4f}%_threshold_{ambiguous_threshold * 100:.4f}%"

    direction = "LONG" if premium > 0 else "SHORT"
    return direction, abs(premium), f"premium_{premium * 100:+.4f}%"


# ─── v1.6.0 fleet patches ────────────────────────────────────

def in_quiet_hours(config, now=None):
    """FP-001: skip emission during low-liquidity UTC window.

    Default 00:00-04:00 UTC. Returns (in_quiet_hours: bool, current_hour: int).
    Apex setups (score >= quietHoursApexBypassScore) bypass.
    """
    start = int(config.get("quietHoursStartUtc", 0))
    end = int(config.get("quietHoursEndUtc", 4))
    if start == end:
        return False, -1
    h = (now or datetime.now(timezone.utc)).hour
    if start < end:
        return (start <= h < end), h
    return (h >= start or h < end), h


def all_confirmations_present(setup):
    """FP-003: require every soft confirmation to contribute >= 1
    in addition to clearing minScore.

    The 4TF + SM hard gates are already required by evaluate_setup. This
    additionally checks that each of (Volume, OI velocity, SM premium,
    Price cleanliness) fired at minimum tier — i.e. the signal pattern is
    complete, not just score-summed.

    Returns (ok: bool, missing: list[str]).
    """
    reasons = setup.get("reasons") or []
    needed = {
        "VOLUME": ("VOL_SPIKE", "VOL_EXTREME", "VOL_STRONG"),
        "OI_VELOCITY": ("OI_ACCELERATING", "OI_rising", "OI_VEL_BUILDING", "OI_VEL_ACCEL", "OI_VEL_FAST", "OI_VEL"),
        "SM_PREMIUM": ("SM_EXTREME", "SM_STRONG", "SM_PREMIUM_MOD", "SM_PREMIUM_STRONG", "SM_PREMIUM"),
        "PRICE_CLEAN": ("CLEAN_PX",),
    }
    missing = []
    for label, prefixes in needed.items():
        if not any(any(r.startswith(p) for p in prefixes) for r in reasons):
            missing.append(label)
    return (not missing), missing


# ═══════════════════════════════════════════════════════════════
# Sizing (conviction-scaled)
# ═══════════════════════════════════════════════════════════════

def resolve_sizing_tier(score, config):
    """Resolve the highest-applicable sizing tier for a given score.

    Returns dict with keys: leverage, marginPct, label, minScore.
    Returns None if score doesn't meet any tier's minScore.
    """
    tiers = config.get("sizingTiers") or _DEFAULT_SIZING_TIERS
    applicable = [t for t in tiers if score >= int(t.get("minScore", 0))]
    if not applicable:
        return None
    return max(applicable, key=lambda t: int(t.get("minScore", 0)))


def compute_leverage(score, config):
    """Return the leverage for this score, hard-capped at maxLeverage."""
    max_lev = int(config.get("maxLeverage", 10))
    tier = resolve_sizing_tier(score, config)
    if not tier:
        return 0
    lev = int(tier.get("leverage", 3))
    return min(lev, max_lev)


def compute_margin(account_value, score, config):
    """Compute margin allocation based on the resolved sizing tier for this score.

    Returns (margin_usd, tier_label).
    """
    tier = resolve_sizing_tier(score, config)
    if not tier:
        return 0.0, "none"
    pct = float(tier.get("marginPct", 0.20))
    return round(account_value * pct, 2), str(tier.get("label", ""))


# ═══════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════

def get_brentoil_full_picture():
    """Fetch market data for BRENTOIL. XYZ DEX asset — no funding info expected."""
    raw = cfg.mcporter_call(
        "market_get_asset_data",
        asset=ASSET,
        candle_intervals=["5m", "15m", "1h", "4h"],
        include_funding=False,
        include_order_book=False,
        dex="xyz",
    )
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else None


def get_account_value_and_held():
    """Returns (account_value, held_assets list).

    Account value summed across main + xyz sub-DEX views (single wallet,
    two views per Hyperliquid HIP-3 structure).
    """
    if not STRATEGY_ADDRESS:
        return 0.0, []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
        if not ch:
            return 0.0, []
        data = ch.get("data", ch)
        account_value = 0.0
        held = []
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            ms = s.get("marginSummary", {})
            try:
                account_value = max(account_value, float(ms.get("accountValue", 0)))  # one wallet, two sub-DEX views -> count equity ONCE (summing double-counts the shared free balance -> 2x sizing)
            except (TypeError, ValueError):
                pass
            for ap in s.get("assetPositions", []):
                pos = ap.get("position", ap)
                try:
                    szi = float(pos.get("szi", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if szi == 0:
                    continue
                coin = pos.get("coin", "")
                if coin:
                    held.append(coin)
        return account_value, held
    except Exception:
        return 0.0, []


# ═══════════════════════════════════════════════════════════════
# THESIS BUILDER — preserved from v1.7.0 evaluate_setup
# ═══════════════════════════════════════════════════════════════

def build_brentoil_thesis(config):
    """Build BRENTOIL entry thesis. Returns thesis dict or {"blocked":True,"reason":...}."""
    asset_data = get_brentoil_full_picture()
    if not asset_data:
        return {"blocked": True, "reason": "no_asset_data"}

    candles = asset_data.get("candles") or {}
    candles_by_tf = {
        "5m": candles.get("5m", []) or [],
        "15m": candles.get("15m", []) or [],
        "1h": candles.get("1h", []) or [],
        "4h": candles.get("4h", []) or [],
    }

    reasons = []

    # Gate 1: 4TF alignment
    direction, aligned, trends, align_detail = check_4tf_alignment(candles_by_tf)
    if not aligned:
        return {"blocked": True, "reason": f"4TF_MISALIGNED:{align_detail}", "trends": trends}
    reasons.append(f"4TF_aligned_{direction}_{align_detail}")

    # Gate 2: SM HARD BLOCK
    sm_dir, sm_premium_abs, sm_detail = get_sm_direction(asset_data, config)
    if sm_dir is None:
        return {"blocked": True, "reason": f"SM_HARD_BLOCK:{sm_detail}", "trends": trends}
    if sm_dir != direction:
        return {
            "blocked": True,
            "reason": f"SM_CONTRADICTS:setup={direction}_sm={sm_dir}",
            "trends": trends,
            "sm": sm_detail,
        }
    reasons.append(f"SM_aligned_{sm_dir}_{sm_detail}")

    # Base score from 4TF + SM alignment
    score = 6  # baseline for any aligned setup

    # Gate 3: SM conviction strength
    sm_mod = float(config.get("smPremiumModerateAbsPct", 0.001))
    sm_str = float(config.get("smPremiumStrongAbsPct", 0.003))
    sm_score, sm_reason = sm_conviction_score(sm_premium_abs, moderate_threshold=sm_mod, strong_threshold=sm_str)
    score += sm_score
    if sm_reason:
        reasons.append(sm_reason)

    # Gate 4: OI velocity
    oi_vel = extract_oi_velocity_1h(asset_data)
    oi_score, oi_reason = score_oi_velocity(oi_vel)
    score += oi_score
    if oi_reason:
        reasons.append(oi_reason)

    # Gate 5: Volume spike
    vol_threshold = float(config.get("volumeSpikeThreshold", 2.5))
    vol_strong = float(config.get("volumeSpikeStrongThreshold", 5.0))
    vol_score, vol_reason = volume_spike_score(
        candles_by_tf["15m"], candles_by_tf["1h"],
        threshold=vol_threshold, strong_threshold=vol_strong,
    )
    score += vol_score
    if vol_reason:
        reasons.append(vol_reason)

    # Gate 6: Price cleanliness
    clean_score, clean_reason = price_cleanliness_score(
        candles_by_tf["5m"],
        direction,
        max_wick_pct=float(config.get("priceCleanlinessMaxWickPct", 1.5)),
        lookback_minutes=int(config.get("priceCleanlinessLookbackMinutes", 30)),
    )
    score += clean_score
    if clean_reason:
        reasons.append(clean_reason)

    # Mark price for telemetry
    mark_px = safe_float((asset_data.get("asset_context") or {}).get("markPx"))

    # Compute per-TF momentum for telemetry
    def _mom(tf_candles, n=1):
        if len(tf_candles) < n + 1:
            return 0.0
        old = candle_close(tf_candles[-(n + 1)])
        new = candle_close(tf_candles[-1])
        if old == 0:
            return 0.0
        return (new - old) / old * 100

    return {
        "blocked": False,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trends": trends,
        "sm_premium_abs": sm_premium_abs,
        "sm_detail": sm_detail,
        "oi_vel": oi_vel,
        "mark_px": mark_px,
        "mom": {
            "5m": _mom(candles_by_tf["5m"], 1),
            "15m": _mom(candles_by_tf["15m"], 1),
            "1h": _mom(candles_by_tf["1h"], 1),
            "4h": _mom(candles_by_tf["4h"], 1),
        },
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL PUSH
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, config, account_value, held_assets):
    """Push a signal payload to the runtime via senpi_runtime_helpers.

    Direct HTTP POST to runtime API on 127.0.0.1; no subprocess.
    asset/direction/signal_type at top-level kwargs per the SignalItem
    wire schema. Score normalized 0..1 for SignalItem.score (Dire's
    composite score 0-13 scaled), with raw int score in `data` for
    telemetry.
    """
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "dire-config.json (preferred) or DIRE_WALLET env var",
            file=sys.stderr,
        )
        return False

    # Single-asset dedup — refuse to push if already holding BRENTOIL
    held_upper = {h.upper() for h in held_assets}
    if "BRENTOIL" in held_upper or "XYZ:BRENTOIL" in held_upper:
        return False

    score = thesis["score"]
    leverage = compute_leverage(score, config)
    margin_usd, tier_label = compute_margin(account_value, score, config)

    if leverage <= 0 or margin_usd < 10:
        print(
            f"SIZING_UNRESOLVED lev={leverage} margin=${margin_usd:.2f} score={score}",
            file=sys.stderr,
        )
        return False

    mom = thesis["mom"]
    trends = thesis.get("trends") or {}

    data_block = {
        "score": score,
        "tier": tier_label,
        "leverage": leverage,
        "marginUsd": margin_usd,
        "reasons": thesis["reasons"],
        "trend4h": trends.get("4h", {}).get("trend"),
        "trend1h": trends.get("1h", {}).get("trend"),
        "trend15m": trends.get("15m", {}).get("trend"),
        "trend5m": trends.get("5m", {}).get("trend"),
        "smPremiumAbsPct": round(thesis.get("sm_premium_abs", 0) * 100, 5),
        "smDetail": thesis.get("sm_detail"),
        "oiChange1h": thesis.get("oi_vel"),
        "markPx": thesis.get("mark_px"),
        "priceChange5m": mom["5m"],
        "priceChange15m": mom["15m"],
        "priceChange1h": mom["1h"],
        "priceChange4h": mom["4h"],
        "heldAssets": held_assets,
        "accountValue": account_value,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=ASSET,
            direction=thesis["direction"],
            score=min(score / 13.0, 1.0),   # 0..1 confidence (Dire max ~13)
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED BRENTOIL {thesis['direction']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        print(f"INGEST_EXCEPTION BRENTOIL {thesis['direction']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()
    config = cfg.load_config() or {}

    thesis = build_brentoil_thesis(config)

    if thesis.get("blocked"):
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": 1,
            "candidates": 0,
            "signals_pushed": 0,
            "note": f"BLOCKED: {thesis['reason']}",
            "elapsed_sec": round(elapsed, 2),
            "_dire_producer_version": VERSION,
        }))
        return

    min_score = int(config.get("minScore", MIN_SCORE_DEFAULT))
    if thesis["score"] < min_score:
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": 1,
            "candidates": 1,
            "signals_pushed": 0,
            "note": f"score_low {thesis['score']}/{min_score}",
            "direction": thesis["direction"],
            "reasons": thesis["reasons"],
            "elapsed_sec": round(elapsed, 2),
            "_dire_producer_version": VERSION,
        }))
        return

    # FP-003: require all soft confirmations (Volume, OI velocity,
    # SM premium, Price cleanliness) to contribute >= 1
    if bool(config.get("requireAllConfirmations", True)):
        ok, missing = all_confirmations_present(thesis)
        if not ok:
            elapsed = time.time() - run_start
            print(json.dumps({
                "status": "ok",
                "scanned": 1,
                "candidates": 1,
                "signals_pushed": 0,
                "note": f"confirmations_incomplete missing={','.join(missing)}",
                "direction": thesis["direction"],
                "score": thesis["score"],
                "reasons": thesis["reasons"],
                "elapsed_sec": round(elapsed, 2),
                "_dire_producer_version": VERSION,
            }))
            return

    # FP-001 quiet hours — apex setups bypass
    quiet, current_hour = in_quiet_hours(config)
    apex_bypass = int(config.get("quietHoursApexBypassScore", 12))
    if quiet and thesis["score"] < apex_bypass:
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": 1,
            "candidates": 1,
            "signals_pushed": 0,
            "note": f"QUIET_HOURS hour={current_hour}_UTC score={thesis['score']}_below_apex_{apex_bypass}",
            "direction": thesis["direction"],
            "elapsed_sec": round(elapsed, 2),
            "_dire_producer_version": VERSION,
        }))
        return

    # All gates cleared — push the signal
    account_value, held_assets = get_account_value_and_held()
    pushed = push_signal(thesis, config, account_value, held_assets)
    signals_pushed = 1 if pushed else 0

    elapsed = time.time() - run_start
    print(json.dumps({
        "status": "ok",
        "scanned": 1,
        "candidates": 1,
        "signals_pushed": signals_pushed,
        "note": "PUSHED" if pushed else "PUSH_FAILED_OR_HELD",
        "direction": thesis["direction"],
        "score": thesis["score"],
        "reasons": thesis["reasons"][:5],
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_dire_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v2.0.0 — long-lived daemon. Replaces openclaw cron + agentTurn.
    # producer_daemon owns the per-tick scanner_lock with stale-PID
    # auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,
        name=f"dire-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=240,
    )
