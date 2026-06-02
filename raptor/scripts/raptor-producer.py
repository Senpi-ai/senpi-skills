#!/usr/bin/env python3
# Senpi RAPTOR Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""RAPTOR v4.0.0 Producer — Hot Streak Follower, helpers-native.

v4.0.0 (2026-05-12): plumbing migration from v3.4. NO thesis change.
v3.4 quality-first pipeline (ELITE/RELIABLE traders → positions →
SM alignment → score), v3.2/v3.3 whale entry-price discipline (5%
threshold), v3.4 nested-positions parser fix, MIN_SCORE 6,
conviction-scaled leverage tiers, per-trader event dedupe, per-asset
cooldown all preserved verbatim.

Six-layer plumbing flip: mcporter → SenpiClient, scanner create_position
→ producer push_signal, openclaw cron → producer_daemon, Python state
files for daily cap → runtime.guard_rails, MARKET exits → FEE_OPTIMIZED_LIMIT.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raptor_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "4.0.1"
SCANNER_NAME = "raptor_signals"
SIGNAL_TYPE = "RAPTOR_HOT_STREAK_FOLLOW"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v3.4
# ═══════════════════════════════════════════════════════════════

XYZ_BANNED = True
MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY = 5.0   # v3.3 tightened from 20% → 5%


def _resolve_wallet():
    env_val = (os.environ.get("RAPTOR_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score, tiers, default_leverage):
    for tier in sorted(tiers, key=lambda t: t.get("minScore", 0), reverse=True):
        if score >= tier.get("minScore", 0):
            return tier.get("leverage", default_leverage)
    return default_leverage


def _extract_list(raw, *candidate_paths):
    if raw is None:
        return []
    for path in candidate_paths:
        cur = raw
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                cur = None
                break
        if isinstance(cur, list):
            return cur
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return []


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING — preserved verbatim from v3.4
# ═══════════════════════════════════════════════════════════════

def fetch_quality_hot_traders(limit=20, min_delta_usd=500_000):
    raw = cfg.mcp_call(
        "discovery_get_top_traders",
        time_frame="WEEKLY",
        sort_by="PROFIT_AND_LOSS_UNREALIZED",
        consistency=["ELITE", "RELIABLE"],
        open_position_filter=True,
        limit=limit,
    )
    if not raw:
        return []
    raw_list = _extract_list(raw, ("data", "traders"), ("traders",))
    quality = []
    for t in raw_list:
        if not isinstance(t, dict):
            continue
        addr = str(t.get("address", "")).lower()
        if not addr:
            continue
        unrealized = safe_float(t.get("unRealizedProfitAndLoss", 0))
        realized = safe_float(t.get("realizedProfitAndLoss", 0))
        total_pnl = safe_float(t.get("profitAndLoss", unrealized + realized))
        tcs_label = str(t.get("tcsLabel", "")).upper()
        if tcs_label not in ("ELITE", "RELIABLE"):
            continue
        if unrealized < min_delta_usd:
            continue
        quality.append({
            "address": addr,
            "unrealized_pnl": unrealized,
            "realized_pnl": realized,
            "total_pnl": total_pnl,
            "tcs_label": tcs_label,
            "tcs_value": safe_float(t.get("tcsValue", 0)),
            "roi": safe_float(t.get("returnOnInvestment", 0)),
        })
    quality.sort(key=lambda x: x["unrealized_pnl"], reverse=True)
    return quality


def fetch_trader_positions(trader_address):
    """v3.4 nested-dict parser fix preserved."""
    raw = cfg.mcp_call("leaderboard_get_trader_positions", trader_id=trader_address)
    if not raw:
        return []
    positions = _extract_list(
        raw,
        ("data", "positions", "positions"),   # nested-dict (actual prod shape)
        ("data", "top_positions", "positions"),
        ("data", "positions"),
        ("data", "top_positions"),
        ("positions",),
        ("top_positions",),
    )
    return positions


def fetch_sm_map():
    raw = cfg.mcp_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw
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
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                            m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


# ═══════════════════════════════════════════════════════════════
# DEDUPE + SCORING — preserved from v3.4
# ═══════════════════════════════════════════════════════════════

SEEN_EVENTS_FILE = "seen-events.json"


def load_seen_events():
    p = os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen_events(seen, dedupe_hours=4):
    cutoff = time.time() - (dedupe_hours * 3600)
    cleaned = {k: v for k, v in seen.items() if v > cutoff}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE), cleaned)


def is_event_seen(seen, trader_id, asset, dedupe_hours=4):
    key = f"{trader_id[:10].lower()}:{asset}"
    ts = seen.get(key, 0)
    if ts <= 0:
        return False
    return (time.time() - ts) < (dedupe_hours * 3600)


def mark_event_seen(seen, trader_id, asset):
    key = f"{trader_id[:10].lower()}:{asset}"
    seen[key] = time.time()


def build_signal(trader, positions, sm_map, hot_cfg, sm_cfg):
    """v3.4 build_signal preserved verbatim — quality whale → strongest position
    → SM alignment → entry-discipline gate → scoring."""
    trader_id = trader["address"]

    best_pos = None
    best_abs_pnl = 0.0
    total_abs_pnl = 0.0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        asset = str(
            pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
        ).upper()
        if not asset:
            continue
        if XYZ_BANNED and asset.lower().startswith("xyz:"):
            continue
        delta_pnl = safe_float(
            pos.get("delta_pnl",
                    pos.get("deltaPnl",
                            pos.get("unrealizedPnl",
                                    pos.get("unrealized_pnl",
                                            pos.get("pnl", 0)))))
        )
        direction = str(
            pos.get("direction",
                    pos.get("side",
                            "LONG" if delta_pnl >= 0 else "SHORT"))
        ).upper()
        if direction not in ("LONG", "SHORT"):
            continue
        whale_entry_px = safe_float(
            pos.get("entryPx",
                    pos.get("entry_px",
                            pos.get("entryPrice",
                                    pos.get("entry_price",
                                            pos.get("avgEntryPx",
                                                    pos.get("avg_entry_px", 0))))))
        )
        abs_pnl = abs(delta_pnl)
        total_abs_pnl += abs_pnl
        if abs_pnl > best_abs_pnl:
            best_abs_pnl = abs_pnl
            best_pos = {
                "asset": asset,
                "direction": direction,
                "delta_pnl": delta_pnl,
                "whale_entry_px": whale_entry_px,
            }

    if not best_pos or best_abs_pnl < hot_cfg.get("minPositionPnl", 100_000):
        return None

    concentration = (best_abs_pnl / total_abs_pnl) if total_abs_pnl > 0 else 0
    if concentration < hot_cfg.get("minConcentration", 0.35):
        return None

    sm = sm_map.get(best_pos["asset"])
    if not sm:
        return None
    if sm_cfg.get("requireDirectionMatch", True) and sm["direction"] != best_pos["direction"]:
        return None
    if sm["pct"] < sm_cfg.get("minSmPct", 2.0):
        return None
    if sm["traders"] < sm_cfg.get("minSmTraders", 10):
        return None

    # v3.3 ENTRY DISCIPLINE — don't buy whale's top
    whale_entry_px = best_pos.get("whale_entry_px", 0)
    current_px = 0
    if whale_entry_px > 0:
        try:
            px_raw = cfg.mcp_call("market_get_prices", assets=[best_pos["asset"]])
            if px_raw:
                data = px_raw.get("data", px_raw)
                if isinstance(data, dict):
                    current_px = safe_float(data.get(best_pos["asset"], 0))
        except Exception:
            current_px = 0
        if current_px > 0:
            if best_pos["direction"] == "LONG":
                run_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
                if run_pct > MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY:
                    return None
            else:
                run_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
                if run_pct > MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY:
                    return None

    # Scoring
    score = 0
    reasons = []
    tcs = trader["tcs_label"]
    if tcs == "ELITE":
        score += 3
        reasons.append(f"ELITE_tcs{trader['tcs_value']:.0f}")
    elif tcs == "RELIABLE":
        score += 2
        reasons.append(f"RELIABLE_tcs{trader['tcs_value']:.0f}")
    else:
        return None

    trader_delta = trader["unrealized_pnl"]
    if trader_delta >= hot_cfg.get("tier3Threshold", 3_000_000):
        score += 3; reasons.append(f"TIER3_${trader_delta/1e6:.1f}M")
    elif trader_delta >= hot_cfg.get("tier2Threshold", 1_500_000):
        score += 2; reasons.append(f"TIER2_${trader_delta/1e6:.1f}M")
    else:
        score += 1; reasons.append(f"TIER1_${trader_delta/1e6:.1f}M")

    if trader["roi"] >= 50:
        score += 1; reasons.append(f"ROI_{trader['roi']:.0f}%")

    if concentration >= 0.70:
        score += 2; reasons.append(f"HIGH_CONV_{concentration:.0%}")
    elif concentration >= 0.55:
        score += 1; reasons.append(f"CONC_{concentration:.0%}")

    if sm["pct"] >= 8:
        score += 2; reasons.append(f"SM_STRONG_{sm['pct']:.1f}%")
    elif sm["pct"] >= 4:
        score += 1; reasons.append(f"SM_ALIGNED_{sm['pct']:.1f}%")

    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    if best_pos["direction"] == "LONG":
        if p4h > 0.5 and p1h > 0.2:
            score += 2; reasons.append(f"4H+1H_CONFIRMS_+{p4h:.1f}/+{p1h:.1f}%")
        elif p4h > 0.5:
            score += 1; reasons.append(f"4H_CONFIRMS_+{p4h:.1f}%")
        elif p4h < -2:
            score -= 1; reasons.append(f"4H_OPPOSING_{p4h:.1f}%")
    else:
        if p4h < -0.5 and p1h < -0.2:
            score += 2; reasons.append(f"4H+1H_CONFIRMS_{p4h:.1f}/{p1h:.1f}%")
        elif p4h < -0.5:
            score += 1; reasons.append(f"4H_CONFIRMS_{p4h:.1f}%")
        elif p4h > 2:
            score -= 1; reasons.append(f"4H_OPPOSING_+{p4h:.1f}%")

    c15m = sm.get("contrib_15m", 0)
    if c15m > 0.5:
        score += 1; reasons.append(f"15M_SPIKE_+{c15m:.2f}")
    elif c15m <= 0:
        score -= 1; reasons.append(f"15M_STALE_{c15m:.2f}")

    # v3.2 entry-discipline BONUS
    if whale_entry_px > 0 and current_px > 0:
        if best_pos["direction"] == "LONG":
            edge_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
        else:
            edge_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
        if edge_pct >= 5:
            score += 2; reasons.append(f"BETTER_THAN_WHALE_+{edge_pct:.1f}%")
        elif edge_pct >= 2:
            score += 1; reasons.append(f"EDGE_VS_WHALE_+{edge_pct:.1f}%")

    return {
        "asset": best_pos["asset"],
        "direction": best_pos["direction"],
        "score": score,
        "reasons": reasons,
        "traderId": trader_id[:10] + "...",
        "fullTraderId": trader_id,
        "tcs": tcs,
        "traderDeltaPnl": trader_delta,
        "positionDeltaPnl": best_pos["delta_pnl"],
        "concentration": concentration,
        "smPct": sm["pct"],
        "smTraders": sm["traders"],
        "priceChg4h": p4h,
        "priceChg1h": p1h,
        "whaleEntryPx": whale_entry_px if whale_entry_px > 0 else None,
        "currentPx": current_px if (whale_entry_px > 0 and current_px > 0) else None,
    }


def get_safe_leverage(wallet, asset, requested_leverage):
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
                    max_lev = int(float(lev.get("value", 20)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


def push_signal(signal, leverage, margin_usd, held_assets):
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False
    if signal["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": signal["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "tcs": signal["tcs"],
        "traderId": signal["traderId"],
        "traderDeltaPnl": signal["traderDeltaPnl"],
        "positionDeltaPnl": signal["positionDeltaPnl"],
        "concentration": signal["concentration"],
        "smPct": signal["smPct"],
        "smTraders": signal["smTraders"],
        "priceChg4h": signal["priceChg4h"],
        "priceChg1h": signal["priceChg1h"],
        "whaleEntryPx": signal["whaleEntryPx"],
        "currentPx": signal["currentPx"],
        "reasons": signal["reasons"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=signal["asset"],
            direction=signal["direction"],
            score=min(signal["score"] / 16.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {signal['asset']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {signal['asset']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_raptor_producer_version": VERSION})
        return

    config = cfg.load_config()
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_raptor_producer_version": VERSION})
        return

    hot_cfg = config.get("hotStreak", {})
    sm_cfg = config.get("smAlignment", {})
    dedupe_cfg = config.get("dedupe", {})
    entry_cfg = config.get("entry", {})
    leverage_cfg = config.get("leverage", {
        "default": 7,
        "tiers": [
            {"minScore": 6, "leverage": 7},
            {"minScore": 8, "leverage": 8},
            {"minScore": 10, "leverage": 10},
        ],
    })

    quality_traders = fetch_quality_hot_traders(
        limit=hot_cfg.get("qualityPoolSize", 20),
        min_delta_usd=hot_cfg.get("minDeltaPnl", 500_000),
    )
    if not quality_traders:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "candidates": 0,
            "signals_pushed": 0,
            "note": "No ELITE/RELIABLE traders above threshold.",
            "elapsed_sec": round(elapsed, 2),
            "_raptor_producer_version": VERSION,
        })
        return

    sm_map = fetch_sm_map()
    if not sm_map:
        cfg.output({"status": "ok", "note": "No SM data.",
                    "_raptor_producer_version": VERSION})
        return

    seen_events = load_seen_events()
    held_upper = {h.upper() for h in held_assets}
    dedupe_hours = dedupe_cfg.get("eventDedupeHours", 4)
    scan_limit = min(len(quality_traders), hot_cfg.get("positionsFetchLimit", 10))

    candidates = []
    for t in quality_traders[:scan_limit]:
        positions_data = fetch_trader_positions(t["address"])
        if not positions_data:
            continue
        signal = build_signal(t, positions_data, sm_map, hot_cfg, sm_cfg)
        if not signal:
            continue
        if is_event_seen(seen_events, t["address"], signal["asset"], dedupe_hours):
            continue
        if signal["asset"] in held_upper:
            continue
        candidates.append(signal)

    if not candidates:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "quality_traders": len(quality_traders),
            "candidates": 0,
            "signals_pushed": 0,
            "note": "No candidates passed filters.",
            "elapsed_sec": round(elapsed, 2),
            "_raptor_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda s: s["score"], reverse=True)
    best = candidates[0]
    min_score = entry_cfg.get("minScore", 6)
    if best["score"] < min_score:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "quality_traders": len(quality_traders),
            "candidates": len(candidates),
            "signals_pushed": 0,
            "note": f"Best score {best['score']} < {min_score}",
            "elapsed_sec": round(elapsed, 2),
            "_raptor_producer_version": VERSION,
        })
        return

    # Conviction-tier sizing
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    high_conv_pct = entry_cfg.get("marginPctHighConv", 0.35)
    margin_pct = high_conv_pct if best["score"] >= 10 else base_margin_pct
    margin_usd = round(account_value * margin_pct, 2)

    requested_leverage = get_leverage_for_score(
        best["score"], leverage_cfg.get("tiers", []), leverage_cfg.get("default", 7)
    )
    leverage = get_safe_leverage(STRATEGY_ADDRESS, best["asset"], requested_leverage)

    mark_event_seen(seen_events, best["fullTraderId"], best["asset"])
    save_seen_events(seen_events, dedupe_hours)

    pushed = push_signal(best, leverage, margin_usd, held_assets)
    elapsed = time.time() - run_start

    cfg.output({
        "status": "ok",
        "quality_traders": len(quality_traders),
        "candidates": len(candidates),
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "asset": best["asset"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "tcs": best["tcs"],
            "concentration": best["concentration"],
            "reasons": best["reasons"][:6],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_raptor_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,                  # 3min — preserved from v3.x
        name=f"raptor-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
