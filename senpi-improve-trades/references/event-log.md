# Telemetry event log — shapes the engine reads (authoritative, from runtime `main`)

Sourced from the runtime code (`src/cli/senpi-commands.ts`, `src/index.ts` RPC handlers,
`src/telemetry/event-store.ts`, `src/utils/event-catalog.ts`) — shipped by runtime **#192** and
documented by skills **#393**. The local event log is a per-strategy on-disk ring (recent-only,
2-file rotation), read via the gateway CLI **with no collector/keys**. Older runtime builds don't
expose the RPCs → the CLI returns `unknown method: senpi.getEvents`; the engine must **fail open to
discovery** in that case.

## `openclaw senpi events --runtime <id> --json`  (also `--address <wallet>` · `--name <event>` · `--since <iso|ms>` · `--asset <sym>` · `-l <n>`)
```json
{ "ok": true, "address": "0x…",
  "entries": [
    { "name": "position.opened", "level": "info", "body": "…", "ts": 1704067200000,
      "attrs": { "senpi.position.id": "pos-…", "senpi.asset": "BTC", "senpi.dex": "HYPE",
                 "senpi.position.direction": "long", "senpi.position.entry_price": 42000.5,
                 "senpi.position.size": 0.05, "senpi.position.leverage": 2.5, "senpi.position.margin": 100 } } ] }
```
- `ts` = **epoch ms**. `attrs` keys are dotted `senpi.*`. Every event carries `senpi.asset` (or `senpi.signal.asset`) and, on lifecycle events, `senpi.position.id` (threads the trade).

## `openclaw senpi explain <asset> --runtime <id> --json`
Same `{ok, asset, entries[]}` — the asset's lifecycle events, oldest-first, threaded by `senpi.position.id`.

## Trade-lifecycle event names → attribute keys (from `event-catalog.ts`)
| Event `name` | Key attrs (`attrs["…"]`) |
|---|---|
| `position.opened` | `senpi.position.id`, `senpi.asset`, `senpi.position.direction`, `.entry_price`, `.size`, `.leverage`, `.margin` |
| `position.closed` | `senpi.position.id`, `senpi.asset`, `.direction`, `senpi.position.close_reason`, `.closed_price`, `.closed_size`, `.roe`, `senpi.order.id` |
| `dsl.created` | `senpi.position.id`, `senpi.dsl.preset`, `senpi.dsl.phase`, `.entry_price` |
| `dsl.closed` | `senpi.position.id`, `senpi.asset`, **`senpi.dsl.close_reason`** (`tier_breach`/`max_retrace`/`manual`/…), `senpi.dsl.current_roe`, `senpi.dsl.phase`, `senpi.dsl.tier_index` — **no order_id** (DSL owns the position) |
| `dsl.tier_advanced` | `senpi.position.id`, `senpi.dsl.tier_index`, `.current_roe`, `.floor_price`, `.new_floor_price` |
| `order.filled` | `senpi.order.id`, `senpi.position.id`, `senpi.asset`, `senpi.order.fill_price`, `.fill_size`, `.execution_as_maker` |
| `order.failed` | `senpi.order.id`, `senpi.asset`, `senpi.order.reason`, `.error_name` |
| **`signal.outcome`** | `senpi.signal.asset`, `senpi.signal.direction`, `senpi.signal.score`, **`senpi.outcome.result`** (`accepted`/`rejected`/`blocked`/`error`), **`senpi.outcome.reason_code`** (`submitted`/`no_slots`/`risk_gate_leverage`/`risk_gate_max_drawdown`/`risk_gate_notional`/`no_margin`/`signal_not_ready`/`asset_banned`), `senpi.outcome.margin_pct`, `.leverage`, `.notional_value`, `senpi.position.id?`, `senpi.order.id?` |

**Exit reason (fixes `exit_reason: UNKNOWN`)** = the `close_reason` on `dsl.closed` (or `position.closed`) for a position id. Native, no reconstruction.
**"What did I miss"** = `signal.outcome` where `senpi.outcome.result ∈ {rejected, blocked}` — the signals that never became trades, with the granular `reason_code`.

## `openclaw senpi audit --runtime <id> --json`  (`--tool` · `--action-type read|create|update|delete` · `--success` · `--since/--until` · `-l`)
```json
{ "ok": true, "strategyId": "…", "result": { "count": 1, "total": 42, "has_more": true,
  "entries": [ { "id": "…", "request_id": "…", "timestamp": "2026-06-30T14:02:11Z", "completed_at": "…",
    "duration_ms": 5000, "tool": "create_position", "action_type": "create",
    "ai_reasoning": "Risk gate passed; signal confidence 92%", "success": true,
    "error_code": null, "error_message": null, "affected_resources": [ { "type": "position", "id": "0x…" } ] } ] } }
```
- `timestamp` = **ISO 8601** (not ms). `ai_reasoning` = the decision "why" (null for read tools).

## Join to the authoritative ledger
`order.filled` / `position.closed` carry `senpi.order.id` → `execution_get_closed_position_details({closedOrderId})` → realized PnL + **fees** + funding. `dsl.closed` has no order_id (join by `position.id` + the paired `order.filled`).

## Engine consequence
- Event-log CLI is per-**runtime**; map a strategy wallet → its runtime id via the registry (`installed_runtimes.json`, already read for mandates). Closed strategies aren't registered → no ring → those trades stay discovery-only (`exit_reason: UNKNOWN`, honest).
- Every event-log read is shelled out (`openclaw … --json`) and **fail-open**: any non-zero exit / `unknown method` / parse error → skip enrichment, discovery still lists the trades.
