# State Management Reference

## State Flow

```
FRESH ─────▶ ONBOARDING ─────▶ UNFUNDED ─────▶ AWAITING_FIRST_TRADE ─────▶ READY
  │               │                │                    │
  └───────────────┴────────────────┴────────────────────┘
                            │
                            ▼
                         FAILED
```

## State Definitions

| State | Description | Skill Responsible |
|-------|-------------|-------------------|
| `FRESH` | No state.json exists | `agent-onboarding` |
| `ONBOARDING` | Onboarding in progress | `agent-onboarding` |
| `UNFUNDED` | Account created, wallet empty | `agent-onboarding` (balance monitoring) |
| `AWAITING_FIRST_TRADE` | Funded, no trades yet | `senpi-getting-started-guide` |
| `READY` | First trade complete | Normal processing |
| `FAILED` | Error occurred | Offer retry |

## State Transitions

| From | To | Trigger |
|------|----|---------|
| `FRESH` | `ONBOARDING` | User sends first message; onboarding skill sets `state` to `ONBOARDING` and `onboarding.startedAt` when starting the flow |
| `ONBOARDING` | `UNFUNDED` | Credentials saved, MCP configured (Step 7 writes `state: UNFUNDED`, `onboarding.step: COMPLETE`) |
| `UNFUNDED` | `AWAITING_FIRST_TRADE` | Wallet balance >= $100 |
| `AWAITING_FIRST_TRADE` | `READY` | First trade completed or skipped |
| Any state | `FAILED` | Error occurs |

## State File Location

`~/.config/senpi/state.json`

## State File Schema

```json
{
  "version": "1.0.0",
  "state": "UNFUNDED",
  "error": null,
  
  "onboarding": {
    "step": "COMPLETE",
    "completedAt": "2025-02-27T10:00:00Z",
    "identityType": "TELEGRAM",
    "subject": "username",
    "walletGenerated": false
  },
  
  "account": {
    "userId": "user_xxx",
    "referralCode": "ABC123",
    "agentWalletAddress": "0x..."
  },
  
  "wallet": {
    "address": "0x...",
    "funded": false,
    "lastBalanceCheck": "2025-02-27T10:00:00Z"
  },
  
  "mcp": {
    "configured": true,
    "endpoint": "https://mcp.prod.senpi.ai/mcp"
  },
  
  "firstTrade": {
    "completed": false,
    "skipped": false
  }
}
```

## Resume from ONBOARDING

When state is `ONBOARDING`, the onboarding skill must read `onboarding.step` and resume from that step instead of starting at Step 0. Step values and corresponding flow steps:

| `onboarding.step` | Resume at |
|-------------------|-----------|
| `IDENTITY` | Step 1 (Collect Identity) |
| `REFERRAL` | Step 2 (Set Referral Code) |
| `API_CALL` | Step 3 (Call Onboarding API) |
| `PARSE` | Step 4 (Parse Response) |
| `CREDENTIALS` | Step 5 (Persist Credentials) |
| `MCP_CONFIG` | Step 6 (Configure MCP Server) |

The onboarding skill sets `state` to `ONBOARDING` when transitioning from `FRESH` at flow start, and updates `onboarding.step` after each step so interruption and resume work correctly.

## Handoff to senpi-getting-started-guide

When state transitions to `AWAITING_FIRST_TRADE`:

1. The `agent-onboarding` skill stops handling trading prompts
2. The `senpi-getting-started-guide` skill takes over
3. User triggers with: "let's trade", "first trade", "teach me to trade"
4. Skip with: "skip tutorial" → state becomes `READY`

## Balance Monitoring (while UNFUNDED)

On each user message when state is `UNFUNDED`:

```bash
# Check balance via MCP (USD/USDC equivalent)
# If balance >= 100:
#   Update state.json: state = "AWAITING_FIRST_TRADE", wallet.funded = true
#   Prompt: "🎉 Your wallet is funded! Ready for your first trade?"
# If balance < 100:
#   Prepend funding reminder: at least $100 required (max 3 times).
#   Always include the agent wallet address in the reminder (state.account.agentWalletAddress or state.wallet.address).
#   Example: "💰 Fund with at least $100 USDC. Address: {address} — Base, Arbitrum, Optimism, Polygon, Ethereum."
#   Continue processing user's request
```

**First-trade intent while UNFUNDED:** If the user says "let's trade", "first trade", "teach me to trade", or similar, check balance first. If balance is still under $100, do **not** hand off to senpi-getting-started-guide. Remind them to fund **at least $100 USDC** and **include the agent wallet address** in the message. Say: "When you've sent the funds, tell me **'I funded my wallet'** or **'check my balance'** so I can confirm." User-triggered phrases like "I funded my wallet" or "check my balance" trigger a manual balance check and do not count toward the 3-reminder limit.

## Reading State on Startup

```bash
if [ -f ~/.config/senpi/state.json ]; then
  STATE=$(cat ~/.config/senpi/state.json | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).state")
else
  STATE="FRESH"
fi

case $STATE in
  "FRESH")
    # Start onboarding
    ;;
  "ONBOARDING")
    # Resume from saved step: read onboarding.step and continue from that step.
    # Step values: IDENTITY → REFERRAL → API_CALL → PARSE → CREDENTIALS → MCP_CONFIG. See agent-onboarding SKILL.md.
    ;;
  "UNFUNDED")
    # Check balance, show funding reminder
    ;;
  "AWAITING_FIRST_TRADE")
    # Hand off to senpi-getting-started-guide skill
    ;;
  "READY")
    # Process normally
    ;;
  "FAILED")
    # Offer retry
    ;;
esac
```
