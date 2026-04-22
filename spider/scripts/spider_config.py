#!/usr/bin/env python3
# Senpi SPIDER Config v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SPIDER v2.0 — Configuration constants.

v2.0 is a full rewrite from v1.0 (Elite convergence scanner, single-position).
v2.0 is an agentic portfolio operator — the spider builds a web (basket)
around an anchor point, then waits. Patience is alpha.

Override via config/spider-config.json or environment variables.
"""

import json
import os

# ── State directory (where spider-log.jsonl lives) ───────────────
STATE_DIR = os.environ.get(
    "SPIDER_STATE_DIR",
    "/data/workspace/skills/spider-strategy/state",
)

# ── Anchor universe ──────────────────────────────────────────────
ANCHOR_UNIVERSE_SIZE = 15
ANCHOR_TOP_N_OUTPUT = 5
ANCHOR_STRIKE_THRESHOLD = 7.0
ANCHOR_STEADY_THRESHOLD = 6.5
ANCHOR_PERSISTENCE_DAYS = 2
ANCHOR_MAX_LEVERAGE = 3
ANCHOR_MIN_HOLD_DAYS = 7

# ── Basket universe ──────────────────────────────────────────────
BASKET_UNIVERSE_SIZE = 50
BASKET_TOP_N_OUTPUT = 10
BASKET_TARGET_MEMBERS = 4
BASKET_MIN_MEMBERS = 3
BASKET_MAX_MEMBERS = 5
BASKET_TARGET_HEDGE_RATIO = 0.40
BASKET_HEDGE_RATIO_TOLERANCE = (0.25, 0.55)
BASKET_MAX_LEVERAGE = 2
BASKET_MIN_HOLD_DAYS_PER_MEMBER = 3
BASKET_CORRELATION_EXCLUSION = 0.85

# v2.0 — basket member strictness gate (fleet-learning addition)
# Fleet data shows bleeders admit weak signals. Basket members must clear
# an individual score floor; below the floor Spider runs anchor-only.
BASKET_MEMBER_MIN_SCORE = 6.5
BASKET_MIN_MEMBERS_TO_OPEN_BASKET = 3  # if fewer qualify, anchor-only mode

# ── Arena signal floor ───────────────────────────────────────────
ARENA_MIN_ACTIVE_TRADERS = 150
ARENA_RECENCY_FILTER_DAYS = 5
ARENA_TOP_N = 10

# ── Cold start ───────────────────────────────────────────────────
WARMUP_DAYS = 7
PILOT_PROTOCOL = {
    1: {"anchor_pct": 0.50, "basket_pct": 0.00},
    2: {"anchor_pct": 0.75, "basket_pct": 0.25},
    3: {"anchor_pct": 1.00, "basket_pct": 0.40},
}
PILOT_ABORT_DRAWDOWN_PCT = 5.0
PILOT_ABORT_SCORE_FLOOR = 6.5
WARMUP_RESET_DAYS_AFTER_ABORT = 3

# ── Regime vetoes (block first-strike) ───────────────────────────
REGIME_VETO_BTC_DRAWDOWN_48H_PCT = 10
REGIME_VETO_VOL_REGIMES = ("expansion_spike",)
REGIME_VETO_FUNDING_BREADTH = ("flipping",)

# ── Risk envelope ────────────────────────────────────────────────
PORTFOLIO_MAX_GROSS_EXPOSURE_PCT = 200
PORTFOLIO_MAX_DRAWDOWN_WEEKLY_PCT = 12
PORTFOLIO_DRAWDOWN_ACTION = "close_basket_first"
PORTFOLIO_CASH_FLOOR_PCT = 10

# Per-leg safety net (v1 DSL equivalent — the stop that must rarely fire)
PER_LEG_HARD_STOP_PCT = 25

# Fleet concentration — leverage modifier only, never a hard veto
FLEET_CONCENTRATION_RULES = [
    {"if_same_direction_predator_count_gte": 5, "cap_leverage_at": 2},
    {"if_fleet_notional_pct_in_asset_gt": 30, "cap_leverage_at": 2},
]

# ── v2.0 fleet-learning constraints ─────────────────────────────
# Fleet data (Apr 2026): bleeders average 400+ fills with -23% ROI.
# Winners: Kodiak 316 fills / +5.6% ROI, Vulture 72 fills / +3.1% ROI.
# Lesson: low turnover + strict gates + asymmetric payoffs.

# Trade frequency ceiling — NOT runtime-enforced; the agent must obey it
# in its LLM action layer. Exceeding it requires explicit rationale.
MAX_FILLS_PER_7D = 12
FILLS_PER_7D_SOFT_WARNING = 8

# Fee pathology early warning
FEE_PCT_OF_GROSS_CEILING = 25.0         # rolling 4w
FEE_WARNING_WINDOW_WEEKS = 4

# Safety stop pathology early warning
SAFETY_STOP_30D_CEILING = 1             # more than 1 per-leg safety stop in 30d

# ── Cycle ────────────────────────────────────────────────────────
DAILY_DECISION_TIME_UTC = "13:00"
SKIP_THURSDAY_MORNING = True
THURSDAY_SKIP_WINDOW_UTC = ("00:00", "12:00")

EMERGENCY_CHECK_INTERVAL_HOURS = 1
WEEKLY_REVIEW_DAY = "sunday"
WEEKLY_REVIEW_TIME_UTC = "15:00"

# ── Signal source weights ────────────────────────────────────────
ANCHOR_WEIGHTS = {
    "arena_leaders": 0.40,
    "sm_consensus_7d_delta": 0.30,
    "funding_favorability": 0.15,
    "relative_strength_30d": 0.15,
}

BASKET_WEIGHTS = {
    "funding_descending": 0.40,
    "sm_rotation_negative": 0.25,
    "relative_weakness_vs_anchor_7d": 0.20,
    "squeeze_risk_inverse": 0.15,
}


# ═══════════════════════════════════════════════════════════════
# Config file overlay
# ═══════════════════════════════════════════════════════════════

def _load_overlay():
    """Load config/spider-config.json if present and overlay onto module globals."""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "spider-config.json",
    )
    if not os.path.exists(cfg_path):
        return
    try:
        with open(cfg_path) as f:
            overlay = json.load(f)
        for k, v in overlay.items():
            if k in globals():
                globals()[k] = v
    except (IOError, OSError, json.JSONDecodeError):
        pass


_load_overlay()
