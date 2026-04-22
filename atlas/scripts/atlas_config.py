#!/usr/bin/env python3
# Senpi ATLAS Config v0.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""ATLAS v0.1 — Configuration constants.

Override via config/atlas-config.json or environment variables.
"""

import json
import os

# ── State directory (where atlas-log.jsonl lives) ────────────────
STATE_DIR = os.environ.get(
    "ATLAS_STATE_DIR",
    "/data/workspace/skills/atlas-strategy/state",
)

# ── Anchor universe ──────────────────────────────────────────────
ANCHOR_UNIVERSE_SIZE = 15        # top-N volume perps eligible as anchor
ANCHOR_TOP_N_OUTPUT = 5          # how many candidates to surface to LLM
ANCHOR_STRIKE_THRESHOLD = 7.0    # first-strike score floor (steady-state: 6.5)
ANCHOR_STEADY_THRESHOLD = 6.5
ANCHOR_PERSISTENCE_DAYS = 2      # consecutive qualifying scans required for first strike
ANCHOR_MAX_LEVERAGE = 3
ANCHOR_MIN_HOLD_DAYS = 7

# ── Basket universe ──────────────────────────────────────────────
BASKET_UNIVERSE_SIZE = 50
BASKET_TOP_N_OUTPUT = 10
BASKET_TARGET_MEMBERS = 4        # min 3, max 5
BASKET_MIN_MEMBERS = 3
BASKET_MAX_MEMBERS = 5
BASKET_TARGET_HEDGE_RATIO = 0.40 # of anchor notional
BASKET_HEDGE_RATIO_TOLERANCE = (0.25, 0.55)
BASKET_MAX_LEVERAGE = 2
BASKET_MIN_HOLD_DAYS_PER_MEMBER = 3   # prevents oscillation
BASKET_CORRELATION_EXCLUSION = 0.85   # exclude candidates with corr > this to anchor

# ── Arena signal floor ───────────────────────────────────────────
ARENA_MIN_ACTIVE_TRADERS = 150        # below: fall back to SM-only with reduced conviction
ARENA_RECENCY_FILTER_DAYS = 5
ARENA_TOP_N = 10                       # use top-10 ROE leaders

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

# Fleet concentration — leverage modifier only, never a hard veto
FLEET_CONCENTRATION_RULES = [
    {"if_same_direction_predator_count_gte": 5, "cap_leverage_at": 2},
    {"if_fleet_notional_pct_in_asset_gt": 30, "cap_leverage_at": 2},
]

# ── Cycle ────────────────────────────────────────────────────────
DAILY_DECISION_TIME_UTC = "13:00"
SKIP_THURSDAY_MORNING = True               # Arena resets Thursday 00:00 UTC
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

# ── Per-leg safety net (only active until dsl_portfolio engine ships) ──
PER_LEG_HARD_STOP_PCT = 25  # safety net only; normal exits via daily decision loop


# ═══════════════════════════════════════════════════════════════
# Config file overlay
# ═══════════════════════════════════════════════════════════════

def _load_overlay():
    """Load config/atlas-config.json if present and overlay onto module globals."""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "atlas-config.json",
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
