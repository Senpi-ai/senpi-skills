#!/usr/bin/env python3
# Senpi JAGUAR Producer v4.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""JAGUAR v4.0.0 Producer — senpi_runtime_helpers migration.

v4.0.0 (2026-05-12): plumbing migration. NO thesis change.
Striker-only rank-jump detection preserved verbatim from v3.7:
FIRST_JUMP / IMMEDIATE_MOVER scoring, Hyperfeed 15m/1h velocity,
$3M day-notional liquidity floor, conviction-scaled leverage
(7x / 10x), MIN_SCORE=9.

What flipped (plumbing only):
  - Scanner → Producer (Vulture/Mantis/Bison pattern). No more
    `create_position` calls — signals are emitted via
    `cfg._wrapper_client.push_signal()` and the runtime owns
    execution (LLM-pass-through gate + risk.guard_rails + DSL).
  - mcporter subprocess → in-process SenpiClient.mcp_call() via
    jaguar_config.mcporter_call shim.
  - Cron → long-lived daemon via `producer_daemon` (180s tick).
  - fcntl reentrancy guard dropped — `producer_daemon` owns the
    per-tick scanner_lock with stale-PID auto-recovery.
  - Daily-cap state file dropped — runtime's risk.guard_rails owns
    max_entries_per_day from v3.7 onward.

What's preserved verbatim:
  - v3.2 rank-jump threshold floor (STRIKER_MIN_RANK_JUMP=10)
  - v3.3 prev-rank floor (STRIKER_MIN_PREV_RANK=20)
  - v3.3 reason count floor (STRIKER_MIN_REASONS=3)
  - v3.4 silent-None fix: day_notional ≥ $3M absolute liquidity floor
    instead of vol_ratio hard gate (which API doesn't emit)
  - v3.0 striker-only thesis (no Stalker, no Hunter, no Pyramiding)
  - Conviction-scaled leverage tiers (score≥10 → 10x, ≥9 → 7x)
  - 15m velocity hard gate (contrib_15m > 0)
  - XYZ ban

Striker thesis: "one amazing trade per day" — rank-jump detection
catches violent FIRST_JUMP signals (asset rocketing from rank 20+
into the top 10 with ≥10 rank jump, score ≥9). Rare but
high-conviction. Producer emits signal; runtime gates, sizes, and
manages exit via DSL.

Environment / config resolution:
  Strategy wallet is read from config/jaguar-config.json (the
  canonical source). JAGUAR_WALLET env var is supported as an
  optional override. Fleet rule: no wallet-specific hardcoding.

  SENPI_AUTH_TOKEN       — REQUIRED. Bearer token for MCP + signal POST.
  JAGUAR_WALLET          — optional override; defaults to config.wallet
  JAGUAR_DECISION_MODEL  — bare LLM model name (no provider prefix)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jaguar_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


# Reentrancy guard removed in v4.0.0. producer_daemon owns the per-tick
# scanner_lock with stale-PID auto-recovery via os.kill(pid, 0). Do NOT
# add an inner scanner_lock(...) inside main() — fcntl flock is not
# reentrant; nested call raises BlockingIOError every tick.


VERSION = "4.0.2"

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "jaguar_signals"

# Signal type passed explicitly to push_signal() per Rachin's review
# of Cheetah PR #209.
SIGNAL_TYPE = "JAGUAR_STRIKER"


def _resolve_wallet():
    """Resolve strategy wallet — config.json is the canonical source.
    Env var JAGUAR_WALLET is supported as an optional override (useful
    for CI/testing) but is NOT required for routine daemon runs."""
    env_val = (os.environ.get("JAGUAR_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v3.7
# ═══════════════════════════════════════════════════════════════

MIN_SCORE = 9
XYZ_BANNED = True

# Conviction-scaled leverage. Fleet analysis: >10x destroys edge via
# fee amplification. Striker apex still capped at 10x.
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10, "label": "apex"},
    {"min_score": 9,  "leverage": 7,  "label": "conviction"},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10

# Striker thresholds (v3.2 / v3.3 — preserved verbatim)
STRIKER_MIN_RANK_JUMP = 10   # v3.2 floor — rank-jump detector
STRIKER_MIN_PREV_RANK = 20   # v3.3
STRIKER_MIN_VOLUME_RATIO = 1.5
STRIKER_MIN_REASONS = 3      # v3.3

# v3.4 absolute liquidity floor (replaces silent-None vol_ratio gate)
MIN_DAY_NOTIONAL_VOLUME_USD = 3_000_000  # $3M 24h liquidity floor

# Margin fraction — runtime applies this as the position's marginUsd
# baseline. Signal carries it; the LLM gate copies it through.
MARGIN_PCT = 0.50

# Scan history retention — used for previous-rank / contrib comparison
MAX_SCAN_HISTORY = 60


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE), tier["label"]
    return DEFAULT_LEVERAGE, "default"


def get_safe_leverage(wallet, coin, desired):
    """Clamp leverage to per-asset HL max (fleet pattern). Without this,
    signals on assets with HL max < MAX_LEVERAGE (e.g. XMR 5x) hit
    CREATE_INVALID_LEVERAGE downstream. The producer pre-clamps in v4
    so the LLM gate's leverage echo is already venue-safe."""
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
                if "maxLeverage" in d or "max_leverage" in d:
                    max_lev = int(d.get("maxLeverage", d.get("max_leverage", MAX_LEVERAGE)))
                    return min(desired, max_lev, MAX_LEVERAGE)
    except Exception:
        pass
    return min(desired, MAX_LEVERAGE)


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
# STATE — scan history (only durable state in v4)
# ═══════════════════════════════════════════════════════════════

def _state_dir():
    d = cfg.SKILL_DIR / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_scan_history():
    p = _state_dir() / "scan-history.json"
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > MAX_SCAN_HISTORY:
        history["scans"] = scans[-MAX_SCAN_HISTORY:]
    p = _state_dir() / "scan-history.json"
    tmp = str(p) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp, str(p))


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
# STRIKER SIGNAL DETECTION — preserved verbatim from v3.7
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP signals with Hyperfeed velocity scoring.

    Preserved verbatim from v3.7. Producer used to call execute_entry
    on the top-scored signal; in v4 we return ALL qualifying signals
    sorted by score and the runtime decides.
    """
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

        # v3.4: absolute liquidity floor (replaces silent-None vol_ratio gate)
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
            "contrib15m": contrib_15m,
            "contrib1h": contrib_1h,
            "dayNotionalUsd": day_notional,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ═══════════════════════════════════════════════════════════════
# CONTEXT ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def fetch_held_assets():
    """Read current positions from the strategy wallet."""
    if not STRATEGY_ADDRESS:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
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


def fetch_account_value():
    """Read total account value for margin sizing."""
    if not STRATEGY_ADDRESS:
        return 0.0
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=STRATEGY_ADDRESS)
        if not ch:
            return 0.0
        data = ch.get("data", ch)
        total = 0.0
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            ms = s.get("marginSummary", {})
            total += float(ms.get("accountValue", 0) or 0)
        return total
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(candidate, account_value, held_assets):
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "jaguar-config.json (preferred) or JAGUAR_WALLET env var",
            file=sys.stderr,
        )
        return False

    # Skip if already holding this asset
    if candidate["token"].upper() in {h.upper() for h in held_assets}:
        return False

    desired_leverage, tier_label = get_leverage_for_score(candidate["score"])
    leverage = get_safe_leverage(STRATEGY_ADDRESS, candidate["token"], desired_leverage)
    margin_usd = round(account_value * MARGIN_PCT, 2) if account_value > 0 else 0

    data_block = {
        "score": candidate["score"],
        "tier": tier_label,
        "leverage": leverage,
        "marginUsd": margin_usd,
        "marginPct": MARGIN_PCT,
        "reasons": candidate["reasons"],
        "mode": "STRIKER",
        "currentRank": candidate["currentRank"],
        "rankJump": candidate["rankJump"],
        "isFirstJump": candidate["isFirstJump"],
        "contribVelocity": candidate["contribVelocity"],
        "contrib15m": candidate["contrib15m"],
        "contrib1h": candidate["contrib1h"],
        "volRatio": candidate["volRatio"],
        "smPct": candidate["contribution"],
        "smTraders": candidate["traders"],
        "priceChange4hPct": candidate["priceChg4h"],
        "dayNotionalUsd": candidate["dayNotionalUsd"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=candidate["token"],
            direction=candidate["direction"],
            score=candidate["score"] / 14.0,
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {candidate['token']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        print(f"INGEST_EXCEPTION {candidate['token']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()

    # Fetch SM data
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        print(json.dumps({
            "status": "no_markets",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_jaguar_producer_version": VERSION,
        }))
        return

    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        print(json.dumps({
            "status": "bad_shape",
            "_jaguar_producer_version": VERSION,
        }))
        return

    # Build scan snapshot
    #
    # v4.0.1 ORDER-OF-OPERATIONS FIX: detect FIRST, then append.
    #
    # Bug: prior to v4.0.1, the producer appended `current_scan` to
    # history["scans"] BEFORE calling detect_striker_signals(). Inside the
    # detection function, `prev_scans[-1]` then returned the SAME scan that
    # was just appended — so every asset's `rank_jump` computed as
    # current_rank - current_rank = 0. No asset ever met STRIKER_MIN_RANK_JUMP
    # and the scanner went completely silent. In a historical-replay test,
    # this dropped 25 valid signals over 2 days (VVV/XMR/GRASS spikes among
    # them). The agent's own diagnosis on 2026-05-29; ferried into the repo.
    #
    # Correct order: detect (with the still-prior history), THEN append the
    # current scan for the next tick to compare against.
    current_scan = build_scan_snapshot(markets)
    history = load_scan_history()

    # First-ever scan? Seed history and return — nothing to compare against yet.
    if not history.get("scans"):
        history["scans"] = [current_scan]
        save_scan_history(history)
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": 0,
            "signals_pushed": 0,
            "note": "Building scan history (need 2+ scans)",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_jaguar_producer_version": VERSION,
        }))
        return

    # Detect Striker signals against the previous scan (now actually prior, not self)
    candidates = detect_striker_signals(current_scan, history)

    # Append current scan AFTER detection so the next tick has a real prior.
    history["scans"].append(current_scan)
    save_scan_history(history)

    if not candidates:
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_SCORE,
            "elapsed_sec": round(elapsed, 2),
            "_jaguar_producer_version": VERSION,
        }))
        return

    # Enrich context once per tick — shared across candidates
    held_assets = fetch_held_assets()
    account_value = fetch_account_value()

    # Push qualifying candidates. Runtime's LLM gate + risk
    # guard_rails will decide which (if any) to execute.
    pushed = 0
    for c in candidates:
        if push_signal(c, account_value, held_assets):
            pushed += 1

    elapsed = time.time() - run_start
    warn = "WARN_OVER_60S" if elapsed > 60 else None
    print(json.dumps({
        "status": "ok",
        "scanned": len(markets),
        "candidates": len(candidates),
        "signals_pushed": pushed,
        "min_score": MIN_SCORE,
        "best_score": candidates[0]["score"] if candidates else None,
        "best_asset": candidates[0]["token"] if candidates else None,
        "held_assets": held_assets,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_jaguar_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v4.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,           # Jaguar's v3.x cron was every 3 min
        name=f"jaguar-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=240,
    )
