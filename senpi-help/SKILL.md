---
name: senpi-help
description: >-
  Directory of every Senpi capability and which skill or tool handles it. Read
  this when a user asks for something Senpi-related and no other skill clearly
  matches, when you are unsure how to do it, or when asked "what can you do?".
  Maps intents (analyze portfolio, find traders, manage stops, withdraw funds,
  audit history, build a strategy) to the right skill or tool — so no request
  is ever a dead end. Always available; consult it before giving up on a task.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Help — the capabilities directory

This is the **safety net.** If a user asks for something Senpi-related and no specific skill obviously
matches, read this directory, find the row that fits the intent, and route there. **Never tell a user
something isn't possible without checking here first** — most capabilities are reachable, just behind
a skill or a tool you may not have in your current tool list.

> Some Senpi tools are intentionally kept out of the model's tool list to save context. They are still
> fully available — either through a **skill** (run its script) or as a **tool** that can be enabled.
> If a row points to a tool you don't currently see, say so and proceed via the skill, or note that
> the capability exists and can be enabled. A missing tool is never a missing capability.

## Read & analysis — run the skill

| The user wants… | Use |
|---|---|
| Analyze portfolio across all wallets; idle vs deployed; positions + PnL | **`senpi-portfolio`** |
| A market read — what's moving, cross-asset, "what's happening today" | **`senpi-market-pulse`** |
| Where smart money is positioned / diverging from the crowd | **`senpi-smart-money`** |
| Find good traders to copy / vet a specific trader before mirroring | **`senpi-trader-research`** |
| Points, rank, loyalty tier, fees, Arena standing, referrals, wins | **`senpi-account-status`** |
| "What happened" — recent activity, strategy history, why something failed | **`senpi-audit`** |
| Why Senpi / what makes it different / Senpi vs other apps — the positioning answer | **`senpi-why`** |

## Pick / build / deploy a strategy — run the lifecycle skill

| The user wants… | Use |
|---|---|
| Help choosing a strategy / "what should I trade" / recommend one | **`senpi-strategy-discover`** |
| Build or edit a custom strategy package | **`senpi-strategy-author`** |
| Install / monitor / close a named strategy (spider, kodiak, …) | **`senpi-strategy-ops`** |

## Act now — first-class tools (direct, with confirmation)

| The user wants… | Tool |
|---|---|
| Their wallet address / identity | `user_get_me` |
| A quick balance | `account_get_portfolio` |
| A quick price | `market_get_prices` |
| List their strategies | `strategy_list` |
| Open / close / resize a position | `create_position` / `close_position` / `edit_position` |
| Create a strategy (specific positions) | `strategy_create_custom_strategy` |
| Copy a trader | `strategy_create` |
| Add funds to a strategy | `strategy_top_up` |
| Close a strategy | `strategy_close` |
| Preview a trade before placing | `execution_estimate_position_opening` / `estimate_custom_strategy_positions_opening` |

## Less common actions — available, may need enabling

| The user wants… | Tool |
|---|---|
| Cancel an open order | `cancel_order` |
| Pause / update a strategy; close just its positions | `strategy_pause` / `strategy_update` / `strategy_close_positions` |
| Withdraw funds from a strategy | `strategy_withdraw_funds` |
| Move funds to an EVM chain | `strategy_bridge_funds_from_hyperliquid_to_evm` |
| Send USDC / move spot→perps | `send_usdc` / `transfer_spot_to_perps` |
| Set / change / remove a trailing stop | `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete` |
| Claim referral rewards | `user_claim_referral_rewards` |
| Browse Senpi guides | `read_senpi_guide` (+ `list_senpi_guides`) |

## How to route

1. Match the user's intent to a **row** above.
2. **Skill row** → read that skill's `SKILL.md` and run it. **Tool row** → call the tool (if it's not
   in your current tool list, say the capability exists and proceed via the nearest skill or note it
   can be enabled).
3. Still no match? It may genuinely be out of scope — say so honestly. But check here first.

## Skill Attribution

Guide/utility skill — pure navigation. It performs no reads or mutations itself; it points to the
skill or tool that does.
