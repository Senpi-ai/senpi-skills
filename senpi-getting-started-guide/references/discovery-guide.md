# Discovery Guide Reference

Use this when running **Step 2: Discovery** of the first-trade tutorial. This step is about **finding top traders and recommending one to mirror** (not opening individual asset/direction positions). Use **`discovery_get_top_traders`**; optionally **`discovery_get_trader_state`** or **`discovery_get_trader_history`** to confirm a trader has open positions. **Only present as options traders that currently have open positions** — do not offer traders with no open positions. **Show the user only user-friendly copy;** do not mention state, step names, or MCP/tool names.

---

## MCP Usage

- **`discovery_get_top_traders`** — Fetch top traders (e.g. by PnL, win rate, or consistency).
- **Filter to traders with open positions** — Only show options for traders that **currently have open positions**. Use **`discovery_get_trader_state`** (or equivalent) to confirm open positions before including a trader in the list. Do not offer traders with no open positions.
- Optionally **`discovery_get_trader_history`** — Get extra detail for a specific trader before recommending.
- Prefer traders whose positions are in liquid assets (ETH, BTC, SOL) so the mirror strategy has smooth entry/exit.

---

## What to Look For

- **Open positions** — Only include traders that **currently have open positions** (so the mirror strategy has something to copy).
- **Strong performance** — Among those, prefer top traders by PnL, win rate, or consistency.
- **Liquid assets** — Traders with positions in ETH, BTC, SOL are better for a first mirror strategy.
- **Recent activity** — Traders with recent activity so the mirror has clear positions to follow.

---

## Display Template

After fetching with **`discovery_get_top_traders`** and **filtering to traders with open positions**, show the user something like:

> 🔍 **Scanning top traders...**
>
> Here are some of the best performers **with open positions** right now:
>
> **Top Traders:**
>
> | Rank | Trader | PnL (7d) | Win Rate | Open Positions |
> |------|--------|----------|----------|----------------|
> | 1    | 0xABC… | +$2,450  | 68%      | ETH LONG, BTC SHORT |
> | 2    | 0xDEF… | +$1,890  | 62%      | SOL LONG |
> | 3    | 0x123… | +$1,200  | 58%      | ETH SHORT |
>
> 💡 **Recommendation:** For your first trade I suggest **mirroring the #1 trader** (0xABC…):
> - Strong recent PnL and win rate
> - Liquid markets (ETH, BTC) for easy entry/exit
> - Good for learning how copy trading works
>
> Want to create a **$100 mirror strategy** copying this trader? Say **"confirm"** to proceed, or tell me if you’d prefer a different trader.

Store the **chosen trader’s identifier** (e.g. address or id from the API) for use in **`strategy_create`** in the next step. **Minimum budget for the first strategy is $100** — do not suggest $50 or less.

---

## State Update

After discovery, update `firstTrade` in state with the **recommended trader** (for use in strategy_create):

```json
"firstTrade": {
  "step": "DISCOVERY",
  "recommendedTraderId": "<trader id or address from discovery_get_top_traders>",
  "recommendedTraderName": "<optional display name or truncated address>"
}
```

Preserve other existing fields in `state.json` when merging. Do **not** use `recommendedAsset` or `recommendedDirection` — this flow mirrors a **trader**, not a single asset/direction position.
