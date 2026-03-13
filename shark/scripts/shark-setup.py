#!/usr/bin/env python3
"""shark-setup.py — SHARK strategy setup wizard.

Creates strategy registry entry, initializes state directories,
validates wallet/strategy, fetches max leverage data, and outputs
cron templates ready to install.

Usage:
  python3 shark-setup.py --wallet 0x... --strategy-id UUID --budget 5000 --chat-id 12345
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-setup"


def parse_args():
    parser = argparse.ArgumentParser(description="SHARK strategy setup wizard")
    parser.add_argument("--wallet", required=True, help="Strategy wallet address")
    parser.add_argument("--strategy-id", required=True, help="Senpi strategy ID")
    parser.add_argument("--budget", type=float, required=True, help="Budget in USDC")
    parser.add_argument("--chat-id", type=str, default="", help="Telegram chat ID for notifications")
    parser.add_argument("--leverage", type=int, default=8, help="Default leverage (7-10)")
    parser.add_argument("--margin-pct", type=float, default=0.18, help="Margin as % of budget per trade")
    parser.add_argument("--risk", choices=["conservative", "moderate", "aggressive"], default="aggressive",
                        help="Risk profile")
    return parser.parse_args()


def validate_strategy(strategy_id: str, wallet: str) -> tuple[bool, str | None]:
    """Validate strategy exists and wallet matches."""
    strategy, err = cfg.fetch_strategy(strategy_id)
    if err:
        return False, f"Could not fetch strategy: {err}"
    if not strategy:
        return False, "Strategy not found"

    status = (strategy.get("status") or "").upper()
    if status not in ("ACTIVE", "PAUSED"):
        return False, f"Strategy status is {status} — must be ACTIVE or PAUSED"

    strat_wallet = (strategy.get("strategyWalletAddress") or "").lower()
    if strat_wallet != wallet.lower():
        return False, f"Wallet mismatch: strategy has {strat_wallet}, you provided {wallet}"

    return True, None


def compute_parameters(budget: float, margin_pct: float, leverage: int, risk: str) -> dict:
    """Compute all derived parameters from budget."""
    margin_per_trade = round(budget * margin_pct, 2)
    daily_loss_limit = round(budget * 0.12, 2)
    drawdown_cap = round(budget * 0.25, 2)
    auto_delever = round(budget * 0.80, 2)  # Delever at 80% of budget remaining

    return {
        "marginPerTrade": margin_per_trade,
        "notionalPerTrade": round(margin_per_trade * leverage, 2),
        "dailyLossLimit": daily_loss_limit,
        "drawdownCap": drawdown_cap,
        "autoDeleverThreshold": auto_delever,
        "maxSingleLossPct": 5,
    }


def create_registry_entry(wallet: str, strategy_id: str, budget: float,
                          leverage: int, margin_pct: float, risk: str,
                          params: dict) -> dict:
    """Create strategy registry entry."""
    return {
        "name": "Liquidation Cascade",
        "wallet": wallet,
        "strategyId": strategy_id,
        "budget": budget,
        "maxSlots": 2,
        "marginPct": margin_pct,
        "defaultLeverage": leverage,
        "tradingRisk": risk,
        "dailyLossLimit": params["dailyLossLimit"],
        "drawdownCap": params["drawdownCap"],
        "autoDeleverThreshold": params["autoDeleverThreshold"],
        "maxSingleLossPct": params["maxSingleLossPct"],
        "maxEntriesPerDay": 6,
        "dsl": {
            "preset": "aggressive",
            "tiers": [
                {"triggerPct": 5, "lockPct": 2},
                {"triggerPct": 10, "lockPct": 5},
                {"triggerPct": 20, "lockPct": 14},
                {"triggerPct": 30, "lockPct": 24},
                {"triggerPct": 40, "lockPct": 34},
                {"triggerPct": 50, "lockPct": 44},
                {"triggerPct": 65, "lockPct": 56},
                {"triggerPct": 80, "lockPct": 72},
                {"triggerPct": 100, "lockPct": 90},
            ]
        },
        "enabled": True,
        "createdAt": cfg.now_iso(),
    }


def generate_cron_templates(strategy_id: str, chat_id: str) -> list[dict]:
    """Generate cron job templates for all 7 SHARK crons."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    state_base = cfg.DEFAULT_STATE_BASE
    dsl_base = cfg.DSL_BASE_DIR

    templates = [
        {
            "name": f"shark-oi-tracker-{strategy_id[:8]}",
            "description": "OI + price + funding snapshot collection",
            "schedule": {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 {scripts_dir}/shark-oi-tracker.py"
            },
        },
        {
            "name": f"shark-liq-mapper-{strategy_id[:8]}",
            "description": "Liquidation zone estimation + scoring",
            "schedule": {"kind": "cron", "expr": "1-59/5 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 {scripts_dir}/shark-liq-mapper.py"
            },
        },
        {
            "name": f"shark-proximity-{strategy_id[:8]}",
            "description": "Proximity scanner for stalking assets",
            "schedule": {"kind": "cron", "expr": "*/2 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 {scripts_dir}/shark-proximity.py"
            },
        },
        {
            "name": f"shark-entry-{strategy_id[:8]}",
            "description": "Cascade entry (main session for notifications)",
            "schedule": {"kind": "cron", "expr": "*/2 * * * *", "tz": "UTC"},
            "sessionTarget": "main",
            "wakeMode": "now",
            "payload": {
                "kind": "systemEvent",
                "text": f"python3 {scripts_dir}/shark-entry.py"
            },
        },
        {
            "name": f"shark-risk-{strategy_id[:8]}",
            "description": "Risk guardian — daily loss, drawdown, cascade invalidation",
            "schedule": {"kind": "cron", "expr": "2-59/5 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 {scripts_dir}/shark-risk.py"
            },
        },
        {
            "name": f"shark-dsl-{strategy_id[:8]}",
            "description": "DSL v5.3.1 trailing stops",
            "schedule": {"kind": "cron", "expr": "*/3 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": (
                    f"DSL_STATE_DIR={dsl_base} DSL_STRATEGY_ID={strategy_id} "
                    f"python3 {os.path.expanduser('~/.agents/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py')}"
                )
            },
        },
        {
            "name": f"shark-health-{strategy_id[:8]}",
            "description": "Health check — orphan DSL, state validation",
            "schedule": {"kind": "cron", "expr": "*/10 * * * *", "tz": "UTC"},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 {scripts_dir}/shark-health.py"
            },
        },
    ]

    return templates


def run():
    args = parse_args()

    print(f"🦈 SHARK Setup — Liquidation Cascade Front-Runner")
    print(f"{'=' * 50}")
    print()

    # Validate inputs
    if args.budget < 100:
        print("❌ Budget must be at least $100")
        sys.exit(1)

    if args.leverage < 7 or args.leverage > 10:
        print(f"⚠️  Leverage {args.leverage}x outside recommended 7-10x range")

    # Step 1: Validate strategy
    print("1. Validating strategy...")
    valid, err = validate_strategy(args.strategy_id, args.wallet)
    if not valid:
        print(f"   ❌ {err}")
        sys.exit(1)
    print("   ✅ Strategy valid and active")

    # Step 2: Compute parameters
    print("2. Computing parameters...")
    params = compute_parameters(args.budget, args.margin_pct, args.leverage, args.risk)
    print(f"   Budget:            ${args.budget:,.2f}")
    print(f"   Margin/trade:      ${params['marginPerTrade']:,.2f} ({args.margin_pct:.0%})")
    print(f"   Notional/trade:    ${params['notionalPerTrade']:,.2f}")
    print(f"   Default leverage:  {args.leverage}x")
    print(f"   Daily loss limit:  ${params['dailyLossLimit']:,.2f}")
    print(f"   Drawdown cap:      ${params['drawdownCap']:,.2f}")
    print(f"   Max slots:         2")
    print(f"   Risk profile:      {args.risk}")

    # Step 3: Fetch max leverage data
    print("3. Fetching max leverage data...")
    instruments, err = cfg.fetch_instruments()
    if err:
        print(f"   ⚠️  Could not fetch instruments: {err}")
    else:
        max_levs = {}
        for inst in instruments:
            name = inst.get("name")
            max_lev = inst.get("max_leverage", 0)
            if name and max_lev:
                max_levs[name] = max_lev
        restricted = [(k, v) for k, v in max_levs.items() if v < args.leverage]
        if restricted:
            print(f"   ⚠️  {len(restricted)} assets have max leverage below {args.leverage}x")
        else:
            print(f"   ✅ All assets support {args.leverage}x leverage")

    # Step 4: Create registry entry
    print("4. Creating strategy registry...")
    entry = create_registry_entry(
        args.wallet, args.strategy_id, args.budget,
        args.leverage, args.margin_pct, args.risk, params
    )

    # Load existing strategies
    existing = cfg.load_all_strategies()
    # Remove old entry if exists
    existing = [s for s in existing if s.get("strategyId") != args.strategy_id]
    existing.append(entry)

    os.makedirs(os.path.dirname(cfg.STRATEGY_REGISTRY), exist_ok=True)
    cfg.save_strategies(existing)
    print(f"   ✅ Registry saved: {cfg.STRATEGY_REGISTRY}")

    # Step 5: Initialize state directory
    print("5. Initializing state directory...")
    sd = cfg.state_dir(args.strategy_id)
    dsl_dir = cfg.dsl_state_path(args.strategy_id)

    # Create initial state files
    initial_state = {
        "stalking": [],
        "strike": [],
        "active_positions": {},
        "updated_at": cfg.now_iso(),
    }
    cfg.atomic_write(os.path.join(sd, "shark-state.json"), initial_state)

    initial_counter = {
        "date": time.strftime("%Y-%m-%d"),
        "accountValueStart": args.budget,
        "entries": 0,
        "realizedPnl": 0,
        "gate": "OPEN",
        "gateReason": None,
        "cooldownUntil": None,
        "lastResults": [],
        "maxEntriesPerDay": 6,
        "maxConsecutiveLosses": 3,
        "cooldownMinutes": 45,
    }
    cfg.atomic_write(os.path.join(sd, "trade-counter.json"), initial_counter)

    peak = {"peak": args.budget, "updated_at": cfg.now_iso()}
    cfg.atomic_write(os.path.join(sd, "peak-balance.json"), peak)

    print(f"   ✅ State dir: {sd}")
    print(f"   ✅ DSL dir:   {dsl_dir}")

    # Step 6: Generate cron templates
    print("6. Cron templates:")
    print()
    templates = generate_cron_templates(args.strategy_id, args.chat_id)
    for t in templates:
        print(f"   📋 {t['name']}")
        print(f"      Schedule: {t['schedule']['expr']} ({t['sessionTarget']})")
        print()

    # Output JSON for agent to create crons
    print("=" * 50)
    print("CRON_TEMPLATES_JSON:")
    print(json.dumps(templates, indent=2))
    print("=" * 50)

    print()
    print("✅ SHARK setup complete!")
    print()
    print("⚠️  OI tracker needs ~1 hour of data before signals are reliable.")
    print("   The strategy will start scanning automatically once enough data is collected.")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        sys.exit(1)
