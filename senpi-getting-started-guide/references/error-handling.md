# First Trade Error Handling Reference

Consult this file when the first-trade tutorial hits errors during discovery, strategy create, monitor, or strategy close.

---

## Insufficient Balance

If the user tries to create a strategy but balance is less than the required amount (or wallet has less than $100 at tutorial start, redirect to funding — at least $100 USDC is required to start the first-trade tutorial):

**Display:**

> ⚠️ **Insufficient balance**
>
> You need at least $50 to create a mirror strategy (or reduce the budget).
>
> Current balance: $10.00
>
> Options:
> 1. Fund your wallet with more USDC
> 2. Use a smaller budget when the tool supports it

Then pause the tutorial until the user funds or chooses a smaller budget.

---

## strategy_create Failed

If the MCP returns an error when creating the strategy (e.g. `strategy_create` fails):

**Display:**

> ❌ **Couldn't create strategy**
>
> Error: {error message from MCP}
>
> This might be due to:
> - Trader not available for mirroring
> - Market/network issues
> - Invalid trader id or budget
>
> Want to try again? Say "yes" to retry, or "pick another trader" to go back to discovery.

Do not update `firstTrade.step` to `STRATEGY_CREATED`; leave at `DISCOVERY` or previous step so the user can retry.

---

## strategy_close Failed

If the MCP returns an error when closing the strategy:

**Display:**

> ❌ **Couldn't close strategy**
>
> Error: {error message from MCP}
>
> You can try again: "close my strategy". If it keeps failing, check your portfolio or reconnect MCP.

Leave `firstTrade.step` at `STRATEGY_CREATED` until close succeeds.

---

## Strategy Already Exists / Duplicate

If the user already has an active strategy from this tutorial (or tries to create a second mirror with the same scope):

**Display:**

> ℹ️ **You already have an active strategy from this tutorial**
>
> Strategy ID: {strategyId from state}
>
> Options:
> - "how's my strategy?" — Check status
> - "close my strategy" — Close it and finish the tutorial
> - To mirror another trader later, close this one first or create a new strategy with a different name/budget

Resume the tutorial from monitor/close using the existing strategy.

---

## Recovery

- **MCP disconnected mid-tutorial:** Direct user to ensure MCP is configured (see agent-onboarding or platform-config). Do not update state until they reconnect.
- **User closes chat mid-flow:** On next message, read `firstTrade.step` from state and resume from the appropriate step. See [references/next-steps.md](references/next-steps.md) for resume handling.
