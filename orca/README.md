# 🐋 Orca — Gen-1 Vanilla Striker

The fleet's reference universe trend-follower: rank-jump detection on the HL leaderboard, single MCP call per scan, plugin-runtime exits.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Orca hunts the cleanest pattern in trend-following crypto perps: an asset that has just broken into the top of the volume leaderboard and is being confirmed by velocity and volume in the same window. The "FIRST_JUMP" trigger fires when an asset moves from rank #25+ to a much higher slot — rank jump ≥ 15 — and scores ≥ 9 with 4+ supporting reasons, the 4h trend is aligned, 15m velocity is positive, and volume is ≥ 1.5x baseline.

It is deliberately the Gen-1 vanilla Striker — no scoring novelty, no DSL exotica, no per-asset specialization. Universe-wide via `leaderboard_get_markets`, one API call per scan, scanning every 90 seconds. The runtime plugin owns daily caps, cooldowns, position tracking, and FEE_OPTIMIZED_LIMIT exits. Orca exists as the fleet's reference baseline: if a more exotic strategy can't beat it, the exotic isn't earning its complexity.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | Top-N HL leaderboard (universe-wide via `leaderboard_get_markets`) |
| Tick interval | 90 s |
| Trigger | FIRST_JUMP from rank #25+, rank jump ≥ 15 |
| MIN_SCORE | 9 (with 4+ supporting reasons) |
| Trend / velocity gates | 4h trend aligned, 15m velocity > 0 |
| Volume gate | ≥ 1.5x baseline |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT (DSL-managed by runtime) |

## Scanner pattern

This strategy uses the **universe rank-jump / Striker** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanner + action + DSL) |
| `scripts/orca-producer.py` | Long-lived daemon (90 s tick) |
| `scripts/orca_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/orca-config.json` | Operator-tunable defaults |

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

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

Restart the gateway: `openclaw gateway restart`. Confirm with `curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — Install the senpi-trading-runtime skill

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers   # should return a path
```

### Step 2 — Pull Orca

```bash
mkdir -p /data/workspace/skills/orca-strategy/{config,scripts,state}
for f in scripts/orca-producer.py scripts/orca_config.py runtime.yaml SKILL.md README.md \
         config/orca-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/orca/$f" \
    -o "/data/workspace/skills/orca-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/orca-strategy/config/orca-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Required env vars

```bash
export ORCA_WALLET=<your-orca-wallet>
export SENPI_AUTH_TOKEN=...
export ORCA_DECISION_MODEL=gemini-2.5-pro    # bare model name
```

### Step 5 — Recreate runtime + launch daemon

```bash
# Delete old v3.x runtime (new YAML has scanner/action blocks v3.x lacks)
openclaw senpi runtime list | grep orca
openclaw senpi runtime delete <old-orca-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/orca-strategy/runtime.yaml

# Stop any v3.x cron
openclaw cron list | grep orca
openclaw cron delete <orca-cron-id>

# Launch the v4.0 daemon
nohup python3 -u /data/workspace/skills/orca-strategy/scripts/orca-producer.py \
  > /tmp/orca-producer.log 2>&1 &
```

If the daemon boots with `daemon_aborted_no_runtime: alive_check returned False`, the runtime wasn't installed — re-register: `openclaw senpi runtime create --path /data/workspace/skills/orca-strategy/runtime.yaml`.

## Verification

```bash
tail -f /tmp/orca-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 90 s.

## Changelog

- **v4.0.0** — Plumbing-only migration from v3.0 (NO thesis change). Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits. v3.0 Gen-1 vanilla Striker logic (FIRST_JUMP + base scoring + volume confirmation) preserved verbatim.

## License

MIT — Built by Senpi (https://senpi.ai).
