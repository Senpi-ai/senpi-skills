# 🪳 ROACH — Striker Only

Rank-jump explosion hunter that fires only on extreme upside breakouts.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

ROACH disables Stalker entirely and only trades STRIKER signals — violent FIRST_JUMP / IMMEDIATE_MOVER explosions backed by 1.5x volume, 1h price alignment, and 4h trend agreement. The thesis is that slow-build rank entries lose money in expectation (Fox v1.0 logged 17 Stalker trades at 17.6% win rate, -$91 net), while the rare explosive breakout is the only profitable shape in the leaderboard-momentum family.

ROACH will be quiet. Days with zero trades are expected and correct. Striker signals require a 10+ rank jump from #25+, score ≥ 10 with 4+ reasons, cc_15m ≥ 0.5, 1h price aligned ≥ 0.1%, volume ≥ 1.5x. That's rare. The patience IS the edge — Roach is the only fleet predator that explicitly chooses to skip the slow-build setups other rank-momentum strategies pursue.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | All Hyperliquid perps (top-N rank changes) |
| Tick interval | 90s |
| MIN_SCORE | 10 (4+ reasons required) |
| Leverage tiers | Score-scaled (see SKILL.md) |
| Max entries per day | Runtime-enforced |
| Per-asset cooldown | Runtime-enforced |
| Daily loss limit | Runtime `risk.guard_rails` |
| Drawdown halt | Runtime `risk.guard_rails` |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-first 60s, taker fallback) |

## Scanner pattern

This strategy uses the **rank-jump / leaderboard-momentum** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (unchanged from v2.x) |
| scripts/roach-producer.py | Long-lived producer daemon |
| scripts/roach_config.py | SDK probe + SenpiClient wrapper |
| config/roach-config.json | Operator-tunable defaults |

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

### Step 2 — Pull Roach

```bash
mkdir -p /data/workspace/skills/roach-strategy/{config,scripts,state,references}
for f in scripts/roach-producer.py scripts/roach_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/$f" \
    -o "/data/workspace/skills/roach-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export ROACH_WALLET=<your-roach-wallet>          # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export ROACH_DECISION_MODEL=<your-preferred-model>
```

For **Roach-B** (variant): use the same skill files but set `ROACH_WALLET=<roach-b-wallet>` on that agent's host.

### Step 4 — Stop any prior cron, start the daemon

```bash
openclaw cron list | grep roach
openclaw cron delete <roach-cron-id>

nohup python3 -u /data/workspace/skills/roach-strategy/scripts/roach-producer.py \
  > /tmp/roach-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/roach-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (90s interval). Roach is intentionally quiet — heartbeat ticks dominate; Striker fires are rare and that's the design.

## Changelog

### v3.0.0 — senpi_runtime_helpers migration

Plumbing-only migration from v2.1.0. NO thesis change. Producer flips to in-process `SenpiClient` (direct HTTPS for MCP, direct HTTP POST to runtime `/signals`). `producer_daemon` replaces openclaw cron.

### v2.0 architecture (preserved)

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner + calls `create_position` | Producer pushes signals via `SenpiClient.push_signal()` direct HTTP POST; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Exit | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v2 mattered:** v1 used MARKET orders for every exit, paying ~3 bp/exit in HL taker fees. v2's maker-first exits target 50-70% recovery on HL exit fees with no thesis change.

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — see root repo LICENSE.
