#!/usr/bin/env python3
# Senpi KESTREL Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""KESTREL v3.0.0 Producer — XYZ Macro Breakout Rider.

PLUMBING-ONLY MIGRATION from v2.0. NO thesis change. NO scoring change.
NO threshold change. Producer ports onto senpi_runtime_helpers (in-process
SenpiClient + direct HTTP POST to runtime /signals + producer_daemon
long-lived loop).

v1.x was a full-agency Python scanner: scored signals, called
create_position directly, maintained Python-side counters/cooldowns.
v2.0 flips to producer + senpi-trading-runtime.

WHAT v2.0 ENABLES:
  - Producer (kestrel-producer.py) emits signals via
    `SenpiClient.push_signal()` (direct HTTP POST). NO execution code.
  - Runtime LLM gate is regime-aware — applies macro vetoes
    (BTC drawdown propagating to risk-off equities, vol expansion
    spikes, weekend-only assets during low-liquidity windows)
    that the producer can't see.
  - risk.guard_rails ENFORCES daily caps, drawdown halt,
    consecutive-loss halt, per-asset cooldown.
  - DSL uses FEE_OPTIMIZED_LIMIT on entries AND exits with
    ensure_execution_as_taker:true (fixes v1.x's resting-on-book
    bug — exits must complete, taker fallback is the safety floor).
  - Trade chain DB emits LIFECYCLE / DECISION_EXECUTED /
    ACTION_RESULT / DSL_CREATED / DSL_CLOSED — telemetry restored.

WHAT'S PRESERVED FROM v1.1.1:
  - 13-asset macro universe (CL, BRENTOIL, GOLD, SP500, XYZ100,
    AAPL, NVDA, GOOGL, TSLA, AMZN, META, MSFT) — SILVER removed
  - Hard gate: 1H price move >= 1.5% (BREAKOUT_THRESHOLD_1H)
  - Spread gate (loosened 0.2% -> 0.35% in v2.0)
  - Volume surge scoring
  - SM concentration scoring (XYZ dex filtered)
  - Funding alignment
  - MOVE_EXHAUSTION penalty (relaxed thresholds in v2.0)

v2.0 CALIBRATION CHANGES (from v1.2 analysis):
  - 1H base scores increased: 1.5%->+3 (was +2), 2%->+4 (was +3),
    3%->+5 (was +4). The 1H breakout is the PRIMARY signal —
    weight it heavier.
  - MOVE_EXHAUSTION threshold 4%->6%, MOVE_TIRING 2.5%->4%.
    v1.1's penalties cancelled out 4H alignment too aggressively —
    average net 4H contribution was 0-1 pts. Relaxed thresholds let
    typical 2-4% 4H trends actually contribute.
  - MIN_SCORE 6 -> 5 (modest relaxation; combined with #1, total
    expected entry frequency ~3-5x).
  - Spread gate 0.2% -> 0.35% (XYZ tickers have wider natural spreads
    than crypto; 0.2% rejected valid signals on smaller equities).

Environment / config resolution:
  Strategy wallet from config/kestrel-config.json (canonical).

  KESTREL_WALLET            — optional override; defaults to config.wallet
  OPENCLAW_BIN              — optional, default "openclaw"
  EXTERNAL_SCANNER_NAME     — optional override (default "kestrel_signals")
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kestrel_config as cfg

# senpi_runtime_helpers ships inside the senpi-trading-runtime skill.
# Global skills install under ~/.openclaw/skills/ on standard hosts
# (e.g. /data/.openclaw/skills/ on Railway). Some setups install user
# skills under ${OPENCLAW_WORKSPACE}/skills/. Probe both in order.
_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import producer_daemon  # type: ignore  # noqa: E402


VERSION = "3.0.3"
SCANNER_NAME = os.environ.get("EXTERNAL_SCANNER_NAME", "kestrel_signals")
SIGNAL_TYPE = "KESTREL_XYZ_BREAKOUT"


def _resolve_wallet():
    env_wallet = (os.environ.get("KESTREL_WALLET") or "").strip()
    if env_wallet:
        return env_wallet
    legacy = (os.environ.get("STRATEGY_ADDRESS") or "").strip()
    if legacy:
        sys.stderr.write(
            "[kestrel v3.0.0] DEPRECATION: STRATEGY_ADDRESS env var is BANNED "
            "by v2.0.9 contamination rule. Set KESTREL_WALLET instead. "
            "Honoring legacy value for this run only.\n"
        )
        return legacy
    config = cfg.load_config()
    return (config.get("wallet") or "").strip()


KESTREL_WALLET = _resolve_wallet()
STRATEGY_ADDRESS = KESTREL_WALLET  # alias used by signal payload


# ═══════════════════════════════════════════════════════════════
# WALLET-ISOLATED STATE DIR
# ═══════════════════════════════════════════════════════════════

def _wallet_state_dir():
    if KESTREL_WALLET:
        h = hashlib.sha256(KESTREL_WALLET.lower().encode()).hexdigest()[:12]
    else:
        h = "unset"
    d = cfg.SKILL_DIR / "state" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


_STATE_DIR = _wallet_state_dir()
_COOLDOWN_FILE = _STATE_DIR / "asset-cooldowns.json"
_COUNTER_FILE = _STATE_DIR / "trade-counter.json"
_LAST_CLOSED_FILE = _STATE_DIR / "last-closed.json"
_PREV_HELD_FILE = _STATE_DIR / "previously-held.json"
_wallet_lock_id = (hashlib.sha256(KESTREL_WALLET.lower().encode()).hexdigest()[:8]
                   if KESTREL_WALLET else "unset")


# ═══════════════════════════════════════════════════════════════
# CONSTANTS (fleet-tuned, not operator-set)
# ═══════════════════════════════════════════════════════════════

# v2.0: SILVER removed (HL doesn't support). Other macro tickers verified.
ALLOWED_ASSETS = {
    # Commodities
    "CL", "BRENTOIL",
    # Precious metals
    "GOLD",
    # Indices
    "SP500", "XYZ100",
    # High-volume equities
    "AAPL", "NVDA", "GOOGL", "TSLA", "AMZN", "META", "MSFT",
}

MIN_SCORE_DEFAULT = 5                  # v2.0: was 6 (v1.2 calibration relax)
BREAKOUT_THRESHOLD_1H = 1.5            # 1.5% 1H breakout = mandatory hard gate
BREAKOUT_THRESHOLD_4H = 3.0            # 3% 4H trend = strong alignment
EXHAUSTION_THRESHOLD = 6.0             # v2.0: was 4 (relaxed)
TIRING_THRESHOLD = 4.0                 # v2.0: was 2.5 (relaxed)
SPREAD_MAX = 0.0035                    # v2.0: was 0.002 (loosened for XYZ)
MAX_POSITIONS = 2
MARGIN_PCT = 0.30                      # 30% per slot
ASSET_COOLDOWN_MINUTES = 180           # 3h between same-asset entries
POST_CLOSE_COOLDOWN_MINUTES = 180      # v2.0 producer-side runtime-cooldown backstop

# Score-tiered leverage
LEVERAGE_TIERS = [
    {"min_score": 9, "leverage": 5},
    {"min_score": 5, "leverage": 3},
]
DEFAULT_LEVERAGE = 3

STARTING_BUDGET = 1000.0


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware)
# ═══════════════════════════════════════════════════════════════

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:     return 12
    elif pnl_pct >= 0:   return 8
    elif pnl_pct >= -5:  return 5
    elif pnl_pct >= -15: return 3
    elif pnl_pct >= -25: return 1
    else:                return 0


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


# ═══════════════════════════════════════════════════════════════
# STATE I/O
# ═══════════════════════════════════════════════════════════════

def load_cooldowns():
    try:
        with open(_COOLDOWN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cooldowns(cooldowns):
    cfg.atomic_write(str(_COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(token, cooldown_minutes=ASSET_COOLDOWN_MINUTES):
    cooldowns = load_cooldowns()
    if token not in cooldowns:
        return False
    last_emit = cooldowns[token].get("emittedTimestamp", 0)
    return ((time.time() - last_emit) / 60) < cooldown_minutes


def mark_asset_emitted(token):
    cooldowns = load_cooldowns()
    cooldowns[token] = {
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
            tc = {"date": today, "entries": 0}
        return tc
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": today, "entries": 0}


def save_trade_counter(tc):
    tc["updatedAt"] = cfg.now_iso()
    cfg.atomic_write(str(_COUNTER_FILE), tc)


# ═══════════════════════════════════════════════════════════════
# v2.0 POST-CLOSE COOLDOWN (producer-side runtime-cooldown backstop)
# ═══════════════════════════════════════════════════════════════

def load_last_closed():
    try:
        with open(_LAST_CLOSED_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_last_closed(d):
    cfg.atomic_write(str(_LAST_CLOSED_FILE), d)


def load_previously_held():
    try:
        with open(_PREV_HELD_FILE) as f:
            data = json.load(f)
        return set(data.get("assets", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_previously_held(held):
    cfg.atomic_write(str(_PREV_HELD_FILE), {
        "assets": sorted(list(held)),
        "updatedAt": cfg.now_iso(),
    })


def detect_closes(current_held, previously_held):
    closed = set(previously_held) - set(current_held)
    if not closed:
        return set()
    last = load_last_closed()
    now_ts = time.time()
    now_iso = cfg.now_iso()
    for asset in closed:
        last[asset] = {"closedTimestamp": now_ts, "closedAt": now_iso}
    save_last_closed(last)
    return closed


def is_in_post_close_cooldown(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    if token not in last:
        return False
    last_close = last[token].get("closedTimestamp", 0)
    return ((time.time() - last_close) / 60) < cooldown_minutes


def post_close_cooldown_remaining_min(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    if token not in last:
        return 0
    last_close = last[token].get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    remaining = cooldown_minutes - elapsed_min
    return round(max(0.0, remaining), 1)


# ═══════════════════════════════════════════════════════════════
# ACCOUNT + HELD ASSETS
# ═══════════════════════════════════════════════════════════════

def get_account_state():
    """Returns (account_value, position_count, held_assets_set).
    Held assets stripped of "xyz:" prefix to match ALLOWED_ASSETS keys."""
    if not KESTREL_WALLET:
        return None, None, set()
    ch = cfg.mcporter_call(
        "strategy_get_clearinghouse_state",
        strategy_wallet=KESTREL_WALLET,
    )
    if not ch:
        return None, None, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
    held = set()
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
                coin = pos.get("coin", "")
                # Strip "xyz:" prefix if present
                token = str(coin).replace("xyz:", "").replace("XYZ:", "").upper()
                if token:
                    held.add(token)
    return total_value, pos_count, held


# ═══════════════════════════════════════════════════════════════
# SM DATA FETCHER (XYZ filtered)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_xyz_map():
    """Returns dict: token -> SM market record (XYZ dex only)."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    sm_map = {}
    if not raw:
        return sm_map
    sm_markets = raw.get("data", raw)
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", sm_markets)
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", [])
    if isinstance(sm_markets, list):
        for m in sm_markets:
            if isinstance(m, dict):
                token = str(m.get("token", "")).upper()
                dex = str(m.get("dex", "")).lower()
                if dex == "xyz" and token:
                    sm_map[token] = m
    return sm_map


# ═══════════════════════════════════════════════════════════════
# BREAKOUT SCORING (v2.0 calibration: 1H weighted heavier, exhaustion relaxed)
# ═══════════════════════════════════════════════════════════════

def score_asset_breakout(asset, sm_map):
    """Score a single XYZ asset for breakout entry. Returns dict or None
    if asset doesn't pass hard gates."""

    data = cfg.mcporter_call(
        "market_get_asset_data",
        asset=f"xyz:{asset}",
        candle_intervals=["1h", "4h"],
        include_funding=True,
        include_order_book=True,
        dex="xyz",
    )
    if not data:
        return None

    ad = data.get("data", data)
    if not isinstance(ad, dict):
        return None

    candles = ad.get("candles", {})
    candles_1h = candles.get("1h", [])

    if len(candles_1h) < 3:
        return None

    # ── 1H breakout (mandatory hard gate) ──
    # v3.0.1 (2026-05-14) — rolling candle boundary fix.
    # Bug: the original code only compared the currently-forming hour candle
    # (candles_1h[-1]) against the previously-closed one (candles_1h[-2]).
    # When a breakout happened in the final 15-30 min of an hour, the
    # currently-forming candle hadn't fully captured the move yet, so
    # abs(pct_1h) < BREAKOUT_THRESHOLD_1H and we skipped. On the next hour
    # rollover, the new fresh candle had near-zero delta against the
    # just-closed breakout candle, so the breakout was permanently
    # swallowed. Verified misses on 2026-05-14: NVDA at 05:00 + 14:00,
    # MSFT at 14:00, CL at 06:00, TSLA at 13:00.
    # Fix: also evaluate the just-closed hour (candles[-2] vs candles[-3])
    # and use whichever delta has the larger magnitude. Catches end-of-hour
    # breakouts on the next tick after rollover.
    last_1h = candles_1h[-1]
    prev_1h = candles_1h[-2]
    close_now = safe_float(last_1h.get("close", last_1h.get("c", 0)))
    close_prev = safe_float(prev_1h.get("close", prev_1h.get("c", 0)))

    if close_prev <= 0 or close_now <= 0:
        return None

    pct_1h_current = ((close_now - close_prev) / close_prev) * 100

    # Just-closed hour: candles[-2] (just closed) vs candles[-3] (prev to it).
    # If this candle had a big move that happened late in the hour, we'd
    # otherwise have missed it.
    pct_1h_recent_closed = 0.0
    if len(candles_1h) >= 3:
        close_prev_prev = safe_float(candles_1h[-3].get("close", candles_1h[-3].get("c", 0)))
        if close_prev_prev > 0:
            pct_1h_recent_closed = ((close_prev - close_prev_prev) / close_prev_prev) * 100

    # Use whichever has larger absolute magnitude — captures late-hour
    # breakouts that the current-hour candle doesn't yet reflect.
    if abs(pct_1h_recent_closed) > abs(pct_1h_current):
        pct_1h = pct_1h_recent_closed
    else:
        pct_1h = pct_1h_current

    if abs(pct_1h) < BREAKOUT_THRESHOLD_1H:
        return None

    # ── 4H trend (trailing window via 1H candles, v1.1 fix preserved) ──
    pct_4h = 0
    if len(candles_1h) >= 5:
        close_1h_4h_ago = safe_float(candles_1h[-5].get("close", candles_1h[-5].get("c", 0)))
        if close_1h_4h_ago > 0:
            pct_4h = ((close_now - close_1h_4h_ago) / close_1h_4h_ago) * 100

    breakout_dir = "LONG" if pct_1h > 0 else "SHORT"

    score = 0
    reasons = []

    # ── 1H breakout magnitude (v2.0: weighted heavier) ──
    if abs(pct_1h) >= 3.0:
        score += 5  # was 4
        reasons.append(f"MASSIVE_BREAKOUT_1H {pct_1h:+.2f}%")
    elif abs(pct_1h) >= 2.0:
        score += 4  # was 3
        reasons.append(f"STRONG_BREAKOUT_1H {pct_1h:+.2f}%")
    elif abs(pct_1h) >= BREAKOUT_THRESHOLD_1H:
        score += 3  # was 2
        reasons.append(f"BREAKOUT_1H {pct_1h:+.2f}%")

    # ── 4H trend alignment ──
    if abs(pct_4h) >= BREAKOUT_THRESHOLD_4H:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score += 2
            reasons.append(f"4H_TREND_CONFIRMS {pct_4h:+.2f}%")
    elif abs(pct_4h) >= 1.0:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score += 1
            reasons.append(f"4H_ALIGNED {pct_4h:+.2f}%")

    # ── Move-exhaustion penalty (v2.0: relaxed thresholds) ──
    # was 4% / 2.5%, now 6% / 4% — only flag truly extended moves
    if abs(pct_4h) >= EXHAUSTION_THRESHOLD:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_PENALTY {pct_4h:+.2f}%")
    elif abs(pct_4h) >= TIRING_THRESHOLD:
        if (breakout_dir == "LONG" and pct_4h > 0) or \
           (breakout_dir == "SHORT" and pct_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_PENALTY {pct_4h:+.2f}%")

    # ── Volume surge ──
    if len(candles_1h) >= 4:
        vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))
                for c in candles_1h[-4:]]
        if len(vols) >= 4 and vols[:-1] and sum(vols[:-1]) > 0:
            avg_prev = sum(vols[:-1]) / len(vols[:-1])
            vol_vs_avg = vols[-1] / avg_prev if avg_prev > 0 else 0
            if vol_vs_avg >= 2.0:
                score += 2
                reasons.append(f"VOLUME_SURGE {vol_vs_avg:.1f}x")
            elif vol_vs_avg >= 1.3:
                score += 1
                reasons.append(f"VOLUME_UP {vol_vs_avg:.1f}x")

    # ── SM confirmation ──
    sm = sm_map.get(asset)
    sm_pct = 0.0
    sm_traders = 0
    sm_dir = ""
    if sm:
        sm_dir = str(sm.get("direction", "")).upper()
        sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
        sm_traders = int(sm.get("trader_count", 0))

        if sm_dir == breakout_dir and sm_pct >= 3:
            score += 2
            reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
        elif sm_dir == breakout_dir and sm_pct >= 1:
            score += 1
            reasons.append(f"SM_BUILDING {sm_pct:.1f}% ({sm_traders}t)")
        elif sm_dir != breakout_dir and sm_pct >= 5:
            score += 1
            reasons.append(f"SM_TRAPPED_{sm_dir} {sm_pct:.1f}%")

    # ── Spread gate (v2.0: relaxed 0.2% -> 0.35%) ──
    # v3.0.1 (2026-05-14) — order book parsing fix.
    # Bug: market_get_asset_data returns the order book as
    #   {"levels": [[bid_levels], [ask_levels]]}
    # but original code looked for top-level "bids"/"asks" keys that
    # don't exist. The `if bids and asks:` check silently evaluated
    # False, the spread gate was bypassed entirely, and spread_pct
    # stayed at None. Any signal that passed other gates fired
    # regardless of how wide the actual XYZ spread was — a real risk
    # on low-liquidity XYZ assets where spreads can blow out.
    # Fix: parse the nested `levels` structure first, fall back to
    # the legacy top-level keys for compat.
    spread_pct = None
    ob = ad.get("order_book", ad.get("orderBook", {}))
    if isinstance(ob, dict):
        levels = ob.get("levels", [])
        bids, asks = [], []
        if isinstance(levels, list) and len(levels) >= 2:
            bids = levels[0] if isinstance(levels[0], list) else []
            asks = levels[1] if isinstance(levels[1], list) else []
        else:
            # Legacy schema fallback (kept for backward compat)
            bids = ob.get("bids", ob.get("bid", []))
            asks = ob.get("asks", ob.get("ask", []))
        if bids and asks:
            best_bid = safe_float(bids[0][0] if isinstance(bids[0], list)
                                  else bids[0].get("price", 0)
                                  if isinstance(bids[0], dict)
                                  else 0)
            best_ask = safe_float(asks[0][0] if isinstance(asks[0], list)
                                  else asks[0].get("price", 0)
                                  if isinstance(asks[0], dict)
                                  else 0)
            if best_bid > 0 and best_ask > 0:
                mid = (best_bid + best_ask) / 2
                spread_pct = (best_ask - best_bid) / mid
                if spread_pct > SPREAD_MAX:
                    return None
                reasons.append(f"SPREAD_OK {spread_pct*100:.3f}%")

    # ── Funding alignment ──
    funding = 0.0
    asset_ctx = ad.get("asset_context", ad.get("assetContext", {}))
    if isinstance(asset_ctx, dict):
        funding = safe_float(asset_ctx.get("funding", 0))
        if (breakout_dir == "LONG" and funding < -0.001) or \
           (breakout_dir == "SHORT" and funding > 0.001):
            score += 1
            reasons.append(f"FUNDING_ALIGNED {funding*100:.4f}%")

    return {
        "token": asset,
        "direction": breakout_dir,
        "pct_1h": pct_1h,
        "pct_4h": pct_4h,
        "score": score,
        "reasons": reasons,
        "sm_pct": sm_pct,
        "sm_traders": sm_traders,
        "sm_dir": sm_dir,
        "funding": funding,
        "spread_pct": spread_pct or 0.0,
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMISSION
# ═══════════════════════════════════════════════════════════════

def build_signal_data(candidate, leverage, margin_usd, held_assets):
    """v3.0.0: helpers `push_signal()` takes asset/direction/signal_type/score
    as top-level kwargs. Everything else goes in `data`."""
    held_list = sorted(list(held_assets)) if held_assets else []
    return {
        "score": candidate["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "rawAsset": candidate["token"],     # bare token (no "xyz:" prefix)
        "pct1h": float(candidate["pct_1h"]),
        "pct4h": float(candidate["pct_4h"]),
        "smPct": float(candidate["sm_pct"]),
        "smTraders": int(candidate["sm_traders"]),
        "smDir": candidate["sm_dir"],
        "fundingRate": float(candidate["funding"]),
        "spreadPct": float(candidate["spread_pct"]),
        "reasons": " | ".join(candidate["reasons"]),
        "heldAssets": held_list,
        # v3.0.3: _kestrel_producer_version is intentionally NOT in the
        # signal `data` payload. The runtime's external_scanner validates
        # the data block against config.fields; an undeclared key here is
        # rejected with INVALID_REQUEST ("received undeclared data field
        # '_kestrel_producer_version'"), which silently dropped EVERY
        # Kestrel signal. The version tag stays in cfg.output(...) log
        # lines only (fleet-standard — see cheetah/dire/etc.), so it's
        # still visible in /tmp/kestrel-producer.log without polluting the
        # validated signal contract. Do NOT re-add it here, and do NOT
        # "fix" this by declaring the tag in runtime.yaml.
    }


def push_signal(candidate, leverage, margin_usd, held_assets):
    """v3.0.0: direct POST to runtime /signals via helpers SenpiClient.

    v3.0.2 SCORE-NORMALIZATION FIX: the runtime requires the TOP-LEVEL
    `score` kwarg to be in [0, 1] (it's the runtime's confidence band, not
    the strategy's raw score). Kestrel's raw scoring stack is 5-10+, so
    passing it verbatim caused every push_signal call to fail with:
        push_signal: top-level score must be in [0, 1] (got 7.0)
    The producer silently went without signals — and Kestrel hadn't traded
    in over a week before the agent diagnosed this on 2026-05-29.

    Fix: normalize the raw score to [0, 1] for the top-level kwarg (a
    Score-10 entry becomes 1.0, Score-5 becomes 0.5). The full raw score
    stays inside `data.score` so the LLM gate still sees the actual
    conviction tier when deciding execute/skip.
    """
    if not STRATEGY_ADDRESS:
        cfg.log("KESTREL_WALLET not set; cannot push signal")
        return False
    data_block = build_signal_data(candidate, leverage, margin_usd, held_assets)
    raw_score = float(candidate["score"])
    normalized = min(max(raw_score / 10.0, 0.0), 1.0)
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=f"xyz:{candidate['token']}",
            direction=candidate["direction"],
            signal_type=SIGNAL_TYPE,
            score=normalized,
            data=data_block,
        )
        return True
    except Exception as e:  # noqa: BLE001
        cfg.log(f"push_signal failed for xyz:{candidate['token']}: "
                f"{type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    if not KESTREL_WALLET:
        cfg.output({
            "status": "error",
            "error": "KESTREL_WALLET not set. Configure config/kestrel-config.json wallet field.",
            "_kestrel_producer_version": VERSION,
        })
        return

    # ── Account + held assets ──
    account_value, pos_count, held_assets = get_account_state()
    if account_value is None or account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "cannot read account value; skip tick",
            "_kestrel_producer_version": VERSION,
        })
        return

    # ── Detect closes vs last tick ──
    previously_held = load_previously_held()
    closed_this_tick = detect_closes(held_assets, previously_held)
    save_previously_held(held_assets)

    # ── Max positions guard (Kestrel runs 2-position macro book) ──
    if pos_count >= MAX_POSITIONS:
        cfg.output({
            "status": "ok",
            "note": f"perched ({pos_count} positions): {sorted(list(held_assets))}",
            "open_positions": pos_count,
            "_kestrel_producer_version": VERSION,
        })
        return

    # ── Daily cap (P&L-aware dynamic) ──
    tc = load_trade_counter()
    dyn_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dyn_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "status": "ok",
            "note": f"daily cap reached: {tc.get('entries')}/{dyn_cap} (PnL {pnl_pct:+.1f}%)",
            "_kestrel_producer_version": VERSION,
        })
        return

    # ── Fetch SM data (XYZ-filtered) once per tick ──
    sm_map = fetch_sm_xyz_map()

    # ── Score every asset in universe ──
    candidates = []
    all_scored = []
    skipped_held = 0
    skipped_post_close = 0
    post_close_skips = []

    for asset in sorted(ALLOWED_ASSETS):
        # Held
        if asset in held_assets:
            skipped_held += 1
            continue

        # Post-close cooldown
        if is_in_post_close_cooldown(asset):
            skipped_post_close += 1
            post_close_skips.append({
                "token": asset,
                "remaining_min": post_close_cooldown_remaining_min(asset),
            })
            continue

        # Emit cooldown
        if is_asset_cooled_down(asset):
            continue

        scored = score_asset_breakout(asset, sm_map)
        if scored is None:
            continue

        all_scored.append({
            "token": scored["token"],
            "direction": scored["direction"],
            "score": scored["score"],
            "pct_1h": round(scored["pct_1h"], 2),
        })

        if scored["score"] >= MIN_SCORE_DEFAULT:
            candidates.append(scored)

    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        cfg.output({
            "status": "ok",
            "note": f"0 candidates at score >= {MIN_SCORE_DEFAULT} ({len(all_scored)} scored)",
            "topScored": top3,
            "skipped_held": skipped_held,
            "skipped_post_close": skipped_post_close,
            "post_close_skips": post_close_skips,
            "closed_this_tick": sorted(list(closed_this_tick)),
            "held_assets": sorted(list(held_assets)),
            "_kestrel_producer_version": VERSION,
        })
        return

    # ── Pick top candidate ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_usd = round(account_value * MARGIN_PCT, 2)
    leverage = get_leverage_for_score(best["score"])

    # ── Emit signal ──
    pushed = 0
    if push_signal(best, leverage, margin_usd, held_assets):
        pushed += 1
        mark_asset_emitted(best["token"])
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

    elapsed = time.time() - run_start
    warn = "WARN_OVER_180S" if elapsed > 180 else None
    cfg.output({
        "status": "ok",
        "candidates_total": len(candidates),
        "all_scored": len(all_scored),
        "skipped_held": skipped_held,
        "skipped_post_close": skipped_post_close,
        "post_close_skips": post_close_skips,
        "closed_this_tick": sorted(list(closed_this_tick)),
        "held_assets": sorted(list(held_assets)),
        "signals_pushed": pushed,
        "best_signal": {
            "asset": f"xyz:{best['token']}",
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "marginUsd": margin_usd,
            "pct1h": round(best["pct_1h"], 2),
            "pct4h": round(best["pct_4h"], 2),
            "reasons": best["reasons"][:6],
        } if pushed else None,
        "account_value": round(account_value, 2),
        "open_positions": pos_count,
        "entries_today": tc.get("entries", 0),
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_kestrel_producer_version": VERSION,
    })


if __name__ == "__main__":
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"kestrel-producer-{_wallet_lock_id}",
        wallet=KESTREL_WALLET,
        scanner=SCANNER_NAME,
        tick_timeout=240,
    )
