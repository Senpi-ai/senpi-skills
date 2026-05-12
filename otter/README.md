# 🦦 OTTER v2.0.0 — Open Interest Velocity Hunter. senpi_runtime_helpers.

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v1.0. NO thesis change. NO scoring change.** Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. Fleet-fix #214 applied (no `wallet=`/`scanner=` daemon kwargs).

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

### Step 1 — Pull the helpers package (one-time per host)

> **Note:** The `_helpers/senpi_runtime_helpers/` package currently lives only on the `helper-mcp-envelope-aligned` branch. Pull from there.

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers
for f in __init__.py _config.py _logging.py cache.py client.py \
         daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/helper-mcp-envelope-aligned/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

### Step 2 — Pull Otter v2.0.0

```bash
mkdir -p /data/workspace/skills/otter-strategy/{config,scripts,state,references}
for f in scripts/otter-producer.py scripts/otter_config.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/otter/$f" \
    -o "/data/workspace/skills/otter-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export OTTER_WALLET=<your-otter-wallet>
export SENPI_AUTH_TOKEN=...
export OTTER_DECISION_MODEL=gemini-2.5-pro   # or any model the runtime supports
```

### Step 4 — Stop v1.x cron, start v2.0.0 daemon

```bash
openclaw cron list | grep otter
openclaw cron delete <otter-cron-id>

nohup python3 -u /data/workspace/skills/otter-strategy/scripts/otter-producer.py \
  > /tmp/otter-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/otter-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s interval — Otter's longer cadence reflects 5min OI sampling cadence).

---

## Thesis

Open Interest is the total notional of open perpetual contracts. Spot trading doesn't generate OI — only fresh perp positions do. So OI growth = real new leveraged capital deployed. When 1h OI delta is >= 5% AND price moves in the same direction by >= 0.5%, that's the **TOP-LEFT (LONGS entering)** or **TOP-RIGHT (SHORTS entering)** quadrant of the OI/price matrix — fresh institutional flow with directional conviction. Otter rides that flow for 1-3 hours then exits via DSL hard timeout.

## What's novel

The fleet uses OI as a **snapshot filter** (size threshold). **Otter is the only agent to track OI delta over time** — a uniquely perp-native signal that no other Senpi agent computes.

## v2 architecture

| Layer | Implementation |
|---|---|
| Trading loop | Producer pushes signals via helpers `push_signal()`; runtime owns execution |
| Entry gate | LLM pass-through (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT, `ensure_execution_as_taker: false` (cancel-and-skip if maker can't fill) |
| Exit order | FEE_OPTIMIZED_LIMIT, `ensure_execution_as_taker: true` (taker fallback as safety) |
| Risk gates | Declarative `runtime.risk.guard_rails` |
| Position lifecycle | Runtime DSL (Phase 1 max_loss 12% / Phase 2 ladder 5/30, 10/55, 15/75, 20/85) |

## Why OI velocity is a real edge

| OI direction | Price direction | Interpretation | Otter trade |
|---|---|---|---|
| **OI ↑** | Price ↑ | New LONGS entering with conviction | LONG (follow flow) |
| **OI ↑** | Price ↓ | New SHORTS entering with conviction | SHORT (follow flow) |
| OI ↓ | Price ↑ | SHORT covering — exhaustion | SKIP (Pangolin/Owl territory) |
| OI ↓ | Price ↓ | LONG unwinding — exhaustion | SKIP (Pangolin/Owl territory) |

Otter only trades the **TOP** quadrants. Bottom quadrants are unwinds that other agents (Pangolin, Owl) already work.

See [`SKILL.md`](SKILL.md) for full setup, env vars, behavior expectations, and bootstrap notes.

## License

MIT — Built by Senpi (https://senpi.ai).
