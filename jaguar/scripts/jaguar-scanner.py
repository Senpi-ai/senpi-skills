#!/usr/bin/env python3
# Senpi JAGUAR Scanner v3.6
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""JAGUAR v3.6 — Striker-Only (pyramiding fix + config-driven starting budget).

v3.6 change (2026-05-06, operator-diagnosed) — TWO fixes:

1. PYRAMIDING BUG: live failure 2026-05-06 evening — NEAR went from $25 →
   $398 margin in 60 seconds via 5 "DSL position increased" events on
   the same signal during partial-fill state. Same Pangolin v2.1 /
   Scorpion v4.1.0 dedup-bug-class — the producer was checking
   held_coins (positions present in clearinghouseState) but NOT
   pending_coins (resting non-reduceOnly entry orders). When an entry
   ALO is partially filled, the next scan tick sees the asset NOT yet
   in held positions and emits the same signal → runtime executes as
   ADD to existing partial fill rather than skip.

   Fix ports the Scorpion v4.1.1 pattern: get_pending_entry_coins(wallet)
   queries strategy_get_open_orders, returns set of non-reduceOnly
   coins. main() unions held_coins ∪ pending_coins before checking
   duplicate. Same-asset retry blocked until the resting order either
   fills (becomes held) or cancels (no longer pending).

2. STARTING_BUDGET config-driven: prior versions hardcoded
   STARTING_BUDGET=1000.0 in this file, which forced operators to edit
   the producer code to rebase capital after drawdown. Per fleet rule
   (memory feedback_never_hardcode_wallet_specific.md), wallet-specific
   values belong in config.json. Ports the Grizzly v5.2
   _resolve_starting_budget() pattern: read 'startingBudget' from
   jaguar-config.json, fall back to 1000.0 if absent. Operator can now
   set "startingBudget": <value> in their local config without modifying
   code that gets clobbered by the next git pull.

   NOTE: Per v3.7 runtime.yaml risk.guard_rails, the dynamic daily cap
   below is now redundant defense. Runtime is the authoritative gate
   when its risk block fires. Producer's get_dynamic_daily_cap stays as
   fallback for v1-runtime hosts that don't enforce risk.guard_rails.

v3.5 change (2026-05-06) — CREATE_INVALID_LEVERAGE on small-cap perps:
Live failure: XMR LONG signal at score 10 tried 10x leverage, HL rejected
with "Max leverage for XMR is 5, got 10." Producer was picking leverage
from conviction tier without clamping to per-asset HL max. Same fleet
pattern that other agents (Wolverine, Grizzly, Cheetah, Vulture, etc.)
use via get_safe_leverage(). Ports the standard pattern: query
strategy_get_asset_trading_limits per asset, clamp to min(desired,
asset_max, MAX_LEVERAGE). Asset list affected: XMR (max 5x), kBONK,
kPEPE-class small caps, and any HIP-3 instrument with caps below the
fleet 10x ceiling. Without this clamp, ~5-10% of striker signals would
silently fail at execute_entry without retry.

v3.4 change (2026-04-23) — THE ACTUAL DORMANCY CAUSE:
After v3.3 widened striker gates, Jaguar still fired 0 trades. Live diag
revealed CHIP SHORT score 11 and XMR SHORT score 9 were BOTH valid
signals but 100% rejected by the `vol_ratio < 1.5` gate. Root cause:
`leaderboard_get_markets` API doesn't emit `vol_ratio` / `volume_ratio` /
`avg_volume` fields, so `vol_ratio` silently defaulted to 0 → rejected
every signal.

v3.4 replaces the 1.5x ratio hard gate with:
  - Absolute liquidity floor via `day_notional_volume` ≥ $3M (fleet standard)
  - Soft vol_ratio bonus when data IS available (no rejection if missing)
Preserves the gate's intent (liquidity/participation check) without
silently zeroing every candidate.

This is the same silent-None family as Pangolin v1.5 and Dog v2.4
`funding_history` parser bugs. Check scanner output for presence of
all gated fields in live API responses before trusting a gate.

v3.3 change (2026-04-22) — widened gates (still useful — kept):
- STRIKER_MIN_RANK_JUMP: 10 → 7
- STRIKER_MIN_PREV_RANK: 25 → 20
- STRIKER_MIN_REASONS: 4 → 3
- Inline `rank_jump >= 10` replaced with STRIKER_MIN_RANK_JUMP constant.
v3.2 widened one gate but the combined (jump>=10 AND prev_rank>=25)
window is (prev#25-35 → current#11-25) — very narrow. And requiring 4
reason labels rejected valid 3-label signals. v3.3 widens all three.

v3.2 change — rank jump threshold loosening (2026-04-15):
- STRIKER_MIN_RANK_JUMP: 15 → 10. 0 events fired in 10,239 evaluations
  under the prior threshold — the signal was unreachable in the current
  market regime. Lowering to 10 re-engages Striker detection.

v3.1 changes from fleet audit (2026-04-09):
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: 14+→20x, 12+→15x, 10+→10x, 9+→7x
- Margin increased to 50% (was 20%)
- has_resting_orders() with reduceOnly filter prevents position stacking
- Hyperfeed multi-window contribution velocity (15m, 1h) scoring
- No thesis exit (unchanged)
- XYZ equities banned

v2.0 changes:
- Stalker REMOVED, Hunter REMOVED, Pyramiding REMOVED
- Leverage reduced from 10x to conviction-scaled
- Exit management handled by plugin runtime (runtime.yaml)

The Striker logic detects FIRST_JUMP signals: assets rocketing from rank 25+
into the top 10 with 15+ rank jump, volume 1.5x, score 9+.
These are violent SM explosions — rare but high-conviction.

2 API calls: leaderboard_get_markets (current) + scan history (previous).
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jaguar_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 3


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

def _resolve_starting_budget():
    """v3.6: read startingBudget from jaguar-config.json, fall back to
    $1000.0. Mirrors Grizzly v5.2 pattern. Lets operators rebase capital
    baseline (e.g. acknowledge a drawdown and start fresh from current
    equity) without editing producer code that gets clobbered by next
    git pull."""
    try:
        c = cfg.load_config()
        v = c.get("startingBudget") if isinstance(c, dict) else None
        if v is not None:
            return float(v)
    except Exception:
        pass
    return 1000.0


STARTING_BUDGET = _resolve_starting_budget()


def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap based on drawdown from starting budget.

    Winners get more trades (ride the hot hand).
    Losers get fewer trades (preserve capital).
    Catastrophic drawdown triggers HARD STOP (circuit breaker).
    """
    if starting_budget <= 0:
        return 4  # Safe fallback
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand — up >5%
    elif pnl_pct >= 0:     return 8    # Small win / breakeven
    elif pnl_pct >= -5:    return 5    # Careful
    elif pnl_pct >= -15:   return 3    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve — only highest conviction
    else:                  return 0    # HARD STOP — circuit breaker

COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 9
XYZ_BANNED = True

# Fleet-standard conviction-scaled leverage.
# Score 14+ is genuinely rare for Striker signals — requires FIRST_JUMP + deep SM
# + 4H strong + high velocity + volume explosion. Max leverage only on extremes.
# Fleet analysis: >10x leverage destroys edge via fee amplification
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 9,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10

# Striker thresholds
# v3.3 (2026-04-22): dormancy fix.
#   v3.2's 10-jump-from-rank-25 window = very narrow (prev#25-35 → current#11-25).
#   Also STRIKER_MIN_REASONS=4 required 4 distinct reason labels — typical valid
#   signals have 3 (IMMEDIATE+FIRST_JUMP+15M_SPIKE or IMMEDIATE+15M_SPIKE+STRONG_4H).
#   Widen: jump 10→7, prev-rank 25→20, reasons 4→3.
STRIKER_MIN_RANK_JUMP = 7   # v3.3: 10→7
STRIKER_MIN_PREV_RANK = 20  # v3.3: 25→20
STRIKER_MIN_VOLUME_RATIO = 1.5
STRIKER_MIN_REASONS = 3     # v3.3: 4→3


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


def get_safe_leverage(wallet, coin, desired):
    """Clamp leverage to the per-asset HL max (PR #194 fleet pattern).
    Without this, signals on assets with HL max < MAX_LEVERAGE (e.g. XMR
    capped at 5x) hit CREATE_INVALID_LEVERAGE and the entry fails. Caught
    live 2026-05-06: XMR LONG signal at score 10 tried 10x, HL rejected
    because XMR max is 5x. Returns the largest leverage that satisfies
    BOTH the conviction tier AND the asset's HL ceiling."""
    try:
        r = cfg.mcporter_call("strategy_get_asset_trading_limits",
                              strategy_wallet=wallet, coin=coin)
        if r:
            d = r.get("data", r)
            if isinstance(d, dict):
                lev = d.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", MAX_LEVERAGE)))
                    return min(desired, max_lev, MAX_LEVERAGE)
                elif isinstance(lev, (int, float)):
                    return min(desired, int(lev), MAX_LEVERAGE)
                # Older schema: maxLeverage / max_leverage flat field
                if "maxLeverage" in d or "max_leverage" in d:
                    max_lev = int(d.get("maxLeverage", d.get("max_leverage", MAX_LEVERAGE)))
                    return min(desired, max_lev, MAX_LEVERAGE)
    except Exception:
        pass
    return min(desired, MAX_LEVERAGE)


def get_pending_entry_coins(wallet):
    """v3.6: return set of coins with non-reduceOnly resting orders
    (pending entries). Used by the dedup check below to prevent the
    pyramiding bug — when an entry ALO is partially filled, the asset
    is NOT yet in held positions but the next scan tick should still
    skip it because there's a pending entry on the book.

    Pangolin v2.1 / Scorpion v4.1.0 dedup-bug-class fix. Reads
    strategy_get_open_orders, filters non-reduceOnly orders, returns
    coin set."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return set()
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return set()
    pending = set()
    for o in orders:
        if not o.get("reduceOnly", False):
            coin = (o.get("coin") or "").upper()
            if coin:
                pending.add(coin)
    return pending


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC (default 600s / 10 min).

    Without auto-cancel, a maker FEE_OPTIMIZED_LIMIT order that never
    fills can lock the scanner out of new entries indefinitely, because
    every subsequent scan sees the stale order and aborts early. Ignores
    reduceOnly orders (those are DSL exit legs)."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600  # 10 minutes
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue  # Treat cancelled order as gone
        has_fresh = True
    return has_fresh


def check_4h_alignment(direction, price_chg_4h):
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# STRIKER SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP signals with Hyperfeed velocity scoring."""

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m.get("token", ""), m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market.get("token", "")
        dex = market.get("dex", "")
        current_rank = market.get("rank", 999)
        direction = market.get("direction", "").upper()
        current_contrib = market.get("contribution", 0)
        traders = market.get("traders", 0)

        if current_rank <= 10:
            continue

        price_chg_4h = market.get("price_chg_4h", 0)
        if not check_4h_alignment(direction, price_chg_4h):
            continue

        if XYZ_BANNED and dex == "xyz":
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        if not prev_market:
            continue

        rank_jump = prev_market.get("rank", 999) - current_rank
        prev_rank = prev_market.get("rank", 999)

        is_first_jump = False
        is_immediate = False
        reasons = []

        if rank_jump >= STRIKER_MIN_RANK_JUMP and prev_rank >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_rank}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_rank >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_rank}->#{current_rank}")

        if not is_first_jump and not is_immediate:
            continue

        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        # Contribution explosion
        if prev_market.get("contribution", 0) > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        # Contribution velocity from history
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m.get("contribution", 0))
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        # ── Scoring ──
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2

        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs(contrib_velocity):.1f}")

        if prev_rank >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        # 4H strength bonus
        if abs(price_chg_4h) > 3:
            score += 1
            reasons.append(f"STRONG_4H {price_chg_4h:+.1f}%")

        # Trader count (SM depth)
        if traders >= 30:
            score += 1
            reasons.append(f"DEEP_SM ({traders}t)")

        # Hyperfeed 15m/1h contribution velocity + freshness gate
        contrib_15m = market.get("contrib_15m", 0)
        contrib_1h = market.get("contrib_1h", 0)

        # Striker-class hard gate: SM must be actively building right now
        if contrib_15m <= 0:
            reasons.append(f"15M_STALE ({contrib_15m:.2f})")
            continue  # Signal not fresh, skip

        if contrib_15m > 2.0:
            score += 3
            reasons.append(f"15M_STRONG_SPIKE +{contrib_15m:.2f}")
        elif contrib_15m > 0.5:
            score += 2
            reasons.append(f"15M_SPIKE +{contrib_15m:.2f}")
        elif contrib_15m > 0.1:
            score += 1
            reasons.append(f"15M_BUILDING +{contrib_15m:.2f}")

        if contrib_1h > 1.0:
            score += 1
            reasons.append(f"1H_ACCEL +{contrib_1h:.2f}")

        # Acceleration pattern
        if contrib_15m > 0 and contrib_1h > 0 and contrib_15m > contrib_1h:
            score += 1
            reasons.append(f"ACCEL_PATTERN 15m({contrib_15m:.2f})>1h({contrib_1h:.2f})")

        if score < MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # v3.4: Volume confirmation — previously a silent-None killer.
        # `vol_ratio` / `volume_ratio` / `avg_volume` fields don't exist on
        # leaderboard_get_markets responses, so vol_ratio was always 0 and
        # every signal was rejected. Jaguar has been silently dormant
        # since the baseline scanner was written.
        #
        # The intent of the gate is "confirm participation/liquidity." We
        # already have stronger participation signals (cc_15m > 0 is a
        # hard gate above, contrib_explosion/velocity score into MIN_SCORE).
        # So: replace the hard 1.5x-ratio gate with:
        #   - Absolute liquidity floor via day_notional_volume (if present)
        #   - Soft ratio bonus when data IS available (no rejection if missing)
        MIN_DAY_NOTIONAL_VOLUME_USD = 3_000_000  # $3M 24h liquidity floor
        day_notional = safe_float(
            market.get("day_notional_volume",
                market.get("dayNotionalVolume",
                    market.get("volume_24h_usd", 0)))
        )
        if day_notional > 0 and day_notional < MIN_DAY_NOTIONAL_VOLUME_USD:
            continue  # liquidity too thin

        # Soft vol_ratio bonus — only add reason if data genuinely available
        vol_ratio = safe_float(market.get("vol_ratio", market.get("volume_ratio", 0)))
        if vol_ratio == 0:
            volume = safe_float(market.get("volume", 0))
            avg_volume = safe_float(market.get("avg_volume", market.get("avgVolume", 0)))
            if avg_volume > 0:
                vol_ratio = volume / avg_volume
        if vol_ratio >= STRIKER_MIN_VOLUME_RATIO:
            reasons.append(f"VOL {vol_ratio:.1f}x")
        elif day_notional > 0:
            reasons.append(f"LIQUID ${day_notional/1e6:.1f}M")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": traders,
            "priceChg4h": price_chg_4h,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ═══════════════════════════════════════════════════════════════
# SCAN HISTORY
# ═══════════════════════════════════════════════════════════════

def load_scan_history():
    p = os.path.join(cfg.STATE_DIR, "scan-history.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > 60:
        history["scans"] = scans[-60:]
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "scan-history.json"), history)


def build_scan_snapshot(markets_data):
    markets = []
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        markets.append({
            "token": str(m.get("token", m.get("asset", ""))).upper(),
            "dex": m.get("dex", ""),
            "rank": int(m.get("rank", m.get("position", 999))),
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return {"markets": markets, "timestamp": now_iso()}


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, token, direction, leverage, margin):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": token,
            "direction": direction,
            "leverage": leverage,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": False,
                "executionTimeoutSeconds": 30,
            },
        }],
    )
    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


# ═══════════════════════════════════════════════════════════════
# COOLDOWN & TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0, "last_entry_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def is_on_cooldown(coin):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(coin)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    our_positions = [p for p in positions if not p.get("coin", "").lower().startswith("xyz")]

    if len(our_positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in our_positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v3_no_thesis_exit": True})
        return

    # Check for resting entry orders (not DSL stops)
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    tc = load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Global cooldown
    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Cooldown ({remaining}min remaining)"})
        return

    # Fetch SM data
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

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

    # Build scan snapshot and save history
    current_scan = build_scan_snapshot(markets)
    history = load_scan_history()
    history["scans"].append(current_scan)
    save_scan_history(history)

    if len(history["scans"]) < 2:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Building scan history (need 2+ scans)"})
        return

    # Detect Striker signals
    signals = detect_striker_signals(current_scan, history)

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Striker signals. Scanned {len(current_scan['markets'])} markets."})
        return

    # Filter and select best signal
    # v3.6: union held_coins with pending_coins. Without pending_coins,
    # an asset with a partially-filled entry ALO would re-emit on the
    # next scan and the runtime would treat it as ADD instead of skip,
    # causing the pyramiding bug observed live 2026-05-06 (NEAR went
    # $25 → $398 margin in 60s via 5 size-up events on the same signal).
    held_coins = {p["coin"].upper() for p in our_positions}
    pending_coins = get_pending_entry_coins(wallet)
    held_coins.update(pending_coins)

    for signal in signals:
        token = signal["token"]

        if is_on_cooldown(token):
            continue

        if token.upper() in held_coins:
            continue

        # Execute entry directly
        # v3.4: clamp leverage to per-asset HL max. XMR (max 5x), kBONK,
        # and other small-cap perps have lower ceilings than Jaguar's
        # 10x conviction tier — without clamp, HL rejects with
        # CREATE_INVALID_LEVERAGE and the entry fails silently.
        desired_leverage = get_leverage_for_score(signal["score"])
        leverage = get_safe_leverage(wallet, token, desired_leverage)
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(wallet, token, signal["direction"], leverage, margin)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            tc["last_entry_ts"] = time.time()
            save_trade_counter(tc)

            cfg.output({
                "status": "ok",
                "action": "ENTRY",
                "signal": {
                    "asset": token,
                    "direction": signal["direction"],
                    "score": signal["score"],
                    "leverage": leverage,
                    "mode": "STRIKER",
                    "reasons": signal["reasons"],
                    "rankJump": signal["rankJump"],
                    "isFirstJump": signal["isFirstJump"],
                    "volRatio": signal["volRatio"],
                    "traders": signal["traders"],
                },
                "execution": {
                    "asset": token,
                    "direction": signal["direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "result": result,
                "_jaguar_version": "3.6",
            })
        else:
            cfg.output({
                "status": "ok",
                "action": "ENTRY_FAILED",
                "signal": {
                    "asset": token,
                    "direction": signal["direction"],
                    "score": signal["score"],
                    "reasons": signal["reasons"],
                },
                "error": result,
                "_jaguar_version": "3.6",
            })
        return

    if signals:
        best = signals[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best: {best['token']} {best['direction']} score {best['score']}<{MIN_SCORE} or filtered. {', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"{len(signals)} Striker signals found but all filtered (cooldown/duplicate)"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
