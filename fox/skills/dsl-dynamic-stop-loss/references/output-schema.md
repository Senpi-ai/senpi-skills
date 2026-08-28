# Output JSON Schema

The script prints a single JSON line to stdout on each run. The agent reads this to determine what action to take.

## Example Output

```json
{
  "status": "active",
  "asset": "HYPE",
  "direction": "LONG",
  "price": 29.26,
  "upnl": 734.37,
  "upnl_pct": 13.46,
  "phase": 2,
  "hw": 29.28,
  "floor": 29.01,
  "trailing_floor": 28.84,
  "tier_floor": 29.01,
  "tier_name": "Tier 1 (10%→lock 5%)",
  "locked_profit": 272.77,
  "retrace_pct": 0.08,
  "breach_count": 0,
  "breaches_needed": 2,
  "breached": false,
  "should_close": false,
  "closed": false,
  "close_result": null,
  "time": "2026-02-20T15:35:06Z",
  "tier_changed": false,
  "previous_tier": null,
  "elapsed_minutes": 13,
  "distance_to_next_tier_pct": 6.54,
  "pending_close": false,
  "consecutive_failures": 0,
  "sl_synced": false,
  "sl_initial_sync": false,
  "sl_order_id": 12345678
}
```

## Status Values

| Status | Meaning | Agent action |
|--------|---------|-------------|
| `"active"` | Normal operation | Silent (or report on schedule) |
| `"inactive"` | Position closed or deactivated | Disable cron, alert user |
| `"pending_close"` | Close failed, will retry next tick | Alert user — "DSL trying to close, API issue" |
| `"error"` | Price fetch failed | Log; alert if `consecutive_failures >= 3` |

## All Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"active"`, `"inactive"`, `"pending_close"`, or `"error"` |
| `asset` | string | Ticker symbol |
| `direction` | string | `"LONG"` or `"SHORT"` |
| `price` | float | Current price |
| `upnl` | float | Unrealized PnL in USD |
| `upnl_pct` | float | Unrealized PnL as ROE % |
| `phase` | int | Current phase (1 or 2) |
| `hw` | float | High water mark price |
| `floor` | float | Current effective floor price |
| `trailing_floor` | float | Floor from high water × retrace |
| `tier_floor` | float/null | Floor from current tier lock |
| `tier_name` | string | Human-readable tier label |
| `locked_profit` | float | USD locked by current tier floor |
| `retrace_pct` | float | Current retrace from high water as % |
| `breach_count` | int | Consecutive breaches so far |
| `breaches_needed` | int | Breaches required to close |
| `breached` | bool | True if price is below floor this tick |
| `should_close` | bool | True if breach threshold met |
| `closed` | bool | True if position was closed this tick |
| `close_result` | string/null | Close API response or error message |
| `time` | string | ISO 8601 timestamp of this check |

### v4 Fields

| Field | Type | Description |
|-------|------|-------------|
| `tier_changed` | bool | True if tier upgraded this tick |
| `previous_tier` | string/null | Previous tier name (only when `tier_changed=true`) |
| `elapsed_minutes` | int | Minutes since position was created |
| `distance_to_next_tier_pct` | float/null | ROE % remaining to next tier. Null at max tier. |
| `pending_close` | bool | True if close was attempted and failed |
| `consecutive_failures` | int | Number of consecutive price fetch failures |

### v5 Hyperliquid SL Fields

| Field | Type | Description |
|-------|------|-------------|
| `sl_synced` | bool | True if the SL was synced to Hyperliquid this tick (via `edit_position`) |
| `sl_initial_sync` | bool | True if this tick was the first sync for this position (e.g. after upgrading to the Hyperliquid SL flow); state now has `slOrderId` and the position is protected by HL native SL |
| `sl_order_id` | int/null | Hyperliquid order ID of the current SL for this position; null if not yet set or unavailable |

## Agent Response Logic

```
if closed == true:
  → alert user "🔴 Position closed by DSL"
  → disable this cron job

if pending_close == true:
  → alert user "⚠️ DSL close failed, retrying next tick"

if status == "error":
  → log the error
  → if consecutive_failures >= 3: alert user about API issues

if tier_changed == true:
  → notify user with tier details and locked profit

if breached == true (but not closing yet):
  → alert "⚠️ BREACH {breach_count}/{breaches_needed}"

if distance_to_next_tier_pct < 2:
  → optionally notify "approaching next tier lock"

if sl_initial_sync == true:
  → optionally notify user "Trailing stop is now synced to Hyperliquid for this position; it will close at the stop level even between cron runs."

otherwise:
  → silent (HEARTBEAT_OK)
```

## dsl-cleanup.py Output (v5)

Strategy-level cleanup script prints a single JSON line:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"cleaned"` (directory deleted) or `"blocked"` (active positions remain) |
| `strategy_id` | string | Strategy UUID |
| `positions_deleted` | int | Number of state files removed (only when status=cleaned) |
| `blocked_by_active` | string[] | Assets still active (only when status=blocked) |
| `time` | string | ISO 8601 timestamp |

See [references/cleanup.md](cleanup.md) for agent responsibilities and usage.
