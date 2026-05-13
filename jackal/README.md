# 🐺 Jackal — The Smart Stalker

The fleet's first SECONDARY-SIGNAL agent: stalk the top Senpi perp traders, copy their fresh entries only when an LLM gate confirms.

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Jackal does not generate its own signals — it watches other people's. Once a day, the producer refreshes a pool of the top 25 Senpi perp traders by composite quality score (win_rate ≥ 0.50, roi_30d ≥ 10%, trader_age ≥ 14d). Every 60 seconds it diffs each pool member's open positions against the last-seen snapshot. A new position appearing on a top trader is a candidate signal.

It is deliberately not a passive copy-trader. Every candidate goes through an LLM `decision_prompt` gate (min_confidence 7) that weighs consensus from the rest of the pool, current TA, funding context, and freshness (only entries < 10 min old qualify). That gate is the edge — passive mirrors blindly inherit the leader's bad days; Jackal filters down to the trades that survive a second opinion. Two concurrent slots, 5x leverage, with the runtime owning daily caps, cooldowns, drawdown halt, and DSL exits.

## Key parameters

| Parameter | Value |
|---|---|
| Pool | Top 25 Senpi traders by composite quality score (refreshed daily) |
| Pool filters | win_rate ≥ 0.50, roi_30d ≥ 10%, trader_age ≥ 14d |
| Tick interval | 60 s |
| Entry age gate | < 10 min (producer-side freshness) |
| Entry decision | LLM-gated via `decision_prompt`, min_confidence 7 (model via `$JACKAL_DECISION_MODEL`) |
| Max concurrent | 2 slots |
| Margin per slot | $300 |
| Leverage | 5x default (runtime-enforced) |
| Max entries/day | 4 |
| Daily loss cap | 5% |
| Consecutive losers pause | 3 → 120 min cooldown |
| Per-asset cooldown | 240 min (4h) |
| Drawdown halt | 20% |
| DSL hard_timeout | 72 h |
| DSL Phase 1 max_loss | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **trader-follower / hot-streak** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: cached daily `discovery_get_top_traders` (pool refresh) + per-tick `discovery_get_trader_state` (diff against last-seen).

## Architecture

```
jackal-producer.py (60s daemon)    senpi-trading-runtime
  refresh pool (daily)              jackal_signals scanner
  diff positions vs last-seen   →   jackal_entry action (LLM-gated)
  enrich + push signal              position_tracker + DSL
                                    risk.guard_rails
```

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanner + action + DSL + risk gates) |
| `scripts/jackal-producer.py` | Long-lived daemon (60 s tick) |
| `scripts/jackal_config.py` | SDK probe + `SenpiClient` wrapper |
| `scripts/jackal_state.py` | Last-seen position-diff state |

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

### Step 2 — Pull Jackal

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
export JACKAL_DECISION_MODEL=<your-preferred-model>   # or any model the runtime supports
```

### Step 4 — Stop legacy cron, start daemon

```bash
openclaw cron list | grep jackal
openclaw cron delete <jackal-cron-id>

nohup python3 -u /data/workspace/skills/jackal-tracker/scripts/jackal-producer.py \
  > /tmp/jackal-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/jackal-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60 s interval).

## Changelog

- **v3.0.0** — Plumbing-only migration from v2.0 (NO thesis change). Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. v2.0.9 contamination rule applied: `JACKAL_WALLET` replaces generic `STRATEGY_ADDRESS`.
- **v2.0** — Original SECONDARY-SIGNAL architecture (top-trader pool diff + LLM gate).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
