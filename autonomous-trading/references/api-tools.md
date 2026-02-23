## Quick Reference: Key Senpi Tools

| Action | Tool |
|---|---|
| Create custom strategy | `strategy_create_custom_strategy` |
| Open position (market) | `create_position` (with `orderType: "MARKET"`) |
| Open position (limit) | `create_position` (with `orderType: "LIMIT"`, `limitPrice`) |
| Close position | `close_position` |
| Check positions/PnL | `strategy_get_clearinghouse_state` |
| Smart money data | `leaderboard_get_markets` |
| Closed trade details | `execution_get_closed_position_details` |

**Never use:** `create_position` with `dryRun: true` — it executes real trades (known Senpi bug).

