# 🐻 KODIAK — SOL Alpha Hunter

Single-asset alpha hunter for SOL. Multi-factor scoring (SM consensus + trend structure + momentum + funding + OI + BTC correlation + RSI) with conviction-tiered leverage.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Kodiak hunts SOL directional moves where multi-factor confluence is unambiguous. Entry requires base-tech-score floor v5.1 thresholds: 15-min momentum ≥ 0.1%, 4h trend strength ≥ 0.75 (4 of 5 higher-lows / lower-highs), RSI not extreme (LONG max 72, SHORT min 28), and a composite score ≥ 10 from Smart Money consensus, trend structure, momentum, funding, OI, BTC correlation, and RSI.

Kodiak is the founding member of the Kodiak family — its SOL thesis was ported to BTC (Grizzly), ETH (Polar), HYPE (Wolverine), and BRENTOIL (Dire). Leverage is conviction-tiered (5x standard / 6x conviction / 7x apex) so size scales with score. A 4h per-asset cooldown after each emit prevents re-firing on the same setup, and exits are owned by the DSL — no Python execution code is in the producer.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | SOL (single-asset) |
| Tick interval | 180s |
| MIN_SCORE | 10 |
| MIN_MOM_15M_PCT | 0.1% |
| MIN_TREND_STRENGTH_4H | 0.75 |
| RSI_LONG_MAX / RSI_SHORT_MIN | 72 / 28 |
| Leverage tiers | 5x (score 10) / 6x (11-12) / 7x apex (13+) |
| Max positions | 1 |
| Per-asset cooldown | 240 min (4h) |
| Margin per trade | 20% of account value |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **Single-asset alpha hunter (Kodiak family)** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `market_get_asset_data` for SOL.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (external_scanner, risk guard rails, DSL) |
| `scripts/kodiak-producer.py` | Long-lived daemon emitting SOL entry signals |
| `scripts/kodiak_config.py` | SDK probe + SenpiClient wrapper |
| `config/kodiak-config.json` | Operator-tunable defaults |

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

Skip if the senpi-trading-runtime skill is already installed on this host.

### Step 2 — Pull Kodiak

```bash
mkdir -p /data/workspace/skills/kodiak-strategy/{config,scripts,state,references}

for f in scripts/kodiak-producer.py scripts/kodiak_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/$f" \
    -o "/data/workspace/skills/kodiak-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export KODIAK_WALLET=<your-kodiak-wallet>       # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export KODIAK_DECISION_MODEL=<your-preferred-model>   # bare model name
```

Optional (sensible defaults):

| Env var | Default |
|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` |
| `SENPI_RUNTIME_API_PORT` | `8787` |
| `OPENCLAW_WORKSPACE` | `/data/workspace` |
| `KODIAK_MARGIN_PCT` | `0.20` |

### Step 4 — Start the daemon

```bash
# Stop any prior cron
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

## Verification

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

## What NOT to do

- Do NOT add an openclaw cron — the daemon supervises itself
- Do NOT set `STRATEGY_ADDRESS` env var — banned per v2.0.9
- Do NOT delete the runtime — orphan-position bug applies if positions are open

## Changelog

### v7.0.0 — `senpi_runtime_helpers` migration

Plumbing-only migration. NO thesis change. v6.0.1's scoring tables, leverage tiers, asset cooldown, base-tech-score floor all preserved verbatim.

- `kodiak-producer.py` and `kodiak_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` (PID-aliveness auto-recovery) instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` unchanged. `external_scanner.name: kodiak_signals` matches the producer's `client.push_signal(scanner=...)`.
- Dead fields stripped from `build_signal_payload`; `signal_type="KODIAK_SOL_THESIS"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged (avoids relying on the runtime YAML's `defaultSignalType` fallback).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
