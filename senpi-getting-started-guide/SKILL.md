---
name: senpi-getting-started-guide
description: >
  Guides users through their first trade on Senpi/Hyperliquid. Walks through
  discovery, position opening, monitoring, and closing with celebration.
  Use when user says "let's trade", "first trade", "teach me to trade",
  "how do I trade", or when state is AWAITING_FIRST_TRADE. Requires Senpi
  MCP to be connected and wallet to be funded.
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: 1.0.0
  homepage: https://agents.senpi.ai
---

# Getting Started: Your First Trade

Guide users through their first complete trade on Hyperliquid via Senpi. This skill teaches the core trading loop: discover → open → monitor → close.

## Prerequisites

Before starting this tutorial, verify:

1. **MCP Connected** — Senpi MCP server is configured and accessible
2. **Wallet Funded** — User has USDC in their agent wallet (minimum $50 recommended)
3. **State Check** — User's state is `AWAITING_FIRST_TRADE` or they explicitly asked for help trading

```bash
# Ensure state file exists (per state lifecycle); if missing, create and redirect to onboarding
SENPI_STATE_DIR="${SENPI_STATE_DIR:-$HOME/.config/senpi}"
if [ ! -f "$SENPI_STATE_DIR/state.json" ]; then
  mkdir -p "$SENPI_STATE_DIR"
  cat > "$SENPI_STATE_DIR/state.json" << 'STATEEOF'
{
  "version": "1.0.0",
  "state": "FRESH",
  "error": null,
  "onboarding": { "step": "IDENTITY", "walletGenerated": false },
  "wallet": { "funded": false },
  "firstTrade": { "completed": false, "skipped": false },
  "mcp": { "configured": false }
}
STATEEOF
  echo "State file was missing; created with FRESH. User must complete onboarding first."
  exit 1
fi

# Check state
if [ -f "$SENPI_STATE_DIR/state.json" ]; then
  STATE=$(cat "$SENPI_STATE_DIR/state.json" | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).state")
  if [ "$STATE" != "AWAITING_FIRST_TRADE" ] && [ "$STATE" != "READY" ]; then
    echo "User needs to complete onboarding first"
    exit 1
  fi
fi
```

---

## Triggers

Start this tutorial when:

- User says: "let's trade", "first trade", "teach me to trade", "how do I trade", "make a trade"
- State is `AWAITING_FIRST_TRADE` and user sends any trading-related message
- User explicitly asks for trading guidance

**Do NOT start if:**
- Wallet is not funded (redirect to funding instructions)
- MCP is not connected (redirect to onboarding)
- User says "skip tutorial" (update state to READY and exit)

---

## Tutorial Flow

### Step 1: Introduction

**Display to user:**

> 🚀 **Let's make your first trade!**
>
> I'll walk you through a complete trade cycle:
>
> 1️⃣ **Discover** — Find what smart money is trading
> 2️⃣ **Open** — Enter a small test position
> 3️⃣ **Monitor** — Watch your position
> 4️⃣ **Close** — Take profit or cut losses
>
> We'll use a small position ($50, 3x leverage) so you can learn safely.
>
> **Risk disclaimer:** Trading involves risk. Only trade with funds you can afford to lose.
>
> Ready? Say **"yes"** to continue or **"skip"** if you're experienced.

**Update state:**

```bash
# Update state.json
cat > ~/.config/senpi/state.json << EOF
{
  ... (preserve existing fields),
  "firstTrade": {
    "started": true,
    "step": "INTRODUCTION",
    "startedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  }
}
EOF
```

**Wait for user confirmation before proceeding.**

---

### Step 2: Discovery

**Action:** Use MCP to fetch top traders and their current positions.

Look for:
- Traders with high conviction (multiple top traders in same direction)
- Liquid assets (ETH, BTC, SOL preferred for first trade)
- Recent entries (positions opened in last few hours)

**Display to user:**

> 🔍 **Scanning smart money activity...**
>
> Here's what top traders are doing right now:
>
> **Top Opportunities:**
>
> | Asset | Direction | # Traders | Avg Entry | Conviction |
> |-------|-----------|-----------|-----------|------------|
> | ETH   | LONG      | 3         | $3,180    | 🟢 High    |
> | SOL   | LONG      | 2         | $142      | 🟡 Medium  |
> | BTC   | SHORT     | 1         | $67,500   | 🟡 Medium  |
>
> 💡 **Recommendation:** I suggest **ETH LONG** for your first trade:
> - Most liquid market (easy to enter/exit)
> - Strong conviction (3 top traders agree)
> - Moderate volatility (good for learning)
>
> Want to open an **ETH LONG** position? Say **"yes"** or tell me a different asset.

**Update state:**

```bash
# Update firstTrade.step
"firstTrade": {
  "step": "DISCOVERY",
  "recommendedAsset": "ETH",
  "recommendedDirection": "LONG"
}
```

---

### Step 3: Position Sizing

**Before opening, explain the position:**

> 📊 **Position Details:**
>
> | Parameter | Value | Explanation |
> |-----------|-------|-------------|
> | Asset | ETH | Ethereum perpetual futures |
> | Direction | LONG | Profit when price goes up |
> | Size | $50 | Small size for learning |
> | Leverage | 3x | Moderate leverage |
> | Margin | ~$16.67 | Amount locked as collateral |
>
> **Risk/Reward Profile:**
>
> | ETH Move | Your P&L | ROE |
> |----------|----------|-----|
> | +5% | +$7.50 | +15% |
> | +2% | +$3.00 | +6% |
> | +1% | +$1.50 | +3% |
> | -1% | -$1.50 | -3% |
> | -2% | -$3.00 | -6% |
> | -5% | -$7.50 | -15% |
>
> ⚠️ **Liquidation:** If ETH drops ~33% from entry, position auto-closes.
>
> Ready to open? Say **"confirm"** to execute.

---

### Step 4: Open Position

**On user confirmation, execute via MCP:**

Use the appropriate MCP tool to create the position with:
- Asset: ETH (or user's choice)
- Direction: LONG (or as determined)
- Size: $50 USD
- Leverage: 3x

**Display result:**

> ✅ **Position Opened!**
>
> ```
> ┌─────────────────────────────────────┐
> │  ETH LONG                           │
> ├─────────────────────────────────────┤
> │  Entry Price:    $3,195.50          │
> │  Position Size:  $50.00             │
> │  Leverage:       3x                 │
> │  Margin Used:    $16.67             │
> │  Strategy ID:    strat_abc123       │
> └─────────────────────────────────────┘
> ```
>
> 🎯 **What to watch:**
> - Current price vs entry price
> - Unrealized P&L
> - ROE (Return on Equity)
>
> **Commands you can use:**
> - "how's my position?" — Check current status
> - "close my position" — Exit the trade
> - "set stop loss at $3,100" — Add protection
>
> I'll keep an eye on it. Let me know when you want to close!

**Update state:**

```bash
"firstTrade": {
  "step": "POSITION_OPEN",
  "positionOpenedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "tradeDetails": {
    "asset": "ETH",
    "direction": "LONG",
    "size": 50,
    "leverage": 3,
    "entryPrice": 3195.50,
    "strategyId": "strat_abc123"
  }
}
```

---

### Step 5: Monitor Position

**When user asks "how's my position?" or similar:**

Fetch position status via MCP and display:

> 📈 **Position Status**
>
> ```
> ETH LONG — OPEN
> ─────────────────────────────────────
> Entry:          $3,195.50
> Current:        $3,220.00  (+0.77%)
> 
> Position Size:  $50.00
> Unrealized PnL: +$1.15
> ROE:            +2.30%
> 
> Duration:       45 minutes
> ─────────────────────────────────────
> ```
>
> 🟢 **Looking good!** You're up $1.15 so far.
>
> Options:
> - **Hold** — Wait for more profit
> - **Close** — Lock in the gain now
> - **Add protection** — "set trailing stop"
>
> What would you like to do?

---

### Step 6: Close Position

**When user says "close", "exit", "take profit", etc:**

Execute close via MCP.

**Display result:**

> 🔒 **Closing position...**
>
> ✅ **Position Closed!**
>
> ```
> ┌─────────────────────────────────────────┐
> │  ETH LONG — CLOSED                      │
> ├─────────────────────────────────────────┤
> │                                         │
> │  Entry Price:     $3,195.50             │
> │  Exit Price:      $3,245.00             │
> │  Price Change:    +1.55%                │
> │                                         │
> │  Position Size:   $50.00                │
> │  Realized PnL:    +$4.65                │
> │  ROE:             +9.30%                │
> │                                         │
> │  Duration:        2h 15m                │
> │  Fees Paid:       $0.08                 │
> │                                         │
> └─────────────────────────────────────────┘
> ```

**Update state:**

```bash
"firstTrade": {
  "step": "POSITION_CLOSE",
  "tradeDetails": {
    ... (existing fields),
    "exitPrice": 3245.00,
    "pnl": 4.65,
    "pnlPercent": 9.30,
    "duration": "2h 15m",
    "closedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  }
}
```

---

### Step 7: Celebrate & Next Steps

**Immediately after close, display celebration:**

**If profitable:**

> 🎊 **CONGRATULATIONS ON YOUR FIRST TRADE!**
>
> ```
>  ╔═══════════════════════════════════════╗
>  ║                                       ║
>  ║   🏆  FIRST TRADE COMPLETE  🏆        ║
>  ║                                       ║
>  ║      You made: +$4.65 (+9.30%)       ║
>  ║                                       ║
>  ╚═══════════════════════════════════════╝
> ```
>
> **What you learned:**
> ✅ How to find opportunities using smart money data
> ✅ How to size and open a leveraged position
> ✅ How to monitor and close for profit
>
> **Your trading journey begins!** Here's what to explore next:
>
> | Skill | What it does | Command |
> |-------|--------------|---------|
> | 🛡️ **DSL** | Auto stop-loss protection | `npx skills add Senpi-ai/senpi-skills/dsl` |
> | 🐺 **WOLF** | Fully autonomous trading | `npx skills add Senpi-ai/senpi-skills/wolf` |
> | 📊 **Scanner** | Find best setups | `npx skills add Senpi-ai/senpi-skills/scanner` |
> | 🐋 **Whale Index** | Copy top traders | `npx skills add Senpi-ai/senpi-skills/whale-index` |
>
> **Quick commands:**
> - "find opportunities" — Scan for new trades
> - "show my portfolio" — See all positions
> - "open BTC short $100" — Manual trade
>
> Happy trading! 🚀

**If loss:**

> 📊 **FIRST TRADE COMPLETE**
>
> ```
>  ┌───────────────────────────────────────┐
>  │                                       │
>  │   📉  Trade Result: -$2.30 (-4.6%)   │
>  │                                       │
>  └───────────────────────────────────────┘
> ```
>
> **That's okay!** Losses are part of trading. Here's what matters:
>
> ✅ You kept the position small ($50)
> ✅ You learned the full trade cycle
> ✅ You controlled your exit (didn't let it run wild)
>
> **Pro tip:** Install **DSL** (Dynamic Stop Loss) to automatically protect your positions:
> ```bash
> npx skills add Senpi-ai/senpi-skills/dsl
> ```
>
> **Your trading journey begins!** Explore these skills:
>
> | Skill | What it does | Why it helps |
> |-------|--------------|--------------|
> | 🛡️ **DSL** | Auto stop-loss | Limits downside automatically |
> | 📊 **Scanner** | Find setups | Better entry timing |
> | 🐺 **WOLF** | Autonomous | Removes emotion from trading |
>
> Ready to try again? Say "find opportunities" to scan for new trades.

**Update state to READY:**

```bash
cat > ~/.config/senpi/state.json << EOF
{
  ... (preserve existing fields),
  "state": "READY",
  "firstTrade": {
    "started": true,
    "completed": true,
    "skipped": false,
    "step": "COMPLETE",
    "startedAt": "...",
    "completedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "tradeDetails": {
      "asset": "ETH",
      "direction": "LONG",
      "size": 50,
      "leverage": 3,
      "entryPrice": 3195.50,
      "exitPrice": 3245.00,
      "pnl": 4.65,
      "pnlPercent": 9.30,
      "profitable": true
    }
  }
}
EOF
```

---

## Skip Tutorial

**If user says "skip", "skip tutorial", "I know how to trade":**

> 👍 **Tutorial skipped!**
>
> You're all set to trade on your own. Quick reference:
>
> | Action | Command |
> |--------|---------|
> | Find setups | "find opportunities" |
> | Open trade | "open ETH long $100" |
> | Check positions | "show my portfolio" |
> | Close trade | "close my ETH position" |
> | Get help | "how do I trade?" |
>
> **Recommended skills:**
> ```bash
> npx skills add Senpi-ai/senpi-skills --list
> ```
>
> Happy trading! 🚀

**Update state:**

```bash
"state": "READY",
"firstTrade": {
  "started": false,
  "completed": false,
  "skipped": true,
  "skippedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
```

---

## Error Handling

### Insufficient Balance

If user tries to open position but balance < required margin:

> ⚠️ **Insufficient balance**
>
> You need at least $17 to open a $50 position with 3x leverage.
>
> Current balance: $10.00
>
> Options:
> 1. Fund your wallet with more USDC
> 2. Try a smaller position: "open ETH long $25"

### Position Open Failed

If MCP returns an error when opening:

> ❌ **Couldn't open position**
>
> Error: {error message from MCP}
>
> This might be due to:
> - Market is closed/paused
> - Price moved too fast (slippage)
> - Network issues
>
> Want to try again? Say "yes" to retry.

### Position Already Exists

If user tries to open same direction on same asset:

> ℹ️ **You already have an ETH LONG position**
>
> Current position: $50 @ $3,195.50
>
> Options:
> - "add to my position" — Increase size
> - "close my position" — Exit first
> - "open SOL long $50" — Trade different asset

---

## Resume Handling

If tutorial was interrupted (user closed chat, etc.), check state on next message:

```bash
STEP=$(cat ~/.config/senpi/state.json | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).firstTrade?.step || ''")

case $STEP in
  "INTRODUCTION")
    # User confirmed but didn't proceed - go to discovery
    ;;
  "DISCOVERY")
    # User saw opportunities - ask if ready to open
    ;;
  "POSITION_OPEN")
    # Position is open - check status and offer to close
    ;;
  "POSITION_CLOSE")
    # Just closed - show celebration
    ;;
esac
```

**Resume message:**

> 👋 Welcome back! You were in the middle of your first trade tutorial.
>
> [Show current status based on step]
>
> Want to continue? Say "yes" or "start over" to begin fresh.

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
```