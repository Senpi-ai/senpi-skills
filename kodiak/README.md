# 🐻 KODIAK v7.0.0 — SOL Alpha Hunter (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

SOL-only alpha hunter. Single-asset focus. Multi-factor scoring (SM consensus + trend structure + momentum + funding + OI + BTC correlation + RSI). Conviction-tiered leverage (5x default, 6x at score 11+, 7x apex at score 13+).

## What changed in v7.0.0

**Plumbing-only migration. NO thesis change.** v6.0.1's scoring tables, leverage tiers, asset cooldown, base-tech-score floor are all preserved verbatim.

- `kodiak-producer.py` and `kodiak_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` (PID-aliveness auto-recovery) instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires `senpi-trading-runtime >= 1.1.0`.
- `runtime.yaml` unchanged. `external_scanner.name: kodiak_signals` matches the producer's `client.push_signal(scanner=...)`.
- Per Rachin's review of Cheetah PR #209: dead fields stripped from `build_signal_payload`; `signal_type="KODIAK_SOL_THESIS"` passed explicitly.

## Thesis (preserved from v6.0.1)

SOL alpha hunter. Single-asset focus. v5.1 base-tech-score floor:

| Component | Threshold | Note |
|---|---|---|
| MIN_SCORE | 10 | Multi-factor composite |
| MIN_MOM_15M_PCT | 0.1% | 15-min momentum floor |
| MIN_TREND_STRENGTH_4H | 0.75 | 4 of 5 higher-lows / lower-highs |
| RSI_LONG_MAX | 72 | Don't chase overbought |
| RSI_SHORT_MIN | 28 | Don't chase oversold |
| ASSET_COOLDOWN_MINUTES | 240 | 4h post-emit cooldown |

Conviction-tiered leverage:

| Score | Leverage | Label |
|---|---|---|
| 13+ | 7x | apex |
| 11-12 | 6x | conviction |
| 10 | 5x | standard |

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

Skip if already pulled for Cheetah v7.0.0 / Turbine v3.2 / another v3 skill.

### Step 2 — Pull Kodiak v7.0.0

```bash
mkdir -p /data/workspace/skills/kodiak-strategy/{config,scripts,state,references}

for f in scripts/kodiak-producer.py scripts/kodiak_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/$f" \
    -o "/data/workspace/skills/kodiak-strategy/$f"
done
```

`runtime.yaml` is unchanged from v6.x — don't touch the existing runtime.

### Step 3 — Required env vars

```bash
export KODIAK_WALLET=<your-kodiak-wallet>       # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export KODIAK_DECISION_MODEL=gemini-3.1-pro-preview   # bare model name
```

Optional (sensible defaults):

| Env var | Default |
|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` |
| `SENPI_RUNTIME_API_PORT` | `8787` |
| `OPENCLAW_WORKSPACE` | `/data/workspace` |
| `KODIAK_MARGIN_PCT` | `0.20` |

### Step 4 — Stop the v6.x cron, start the v7.0.0 daemon

```bash
# Find and delete the v6.x cron
openclaw cron list | grep kodiak
openclaw cron delete <kodiak-cron-id>
```

Start the daemon (long-lived process, no cron):

```bash
# Option A — supervised by tini:
exec tini -- python3 -u /data/workspace/skills/kodiak-strategy/scripts/kodiak-producer.py

# Option B — nohup:
nohup python3 -u /data/workspace/skills/kodiak-strategy/scripts/kodiak-producer.py \
  > /tmp/kodiak-producer.log 2>&1 &
```

## Smoke test after deploy

```bash
tail -f /tmp/kodiak-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: every line shows `status=ok`. Kodiak's tick interval is 180s (3 min) so the first tick fires shortly after startup.

| Status | Meaning | What to do |
|---|---|---|
| `ok` | Tick succeeded | Healthy |
| `skipped_locked` | Lock collision | Confirm no inner `scanner_lock` was added |
| `error` | `fn` raised | Read the `error` field |
| `timeout` | `fn` took > 240s | Check MCP latency |

`daemon_self_terminated_no_runtime` is normal when the runtime is deleted.

## Key parameters

| Setting | Value |
|---|---|
| Asset | SOL only |
| Leverage | 5x / 6x / 7x (score-tiered) |
| Max positions | 1 |
| MIN_SCORE | 10 |
| Asset cooldown | 240 min |
| Margin per trade | 20% of account value |

## What NOT to do

- ❌ **Do NOT** add an openclaw cron — the daemon supervises itself
- ❌ **Do NOT** set `STRATEGY_ADDRESS` env var — banned per v2.0.9
- ❌ **Do NOT** delete the runtime — runtime.yaml unchanged from v6.x; orphan-position bug applies if positions are open

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
