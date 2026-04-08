#!/usr/bin/env python3
# Senpi FOX Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""FOX v2.0 — Dual-Mode Emerging Movers Scanner (Hardened + Lessons + minReasons).

FOX v2.0 is a variant of the hardened dual-mode scanner with all live trading
lessons applied, plus one experimental scoring tweak:

  STALKER minReasons = 3: Stalker entries must have at least 3 distinct
  scoring reasons (not just pass the score threshold). Fox v1.0 data showed
  the weakest losers had only 2 reasons — STALKER_CLIMB + SM_ACTIVE — reaching
  score 7 via base award + trader count + time bonus without real breadth
  of confirmation. Requiring 3 reasons forces at least one additional signal
  (contribution acceleration, deep start, etc.) beyond the auto-awarded base.

All other fixes from live data:
- Stalker minScore raised from 6 to 7 (score 6 entries were 100% losers)
- Stalker minTotalClimb raised from 5 to 8 (weak +5/+6 climbs were noise)
- Stalker consecutive-loss streak gate: 3 losses → minScore raised to 9
- Tighter Phase 1 for low-score entries (deadWeight 8 min, timeout 25 min)
- XYZ banned, leverage 7-10x, max 3 positions, 2-hour cooldown

Two entry modes:
  STALKER: SM accumulating over 3+ scans. Score 7+, min 3 reasons. Enter before the crowd.
  STRIKER: Violent FIRST_JUMP + volume >= 1.5x. Score 9+, min 4 reasons. Enter the explosion.

Uses: leaderboard_get_markets (single API call per scan)
Runs every 90 seconds.
"""

import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fox_config as cfg

TOP_N = 50
ERRATIC_REVERSAL_THRESHOLD = 5


# ─── Fetch & Parse ───────────────────────────────────────────

def fetch_markets():
    """Fetch current SM market concentration."""
    try:
        data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
        data = data.get("data", data)  # unwrap top-level 'data' wrapper
        raw = data.get("markets", data)
        if isinstance(raw, dict):
            raw = raw.get("markets", [])
        return raw
    except Exception as e:
        return None


def parse_scan(raw_markets):
    """Parse raw markets into a scan snapshot.
    HARDCODED: xyz: assets filtered out at scan level — they never enter the signal pipeline."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scan = {"time": now, "markets": []}
    for i, m in enumerate(raw_markets[:TOP_N]):
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        dex = m.get("dex", "")
        # HARDCODED GATE: Ban XYZ equities (SNDK -$57, GOLD -$8, CRCL -$9, MU -$7 in Fox data)
        if dex and dex.lower() == "xyz":
            continue
        if token.lower().startswith("xyz:"):
            continue
        scan["markets"].append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": m.get("direction", ""),
            "contribution": round(m.get("pct_of_top_traders_gain", 0), 6),
            "traders": m.get("trader_count", 0),
            "price_chg_4h": round(m.get("token_price_change_pct_4h", 0) or 0, 4),
        })
    return scan


def get_market_in_scan(scan, token, dex=""):
    for m in scan["markets"]:
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ─── Volume Confirmation (raw asset volume) ──────────────────

def check_asset_volume(token, dex=""):
    """Check if raw asset volume is alive. Returns (ratio, is_strong).
    This is SEPARATE from SM contribution velocity."""
    asset_name = f"{dex}:{token}" if dex else token
    data = cfg.mcporter_call("market_get_asset_data", asset=asset_name,
                              candle_intervals=["1h"],
                              include_funding=False, include_order_book=False)
    if not data:
        return 0, False

    candle_data = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(candle_data, dict):
        candles = candle_data.get("candles", {}).get("1h", [])
    else:
        return 0, False

    if len(candles) < 6:
        return 0, False

    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-6:]]
    avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
    latest_vol = vols[-1] if vols else 0

    ratio = latest_vol / avg_vol if avg_vol > 0 else 0
    return ratio, ratio >= 1.5


# ─── Erratic History Check ───────────────────────────────────

def is_erratic_history(rank_history, exclude_last=False):
    """Detect zigzag rank patterns."""
    nums = [r for r in rank_history if r is not None]
    if exclude_last and len(nums) > 1:
        nums = nums[:-1]
    if len(nums) < 3:
        return False
    for i in range(1, len(nums) - 1):
        prev_delta = nums[i] - nums[i - 1]
        next_delta = nums[i + 1] - nums[i]
        if prev_delta < 0 and next_delta > ERRATIC_REVERSAL_THRESHOLD:
            return True
        if prev_delta > 0 and next_delta < -ERRATIC_REVERSAL_THRESHOLD:
            return True
    return False


# ─── Time-of-Day Modifier ────────────────────────────────────

def time_of_day_modifier():
    """UTC time-of-day scoring adjustment."""
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return 1, "time_bonus_optimal_window"
    elif hour >= 18 or hour < 2:
        return -2, "time_penalty_chop_zone"
    return 0, None


# ─── 4H Trend Alignment ─────────────────────────────────────

def check_4h_alignment(direction, price_chg_4h):
    """4H trend must agree with signal direction. Hard block."""
    if direction == "LONG" and price_chg_4h < 0:
        return False
    if direction == "SHORT" and price_chg_4h > 0:
        return False
    return True


# ─── MODE A: STALKER (Accumulation Detection) ───────────────

def detect_stalker_signals(current_scan, history, config):
    """Detect steady rank climbers over 3+ consecutive scans.
    v1.2: minScore raised to 7, minTotalClimb raised to 8 based on Fox data.
    Fox had 17 Stalker trades at score 6-7 with 17.6% win rate (-$91.32).
    Weak climbs of +5/+6 were pure noise. Raising thresholds eliminates chop."""

    stalker_cfg = config.get("stalker", {})
    min_consecutive_scans = stalker_cfg.get("minConsecutiveScans", 3)
    min_total_climb = stalker_cfg.get("minTotalClimb", 8)    # v1.2: was 5
    min_score = stalker_cfg.get("minScore", 7)                # v1.2: was 6
    require_volume_building = stalker_cfg.get("requireVolumeBuilding", True)

    prev_scans = history.get("scans", [])
    if len(prev_scans) < min_consecutive_scans:
        return []

    signals = []

    for market in current_scan["markets"]:
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = market["direction"].upper()

        # Skip if already in top 10 (move is over)
        if current_rank <= 10:
            continue

        # 4H trend alignment (hard block)
        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        # Build rank history over recent scans
        rank_history = []
        contrib_history = []
        for scan in prev_scans[-(min_consecutive_scans + 2):]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                rank_history.append(m["rank"])
                contrib_history.append(m["contribution"])
            else:
                rank_history.append(None)
                contrib_history.append(None)
        rank_history.append(current_rank)
        contrib_history.append(market["contribution"])

        # Filter: need at least min_consecutive_scans of data
        valid_ranks = [(i, r) for i, r in enumerate(rank_history) if r is not None]
        if len(valid_ranks) < min_consecutive_scans + 1:
            continue

        # Check for CONSISTENT climbing: each scan's rank <= previous (or equal)
        recent_ranks = [r for _, r in valid_ranks[-(min_consecutive_scans + 1):]]
        is_climbing = all(recent_ranks[i] >= recent_ranks[i + 1] for i in range(len(recent_ranks) - 1))
        total_climb = recent_ranks[0] - recent_ranks[-1]

        if not is_climbing or total_climb < min_total_climb:
            continue

        # Check rank history isn't erratic (exclude current for fairness)
        if is_erratic_history(rank_history, exclude_last=True):
            continue

        # Volume building check: contribution should be increasing
        valid_contribs = [c for c in contrib_history if c is not None]
        volume_building = True
        if require_volume_building and len(valid_contribs) >= 3:
            recent_c = valid_contribs[-3:]
            volume_building = all(recent_c[i] <= recent_c[i + 1] for i in range(len(recent_c) - 1))

        if require_volume_building and not volume_building:
            continue

        # Score
        score = 0
        reasons = []

        # Base: sustained climb
        score += 3
        reasons.append(f"STALKER_CLIMB +{total_climb} over {len(recent_ranks)} scans")

        # Contribution velocity
        if len(valid_contribs) >= 2:
            deltas = [valid_contribs[i + 1] - valid_contribs[i] for i in range(len(valid_contribs) - 1)]
            vel = sum(deltas) / len(deltas)
            if vel > 0.001:
                score += 2
                reasons.append(f"CONTRIB_ACCEL +{vel * 100:.3f}%/scan")
            elif vel > 0:
                score += 1
                reasons.append(f"CONTRIB_POSITIVE +{vel * 100:.4f}%/scan")

        # Trader count
        if market["traders"] >= 10:
            score += 1
            reasons.append(f"SM_ACTIVE {market['traders']} traders")

        # Starting from deep
        if recent_ranks[0] >= 30:
            score += 1
            reasons.append(f"DEEP_START from #{recent_ranks[0]}")

        # Time-of-day
        tod_mod, tod_reason = time_of_day_modifier()
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        if score >= min_score:
            # FOX v2.0 EXPERIMENT: Stalker minReasons gate.
            # Require at least 3 distinct reasons beyond just passing the score threshold.
            # Fox v1.0 data: weakest losers had only 2 reasons (STALKER_CLIMB + SM_ACTIVE).
            # Requiring breadth of confirmation prevents thin signals from entering.
            STALKER_MIN_REASONS = 3
            if len(reasons) < STALKER_MIN_REASONS:
                continue

            signals.append({
                "token": token,
                "dex": dex if dex else None,
                "direction": direction,
                "mode": "STALKER",
                "score": score,
                "reasons": reasons,
                "currentRank": current_rank,
                "totalClimb": total_climb,
                "consecutiveScans": len(recent_ranks),
                "contribution": round(market["contribution"] * 100, 3),
                "traders": market["traders"],
                "priceChg4h": market.get("price_chg_4h", 0),
                "rankHistory": rank_history,
            })

    return signals


# ─── MODE B: STRIKER (Explosion Detection) ───────────────────

def detect_striker_signals(current_scan, history, config):
    """Detect violent FIRST_JUMP signals — current Fox v7.2 Feral Gauntlet.
    Requires raw volume confirmation to filter blow-off tops."""

    striker_cfg = config.get("striker", {})
    min_score = striker_cfg.get("minScore", 9)
    min_reasons = striker_cfg.get("minReasons", 4)
    min_rank_jump = striker_cfg.get("minRankJump", 15)
    min_velocity_override = striker_cfg.get("minVelocityOverride", 15)
    min_velocity_floor = striker_cfg.get("minVelocityFloor", 10)
    require_volume = striker_cfg.get("requireVolumeConfirmation", True)
    min_vol_ratio = striker_cfg.get("minVolRatio", 1.5)

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]

    prev_top50_tokens = set()
    for m in latest_prev["markets"]:
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

    signals = []

    for market in current_scan["markets"]:
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = market["direction"].upper()
        current_contrib = market["contribution"]

        # Destination ceiling: reject if in top 10
        if current_rank <= 10:
            continue

        # 4H trend alignment (hard block)
        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)

        if not prev_market:
            continue

        rank_jump = prev_market["rank"] - current_rank

        # ── FIRST_JUMP detection ──
        is_first_jump = False
        is_immediate = False
        is_contrib_explosion = False
        reasons = []

        # Must be a big single-scan jump from deep
        if rank_jump >= 10 and prev_market["rank"] >= 25:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_market['rank']}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_market["rank"] >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_market['rank']}->#{current_rank}")

        # Contribution explosion
        if prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                is_contrib_explosion = True
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        # Must have FIRST_JUMP or be IMMEDIATE
        if not is_first_jump and not is_immediate:
            continue

        # Explosive threshold: rank jump >= minRankJump OR velocity override
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        abs_velocity = abs(contrib_velocity)

        if rank_jump < min_rank_jump and abs_velocity < min_velocity_override:
            continue

        # Velocity floor
        if abs_velocity < min_velocity_floor:
            # For FIRST_JUMP, velocity > 0 is enough
            if is_first_jump and contrib_velocity > 0:
                pass
            else:
                continue

        # ── SCORING ──
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs_velocity > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs_velocity:.1f}")

        # Deep climber bonus
        if prev_market["rank"] >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        # Multi-scan climb bonus
        if old_market:
            total_climb = old_market["rank"] - current_rank
            if total_climb >= 10:
                score += 1
                reasons.append(f"CLIMBING +{total_climb} over scans")

        # Time-of-day
        tod_mod, tod_reason = time_of_day_modifier()
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        if score < min_score or len(reasons) < min_reasons:
            continue

        # ── Volume confirmation (the PUMP filter) ──
        vol_ratio, vol_strong = 0, True
        if require_volume:
            vol_ratio, vol_strong = check_asset_volume(token, dex)
            if not vol_strong:
                continue
            reasons.append(f"VOL_CONFIRMED {vol_ratio:.1f}x")

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
            "isContribExplosion": is_contrib_explosion,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": market["traders"],
            "priceChg4h": market.get("price_chg_4h", 0),
        })

    return signals


# ─── Main ─────────────────────────────────────────────────────

# HARDCODED CONSTANTS — learned from 5+ days of live trading across 22 agents.
# These are NOT configurable by the agent. They are in the code.
MIN_LEVERAGE = 7          # Assets below 7x exchange max are skipped (3x entries can't overcome fees)
MAX_LEVERAGE = 10         # Never exceed 10x
MAX_POSITIONS = 3         # Concentration > diversification
MAX_DAILY_LOSS_PCT = 10   # Fox's setting — Vixen drifted to 25% and bled 2.5x more per bad day
XYZ_BANNED = True         # SNDK was Fox's biggest single loss; all xyz equities net negative across all agents

def run():
    config = cfg.load_config()
    entry_cfg = config.get("entry", {})
    cooldown_min = entry_cfg.get("assetCooldownMinutes", 120)

    # Fetch markets
    raw_markets = fetch_markets()
    if raw_markets is None:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    # Parse current scan (XYZ filtered at scan level)
    current_scan = parse_scan(raw_markets)

    # Load history
    history = cfg.load_scan_history()

    # v1.2: Check Stalker losing streak — if last 3 Stalker results were all
    # losses, temporarily raise minScore to 9. Resets when a Stalker wins.
    # This prevents the "death by a thousand cuts" bleed Fox experienced.
    tc = cfg.load_trade_counter()
    stalker_results = tc.get("stalkerResults", [])
    stalker_streak_active = False
    if len(stalker_results) >= 3 and all(r == "L" for r in stalker_results[-3:]):
        stalker_streak_active = True

    # Detect both modes
    stalker_signals = detect_stalker_signals(current_scan, history, entry_cfg)
    striker_signals = detect_striker_signals(current_scan, history, entry_cfg)

    # v1.2: Apply streak filter to Stalker signals
    if stalker_streak_active:
        stalker_signals = [s for s in stalker_signals if s["score"] >= 9]

    # Save history
    history["scans"].append(current_scan)
    cfg.save_scan_history(history)

    # Apply per-asset cooldowns (HARDCODED 2-hour — the PAXG double-entry lesson)
    stalker_signals = [s for s in stalker_signals if not cfg.is_asset_cooled_down(s["token"], cooldown_min)]
    striker_signals = [s for s in striker_signals if not cfg.is_asset_cooled_down(s["token"], cooldown_min)]

    # Sort by score (highest first)
    stalker_signals.sort(key=lambda s: s["score"], reverse=True)
    striker_signals.sort(key=lambda s: s["score"], reverse=True)

    # Combine — Striker takes priority over Stalker for same token
    striker_tokens = {s["token"] for s in striker_signals}
    combined = striker_signals + [s for s in stalker_signals if s["token"] not in striker_tokens]
    combined.sort(key=lambda s: s["score"], reverse=True)

    # Output — include hardcoded constraints so the agent can't override them
    cfg.output({
        "status": "ok",
        "time": current_scan["time"],
        "totalMarkets": len(current_scan["markets"]),
        "scansInHistory": len(history["scans"]),
        "stalkerSignals": stalker_signals,
        "strikerSignals": striker_signals,
        "combined": combined,
        "hasStalker": len(stalker_signals) > 0,
        "hasStriker": len(striker_signals) > 0,
        "hasSignal": len(combined) > 0,
        "stalkerStreakActive": stalker_streak_active,
        # HARDCODED EXECUTION CONSTRAINTS — agent MUST respect these
        "constraints": {
            "minLeverage": MIN_LEVERAGE,
            "maxLeverage": MAX_LEVERAGE,
            "maxPositions": MAX_POSITIONS,
            "maxDailyLossPct": MAX_DAILY_LOSS_PCT,
            "xyzBanned": XYZ_BANNED,
            "assetCooldownMinutes": cooldown_min,
            "_note": "These constraints are HARDCODED in the scanner. Do not override.",
        },
        "_fox_version": "2.0",
    })


if __name__ == "__main__":
    run()
