# 🐺 JACKAL v3.0.0 — The Smart Stalker. senpi_runtime_helpers.

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v2.0. NO thesis change.** Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. v2.0.9 contamination rule applied: `JACKAL_WALLET` replaces generic `STRATEGY_ADDRESS`.

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

The senpi-trading-runtime plugin won't bind its API port (`127.0.0.1:8787`) unless `plugins.entries.runtime` is present in `/data/.openclaw/openclaw.json`. Without that block the plugin logs `No plugin config found — skipping registration` and the producer daemon's `signal_post` calls fail with `[Errno 111] Connection refused`. Confirm or add:

```json
{
  "plugins": {
    "entries": {
      "runtime": {
        "enabled": true,
        "config": {
          "stateDir": "/data/.openclaw/senpi-state",
          "apiKey": "<your SENPI_AUTH_TOKEN>",
          "autoUpdate": { "enabled": false }
        }
      }
    }
  }
}
```

Restart the gateway after editing so the plugin re-registers:

```bash
openclaw gateway restart
sleep 10
curl -s -m 5 http://127.0.0.1:8787/state | head -c 200
# Expected: a JSON response with "success":true,"data":{"runtimes":[...]}
```

If `curl` returns Connection refused, the plugin still isn't registered — check `openclaw plugin list` shows the runtime entry as loaded and re-verify the JSON.

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Jackal v3.0.0

```bash
mkdir -p /data/workspace/skills/jackal-tracker/{config,scripts,state,references}
for f in scripts/jackal-producer.py scripts/jackal_config.py scripts/jackal_state.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/$f" \
    -o "/data/workspace/skills/jackal-tracker/$f"
done
```

### Step 3 — Required env vars

```bash
export JACKAL_WALLET=<your-jackal-wallet>
export SENPI_AUTH_TOKEN=...
export JACKAL_DECISION_MODEL=gemini-2.5-pro   # or any model the runtime supports
```

### Step 4 — Stop v2.x cron, start v3.0.0 daemon

```bash
openclaw cron list | grep jackal
openclaw cron delete <jackal-cron-id>

nohup python3 -u /data/workspace/skills/jackal-tracker/scripts/jackal-producer.py \
  > /tmp/jackal-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/jackal-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60s interval).

---

## Thesis

The fleet's first SECONDARY-SIGNAL agent. Observes top-performing Senpi perp traders, detects new entries by pool members, and lets an LLM decision prompt gate every entry. Not a passive mirror — an intelligent stalker where the runtime LLM gates each candidate against consensus + TA + funding context.

## Architecture

```
jackal-producer.py (60s daemon)    senpi-trading-runtime (v2)
  refresh pool (daily)              jackal_signals scanner
  diff positions vs last-seen   →   jackal_entry action (LLM-gated)
  enrich + push signal              position_tracker + DSL
                                    risk.guard_rails
```

## Key Settings

| Setting | Value |
|---|---|
| Pool | Top 25 by composite quality score (refreshed daily) |
| Pool filters | win_rate ≥ 0.50, roi_30d ≥ 10%, trader_age ≥ 14d |
| Entry age gate | < 10 min (producer-side freshness) |
| Entry decision | LLM-gated via `decision_prompt`, min_confidence 7 (model required via `$JACKAL_DECISION_MODEL`) |
| Max concurrent | 2 slots |
| Leverage | 5x default (runtime-enforced) |
| Margin per slot | $300 |
| Daily loss cap | 5% |
| Max entries/day | 4 |
| Consecutive losers pause | 3 → 120 min cooldown |
| Drawdown halt | 20% |
| Per-asset cooldown | 240 min (4h) |
| DSL hard_timeout | 72h |
| DSL Phase 1 max_loss | 22% |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
