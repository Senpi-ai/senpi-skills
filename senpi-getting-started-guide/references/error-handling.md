# First Trade Error Handling Reference

Consult this file when the first-trade tutorial hits errors during discovery, position open, monitor, or close.

---

## Insufficient Balance

If the user tries to open a position but balance is less than required margin:

**Display:**

> ⚠️ **Insufficient balance**
>
> You need at least $17 to open a $50 position with 3x leverage.
>
> Current balance: $10.00
>
> Options:
> 1. Fund your wallet with more USDC
> 2. Try a smaller position: "open ETH long $25"

Then pause the tutorial until the user funds or chooses a smaller size.

---

## Position Open Failed

If the MCP returns an error when opening the position:

**Display:**

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

Do not update `firstTrade.step` to `POSITION_OPEN`; leave at `DISCOVERY` or previous step so the user can retry.

---

## Position Already Exists

If the user tries to open the same direction on the same asset and already has a position:

**Display:**

> ℹ️ **You already have an ETH LONG position**
>
> Current position: $50 @ $3,195.50
>
> Options:
> - "add to my position" — Increase size
> - "close my position" — Exit first
> - "open SOL long $50" — Trade different asset

Resume the tutorial from monitor/close if they already have a position from this guide, or direct them to close first then continue.

---

## Recovery

- **MCP disconnected mid-tutorial:** Direct user to ensure MCP is configured (see agent-onboarding or platform-config). Do not update state until they reconnect.
- **User closes chat mid-flow:** On next message, read `firstTrade.step` from state and resume from the appropriate step. See [references/next-steps.md](next-steps.md) for resume handling.
