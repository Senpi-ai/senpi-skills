#!/usr/bin/env python3
# Senpi DOG Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""DOG v3.0.0 Producer — Contrarian Pup, senpi_runtime_helpers migration.

v3.0.0 (2026-05-12): plumbing migration from v2.5. NO thesis change.
v2.5 scoring tables, contrarian flip, exhaustion gate, regime confirmation,
persistence bonus, hard gates all preserved verbatim. The producer no
longer executes — it emits scored signals and the runtime owns:
  - LLM gate (decision_mode: llm, pass-through pattern)
  - risk.guard_rails — max_entries_per_day, per_asset_cooldown,
    daily_loss_limit, drawdown_halt, consecutive_losses
  - DSL exits via FEE_OPTIMIZED_LIMIT (replaces v2.x MARKET exits;
    saves ~0.020-0.030% per maker-filled close)

What v2.5 did that v3.0 does NOT do:
  - call create_position directly         → runtime opens via signal flow
  - track entries in state/trade-counter  → runtime guard_rails enforce
  - manage cooldowns in Python            → runtime per_asset_cooldown
  - has_resting_orders() with auto-cancel → runtime+exit timeout handles
  - dynamic daily cap circuit breaker     → drawdown_halt_pct + static cap

What v2.5 did that v3.0 STILL DOES (preserved verbatim):
  - Score BTC/ETH/SOL/HYPE via leaderboard_get_markets every 3 min
  - 3.0% exhaustion gate (v2.5's calibrated sweet spot)
  - CONTRARIAN FLIP: producer scores SM direction, then EMITS the
    opposite direction (signal that reaches runtime is already the
    fade direction)
  - Regime hard-gate: skip if funding_regime contradicts the fade
  - Persistence + crowding-trend scoring bonus
  - 15m velocity freshness gate (cc_15m must be > 0)
  - MIN_SCORE = 8, MAX_LEVERAGE = 10, base leverage 7x / 10x at score 12+
  - US session bonus

Environment / config resolution:
  Strategy wallet is read from config/dog-config.json (the canonical
  source). DOG_WALLET env var is supported as an optional override but
  is NOT required for routine daemon runs — operators just set "wallet"
  in dog-config.json and the launch command stays wallet-agnostic.

  SENPI_API_KEY         — MCP access (required)
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  DOG_WALLET            — optional override; defaults to config.wallet
  DOG_DECISION_MODEL    — bare LLM model name (no provider prefix);
                          resolved into runtime.yaml at create time
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dog_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "3.0.0"
SCANNER_NAME = "dog_signals"
SIGNAL_TYPE = "DOG_CONTRARIAN_FADE"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2.5
# ═══════════════════════════════════════════════════════════════

ASSETS = ["BTC", "ETH", "SOL", "HYPE"]

# Scoring thresholds
MIN_SCORE = 8                          # contrarian floor — preserved from v2.5
MIN_EXHAUSTION_PCT = 3.0               # v2.5 sweet spot (2.5 too loose, 4.5 never fires)
EXHAUSTION_BONUS_SEVERE_PCT = 4.0      # +2 points for deep exhaustion
EXHAUSTION_BONUS_MODERATE_PCT = 2.5    # +1 point

# Leverage tiers — conservative for contrarian
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7

# Max leverage caps per asset (from Hyperliquid)
ASSET_MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10}


def _resolve_wallet():
    """Resolve strategy wallet — env var first, config.json second.

    Env var resolution order (first non-empty wins):
      1. DOG_WALLET — fleet-standard <SKILL>_WALLET name (v2.0.9 rule)
      2. cfg.load_config()["wallet"] — canonical source on disk
    """
    env_val = (os.environ.get("DOG_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# CONTEXT ENRICHMENT — funding regime, persistence, held positions
# ═══════════════════════════════════════════════════════════════

def fetch_funding_regime():
    """Market-wide funding regime via market_get_funding_regime MCP tool."""
    try:
        r = cfg.mcp_call("market_get_funding_regime")
        if r:
            data = r.get("data", r)
            if isinstance(data, dict):
                return data.get("regime")
    except Exception:
        pass
    return None


def fetch_funding_history(asset):
    """Fetch persistence_hours + funding_trend for one asset.

    v2.4 parser fix preserved: MCP returns data.data=[{asset, ...}], not
    data.<field>. v2.3 silently got None for persistence_hours and trend
    because the parser read data.<field> directly. Now iterate the list,
    match by asset, normalize funding_trend enum
    (INTENSIFYING→INCREASING, DECAYING→DECREASING).
    """
    try:
        r = cfg.mcp_call("market_get_funding_history", asset=asset)
        if not r:
            return None
        outer = r.get("data", r)
        rows = outer.get("data") if isinstance(outer, dict) else None
        if not rows:
            return None
        row = next((x for x in rows if x.get("asset") == asset), None)
        if row is None:
            return None
        raw_trend = (row.get("funding_trend") or row.get("trend") or "").upper()
        if raw_trend == "INTENSIFYING":
            trend = "INCREASING"
        elif raw_trend == "DECAYING":
            trend = "DECREASING"
        else:
            trend = raw_trend
        return {
            "persistence_hours": row.get("persistence_hours"),
            "trend": trend,
        }
    except Exception:
        return None


def fetch_held_assets():
    """Read current positions from the strategy wallet."""
    if not STRATEGY_ADDRESS:
        return []
    try:
        ch = cfg.mcp_call("strategy_get_clearinghouse_state",
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


def regime_confirms_fade(fade_direction, regime):
    """Fade is confirmed when regime shows crowding in OPPOSITE direction
    of trade. Dog goes SHORT to fade LONG_CROWDED. Returns True/False/None
    (None = neutral, doesn't confirm or deny).
    """
    if regime is None or regime == "NEUTRAL":
        return None
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# SCORING — v2.5 logic preserved verbatim
# ═══════════════════════════════════════════════════════════════

def score_market(m, regime, fh_map):
    """Score one market. Returns a candidate dict (post contrarian flip)
    or None to skip. v2.5 scoring logic preserved verbatim — only the
    final emission target changes (push_signal vs. create_position)."""
    if not isinstance(m, dict):
        return None
    token = str(m.get("token", "")).upper()
    dex = m.get("dex", "")
    if dex or token not in ASSETS:
        return None

    sm_direction = str(m.get("direction", "")).upper()
    if sm_direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                            m.get("price_change_1h", 0)))
    cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
    cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

    # Hard gate: minimum SM engagement
    if traders < 30:
        return None

    # CONTRARIAN EXHAUSTION GATE
    if abs(p4h) < MIN_EXHAUSTION_PCT:
        return None  # Not exhausted yet — don't fight a fresh trend
    if (sm_direction == "LONG" and p4h < 0) or (sm_direction == "SHORT" and p4h > 0):
        return None  # SM direction opposes price — not an exhaustion pattern

    score, reasons = 0, []

    # ── SM concentration (0-3) ──
    if pct >= 15:
        score += 3
        reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2
        reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1
        reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # ── Trader depth (0-1) ──
    if traders >= 100:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    # ── 4H price alignment (+/-2) ──
    if abs(p4h) >= 2.0:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1
            reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # ── MOVE EXHAUSTION — INVERTED for contrarian ──
    if abs(p4h) >= EXHAUSTION_BONUS_SEVERE_PCT:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}% (great fade)")
    elif abs(p4h) >= EXHAUSTION_BONUS_MODERATE_PCT:
        if (sm_direction == "LONG" and p4h > 0) or (sm_direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"EXHAUSTION {p4h:+.1f}% (fadeable)")

    # ── 1H momentum (0-1) ──
    if (sm_direction == "LONG" and p1h > 0.2) or (sm_direction == "SHORT" and p1h < -0.2):
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # ── 15m velocity freshness gate ──
    # For contrarian: SM must be actively building the position we're about to fade.
    # If SM is already unwinding (15m <= 0), the fade opportunity is passing — skip.
    if cc_15m <= 0:
        return None
    if cc_15m > 2.0:
        score += 3
        reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.5:
        score += 2
        reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1:
        score += 1
        reasons.append(f"15M_BUILDING +{cc_15m:.2f}")

    # ── 1h acceleration (0-1) ──
    if cc_1h > 1.0:
        score += 1
        reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

    # ── Funding alignment (0-1) — Dog likes funded trades ──
    asset_funding = 0.0
    try:
        ad = cfg.mcp_call("market_get_asset_data", asset=token,
                          candle_intervals=[], include_funding=True,
                          include_order_book=False)
        if ad:
            ac = ad.get("data", ad).get("asset_context",
                                         ad.get("data", ad).get("assetContext", {}))
            if isinstance(ac, dict):
                asset_funding = safe_float(ac.get("funding", 0))
                # Note: SM direction is the to-be-faded direction. Dog will
                # EMIT the opposite. Funding "pays the fade" means SM is
                # short into positive funding (paying longs) OR long into
                # negative funding (paying shorts) — same as v2.5 logic.
                fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"
                if (fade_direction == "SHORT" and asset_funding > 0.0002) or \
                   (fade_direction == "LONG" and asset_funding < -0.0002):
                    score += 1
                    reasons.append(f"FUNDING_PAYS {asset_funding*100:.4f}%")
    except Exception:
        pass

    # ── Regime HARD GATE — fade direction must align with crowded regime ──
    fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"
    confirms = regime_confirms_fade(fade_direction, regime)
    if confirms is False:
        return None  # regime contradicts fade — skip
    if confirms is True:
        score += 2
        reasons.append(f"REGIME_CONFIRMS_{regime}")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # ── Persistence + trend via funding_history ──
    ph_val = None
    crowding_trend = None
    fh = fh_map.get(token)
    if fh:
        ph = fh.get("persistence_hours")
        crowding_trend = (fh.get("trend") or "").upper() or None
        try:
            ph_val = float(ph) if ph is not None else None
        except (TypeError, ValueError):
            ph_val = None
        if ph_val is not None:
            if ph_val >= 12:
                score += 2
                reasons.append(f"MATURE_CROWDING_{ph_val:.0f}h")
            elif ph_val >= 6:
                score += 1
                reasons.append(f"STABLE_CROWDING_{ph_val:.0f}h")
        if crowding_trend == "INCREASING":
            score -= 1
            reasons.append("CROWDING_STILL_BUILDING (early fade)")
        elif crowding_trend == "DECREASING":
            score += 1
            reasons.append("CROWDING_UNWINDING (fade confirmed)")

    # ── US session bonus (0-1) ──
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1
        reasons.append("US_SESSION")

    # CONTRARIAN FLIP: emit OPPOSITE direction
    reasons.insert(0, f"CONTRARIAN_FLIP {token} (SM is {sm_direction})")

    # Leverage tier
    leverage = DEFAULT_LEVERAGE
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            leverage = tier["leverage"]
            break
    leverage = min(leverage, ASSET_MAX_LEVERAGE.get(token, 10))

    return {
        "asset": token,
        "direction": fade_direction,
        "sm_direction": sm_direction,
        "score": score,
        "leverage": leverage,
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
        "regime": regime or "UNKNOWN",
        "persistence_hours": ph_val,
        "crowding_trend": crowding_trend,
        "asset_funding": asset_funding,
    }


# ═══════════════════════════════════════════════════════════════
# INGEST — emit signal via SenpiClient.push_signal()
# ═══════════════════════════════════════════════════════════════

def push_signal(candidate, held_assets):
    if not STRATEGY_ADDRESS:
        print(
            "ERROR: strategy wallet not resolved — set 'wallet' in "
            "dog-config.json (preferred) or DOG_WALLET env var",
            file=sys.stderr,
        )
        return False

    # Skip if already holding this asset (defense in depth — runtime
    # per_asset_cooldown would reject anyway, but cleaner at producer)
    if candidate["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": candidate["score"],
        "smDirection": candidate["sm_direction"],
        "leverage": candidate["leverage"],
        "reasons": candidate["reasons"],
        "smPct": candidate["sm_pct"],
        "smTraders": candidate["sm_traders"],
        "priceChange4hPct": candidate["p4h"],
        "priceChange1hPct": candidate["p1h"],
        "contribChange15m": candidate["cc_15m"],
        "contribChange1h": candidate["cc_1h"],
        "contribChange4h": candidate["cc_4h"],
        "fundingRegime": candidate["regime"],
        "persistenceHours": candidate["persistence_hours"],
        "crowdingTrend": candidate["crowding_trend"],
        "assetFunding": candidate["asset_funding"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=candidate["asset"],
            direction=candidate["direction"],
            score=min(candidate["score"] / 14.0, 1.0),  # normalize to 0..1
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {candidate['asset']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {candidate['asset']}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()

    raw = cfg.mcp_call("leaderboard_get_markets", limit=100)
    if not raw:
        print(json.dumps({"status": "no_markets",
                          "_dog_producer_version": VERSION}))
        return

    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        print(json.dumps({"status": "bad_shape",
                          "_dog_producer_version": VERSION}))
        return

    # Pre-pass: build asset map keeping highest-conviction direction per asset
    asset_data = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        if dex or token not in ASSETS:
            continue
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        if token not in asset_data or pct > asset_data[token].get(
                "pct_of_top_traders_gain", 0):
            asset_data[token] = m

    # Enrich once per run — shared context across all candidates
    regime = fetch_funding_regime()
    held_assets = fetch_held_assets()
    fh_map = {tok: fetch_funding_history(tok) for tok in asset_data.keys()}

    # Score each tracked asset
    candidates = []
    for token in ASSETS:
        m = asset_data.get(token)
        if not m:
            continue
        c = score_market(m, regime, fh_map)
        if c is not None:
            candidates.append(c)

    if not candidates:
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": 0,
            "elapsed_sec": round(elapsed, 2),
            "regime": regime,
            "_dog_producer_version": VERSION,
        }))
        return

    # Filter by MIN_SCORE + sort by score descending
    qualified = [c for c in candidates if c["score"] >= MIN_SCORE]
    qualified.sort(key=lambda c: c["score"], reverse=True)

    if not qualified:
        # Best below threshold — informational only
        candidates.sort(key=lambda c: c["score"], reverse=True)
        top = candidates[0]
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": len(candidates),
            "signals_pushed": 0,
            "note": (f"SNIFFING: best {top['asset']} fade={top['direction']} "
                     f"score={top['score']}<{MIN_SCORE}"),
            "regime": regime,
            "elapsed_sec": round(elapsed, 2),
            "_dog_producer_version": VERSION,
        }))
        return

    # Push qualifying candidates. Runtime's LLM gate + risk.guard_rails
    # will decide which (if any) to execute.
    pushed = 0
    for c in qualified:
        if push_signal(c, held_assets):
            pushed += 1

    elapsed = time.time() - run_start
    warn = "WARN_OVER_60S" if elapsed > 60 else None
    print(json.dumps({
        "status": "ok",
        "scanned": len(markets),
        "candidates": len(candidates),
        "qualified": len(qualified),
        "signals_pushed": pushed,
        "regime": regime,
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_dog_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v3.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery via os.kill(pid, 0).
    # Do NOT add an inner scanner_lock(...) inside main() — fcntl flock
    # is not reentrant; nested call raises BlockingIOError every tick.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,                  # 3min — preserved from v2.5 bash loop
        name=f"dog-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
