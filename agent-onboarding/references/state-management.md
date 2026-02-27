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
| `FRESH` | `ONBOARDING` | User sends first message |
| `ONBOARDING` | `UNFUNDED` | Credentials saved, MCP configured |
| `UNFUNDED` | `AWAITING_FIRST_TRADE` | Wallet balance > $0 |
| `AWAITING_FIRST_TRADE` | `READY` | First trade completed or skipped |
| Any state | `FAILED` | Error occurs |

## State File Location

`$SENPI_STATE_DIR/state.json` (default: `~/.config/senpi/state.json`)

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

## Handoff to senpi-getting-started-guide

When state transitions to `AWAITING_FIRST_TRADE`:

1. The `agent-onboarding` skill stops handling trading prompts
2. The `senpi-getting-started-guide` skill takes over
3. User triggers with: "let's trade", "first trade", "teach me to trade"
4. Skip with: "skip tutorial" → state becomes `READY`

## Balance Monitoring (while UNFUNDED)

On each user message when state is `UNFUNDED`:

```bash
# Check balance via MCP
# If balance > 0:
#   Update state.json: state = "AWAITING_FIRST_TRADE", wallet.funded = true
#   Prompt: "🎉 Your wallet is funded! Ready for your first trade?"
# If balance = 0:
#   Prepend funding reminder (max 3 times)
#   Continue processing user's request
```

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
    # Resume from saved step
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
