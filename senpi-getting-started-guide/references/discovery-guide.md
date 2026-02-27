# Discovery Guide Reference

Use this when running **Step 2: Discovery** of the first-trade tutorial. It describes how to use MCP for discovery and what to show the user.

---

## MCP Usage

- Use MCP tools to fetch top traders and their current positions (e.g. discovery-related tools).
- Prefer liquid assets (ETH, BTC, SOL) for the first trade so entry/exit are smooth.

---

## What to Look For

- **Conviction** — Multiple top traders in the same direction on the same asset
- **Liquid assets** — ETH, BTC, SOL preferred for first trade
- **Recent entries** — Positions opened in the last few hours

---

## Display Template

After fetching data, show the user something like:

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

---

## State Update

After discovery, update `firstTrade` in state:

```json
"firstTrade": {
  "step": "DISCOVERY",
  "recommendedAsset": "ETH",
  "recommendedDirection": "LONG"
}
```

Preserve other existing fields in `state.json` when merging.
