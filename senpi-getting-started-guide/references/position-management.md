# Position Management Reference

Use this for **position sizing**, **open**, **monitor**, and **close** steps of the first-trade tutorial. State updates use `~/.config/senpi/state.json`.

---

## Position Sizing (Before Open)

Explain the position to the user with a table. Default first-trade: $50, 3x leverage.

**Display:**

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

## Open Position

- Use the appropriate MCP tool to create the position: asset (e.g. ETH), direction (e.g. LONG), size $50 USD, leverage 3x.
- On success, display entry price, position size, leverage, margin used, and strategy ID. Offer: "how's my position?", "close my position", "set stop loss at $X".

**State update after open:**

```json
"firstTrade": {
  "step": "POSITION_OPEN",
  "positionOpenedAt": "<ISO8601 UTC>",
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

Preserve existing fields; merge only `firstTrade` and nested `tradeDetails`.

---

## Monitor Position

When the user asks "how's my position?" or similar, fetch position status via MCP and display:

- Entry price, current price, % change
- Position size, unrealized PnL, ROE
- Duration

Then offer: **Hold**, **Close**, or **Add protection** (e.g. trailing stop).

---

## Close Position

When the user says "close", "exit", "take profit", etc., execute close via MCP.

**Display:** Entry price, exit price, price change, position size, realized PnL, ROE, duration, fees.

**State update after close:**

```json
"firstTrade": {
  "step": "POSITION_CLOSE",
  "tradeDetails": {
    "...": "(existing fields)",
    "exitPrice": 3245.00,
    "pnl": 4.65,
    "pnlPercent": 9.30,
    "duration": "2h 15m",
    "closedAt": "<ISO8601 UTC>"
  }
}
```

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
  "tradeDetails": { "...": "full object with profitable: true/false" }
}
```

Preserve all other top-level fields in `state.json` when writing.
