# TOOLS.md — Local Notes

This is your cheat sheet. Environment-specific stuff that doesn't belong in skills.

## Senpi MCP

- **Server name:** `senpi`
- **Auth:** JWT token (configured at setup)
- **Connection:** Pre-configured via OpenClaw, no manual setup needed
- The MCP server provides its own instructions and tool descriptions — read them at runtime
- **On every session startup:** Always call `read_senpi_guide` with `uri=senpi://guides/senpi-overview` to load the Senpi platform overview before doing anything else with Senpi tools. **Do this silently — no text output when calling it or after it returns.**

## senpi-agent-tracker

- **Endpoint:** `https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server`
- **Protocol:** JSON-RPC 2.0 over HTTP POST
- **Auth:** None required
- **Data refresh:** Every 15 minutes
- Use this to show live performance stats when presenting the strategy catalog.

Call pattern:
```bash
curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"TOOL_NAME","arguments":{...}}}'
```

| Tool | Key arguments | Returns |
|------|--------------|---------|
| `list_strategies` | `active_only?` | All tracked strategies with slugs and active status |
| `get_performance` | `slug` | PnL, ROI, volume, trades, equity for one strategy |
| `get_pnl_history` | `slug`, `hours` (max 720) | Time-series PnL snapshots up to 30 days |
| `compare_strategies` | `slugs[]` (2–5) | Side-by-side PnL/ROI/volume/trades |
| `get_leaderboard` | `sort_by` (roi/pnl/volume), `limit` (max 15) | Ranked leaderboard of all active strategies |
| `install_skill` | `slug` | Installation instructions for a strategy |

**Slug mapping:** `catalog.json` entries carry a `tracker_slug` field for cross-referencing. Strategies without `tracker_slug` (e.g. Wolf) are not yet in the tracker.

## Telegram

- **Numeric chat IDs only** — `@username` does NOT work
- Target format: `telegram:<chat_id>` (e.g. `telegram:123456789`)
- Check `USER.md` for the user's chat ID

## Shell tools

- `rg` (ripgrep) — recursive by default, do NOT pass `-R` or `-r`
- `node` — use `node -e` for JSON processing
- `python3` — available for scripting
- `grep` — fallback if needed
- **NOT installed:** `jq` — use `node -e` instead

## Cron (Gateway scheduler)

### cron.add

Put `sessionTarget` and `schedule` at the **top level** of the params, not inside `payload`.

**Schedule types** (pick one `kind`):
- `"at"` — one-shot: `{ "kind": "at", "at": "2026-02-01T16:00:00Z" }`
- `"every"` — recurring interval: `{ "kind": "every", "everyMs": 1800000 }` (value in ms)
- `"cron"` — cron expression: `{ "kind": "cron", "expr": "0 7 * * *", "tz": "UTC" }`

Do NOT use `minutes`, `seconds`, or any other property — only `at`, `everyMs`, or `expr`.

**Payload** — use `message` (NOT `text`):
- Main session: `{ "kind": "systemEvent", "message": "Your prompt" }`
- Isolated session: `{ "kind": "agentTurn", "message": "Your prompt" }`

**Example** (one-shot main-session):
```json
{ "name": "Reminder", "schedule": { "kind": "at", "at": "2026-02-01T16:00:00Z" }, "sessionTarget": "main", "wakeMode": "now", "payload": { "kind": "systemEvent", "message": "Reminder text" }, "deleteAfterRun": true }
```

**Example** (every 30 min, isolated):
```json
{ "name": "Healthcheck", "schedule": { "kind": "every", "everyMs": 1800000 }, "sessionTarget": "isolated", "payload": { "kind": "agentTurn", "message": "Run health check" } }
```

### cron.remove

To delete a cron job, use `cron.remove` (NOT `cron.delete`).
Param: `{ "name": "JobName" }`

### cron.list

List all active cron jobs. No params needed.

## Token Refresh

If Senpi calls fail with an auth error, the token has expired. Tell the user to provide a fresh token, then run:
```bash
curl -s -X POST http://127.0.0.1:8080/setup/api/senpi-token \
  -H "Content-Type: application/json" \
  -d '{"token": "NEW_TOKEN"}'
```
This updates the config and restarts the MCP connection.