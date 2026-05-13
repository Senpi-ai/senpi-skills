# 🦖 Raptor — Hot Streak Follower

Follows weekly-winning ELITE/RELIABLE traders into their strongest current position.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Raptor identifies ELITE/RELIABLE traders who are currently winning weekly, picks their strongest position by `|delta_pnl|`, and confirms SM alignment plus 4h/1h price agreement before firing. The edge is quality-first: instead of scanning markets and asking "which asset is interesting?", Raptor scans traders and asks "which whales are hot right now, and what are they actually in?"

Whale entry-discipline is the critical defense: if the asset has already run >5% in the whale's favor from their entry, Raptor skips — we'd be buying their top. Bonus +1/+2 points if we'd actually get a better fill than the whale. The goal is riding hot streaks, not bag-holding for whales mid-exit.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | Whatever the followed whales currently hold |
| Trader filter | ELITE / RELIABLE tier, weekly winners |
| Position selection | Whale's strongest by `|delta_pnl|` |
| Tick interval | 60-180s |
| MIN_SCORE | 6 |
| Whale entry-discipline threshold | 5% (skip if asset has run >5% in whale's favor from their entry) |
| Leverage tiers | Conviction-tier (score-scaled) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **Trader-follower / hot-streak** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `discovery_get_trader_state`, polled every 60-180s.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| `scripts/raptor-producer.py` | Long-lived daemon; emits signals via `push_signal` |
| `scripts/raptor_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/raptor-config.json` | Operator-tunable defaults (wallet, strategyId, chatId) |

## Install

### Step 0 — `openclaw.json` plugin registration (one-time per host)

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

`openclaw gateway restart`. Confirm with `curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — senpi-trading-runtime skill

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers
```

### Step 2 — Pull Raptor

```bash
mkdir -p /data/workspace/skills/raptor-strategy/{config,scripts,state}
for f in scripts/raptor-producer.py scripts/raptor_config.py runtime.yaml SKILL.md README.md \
         config/raptor-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/$f" \
    -o "/data/workspace/skills/raptor-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/raptor-strategy/config/raptor-config.json` with `wallet`, `strategyId`, `chatId`.

### Step 4 — Env vars

```bash
export RAPTOR_WALLET=<your-raptor-wallet>
export SENPI_AUTH_TOKEN=...
export RAPTOR_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Recreate runtime + launch daemon

```bash
openclaw senpi runtime list | grep raptor
openclaw senpi runtime delete <old-raptor-runtime-id>
openclaw senpi runtime create --path /data/workspace/skills/raptor-strategy/runtime.yaml

openclaw cron list | grep raptor
openclaw cron delete <raptor-cron-id>

nohup python3 -u /data/workspace/skills/raptor-strategy/scripts/raptor-producer.py \
  > /tmp/raptor-producer.log 2>&1 &
```

If `daemon_aborted_no_runtime: alive_check returned False`, re-register the runtime.

## Verification

```bash
ps aux | grep raptor-producer
senpi-helpers list
tail -f /tmp/raptor-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 3 min.

## Changelog

### v4.0.0 — helpers-native plumbing migration

Plumbing-only migration from v3.4. NO thesis change. v3.4 quality-first pipeline (ELITE/RELIABLE weekly winners → strongest position → SM alignment), whale entry-discipline 5% threshold, nested-positions parser, MIN_SCORE 6, conviction-tier leverage all preserved verbatim.

## License

MIT — Built by Senpi (https://senpi.ai).
