# First Trade Error Handling Reference

Consult this file when the first-trade tutorial hits errors. **All "Display" blocks are user-facing:** use only plain language; do not show raw errors, state names, file paths, or MCP/tool names.

---

## Insufficient Balance

If the user tries to create a strategy but balance is less than the required amount (or wallet has less than $100 at tutorial start, redirect to funding — at least $100 USDC is required to start the first-trade tutorial):

**Display (user-friendly only):**

> ⚠️ **Not enough balance**
>
> You need at least $50 to create this strategy.
>
> Current balance: $10.00
>
> Add more USDC to your wallet, or we can try a smaller amount when supported.

Then pause the tutorial until the user funds or chooses a smaller budget.

---

## strategy_create Failed

If the MCP returns an error when creating the strategy (e.g. `strategy_create` fails):

**Display (user-friendly only; do not show raw MCP error):**

> ❌ **Couldn't create your strategy**
>
> Something went wrong. Common causes: the trader isn't available to mirror right now, or there was a temporary network issue.
>
> Want to try again? Say **"yes"** to retry, or **"pick another trader"** to choose someone else.

Do not update `firstTrade.step` to `STRATEGY_CREATED`; leave at `DISCOVERY` or previous step so the user can retry.

---

## strategy_close Failed

If the MCP returns an error when closing the strategy:

**Display (user-friendly only; do not show raw MCP error):**

> ❌ **Couldn't close your strategy**
>
> Something went wrong. You can try again: say **"close my strategy"**. If it keeps failing, check your connection or try again in a moment.

Leave `firstTrade.step` at `STRATEGY_CREATED` until close succeeds.

---

## Strategy Already Exists / Duplicate

If the user already has an active strategy from this tutorial (or tries to create a second mirror with the same scope):

**Display (user-friendly only; do not show strategy ID unless user asks):**

> ℹ️ **You already have a strategy running**
>
> From this tutorial. You can ask **"how's my strategy?"** to check it, or **"close my strategy"** to close it and finish. To mirror a different trader later, close this one first.

Resume the tutorial from monitor/close using the existing strategy.

---

## Recovery

- **MCP disconnected mid-tutorial:** Tell the user in plain language: "Your connection was lost. Please check that Senpi is set up and try again." Do not mention MCP or state. Do not update state until they reconnect.
- **User closes chat mid-flow:** On next message, read firstTrade.step from state and resume from the appropriate step. See [references/next-steps.md](references/next-steps.md). Use only user-friendly resume messages.
