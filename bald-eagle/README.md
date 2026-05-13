# Bald Eagle — XYZ Contrarian Fader

Counter-trades SM consensus on six high-liquidity XYZ macro assets when the move is exhausting.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Counter-trade SM consensus on 6 high-liquidity XYZ macro assets when the move is exhausting. SM piles into oil/brent/SP500 after a clean 1-3% intraday move; by the time concentration crosses 10%, the trade is usually exhausted. Bald Eagle pushes a fade signal in the OPPOSITE direction. Wide DSL (480min hard timeout, 12% retrace) gives macro reversals time to develop.

Unlike crypto contrarians, Bald Eagle plays the XYZ DEX exclusively — commodities, indices and metals trade 24/7 on Hyperliquid XYZ. The edge is that macro flow on those assets is slow and crowd-heavy, which produces clean concentration spikes the scanner can fade with high conviction at moderate (5-7x) leverage.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 6 XYZ macro assets: CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100 |
| Tick interval | 300s (5 min) |
| MIN_SCORE | 8 |
| Leverage tiers | 5x / 7x (conviction-scaled) |
| Spread gate | < 0.1% |
| Stale-order auto-purge | 600s |
| Max entries per day | Runtime-enforced |
| Per-asset cooldown | Runtime-enforced |
| Daily loss limit | Runtime `risk.guard_rails` |
| Drawdown halt | Runtime `risk.guard_rails` |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |
| DSL hard_timeout | 480 min |
| DSL retrace | 12% |

## Scanner pattern

This strategy uses the **XYZ contrarian-concentration fade** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/eagle-producer.py | Long-lived producer daemon |
| scripts/eagle_config.py | SDK probe + SenpiClient wrapper |
| config/bald-eagle-config.json | Operator-tunable defaults |

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

`openclaw gateway restart` after editing. Confirm with
`curl -s -m 5 http://127.0.0.1:8787/state | head -c 200`.

### Step 1 — senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the
senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
which senpi-helpers
```

Skip if the senpi-trading-runtime skill is already installed.

### Step 2 — Pull Bald Eagle

```bash
mkdir -p /data/workspace/skills/bald-eagle-strategy/{config,scripts,state}
for f in scripts/eagle-producer.py scripts/eagle_config.py runtime.yaml SKILL.md README.md \
         config/bald-eagle-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bald-eagle/$f" \
    -o "/data/workspace/skills/bald-eagle-strategy/$f"
done
```

### Step 3 — Configure wallet + chat ID

Edit `/data/workspace/skills/bald-eagle-strategy/config/bald-eagle-config.json`
with `wallet`, `strategyId`, `chatId`.

### Step 4 — Env vars

```bash
export EAGLE_WALLET=<your-bald-eagle-wallet>
export SENPI_AUTH_TOKEN=...
export EAGLE_DECISION_MODEL=<your-preferred-model>    # bare model name only; NO provider prefix (e.g. "gemini-2.5-pro", "claude-sonnet-4-20250514", "gpt-4o" — any LLM your OpenClaw host has access to)
```

### Step 5 — Recreate runtime + launch daemon

```bash
openclaw senpi runtime list | grep eagle
openclaw senpi runtime delete <old-eagle-runtime-id>
openclaw senpi runtime create --path /data/workspace/skills/bald-eagle-strategy/runtime.yaml

# Stop any prior v4.x cron / bash loop
openclaw cron list | grep eagle
openclaw cron delete <eagle-cron-id>
pkill -f eagle-scanner.py  # if running via the v4 bash loop

# Launch the producer (long-lived; no cron)
nohup python3 -u /data/workspace/skills/bald-eagle-strategy/scripts/eagle-producer.py \
  > /tmp/eagle-producer.log 2>&1 &
```

If the daemon boots with `daemon_aborted_no_runtime: alive_check
returned False`, the runtime wasn't installed — re-register it via the
create command above.

## Verification

```bash
tail -f /tmp/eagle-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expect `status=ok` every 5 min (300s) with
`_eagle_producer_version: "5.0.0"`.

## Changelog

### v5.0.0 — senpi_runtime_helpers migration

**Plumbing-only migration from v4.1. NO thesis change.** v4.1 6-asset universe (CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100), contrarian flip, conviction-scaled leverage (5x/7x), MIN_SCORE 8, XYZ-tuned wide DSL, spread gate (<0.1%), 600s stale-order auto-purge — all preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces openclaw cron. Runtime owns execution, daily caps, cooldowns, drawdown halt, FEE_OPTIMIZED_LIMIT exits.

### Migrating from v4.x

```bash
cd /data/workspace/skills/bald-eagle-strategy
rm -f scripts/eagle-scanner.py                # replaced by eagle-producer.py
# Pull new files (curl above)
# Stop v4.x cron: openclaw cron list | grep eagle ; openclaw cron delete <id>
# Launch daemon per Step 5
# Reload runtime: openclaw senpi runtime delete <old>; openclaw senpi runtime create --path runtime.yaml
```

State files (`state/trade-counter.json`, `state/cooldowns.json`) are
vestigial in v5.0+ and can be deleted — runtime.guard_rails owns
daily caps and per-asset cooldowns.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
