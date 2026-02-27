---
name: senpi-getting-started-guide
description: >
  Guides users through their first trade on Senpi/Hyperliquid. Walks through
  discovery, position opening, monitoring, and closing with celebration.
  Use when user says "let's trade", "first trade", "teach me to trade",
  "how do I trade", or when state is AWAITING_FIRST_TRADE. Can also run when
  state is not READY (e.g. after entrypoint Step 3); then prompts for wallet
  funding before starting when needed. Requires Senpi MCP to be connected.
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: 1.0.0
  homepage: https://agents.senpi.ai
---

# Getting Started: Your First Trade

Guide users through their first complete trade on Hyperliquid via Senpi. This skill teaches the core trading loop: discover → open → monitor → close.

**Prerequisites:** Senpi MCP connected. State must not be `FRESH` or `ONBOARDING` (onboarding must be complete). If state is not found or not `READY`, the guide will prompt for wallet funding before starting the tutorial when needed; if `AWAITING_FIRST_TRADE` or `READY`, the tutorial runs directly (wallet must be funded for opening positions).

---

## Prerequisites

Before starting the tutorial, verify:

1. **MCP Connected** — Senpi MCP server is configured and accessible.
2. **Onboarding complete** — State is not `FRESH` or `ONBOARDING` (see state check below).
3. **Wallet funded (for opening positions)** — When state is not found or not `READY`, the guide shows a funding reminder first if balance is low; do not start Step 1 until balance ≥ $100 or user confirms they funded.

Ensure state file exists; if missing, create it and redirect to onboarding:

```bash
# Ensure state file exists (per state lifecycle); if missing, create and redirect to onboarding
if [ ! -f ~/.config/senpi/state.json ]; then
  mkdir -p ~/.config/senpi
  cat > ~/.config/senpi/state.json << 'STATEEOF'
{
  "version": "1.0.0",
  "state": "FRESH",
  "error": null,
  "onboarding": {
    "step": "IDENTITY",
    "startedAt": null,
    "completedAt": null,
    "identityType": null,
    "subject": null,
    "walletGenerated": false,
    "existingAccount": false
  },
  "account": {},
  "wallet": { "funded": false },
  "firstTrade": { "completed": false, "skipped": false },
  "mcp": { "configured": false }
}
STATEEOF
  echo "State file was missing; created with FRESH. User must complete onboarding first."
  exit 1
fi

STATE=$(cat ~/.config/senpi/state.json | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).state")
if [ "$STATE" = "FRESH" ] || [ "$STATE" = "ONBOARDING" ]; then
  echo "User needs to complete onboarding first"
  exit 1
fi
```

---

## Triggers

Start this tutorial when:

- User says: "let's trade", "first trade", "teach me to trade", "how do I trade", "make a trade"
- State is `AWAITING_FIRST_TRADE` and user sends a trading-related message
- User explicitly asks for trading guidance

**Do NOT start if:** MCP not connected (redirect to onboarding), or user says "skip tutorial" — then set state to `READY` and exit. See [references/next-steps.md](references/next-steps.md) for skip handling. If wallet has less than $100 when starting the tutorial, do not open a position until funded; see "When state is not found or not READY" below.

---

## Tutorial Flow

Follow steps in order. Reference files contain display copy, state schemas, and error handling.

### When state is not found or not READY (before Step 1)

If the user asked for the first-trade guide (e.g. from the entrypoint or "let's trade") but state is missing or not `READY`:

1. **Check balance** — Use MCP to fetch portfolio/balance.
2. **If balance < $100:** Do **not** start Step 1 (Introduction). Show a single clear message:
   - Include the **agent wallet address** from `state.json` → `account.agentWalletAddress` or `wallet.address`.
   - Say they need at least $100 USDC on a supported chain (Base, Arbitrum, Optimism, Polygon, or Ethereum).
   - Say: "When you've sent the funds, tell me **'I funded my wallet'** or **'let's trade'** and I'll start the tutorial."
3. **If balance ≥ $100:** Update state to `AWAITING_FIRST_TRADE` and `wallet.funded: true` (read-modify-write `~/.config/senpi/state.json`), then proceed to Step 1.

Do not show "User needs to complete onboarding first" when onboarding is already complete (i.e. state is not `FRESH` or `ONBOARDING`).

### Step 1: Introduction

Display the trade cycle (Discover → Open → Monitor → Close), recommend $50 and 3x leverage, and include a short risk disclaimer. Ask user to say **"yes"** to continue or **"skip"** if experienced.

Update state: set `firstTrade.step` to `INTRODUCTION`, `firstTrade.started` true, `startedAt` (ISO 8601). Preserve existing fields in `state.json`.

Wait for user confirmation before proceeding.

---

### Step 2: Discovery

Use MCP to fetch top traders and current positions. Prefer liquid assets (ETH, BTC, SOL) and high conviction. Show a short opportunities table and recommend one asset/direction.

See [references/discovery-guide.md](references/discovery-guide.md) for display template and state update (`firstTrade.step: "DISCOVERY"`, `recommendedAsset`, `recommendedDirection`).

---

### Step 3: Position Sizing

Before opening, explain asset, direction, size ($50), leverage (3x), margin, and a brief risk/reward table. Warn about liquidation. Ask user to say **"confirm"** to execute.

See [references/position-management.md](references/position-management.md) for the full sizing table and wording.

---

### Step 4: Open Position

On user confirmation, create the position via MCP (asset, direction, size $50, leverage 3x). On success, display entry price, size, leverage, margin, strategy ID. Offer: "how's my position?", "close my position", "set stop loss at $X".

Update state per [references/position-management.md](references/position-management.md) (`firstTrade.step: "POSITION_OPEN"`, `tradeDetails`). On failure, see [references/error-handling.md](references/error-handling.md).

---

### Step 5: Monitor Position

When user asks "how's my position?" or similar, fetch position via MCP and show entry, current price, % change, unrealized PnL, ROE, duration. Offer: Hold, Close, or Add protection.

---

### Step 6: Close Position

When user says "close", "exit", "take profit", etc., execute close via MCP. Display entry, exit, PnL, ROE, duration, fees. Update state with `firstTrade.step: "POSITION_CLOSE"` and full `tradeDetails`. See [references/position-management.md](references/position-management.md).

---

### Step 7: Celebrate & Next Steps

Show celebration (profit or loss) and suggest next skills and quick commands. Set state to `READY` and `firstTrade.completed`, `firstTrade.step: "COMPLETE"`, `completedAt`.

Full copy and state shape: [references/next-steps.md](references/next-steps.md) and [references/position-management.md](references/position-management.md).

---

## Interrupted Tutorial / Resume

If the user returns mid-tutorial, read `firstTrade.step` from state and resume from the matching step. See [references/next-steps.md](references/next-steps.md) for resume logic and welcome-back message.

---

## Reference Files

- **[references/error-handling.md](references/error-handling.md)** — Insufficient balance, position open failed, position already exists, recovery
- **[references/discovery-guide.md](references/discovery-guide.md)** — MCP usage for discovery, what to look for, display template
- **[references/position-management.md](references/position-management.md)** — Sizing table, open/monitor/close flow, state updates for firstTrade
- **[references/next-steps.md](references/next-steps.md)** — Celebration copy, skip tutorial, resume handling

---

## Installation

```bash
npx skills add Senpi-ai/senpi-skills/senpi-getting-started-guide
```

Or manually:

```bash
mkdir -p ~/.senpi/skills/senpi-getting-started-guide
curl -sL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-getting-started-guide/SKILL.md" \
  -o ~/.senpi/skills/senpi-getting-started-guide/SKILL.md
# Copy references/ into the same skill directory
```
