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
from wolf_config import mcporter_call

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

# Parse CLI args
parser = argparse.ArgumentParser(description="WOLF v6 Setup")
parser.add_argument("--wallet", help="Strategy wallet address (0x...)")
parser.add_argument("--strategy-id", help="Strategy ID (UUID)")
parser.add_argument("--budget", type=float, help="Trading budget in USD (min $500)")
parser.add_argument("--chat-id", type=int, help="Telegram chat ID")
parser.add_argument("--name", help="Human-readable strategy name (optional)")
parser.add_argument("--dsl-preset", choices=["aggressive", "conservative"], default="aggressive",
                    help="DSL tier preset (default: aggressive)")
parser.add_argument("--mid-model", default="anthropic/claude-sonnet-4-20250514",
                    help="Model ID for Mid-tier isolated crons (DSL, Portfolio, Health)")
parser.add_argument("--budget-model", default="anthropic/claude-haiku-4-5",
                    help="Model ID for Budget-tier isolated crons (SM Flip, Watchdog)")
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
mid_model = args.mid_model
budget_model = args.budget_model

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

if budget < 1000:
    default_leverage = 5
elif budget < 5000:
    default_leverage = 7
elif budget < 15000:
    default_leverage = 10
else:
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
    "dailyLossLimit": daily_loss_limit,
    "autoDeleverThreshold": auto_delever_threshold,
    "dsl": {
        "preset": dsl_preset,
        "tiers": DSL_PRESETS[dsl_preset]
    },
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
        "name": "WOLF Emerging Movers v5 (90s)",
        "schedule": {"kind": "every", "everyMs": 90000},
        "sessionTarget": "main",
        "wakeMode": "now",
        "payload": {
            "kind": "systemEvent",
            "text": f"WOLF v6 Scanner: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/emerging-movers.py`, parse JSON.\n\nMANDATE: Hunt runners before they peak. Multi-strategy aware.\n1. **FIRST_JUMP**: 10+ rank jump from #25+ AND wasn't in previous top 50 (or was >= #30) -> ENTER IMMEDIATELY.\n2. **CONTRIB_EXPLOSION**: 3x+ contrib spike -> ENTER. NEVER downgrade for erratic history.\n3. **IMMEDIATE_MOVER**: 10+ rank jump from #25+ in ONE scan -> ENTER if not downgraded.\n4. **NEW_ENTRY_DEEP**: Appears in top 20 from nowhere -> ENTER.\n5. **Signal routing**: Read wolf-strategies.json. For each signal, find the best-fit strategy: check available slots, existing positions, risk profile match. Route to the strategy with open slots that doesn't already hold the asset.\n6. Min 7x leverage (check max-leverage.json). Alert user on Telegram ({tg}).\n7. **DEAD WEIGHT RULE**: Negative ROE + SM conviction against it for 30+ min -> CUT immediately.\n8. **ROTATION RULE**: If target strategy slots FULL and FIRST_JUMP fires -> compare against weakest position in THAT strategy.\n9. If no actionable signals -> HEARTBEAT_OK.\n10. **AUTO-DELEVER**: Per-strategy threshold check."
        }
    },
    "dsl_combined": {
        "name": "WOLF DSL Combined v6 (3min)",
        "schedule": {"kind": "every", "everyMs": 180000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF DSL: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/dsl-combined.py`, parse JSON.\n\nFor each entry in `results`: if `status==\"closed\"` -> alert Telegram ({tg}) with asset, direction, strategyKey, close_reason, upnl. If `phase1_autocut: true` -> note timeout cut. If `status==\"pending_close\"` -> alert user (retry next run).\nIf `any_closed: true` -> note freed slot(s) for next Emerging Movers run. Else HEARTBEAT_OK."
        }
    },
    "sm_flip": {
        "name": "WOLF SM Flip Detector v6 (5min)",
        "schedule": {"kind": "every", "everyMs": 300000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": budget_model,
            "message": f"WOLF SM Check: Run `python3 {SCRIPTS_DIR}/sm-flip-check.py`, parse JSON.\n\nFor each alert in `alerts`: if `alertLevel == \"FLIP_NOW\"` -> close that position on the wallet for `strategyKey` (set `active: false` in `{WORKSPACE}/state/{{{{strategyKey}}}}/dsl-{{{{ASSET}}}}.json`), alert Telegram ({tg}) with asset, direction, conviction, strategyKey.\nIgnore alerts with `alertLevel` of WATCH or FLIP_WARNING (no action needed).\nIf `hasFlipSignal == false` or no FLIP_NOW alerts -> HEARTBEAT_OK."
        }
    },
    "watchdog": {
        "name": "WOLF Watchdog v6 (5min)",
        "schedule": {"kind": "every", "everyMs": 300000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": budget_model,
            "message": f"WOLF Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS_DIR}/wolf-monitor.py`, parse JSON.\n\nCheck each strategy: crypto_liq_buffer_pct<50% -> WARNING (alert Telegram only); <30% -> CRITICAL (close the position with lowest ROE% in that strategy, then alert Telegram ({tg})). XYZ liq_distance_pct<15% -> alert Telegram.\nIf no alerts -> HEARTBEAT_OK."
        }
    },
    "portfolio": {
        "name": "WOLF Portfolio v6 (15min)",
        "schedule": {"kind": "every", "everyMs": 900000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF Portfolio: Read {WORKSPACE}/wolf-strategies.json, get clearinghouse state per wallet, send Telegram ({tg}).\nFormat: code-block table with per-strategy name/account value/positions (asset, direction, ROE, PnL, DSL tier)/slot usage + global totals."
        }
    },
    "health_check": {
        "name": "WOLF Health Check v6 (10min)",
        "schedule": {"kind": "every", "everyMs": 600000},
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "model": mid_model,
            "message": f"WOLF Health Check: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS_DIR}/job-health-check.py`, parse JSON.\n\nThe script auto-fixes most issues (check the `action` field per issue):\n- auto_created -> DSL was missing, script created it. Alert Telegram ({tg}).\n- auto_deactivated -> Orphan DSL deactivated (position closed externally). No alert needed.\n- auto_replaced -> Direction mismatch fixed with fresh DSL. Alert Telegram ({tg}).\n- updated_state -> Size/entry/leverage reconciled to match on-chain. No alert needed.\n- skipped_fetch_error -> Orphan check skipped due to API error. No alert needed (transient).\n- alert_only -> Script could not auto-fix. Handle manually:\n  - NO_WALLET -> CRITICAL, needs manual config. Alert Telegram ({tg}).\n  - DSL_INACTIVE -> CRITICAL, set `active: true` in the DSL state file. Alert Telegram ({tg}).\nIf no issues -> HEARTBEAT_OK."
        }
    },
    "opportunity_scanner": {
        "name": "WOLF Scanner v6 (15min)",
        "schedule": {"kind": "every", "everyMs": 900000},
        "sessionTarget": "main",
        "wakeMode": "now",
        "payload": {
            "kind": "systemEvent",
            "text": f"WOLF scanner: Run `PYTHONUNBUFFERED=1 timeout 180 python3 {SCRIPTS_DIR}/opportunity-scan-v6.py 2>/dev/null`. Parse JSON.\n\nMulti-strategy signal routing: For each scored opportunity (threshold 175+):\n1. Which strategies have empty slots?\n2. Does any strategy already hold this asset? (skip within strategy, allow cross-strategy)\n3. Which strategy's risk profile matches the signal?\n4. Route to best-fit -> open position on THAT wallet -> create DSL state in THAT strategy's state dir.\nAlert user ({tg}). Otherwise HEARTBEAT_OK."
        }
    }
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
  Default Leverage:  {default_leverage}x
  Notional/Slot:    ${notional_per_slot:,.2f}
  Daily Loss Limit: ${daily_loss_limit:,.2f}
  Auto-Delever:     Below ${auto_delever_threshold:,.2f}
  DSL Preset:       {dsl_preset}
  Telegram:         {tg}
""")

strategies_count = len(registry["strategies"])
print(f"  Total strategies in registry: {strategies_count}")
if strategies_count > 1:
    print(f"  All strategies: {list(registry['strategies'].keys())}")

print("\n" + "=" * 60)
print("  Next Steps: Create 7 cron jobs")
print("=" * 60)
print(f"""
Use OpenClaw cron to create each job. See references/cron-templates.md
for the exact payload text for each of the 7 jobs.

With multi-strategy, crons iterate all enabled strategies internally.
You only need ONE set of crons regardless of strategy count.

  Session & Model Tier Recommendations:
  ┌──────────────────────┬──────────┬──────────┬─────────────────────────────────────────────┐
  │ Cron                 │ Session  │ Payload  │ Model                                       │
  ├──────────────────────┼──────────┼──────────┼─────────────────────────────────────────────┤
  │ Emerging Movers      │ main     │ sysEvent │ Primary (your model)                        │
  │ Opportunity Scanner  │ main     │ sysEvent │ Primary (your model)                        │
  │ DSL Combined         │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ Portfolio Update     │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ Health Check         │ isolated │ agentTrn │ Mid: {mid_model}  │
  │ SM Flip Detector     │ isolated │ agentTrn │ Budget: {budget_model}       │
  │ Watchdog             │ isolated │ agentTrn │ Budget: {budget_model}       │
  └──────────────────────┴──────────┴──────────┴─────────────────────────────────────────────┘

  Main crons share your primary session context (systemEvent).
  Isolated crons run in their own session (agentTurn) — no context pollution.
  All 7 crons can also run on a single model if you prefer simplicity.
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
