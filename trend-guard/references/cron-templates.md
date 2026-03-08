# Trend Guard — Cron Templates

## Session & Model Tier

| Cron | Frequency | Session | Model Tier |
|------|-----------|---------|------------|
| Trend Batch | Hourly at :02 | isolated | Budget |

Runs at `:02` past every hour to ensure the 1h candle has closed and finalized before classification.

---

## Cron Template

```json
{
  "name": "Trend Guard — Hourly Batch",
  "schedule": { "kind": "cron", "cron": "2 * * * *" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "<Budget model ID for your provider>",
    "message": "Run `TREND_BATCH=1 python3 {SCRIPTS}/trend-guard.py`, parse JSON.\nIf status == ok → HEARTBEAT_OK.\nIf status == error → send error to Telegram ({TELEGRAM})."
  }
}
```

Replace placeholders:
- `{SCRIPTS}` — path to trend-guard scripts dir (e.g. `/data/workspace/skills/trend-guard/scripts`)
- `{TELEGRAM}` — your Telegram chat ID (e.g. `telegram:5183731261`)

### Model IDs by provider

| Provider | Budget Model |
|----------|-------------|
| `anthropic` | `anthropic/claude-haiku-4-5` |
| `openai` | `openai/gpt-4o-mini` |
| `google` | `google/gemini-2.0-flash-lite` |

---

## What the Cron Does

1. Agent receives mandate: run `trend-guard.py` in batch mode
2. Script calls `leaderboard_get_markets` — gets top 50 SM assets (1 MCP call)
3. Script calls `market_get_asset_data` for each asset — 24x 1h candles (up to 50 MCP calls)
4. Classifies trend (UP/DOWN/NEUTRAL) + strength score for each asset
5. Writes `trend-cache.json` to `$OPENCLAW_WORKSPACE/trend-cache.json` atomically
6. Outputs summary JSON → agent outputs `HEARTBEAT_OK` (success is silent)

Total runtime: ~60–90 seconds for 50 assets. Well within the 1h window.

---

## Cache File Location

Default: `$OPENCLAW_WORKSPACE/trend-cache.json`

Override: set `TREND_CACHE=/path/to/trend-cache.json` env var in the cron mandate
(same var must be set in the Emerging Movers cron if overriding).

---

## Mandate Text Pattern

The mandate is intentionally minimal — success is `HEARTBEAT_OK`, only errors need action:

```
Run `TREND_BATCH=1 python3 {SCRIPTS}/trend-guard.py`, parse JSON.
If status == ok → HEARTBEAT_OK.
If status == error → send error field to Telegram ({TELEGRAM}).
```

No judgment calls needed. Budget model is appropriate.
