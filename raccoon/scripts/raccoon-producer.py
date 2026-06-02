#!/usr/bin/env python3
# Senpi RACCOON Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""RACCOON v1.0.0 — Weekend XYZ reconciliation trader.

ONLY emits signals during Fri 22:00 UTC → Mon 00:00 UTC (the trade.xyz
no-external-price window when XYZ uses internal oracle only). Outside
this window, the producer outputs WAITING and does nothing.

The thesis: from Fri 17:00 ET through Sun 18:00 ET, trade.xyz has no
external price feed for equities or commodities (the underlying spot
markets are closed). XYZ uses an internal 30-min EWMA oracle that
drifts based on impact-price activity. When external pricing RESUMES
Sunday 18:00 ET / Monday 00:00 UTC, the internal-oracle-implied price
snaps back to the real external value. That snap is the edge.

Raccoon positions during the gap in the direction of accumulated
sentiment (>2% move with volume confirmation + SM agreement). DSL
hard_timeout 48h enforces Monday-open exit so positions don't camp
through the next week.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raccoon_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "raccoon_signals"
SIGNAL_TYPE = "RACCOON_XYZ_WEEKEND"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70
DEFAULT_MIN_MOVE_PCT = 2.0
DEFAULT_MIN_VOL_USD = 1_000_000
DEFAULT_MIN_MAX_LEV = 10  # excludes IPOPs (max_lev 5) which are Lemur's territory
# Weekend window: Fri 22:00 UTC → Mon 00:00 UTC
WEEKEND_START_DOW = 4   # Friday (Python weekday)
WEEKEND_START_HOUR = 22
WEEKEND_END_DOW = 0     # Monday
WEEKEND_END_HOUR = 0


def _resolve_wallet():
    env_val = (os.environ.get("RACCOON_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def in_weekend_window(now=None):
    """True if current UTC time is within Fri 22:00 UTC → Mon 00:00 UTC."""
    now = now or datetime.now(timezone.utc)
    dow = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    # Window: Fri 22:00 UTC → Mon 00:00 UTC
    if dow == WEEKEND_START_DOW and hour >= WEEKEND_START_HOUR:
        return True
    if dow == 5 or dow == 6:  # Saturday or Sunday — all day
        return True
    if dow == WEEKEND_END_DOW and hour < WEEKEND_END_HOUR:
        return True  # Mon before 00:00 — but this is the same as Sun 24:00, which would be Sun 23 hour... covered above
    return False


def fetch_weekend_universe(config):
    """Filter xyz: instruments to those that are tradeable on the weekend
    (i.e., NOT IPOPs which Lemur owns)."""
    raw = cfg.mcp_call("market_list_instruments", dex="xyz")
    if not raw or not raw.get("success", True):
        return []
    data = raw.get("data", raw)
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []

    min_vol = float(config.get("minVolUsd", DEFAULT_MIN_VOL_USD))
    min_max_lev = int(config.get("minMaxLeverage", DEFAULT_MIN_MAX_LEV))

    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        # Exclude IPOPs (max_lev <= 5) which are Lemur's territory
        if int(inst.get("max_leverage", 0)) < min_max_lev:
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        vol_usd = float(ctx.get("dayNtlVlm", 0))
        if vol_usd < min_vol:
            continue
        universe.append({"name": name, "vol_usd": vol_usd})
    return universe


def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h", "4h"],
        include_funding=False,
        include_order_book=False,
    )


def detect_directional_move(candles_1h, min_pct):
    """Returns (direction, move_pct, vol_x). Looks at the move from
    Friday close (oldest in 1h window roughly) to latest close,
    with volume confirmation (>1.5x recent avg)."""
    if len(candles_1h) < 24:
        return None, 0.0, 1.0
    # Compare latest close vs ~48h ago (approximates Fri close → now move)
    earlier = _f(candles_1h[-48], "close", "c") if len(candles_1h) >= 48 else _f(candles_1h[0], "close", "c")
    latest = _f(candles_1h[-1], "close", "c")
    if earlier <= 0 or latest <= 0:
        return None, 0.0, 1.0
    move_pct = ((latest - earlier) / earlier) * 100
    abs_move = abs(move_pct)
    if abs_move < min_pct:
        return None, 0.0, 1.0
    # Volume ratio: recent 6h vs prior 18h
    if len(candles_1h) >= 24:
        recent_vol = sum(_f(c, "volume", "v") for c in candles_1h[-6:]) / 6
        prior_vol = sum(_f(c, "volume", "v") for c in candles_1h[-24:-6]) / 18
        vol_x = recent_vol / prior_vol if prior_vol > 0 else 1.0
    else:
        vol_x = 1.0
    direction = "LONG" if move_pct > 0 else "SHORT"
    return direction, abs_move, vol_x


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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    if len(candles_1h) < 24:
        return None

    min_pct = float(entry_cfg.get("minMoveAbsPct", DEFAULT_MIN_MOVE_PCT))
    direction, move_pct, vol_x = detect_directional_move(candles_1h, min_pct)
    if direction is None:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    score = 0
    reasons = []
    # Move magnitude
    if move_pct >= 4.0:
        score += 3
        reasons.append(f"move_strong_{move_pct:+.2f}%")
    elif move_pct >= 2.5:
        score += 2
        reasons.append(f"move_{move_pct:+.2f}%")
    else:
        score += 1
        reasons.append(f"move_weak_{move_pct:+.2f}%")
    # SM aligned
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    # SM strongly tilted
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")
    # Volume confirmation
    if vol_x >= 1.5:
        score += 1
        reasons.append(f"vol_{vol_x:.1f}x")

    return {
        "coin": asset, "direction": direction, "score": score, "reasons": reasons,
        "move_pct": round(move_pct, 3) if direction == "LONG" else -round(move_pct, 3),
        "vol_ratio": round(vol_x, 2),
        "sm_direction": sm_dir, "sm_tilt_pct": sm_tilt,
    }


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"], "leverage": leverage, "marginUsd": margin_usd,
        "direction": thesis["direction"], "reasons": thesis["reasons"],
        "movePct": thesis["move_pct"], "volRatio": thesis["vol_ratio"],
        "smDirection": thesis["sm_direction"], "smTiltPct": thesis["sm_tilt_pct"],
        "weekendWindow": True,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS, scanner=SCANNER_NAME,
            asset=coin, direction=thesis["direction"],
            score=min(thesis["score"] / 9.0, 1.0),
            signal_type=SIGNAL_TYPE, data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {coin}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    config = cfg.load_config()

    # Weekend gate — only activate during Fri 22:00 UTC → Mon 00:00 UTC
    if not in_weekend_window():
        cfg.output({
            "status": "ok",
            "note": "OUTSIDE_WEEKEND_WINDOW — Raccoon only fires Fri 22:00 UTC → Mon 00:00 UTC",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_raccoon_producer_version": VERSION,
        })
        return

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_raccoon_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_raccoon_producer_version": VERSION})
        return

    universe = fetch_weekend_universe(config)
    if not universe:
        cfg.output({
            "status": "ok",
            "note": "no XYZ instruments match liquidity/leverage filter",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_raccoon_producer_version": VERSION,
        })
        return

    candidates = []
    for inst in universe:
        coin = inst["name"]
        if coin.upper() in held_set:
            continue
        if cfg.was_recently_signaled(coin):
            continue
        thesis = build_thesis(coin, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — in weekend window but no qualifying XYZ move + SM agreement",
            "universe_size": len(universe),
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_raccoon_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
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
            "coin": best["coin"], "direction": best["direction"],
            "score": best["score"], "leverage": leverage, "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "universe_size": len(universe),
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_raccoon_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"raccoon-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
