# Quick Reference: Key Senpi API Tools

| Action | Tool |
|---|---|
| Create custom strategy | `strategy_create_custom_strategy` |
| Open position (market) | `create_position` (with `orderType: "MARKET"`) |
| Open position (limit) | `create_position` (with `orderType: "LIMIT"`, `limitPrice`) |
| Close position | `close_position` |
| Edit stop loss (sync to HL) | `edit_position` (with `stopLoss: { price, orderType: "LIMIT" }`) |
| Check positions/PnL | `strategy_get_clearinghouse_state` |
| Check strategy status | `strategy_get` |
| Check open orders/SLs | `strategy_get_open_orders` |
| Smart money data | `leaderboard_get_markets` |
| Top traders | `leaderboard_get_top` |
| Asset candles + data | `market_get_asset_data` |
| Market prices | `market_get_prices` |
| All instruments | `market_list_instruments` |
| Closed trade details | `execution_get_closed_position_details` |
| Fund strategy wallet | `strategy_top_up` |

**Never use:** `create_position` with `dryRun: true` — it executes real trades (known Senpi bug).

**Never use:** `strategy_close_strategy` to close individual positions — it closes the **entire strategy** (irreversible).

**All tools via mcporter:** `mcporter call senpi.{tool_name} key=value ...`
