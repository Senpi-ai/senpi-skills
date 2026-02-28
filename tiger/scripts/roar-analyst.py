#!/usr/bin/env python3
"""
roar-analyst.py — ROAR Meta-Optimizer for TIGER

Reads TIGER's trade log, state, and config. Builds a performance scorecard
per pattern, applies rule-based adjustments within bounded ranges, and
outputs a proposed changeset as JSON.

Run standalone: python3 scripts/roar-analyst.py
Cron: every 8h + ad-hoc on every 5th trade (trade_count_trigger).
"""

import json
import sys
import os
import copy
from datetime import datetime, timezone, timedelta

# ─── Path setup (same pattern as all Tiger scripts) ──────────
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from tiger_config import load_config, save_config, load_state, load_trade_log
from roar_config import (
    load_roar_state, save_roar_state,
    apply_changeset, should_revert, revert_config,
    disable_pattern, check_re_enable, is_pattern_disabled,
    clamp_to_bounds, is_protected,
    get_nested, set_nested,
    TUNABLE_BOUNDS, DISABLE_DURATION_H,
)

# ─── Constants ───────────────────────────────────────────────

MIN_TRADES_FOR_ADJUST = 5     # minimum trades per pattern before tuning
MIN_TRADES_WIN_RATE = 10      # minimum for win-rate based rules
MIN_TRADES_EXPECTANCY = 20    # minimum for negative-expectancy disable
SIGNAL_STALE_HOURS = 48       # hours of no entries to trigger threshold drop
TRADE_COUNT_TRIGGER = 5       # new trades since last run to flag trigger


# ─── Load Data ───────────────────────────────────────────────

# Trade log is loaded via tiger_config.load_trade_log()


# ─── Scorecard Builder ───────────────────────────────────────

def build_scorecard(trades: list, roar_state: dict) -> dict:
    """
    Build per-pattern performance scorecard from trade log.

    Returns: {
        "by_pattern": {
            "bb_squeeze_breakout": {
                "trades": 15, "wins": 10, "losses": 5,
                "win_rate": 0.667, "avg_pnl_pct": 2.3,
                "avg_hold_minutes": 180, "avg_dsl_exit_tier": 2.1,
                "avg_confluence_at_entry": 0.65, "expectancy": 1.5,
                "last_entry_ts": "...",
            }, ...
        },
        "overall_win_rate": 0.6,
        "overall_avg_pnl": 1.2,
        "total_trades": 45,
        "signals_filtered": 0,
    }
    """
    by_pattern = {}

    for t in trades:
        pattern = t.get("pattern", t.get("setup_type", "unknown"))
        if pattern not in by_pattern:
            by_pattern[pattern] = {
                "trades": 0, "wins": 0, "losses": 0,
                "total_pnl_pct": 0.0, "total_hold_min": 0.0,
                "dsl_exit_tiers": [], "confluence_scores": [],
                "last_entry_ts": None,
            }
        p = by_pattern[pattern]
        p["trades"] += 1

        pnl = t.get("pnl_pct", t.get("realized_pnl_pct", 0.0)) or 0.0
        p["total_pnl_pct"] += pnl
        if pnl > 0:
            p["wins"] += 1
        else:
            p["losses"] += 1

        # Hold duration
        entry_ts = t.get("entry_time") or t.get("opened_at")
        exit_ts = t.get("exit_time") or t.get("closed_at")
        if entry_ts and exit_ts:
            try:
                e = datetime.fromisoformat(str(entry_ts))
                x = datetime.fromisoformat(str(exit_ts))
                p["total_hold_min"] += (x - e).total_seconds() / 60
            except (ValueError, TypeError):
                pass

        # DSL tier at exit
        dsl_tier = t.get("dsl_exit_tier", t.get("exit_tier"))
        if dsl_tier is not None:
            p["dsl_exit_tiers"].append(int(dsl_tier))

        # Confluence score at entry
        conf = t.get("confluence_score", t.get("entry_confluence"))
        if conf is not None:
            p["confluence_scores"].append(float(conf))

        # Track last entry
        ts = entry_ts or t.get("timestamp")
        if ts:
            p["last_entry_ts"] = str(ts)

    # Compute aggregates
    scorecard = {"by_pattern": {}, "total_trades": len(trades), "signals_filtered": 0}
    total_wins = 0
    total_trades = 0
    total_pnl = 0.0

    for pattern, p in by_pattern.items():
        n = p["trades"]
        total_wins += p["wins"]
        total_trades += n
        total_pnl += p["total_pnl_pct"]

        entry = {
            "trades": n,
            "wins": p["wins"],
            "losses": p["losses"],
            "win_rate": round(p["wins"] / n, 3) if n else 0,
            "avg_pnl_pct": round(p["total_pnl_pct"] / n, 3) if n else 0,
            "avg_hold_minutes": round(p["total_hold_min"] / n, 1) if n else 0,
            "avg_dsl_exit_tier": round(sum(p["dsl_exit_tiers"]) / len(p["dsl_exit_tiers"]), 2) if p["dsl_exit_tiers"] else None,
            "avg_confluence_at_entry": round(sum(p["confluence_scores"]) / len(p["confluence_scores"]), 3) if p["confluence_scores"] else None,
            "expectancy": round(p["total_pnl_pct"] / n, 3) if n else 0,
            "last_entry_ts": p["last_entry_ts"],
        }
        scorecard["by_pattern"][pattern] = entry

    scorecard["overall_win_rate"] = round(total_wins / total_trades, 3) if total_trades else 0
    scorecard["overall_avg_pnl"] = round(total_pnl / total_trades, 3) if total_trades else 0

    return scorecard


# ─── Rule-Based Adjustment Engine ────────────────────────────

def generate_proposed_changes(scorecard: dict, config: dict, roar_state: dict) -> list:
    """
    Apply rules and generate a list of proposed changes.
    Each: { "key": dotkey, "old": val, "new": val, "reason": str, "confidence": float }
    """
    changes = []
    now = datetime.now(timezone.utc)

    for pattern, stats in scorecard.get("by_pattern", {}).items():
        n = stats["trades"]

        # ── Rule 1: Low win rate → raise confluence threshold ──
        if n >= MIN_TRADES_WIN_RATE and stats["win_rate"] < 0.40:
            key = f"pattern_confluence_overrides.{pattern}"
            old = get_nested(config, key)
            if old is None:
                # Fall back to NORMAL aggression level
                old = get_nested(config, "min_confluence_score.NORMAL") or 0.5
            new_val = clamp_to_bounds(key, round(old + 0.05, 3))
            confidence = min(1.0, n / 30)  # more trades = more confident
            changes.append({
                "key": key, "old": old, "new": new_val,
                "reason": f"{pattern} win rate {stats['win_rate']:.0%} < 40% over {n} trades — raising threshold",
                "confidence": round(confidence, 2),
            })

        # ── Rule 2: High win rate → lower confluence threshold ──
        elif n >= MIN_TRADES_WIN_RATE and stats["win_rate"] > 0.70:
            key = f"pattern_confluence_overrides.{pattern}"
            old = get_nested(config, key)
            if old is None:
                old = get_nested(config, "min_confluence_score.NORMAL") or 0.5
            new_val = clamp_to_bounds(key, round(old - 0.03, 3))
            confidence = min(1.0, n / 30)
            changes.append({
                "key": key, "old": old, "new": new_val,
                "reason": f"{pattern} win rate {stats['win_rate']:.0%} > 70% over {n} trades — lowering threshold to catch more",
                "confidence": round(confidence, 2),
            })

        # ── Rule 3: DSL exit mostly Tier 1 → loosen phase1 retrace ──
        avg_tier = stats.get("avg_dsl_exit_tier")
        if avg_tier is not None and n >= MIN_TRADES_FOR_ADJUST:
            if avg_tier < 2.0:
                key = "dsl_retrace.phase1"
                old = get_nested(config, key) or 0.015
                new_val = clamp_to_bounds(key, round(old + 0.002, 4))
                changes.append({
                    "key": key, "old": old, "new": new_val,
                    "reason": f"{pattern} avg DSL exit tier {avg_tier:.1f} (< 2) — loosening phase1 retrace to let positions run",
                    "confidence": round(min(1.0, n / 20), 2),
                })
            # ── Rule 4: DSL exit Tier 4+ → tighten phase1 retrace ──
            elif avg_tier >= 4.0:
                key = "dsl_retrace.phase1"
                old = get_nested(config, key) or 0.015
                new_val = clamp_to_bounds(key, round(old - 0.001, 4))
                changes.append({
                    "key": key, "old": old, "new": new_val,
                    "reason": f"{pattern} avg DSL exit tier {avg_tier:.1f} (≥ 4) — tightening phase1 retrace to lock gains",
                    "confidence": round(min(1.0, n / 20), 2),
                })

        # ── Rule 5: No entries in 48h but pattern exists → lower threshold ──
        last_ts = stats.get("last_entry_ts")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(str(last_ts))
                hours_since = (now - last_dt).total_seconds() / 3600
                if hours_since >= SIGNAL_STALE_HOURS and n >= MIN_TRADES_FOR_ADJUST:
                    key = f"pattern_confluence_overrides.{pattern}"
                    old = get_nested(config, key)
                    if old is None:
                        old = get_nested(config, "min_confluence_score.NORMAL") or 0.5
                    new_val = clamp_to_bounds(key, round(old - 0.02, 3))
                    changes.append({
                        "key": key, "old": old, "new": new_val,
                        "reason": f"{pattern} no entries in {hours_since:.0f}h — lowering threshold by 0.02",
                        "confidence": 0.4,
                    })
            except (ValueError, TypeError):
                pass

        # ── Rule 6: Negative expectancy → disable pattern ──
        if n >= MIN_TRADES_EXPECTANCY and stats["expectancy"] < 0:
            if not is_pattern_disabled(roar_state, pattern):
                disable_pattern(roar_state, pattern)
                # Note: we don't add a config change here; the disabled_patterns
                # list is checked by the scanner scripts.
                changes.append({
                    "key": f"_disable_pattern.{pattern}",
                    "old": "enabled", "new": "disabled",
                    "reason": f"{pattern} negative expectancy ({stats['expectancy']:.2f}%) over {n} trades — disabled for {DISABLE_DURATION_H}h",
                    "confidence": round(min(1.0, n / 40), 2),
                })

    return changes


# ─── Main ────────────────────────────────────────────────────

def main():
    config = load_config()
    tiger_state = load_state()
    roar_state = load_roar_state()
    trades = load_trade_log()

    now_iso = datetime.now(timezone.utc).isoformat()
    roar_state["run_count"] = roar_state.get("run_count", 0) + 1

    # ── Check for pattern re-enables ──
    patterns_enabled = check_re_enable(roar_state)

    # ── Trade-count trigger ──
    prev_processed = roar_state.get("trades_processed", 0)
    new_trade_count = len(trades) - prev_processed
    trade_count_trigger = new_trade_count >= TRADE_COUNT_TRIGGER

    # ── Build scorecard ──
    scorecard = build_scorecard(trades, roar_state)

    # ── Check if we should revert previous changes ──
    reverted = False
    previous_stats = roar_state.get("previous_stats")
    current_stats = {
        "overall_win_rate": scorecard["overall_win_rate"],
        "overall_avg_pnl": scorecard["overall_avg_pnl"],
    }

    if previous_stats and should_revert(current_stats, previous_stats):
        reverted_config = revert_config(roar_state)
        if reverted_config:
            save_config(reverted_config)
            config = reverted_config
            reverted = True

    # ── Generate proposed changes ──
    proposed = generate_proposed_changes(scorecard, config, roar_state)

    # ── Apply changes if we have enough data and didn't just revert ──
    changes_applied = False
    patterns_disabled = []

    if proposed and not reverted and scorecard["total_trades"] >= MIN_TRADES_FOR_ADJUST:
        # Separate disable actions from config changes
        config_changes = [c for c in proposed if not c["key"].startswith("_disable_pattern")]
        disable_actions = [c for c in proposed if c["key"].startswith("_disable_pattern")]

        patterns_disabled = [c["key"].split(".", 1)[1] for c in disable_actions]

        if config_changes:
            # Save stats snapshot for next-cycle revert check
            roar_state["previous_stats"] = copy.deepcopy(current_stats)

            # Apply
            config = apply_changeset(config_changes, config, roar_state)
            save_config(config)
            changes_applied = True

            # Record in adjustment history
            roar_state["adjustment_history"].append({
                "ts": now_iso,
                "changes": config_changes,
                "scorecard_snapshot": current_stats,
            })
            # Keep history bounded (last 50 entries)
            roar_state["adjustment_history"] = roar_state["adjustment_history"][-50:]

    # ── Update state ──
    roar_state["last_analysis_ts"] = now_iso
    roar_state["trades_processed"] = len(trades)
    save_roar_state(roar_state)

    # ── Build summary ──
    n_changes = len([c for c in proposed if not c["key"].startswith("_")])
    summary_parts = [f"ROAR run #{roar_state['run_count']}:"]
    summary_parts.append(f"{scorecard['total_trades']} trades analyzed, {n_changes} adjustments proposed")
    if changes_applied:
        summary_parts.append(f"✅ {n_changes} changes applied")
    if reverted:
        summary_parts.append("⏪ Reverted previous changes (performance degraded)")
    if patterns_disabled:
        summary_parts.append(f"🚫 Disabled: {', '.join(patterns_disabled)}")
    if patterns_enabled:
        summary_parts.append(f"✅ Re-enabled: {', '.join(patterns_enabled)}")
    if trade_count_trigger:
        summary_parts.append(f"📊 {new_trade_count} new trades since last run")

    # ── Output ──
    output = {
        "action": "roar_analysis",
        "scorecard": scorecard,
        "proposed_changes": proposed,
        "changes_applied": changes_applied,
        "reverted_previous": reverted,
        "patterns_disabled": patterns_disabled,
        "patterns_enabled": patterns_enabled,
        "trade_count_trigger": trade_count_trigger,
        "next_review_in": "8h",
        "run_count": roar_state["run_count"],
        "summary": " | ".join(summary_parts),
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
