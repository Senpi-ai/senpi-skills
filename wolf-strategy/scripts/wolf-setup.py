#!/usr/bin/env python3
"""
WOLF v6 Setup Wizard
Sets up a WOLF autonomous trading strategy and adds it to the multi-strategy registry.
Calculates all parameters from budget, fetches max-leverage data,
and outputs config + cron templates.

Usage:
  # Agent passes what it knows, only asks user for budget:
  python3 wolf-setup.py --wallet 0x... --strategy-id UUID --chat-id 12345 --budget 6500

  # With custom name and DSL preset:
  python3 wolf-setup.py --wallet 0x... --strategy-id UUID --chat-id 12345 --budget 6500 \
      --name "Aggressive Momentum" --dsl-preset aggressive

  # Interactive mode (prompts for everything):
  python3 wolf-setup.py
"""
import json, sys, os, math, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import mcporter_call, GUARD_RAIL_DEFAULTS

WORKSPACE = os.environ.get("WOLF_WORKSPACE",
    os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))
REGISTRY_FILE = os.path.join(WORKSPACE, "wolf-strategies.json")
LEGACY_CONFIG = os.path.join(WORKSPACE, "wolf-strategy.json")
MAX_LEV_FILE = os.path.join(WORKSPACE, "max-leverage.json")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# DSL presets
DSL_PRESETS = {
    "aggressive": [
        {"triggerPct": 5, "lockPct": 50, "breaches": 3},
        {"triggerPct": 10, "lockPct": 65, "breaches": 2},
        {"triggerPct": 15, "lockPct": 75, "breaches": 2},
        {"triggerPct": 20, "lockPct": 85, "breaches": 1}
    ],
    "conservative": [
        {"triggerPct": 3, "lockPct": 60, "breaches": 4},
        {"triggerPct": 7, "lockPct": 75, "breaches": 3},
        {"triggerPct": 12, "lockPct": 85, "breaches": 2},
        {"triggerPct": 18, "lockPct": 90, "breaches": 1}
    ]
}

# Provider -> model mapping for 2-tier approach
PROVIDER_MODELS = {
    "anthropic": {
        "mid": "anthropic/claude-sonnet-4-5",
        "budget": "anthropic/claude-haiku-4-5",
    },
    "openai": {
        "mid": "openai/gpt-4o",
        "budget": "openai/gpt-4o-mini",
    },
    "google": {
        "mid": "google/gemini-2.0-flash",
        "budget": "google/gemini-2.0-flash-lite",
    },
}

# Parse CLI args
parser = argparse.ArgumentParser(description="WOLF v6 Setup")
parser.add_argument("--wallet", help="Strategy wallet address (0x...)")
parser.add_argument("--strategy-id", help="Strategy ID (UUID)")
parser.add_argument("--budget", type=float, help="Trading budget in USD (min $500)")
parser.add_argument("--chat-id", type=int, help="Telegram chat ID")
parser.add_argument("--name", help="Human-readable strategy name (optional)")
parser.add_argument("--dsl-preset", choices=["aggressive", "conservative"], default="aggressive",
                    help="DSL tier preset (default: aggressive)")
parser.add_argument("--provider", choices=list(PROVIDER_MODELS.keys()), default="anthropic",
                    help="AI provider for model selection (default: anthropic)")
parser.add_argument("--mid-model", default=None,
                    help="Override Mid-tier model ID (default: auto from --provider)")
parser.add_argument("--budget-model", default=None,
                    help="Override Budget-tier model ID (default: auto from --provider)")
parser.add_argument("--trading-risk", choices=["conservative", "moderate", "aggressive"],
                    default="moderate", help="Risk tier for dynamic leverage calculation (default: moderate)")
args = parser.parse_args()

def ask(prompt, default=None, validator=None):
    while True:
        suffix = f" [{default}]" if default else ""
        val = input(f"{prompt}{suffix}: ").strip()
        if not val and default:
            val = str(default)
        if validator:
            try:
                return validator(val)
            except Exception as e:
                print(f"  Invalid: {e}")
        elif val:
            return val
        else:
            print("  Required.")

def validate_wallet(v):
    if not v.startswith("0x") or len(v) != 42:
        raise ValueError("Must be 0x... (42 chars)")
    return v

def validate_uuid(v):
    parts = v.replace("-", "")
    if len(parts) != 32:
        raise ValueError("Must be a UUID (32 hex chars)")
    return v

def validate_budget(v):
    b = float(v)
    if b < 500:
        raise ValueError("Minimum budget is $500")
    return b

def validate_chat_id(v):
    return int(v)

print("=" * 60)
print("  WOLF v6 -- Autonomous Trading Strategy Setup")
print("=" * 60)
print()

# Use CLI args if provided, otherwise prompt
wallet = args.wallet or ask("Strategy wallet address (0x...)", validator=validate_wallet)
if args.wallet:
    validate_wallet(args.wallet)

strategy_id = args.strategy_id or ask("Strategy ID (UUID)", validator=validate_uuid)
if args.strategy_id:
    validate_uuid(args.strategy_id)

budget = args.budget or ask("Trading budget (USD, min $500)", validator=validate_budget)
if args.budget:
    validate_budget(str(args.budget))

chat_id = args.chat_id or ask("Telegram chat ID (numeric)", validator=validate_chat_id)
if args.chat_id:
    validate_chat_id(str(args.chat_id))

strategy_name = args.name or f"Strategy {strategy_id[:8]}"
dsl_preset = args.dsl_preset
provider_models = PROVIDER_MODELS[args.provider]
mid_model = args.mid_model if args.mid_model is not None else provider_models["mid"]
budget_model = args.budget_model if args.budget_model is not None else provider_models["budget"]
trading_risk = args.trading_risk

# Calculate parameters
if budget < 3000:
    slots = 2
elif budget < 6000:
    slots = 2
elif budget < 10000:
    slots = 3
else:
    slots = 3

margin_per_slot = round(budget * 0.30, 2)
margin_buffer = round(budget * (1 - 0.30 * slots), 2)
daily_loss_limit = round(budget * 0.15, 2)
drawdown_cap = round(budget * 0.30, 2)

# Reference leverage for notional display only; actual leverage is computed dynamically
# from tradingRisk + asset maxLeverage + conviction at position-open time.
default_leverage = 10

notional_per_slot = round(margin_per_slot * default_leverage, 2)
auto_delever_threshold = round(budget * 0.80, 2)

# Build strategy key
strategy_key = f"wolf-{strategy_id[:8]}"

# Build strategy entry
strategy_entry = {
    "name": strategy_name,
    "wallet": wallet,
    "strategyId": strategy_id,
    "budget": budget,
    "slots": slots,
    "marginPerSlot": margin_per_slot,
    "defaultLeverage": default_leverage,
    "tradingRisk": trading_risk,
    "dailyLossLimit": daily_loss_limit,
    "autoDeleverThreshold": auto_delever_threshold,
    "dsl": {
        "preset": dsl_preset,
        "tiers": DSL_PRESETS[dsl_preset]
    },
    "guardRails": GUARD_RAIL_DEFAULTS.copy(),
    "enabled": True
}

# Load or create registry
if os.path.exists(REGISTRY_FILE):
    with open(REGISTRY_FILE) as f:
        registry = json.load(f)
else:
    registry = {
        "version": 1,
        "defaultStrategy": None,
        "strategies": {},
        "global": {
            "telegramChatId": str(chat_id),
            "workspace": WORKSPACE,
            "notifications": {
                "provider": "telegram",
                "alertDedupeMinutes": 15
            }
        }
    }

# Add strategy to registry
registry["strategies"][strategy_key] = strategy_entry

# Set as default if it's the only one (or the first)
if registry.get("defaultStrategy") is None or len(registry["strategies"]) == 1:
    registry["defaultStrategy"] = strategy_key

# Update global telegram if needed
if not registry["global"].get("telegramChatId"):
    registry["global"]["telegramChatId"] = str(chat_id)

# Save registry atomically
os.makedirs(WORKSPACE, exist_ok=True)
tmp_file = REGISTRY_FILE + ".tmp"
with open(tmp_file, "w") as f:
    json.dump(registry, f, indent=2)
os.replace(tmp_file, REGISTRY_FILE)
print(f"\n  Registry saved to {REGISTRY_FILE}")

# Create per-strategy state directory
state_dir = os.path.join(WORKSPACE, "state", strategy_key)
os.makedirs(state_dir, exist_ok=True)
print(f"  State directory created: {state_dir}")

# Create other shared directories
for d in ["history", "memory", "logs"]:
    os.makedirs(os.path.join(WORKSPACE, d), exist_ok=True)

# Fetch max-leverage via MCP (covers both crypto and XYZ instruments)
print("\nFetching max-leverage data...")
try:
    data = mcporter_call("market_list_instruments")
    instruments = data.get("instruments", [])
    if not isinstance(instruments, list):
        instruments = []
    max_lev = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name:
            continue
        lev = inst.get("max_leverage") or inst.get("maxLeverage")
        if lev is not None:
            max_lev[name] = int(lev)
    with open(MAX_LEV_FILE, "w") as f:
        json.dump(max_lev, f, indent=2)
    crypto_count = sum(1 for inst in instruments if isinstance(inst, dict) and not inst.get("dex"))
    xyz_count = sum(1 for inst in instruments if isinstance(inst, dict) and inst.get("dex"))
    print(f"  Max leverage data saved ({len(max_lev)} assets: {crypto_count} crypto, {xyz_count} XYZ) to {MAX_LEV_FILE}")
except Exception as e:
    print(f"  Failed to fetch max-leverage: {e}")
    print("  You can manually fetch later.")

# Build cron templates
tg = f"telegram:{chat_id}"
margin_str = str(int(margin_per_slot))

cron_templates = {
    "emerging_movers": {
        "name": "WOLF Emerging Movers v6 (3min)",
        "schedule": {"kind": "every", "everyMs": 180000},
        "sessionTarget": "isolated",
        "timeoutMs": 300000,
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF v6 Scanner: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/emerging-movers.py`, parse JSON.\n\nMANDATE: Hunt runners before they peak. Multi-strategy aware.\n1. **FIRST_JUMP**: 10+ rank jump from #25+ AND wasn't in previous top 50 (or was >= #30) -> ENTER IMMEDIATELY.\n2. **CONTRIB_EXPLOSION**: 3x+ contrib spike -> ENTER. NEVER downgrade for erratic history.\n3. **IMMEDIATE_MOVER**: 10+ rank jump from #25+ in ONE scan -> ENTER if not downgraded.\n4. **NEW_ENTRY_DEEP**: Appears in top 20 from nowhere -> ENTER.\n5. **Signal routing**: Read wolf-strategies.json. For each signal, find the best-fit strategy: check available slots, existing positions, risk profile match. Route to the strategy with open slots that doesn't already hold the asset.\n6. Leverage auto-calculated from tradingRisk + asset maxLeverage + signal conviction. Alert user on Telegram ({tg}).\n7. **DEAD WEIGHT RULE**: Negative ROE + SM conviction against it for 30+ min -> CUT immediately.\n8. **ROTATION RULE**: If target strategy slots FULL and FIRST_JUMP fires -> identify weakest position in THAT strategy. Use `python3 {SCRIPTS_DIR}/open-position.py --strategy {{STRATEGY_KEY}} --asset {{NEW_ASSET}} --direction {{DIR}} --conviction {{CONV}} --scanner --close-asset {{WEAK_ASSET}}` to atomically close + open. Do NOT call close_position separately.\n9. If no actionable signals -> HEARTBEAT_OK.\n10. **AUTO-DELEVER**: Per-strategy threshold check.\n\n**POSITION OPENING**: Use `python3 {SCRIPTS_DIR}/open-position.py --strategy {{STRATEGY_KEY}} --asset {{ASSET}} --direction {{DIRECTION}} --conviction {{CONVICTION}} --scanner` to open positions. Conviction comes from scanner output. This handles position creation + DSL state atomically. Do NOT hand-craft DSL JSON.\nAfter running open-position.py, send each message in `notifications` from its JSON output to Telegram ({tg})."
        }
    },
    "dsl_combined": {
        "name": "WOLF DSL Combined v6 (3min)",
        "schedule": {"kind": "every", "everyMs": 180000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF DSL: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/dsl-combined.py`, parse JSON.\nSend each message in `notifications` to Telegram ({tg}).\nIf `notifications` is empty → HEARTBEAT_OK."
        }
    },
    "sm_flip": {
        "name": "WOLF SM Flip Detector v6 (5min)",
        "schedule": {"kind": "every", "everyMs": 300000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": budget_model,
            "message": f"WOLF SM Check: Run `python3 {SCRIPTS_DIR}/sm-flip-check.py`, parse JSON.\n\nFor each alert in `alerts`: if `alertLevel == \"FLIP_NOW\"` -> close that position on the wallet for `strategyKey` (set `active: false` in `{WORKSPACE}/state/{{strategyKey}}/dsl-{{ASSET}}.json`), alert Telegram ({tg}) with asset, direction, conviction, strategyKey.\nIgnore alerts with `alertLevel` of WATCH or FLIP_WARNING (no action needed).\nIf `hasFlipSignal == false` or no FLIP_NOW alerts -> HEARTBEAT_OK."
        }
    },
    "watchdog": {
        "name": "WOLF Watchdog v6 (5min)",
        "schedule": {"kind": "every", "everyMs": 300000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": budget_model,
            "message": f"WOLF Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS_DIR}/wolf-monitor.py`, parse JSON.\nFor each item in `action_required`: close the specified position (coin + strategyKey), then alert Telegram ({tg}) with what was closed and why.\nIgnore all other alerts in the output — they are informational only.\nIf `action_required` is empty → HEARTBEAT_OK."
        }
    },
    "health_check": {
        "name": "WOLF Health Check v6 (10min)",
        "schedule": {"kind": "every", "everyMs": 600000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF Health Check: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/job-health-check.py`, parse JSON.\nSend each message in `notifications` to Telegram ({tg}).\nIf `notifications` is empty → HEARTBEAT_OK."
        }
    },
    "risk_guardian": {
        "name": "WOLF Risk Guardian v6.1 (5min)",
        "schedule": {"kind": "every", "everyMs": 300000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": budget_model,
            "message": f"WOLF Risk Guardian: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/risk-guardian.py`, parse JSON.\nSend each message in `notifications` to Telegram ({tg}).\nIf `notifications` is empty → HEARTBEAT_OK."
        }
    },
}

print("\n" + "=" * 60)
print("  WOLF v6 Configuration Summary")
print("=" * 60)
print(f"""
  Strategy Key:     {strategy_key}
  Strategy Name:    {strategy_name}
  Wallet:           {wallet}
  Strategy ID:      {strategy_id}
  Budget:           ${budget:,.2f}
  Slots:            {slots}
  Margin/Slot:      ${margin_per_slot:,.2f}
  Default Leverage:  {default_leverage}x (fallback only)
  Trading Risk:     {trading_risk}
  Notional/Slot:    ${notional_per_slot:,.2f}
  Daily Loss Limit: ${daily_loss_limit:,.2f}
  Auto-Delever:     Below ${auto_delever_threshold:,.2f}
  Provider:         {args.provider} (mid={mid_model}, budget={budget_model})
  DSL Preset:       {dsl_preset}
  Telegram:         {tg}
""")

strategies_count = len(registry["strategies"])
print(f"  Total strategies in registry: {strategies_count}")
if strategies_count > 1:
    print(f"  All strategies: {list(registry['strategies'].keys())}")

print("\n" + "=" * 60)
print("  Next Steps: Create 6 cron jobs")
print("=" * 60)
print(f"""
Use OpenClaw cron to create each job. See references/cron-templates.md
for the exact payload text for each of the 6 jobs.

With multi-strategy, crons iterate all enabled strategies internally.
You only need ONE set of crons regardless of strategy count.

  Session & Model Tier Recommendations:
  ┌──────────────────────┬──────────┬──────────┬─────────────────────────────────────────────┐
  │ Cron                 │ Session  │ Payload  │ Model                                       │
  ├──────────────────────┼──────────┼──────────┼─────────────────────────────────────────────┤
  │ Emerging Movers      │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ DSL Combined         │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ Health Check         │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ SM Flip Detector     │ isolated │ agentTrn │ Budget: {budget_model}       │
  │ Watchdog             │ isolated │ agentTrn │ Budget: {budget_model}       │
  │ Risk Guardian        │ isolated │ agentTrn │ Budget: {budget_model}       │
  └──────────────────────┴──────────┴──────────┴─────────────────────────────────────────────┘

  Guard Rails (per strategy): maxEntries={GUARD_RAIL_DEFAULTS['maxEntriesPerDay']}/day, \
consecutiveLossCooldown={GUARD_RAIL_DEFAULTS['maxConsecutiveLosses']}L→{GUARD_RAIL_DEFAULTS['cooldownMinutes']}min

  All crons run in isolated sessions (agentTurn) — no context pollution.
  All 6 crons can also run on a single model if you prefer simplicity.
""")

# Output full result as JSON for programmatic use
result = {
    "success": True,
    "strategyKey": strategy_key,
    "config": strategy_entry,
    "registry": {
        "strategiesCount": strategies_count,
        "strategies": list(registry["strategies"].keys()),
        "defaultStrategy": registry["defaultStrategy"]
    },
    "cronTemplates": cron_templates,
    "maxLeverageFile": MAX_LEV_FILE,
    "registryFile": REGISTRY_FILE,
    "stateDir": state_dir,
}
print(json.dumps(result, indent=2))
