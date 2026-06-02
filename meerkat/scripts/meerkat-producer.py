#!/usr/bin/env python3
# Senpi MEERKAT Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""MEERKAT v1.0.0 — Momentum-Event Sniper.

Meerkat watches the Senpi momentum-event feed (leaderboard_get_momentum_events
— the 4h rolling-window momentum / rank-jump events) and snipes the FRESHEST,
HIGHEST-TIER events the moment they fire, entering in the momentum direction
before the move is broadly known.

Each tick:
  1. Pull the momentum-event feed.
  2. For each event: classify magnitude into a TIER (1/2/3), measure FRESHNESS
     (minutes since it fired), extract direction.
  3. Gate: tier >= minTier AND fresh (age <= maxEventAgeMinutes).
  4. Score by tier + freshness + SM alignment + volume, pick the best, enter in
     the momentum direction.

Distinct from the Striker / rank-jump agents (Jaguar/Orca/Roach) which score a
leaderboard universe; Meerkat is driven directly off the momentum-event feed.
A fresh momentum event can extend, so the DSL is the let-winners-run preset
(wide ladder + a short hard_timeout — momentum that hasn't paid out fast is
stale). Producer NEVER closes — DSL owns exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_momentum_events /
leaderboard_get_markets.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meerkat_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "meerkat_signals"
SIGNAL_TYPE = "MEERKAT_MOMENTUM_EVENT"

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70

DEFAULT_MIN_TIER = 2                 # snipe tier 2+ (tier 3 = strongest)
DEFAULT_TIER2_MIN_PCT = 5.0         # |momentum| for tier 2
DEFAULT_TIER3_MIN_PCT = 10.0        # |momentum| for tier 3
DEFAULT_MAX_EVENT_AGE_MIN = 30.0    # freshness gate — snipe just-formed events


def _resolve_wallet():
    env_val = (os.environ.get("MEERKAT_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Pure momentum-event logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def event_age_minutes(event_ts, now_ts):
    """Minutes since an event fired. event_ts may be epoch seconds or
    milliseconds. None if missing/unparseable."""
    if event_ts is None:
        return None
    try:
        ts = float(event_ts)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts > 1e12:        # milliseconds → seconds
        ts /= 1000.0
    return (now_ts - ts) / 60.0


def event_direction(event):
    """LONG / SHORT for a momentum event: explicit direction/side, else the
    sign of the momentum/change magnitude. None if undeterminable."""
    if not isinstance(event, dict):
        return None
    d = str(event.get("direction", event.get("side", ""))).upper()
    if d in ("LONG", "SHORT"):
        return d
    mag = safe_float(
        event.get("momentum", event.get("change_pct", event.get("changePct", event.get("delta", 0))))
    )
    if mag > 0:
        return "LONG"
    if mag < 0:
        return "SHORT"
    return None


def momentum_tier(magnitude_pct, tier2_min, tier3_min):
    """Classify |momentum| into a tier: 3 (strongest) >= tier3_min,
    2 >= tier2_min, else 1."""
    m = abs(safe_float(magnitude_pct))
    if m >= tier3_min:
        return 3
    if m >= tier2_min:
        return 2
    return 1


def event_score(tier, fresh, sm_aligned, vol_rising):
    """Score a momentum event (max ~7). Tier is the backbone; freshness is the
    sniper edge; SM + volume are confirmation bonuses."""
    score = {1: 1, 2: 2, 3: 3}.get(tier, 0)
    if fresh:
        score += 2
    if sm_aligned:
        score += 1
    if vol_rising:
        score += 1
    return score


# ═══════════════════════════════════════════════════════════════
# Data fetchers (defensive shape unwrapping)
# ═══════════════════════════════════════════════════════════════

def fetch_momentum_events():
    raw = cfg.mcp_call("leaderboard_get_momentum_events")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    d = raw.get("data", raw)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        ev = d.get("events", d.get("momentum_events", d.get("results", [])))
        return ev if isinstance(ev, list) else []
    return []


def event_asset(event):
    if not isinstance(event, dict):
        return ""
    return str(event.get("token", event.get("coin", event.get("asset", event.get("symbol", ""))))).upper()


def event_magnitude(event):
    if not isinstance(event, dict):
        return 0.0
    return safe_float(
        event.get("momentum", event.get("change_pct", event.get("changePct", event.get("delta", 0))))
    )


def event_timestamp(event):
    if not isinstance(event, dict):
        return None
    return event.get("ts", event.get("timestamp", event.get("time", event.get("created_at"))))


def fetch_sm_direction(asset):
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw or not raw.get("success", True):
        return None, 0.0
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def fetch_volume_rising(asset):
    """True if the asset's recent 1h volume is rising vs the prior window."""
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h"],
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return False
    candles = data.get("data", {}).get("candles", {}).get("1h", [])
    if len(candles) < 6:
        return False
    vols = [safe_float(c.get("volume", c.get("v", 0))) for c in candles[-6:]]
    recent = sum(vols[-3:]) / 3
    earlier = sum(vols[:3]) / 3
    return earlier > 0 and (recent - earlier) / earlier > 0.15


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one event
# ═══════════════════════════════════════════════════════════════

def build_thesis(event, config, now):
    asset = event_asset(event)
    if not asset:
        return None
    direction = event_direction(event)
    if direction is None:
        return None

    mag = event_magnitude(event)
    tier2 = float(config.get("tier2MinPct", DEFAULT_TIER2_MIN_PCT))
    tier3 = float(config.get("tier3MinPct", DEFAULT_TIER3_MIN_PCT))
    tier = momentum_tier(mag, tier2, tier3)
    if tier < int(config.get("minTier", DEFAULT_MIN_TIER)):
        return None

    age = event_age_minutes(event_timestamp(event), now)
    max_age = float(config.get("maxEventAgeMinutes", DEFAULT_MAX_EVENT_AGE_MIN))
    # No timestamp → treat as fresh (feed only returns current-window events).
    fresh = age is None or age <= max_age
    if not fresh:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(config.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    sm_aligned = (sm_dir == direction and sm_tilt >= sm_min)
    vol_rising = fetch_volume_rising(asset)

    score = event_score(tier, fresh, sm_aligned, vol_rising)
    reasons = [f"momentum_event_{direction}", f"tier_{tier}", f"mag_{mag:+.1f}%"]
    if fresh:
        reasons.append("fresh" if age is None else f"fresh_{age:.0f}min")
    if sm_aligned:
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vol_rising:
        reasons.append("vol_rising")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "tier": tier,
        "magnitude_pct": round(mag, 2),
        "age_min": round(age, 1) if age is not None else None,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "vol_rising": vol_rising,
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "tier": thesis["tier"],
        "magnitudePct": thesis.get("magnitude_pct") or 0.0,
        "ageMin": thesis.get("age_min") if thesis.get("age_min") is not None else 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "volRising": bool(thesis.get("vol_rising")),
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 7.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {coin}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — snipe the freshest, highest-tier momentum event
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    now = time.time()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_meerkat_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_meerkat_producer_version": VERSION})
        return

    events = fetch_momentum_events()
    if not events:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no momentum events in the feed",
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_meerkat_producer_version": VERSION,
        })
        return

    candidates = []
    for event in events:
        asset = event_asset(event)
        if not asset or asset.upper() in held_set or cfg.was_recently_signaled(asset):
            continue
        thesis = build_thesis(event, config, now)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no fresh tier>=minTier momentum event cleared minScore",
            "events_seen": len(events),
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_meerkat_producer_version": VERSION,
        })
        return

    # Highest score, tie-break by tier then magnitude.
    candidates.sort(key=lambda c: (c["score"], c["tier"], abs(c["magnitude_pct"])), reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(best["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "tier": best["tier"],
            "magnitude_pct": best["magnitude_pct"],
            "age_min": best["age_min"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "candidates_count": len(candidates),
        "events_seen": len(events),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_meerkat_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=120,   # 2min — momentum events are time-sensitive; snipe fast
        name=f"meerkat-producer-{_wallet_lock_id}",
        tick_timeout=90,
    )
