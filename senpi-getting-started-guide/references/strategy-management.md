# Strategy Management Reference

Use this for **strategy sizing**, **create**, **monitor**, and **close** steps of the first-trade tutorial. MCP tools: **`strategy_create`** (with chosen trader from discovery), **`strategy_get`**, **`strategy_get_clearinghouse_state`**, **`execution_get_open_position_details`**, **`strategy_close`**. State updates use `~/.config/senpi/state.json`.

---

## Strategy Sizing (Before Create)

Explain the mirror strategy to the user with a table. Default first-trade: $50 budget, chosen trader from Step 2 (Discovery).

**Display:**

> 📊 **Strategy Details:**
>
> | Parameter | Value | Explanation |
> |-----------|-------|-------------|
> | Mirrored trader | &lt;name or 0x…&gt; | Top trader from discovery |
> | Budget | $50 | Amount allocated to this strategy |
> | Type | Mirror | Copies this trader's positions |
>
> **Risk:** Your strategy will open and manage positions in line with the chosen trader. PnL depends on market moves and trader behavior.
>
> ⚠️ **You can close the strategy anytime** with "close my strategy" — funds return to your wallet.
>
> Ready to create this strategy? Say **"confirm"** to execute.

---

## Create Strategy

- Call MCP **`strategy_create`** with the chosen trader (use `recommendedTraderId` from state) and budget (e.g. $50). Pass the trader identifier and budget as required by the tool.
- On success, confirm the strategy was created and show strategy ID, budget, mirrored trader. Do not mention or display strategy status. Offer: "how's my strategy?", "close my strategy", "show my positions".

**State update after create:**

```json
"firstTrade": {
  "step": "STRATEGY_CREATED",
  "strategyCreatedAt": "<ISO8601 UTC>",
  "tradeDetails": {
    "strategyId": "<id returned by strategy_create>",
    "mirroredTraderId": "<recommendedTraderId from DISCOVERY step>",
    "budgetUsd": 50,
    "createdAt": "<ISO8601 UTC>"
  }
}
```

Preserve existing fields; merge only `firstTrade` and nested `tradeDetails`.

---

## Monitor Strategy

When the user asks "how's my strategy?" or similar, fetch data via MCP:

- **`strategy_get`** — Strategy metadata.
- **`strategy_get_clearinghouse_state`** — Account value, margin, positions for the strategy.
- **`execution_get_open_position_details`** — Per-position details if needed.

Do not mention or display raw strategy status. Display:

- Strategy value, margin used
- Open positions (asset, direction, size, entry, unrealized PnL, ROE)
- Duration

Then offer: **Hold**, **Close strategy**, or **Add protection** (e.g. other skills).

---

## Close Strategy

When the user says "close", "exit", "close my strategy", "take profit", etc., call MCP **`strategy_close`** with the strategy ID from `state.json` → `firstTrade.tradeDetails.strategyId`.

**Display:** Realized PnL, duration, fees if available. Do not mention or display strategy status.

**State update after close:**

```json
"firstTrade": {
  "step": "STRATEGY_CLOSE",
  "tradeDetails": {
    "...": "(existing fields)",
    "closedAt": "<ISO8601 UTC>",
    "pnl": 4.65,
    "pnlPercent": 9.30,
    "duration": "2h 15m"
  }
}
```

Preserve all other `firstTrade` and top-level fields when merging.

---

## Transition to READY

After celebration (see [references/next-steps.md](next-steps.md)), set state to `READY` and mark first trade complete:

```json
"state": "READY",
"firstTrade": {
  "started": true,
  "completed": true,
  "skipped": false,
  "step": "COMPLETE",
  "startedAt": "<ISO8601>",
  "completedAt": "<ISO8601>",
  "tradeDetails": { "...": "full object with strategyId, pnl, profitable: true/false" }
}
```

Preserve all other top-level fields in `state.json` when writing.
