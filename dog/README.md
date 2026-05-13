# 🐕 Dog — The Contrarian Pup

Multi-asset mean-reversion fader on BTC, ETH, SOL, and HYPE.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Dog hunts exhausted smart-money consensus on four crypto majors. Every 3 minutes it scans BTC, ETH, SOL, and HYPE, identifies the dominant top-trader side, then verifies the 4h move is stretched (≥ 3.0% in the SM direction) while SM is still building (15m velocity > 0). The fire signal is the OPPOSITE direction — Dog fades the crowded trade right before it unwinds.

The edge is timing: most contrarian strategies fire too early (SM still piling in) or too late (SM already unwinding, juice gone). Dog's two-gate check — exhaustion + still-building — catches the precise window where the next marginal long/short is the one about to puke. Wide DSL gives the reversal hours to develop.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | BTC, ETH, SOL, HYPE |
| Tick interval | 180s (3 min) |
| MIN_SCORE | 8 (producer); 7 LLM min_confidence |
| Leverage tiers | 7x base, 10x at score 12+ |
| Margin per slot | $300 |
| Max positions | 1 |
| Max entries per day | 3 |
| Per-asset cooldown | 120 min |
| Daily loss limit | 15% |
| Drawdown halt | 25% |
| 4H exhaustion gate | 3.0% |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## Scanner pattern

This strategy uses the **Universe trend-follower** scanner pattern (applied contrarian-flip) — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`, polled every 180s.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| `scripts/dog-producer.py` | Long-lived daemon; emits signals via `push_signal` |
| `scripts/dog_config.py` | SDK probe + `SenpiClient` wrapper |
| `config/dog-config.json` | Operator-tunable defaults (wallet, strategyId, chatId) |

## Signal pipeline

For each asset on each tick:

1. **Identify SM dominant direction** — which side has the heaviest top-trader concentration
2. **Verify the move is exhausted** — 4H price moved ≥ 3.0% in the SM direction (mean reversion setup)
3. **Verify SM is still building** — 15m velocity > 0 (if SM already unwinding, the fade is mature/passing)
4. **Score the contrarian setup** — concentration + exhaustion + velocity + regime + persistence + funding
5. **Fire the FADE** — emit OPPOSITE direction at 7x or 10x leverage; runtime opens via FEE_OPTIMIZED_LIMIT
6. **Hand off to DSL** — wide DSL lets the reversal develop over hours

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

### Step 2 — Pull Dog

```bash
mkdir -p /data/workspace/skills/dog-strategy/{config,scripts,state,references}
for f in scripts/dog-producer.py scripts/dog_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/$f" \
    -o "/data/workspace/skills/dog-strategy/$f"
done
```

Pull `config/dog-config.json` separately (template only — fill in your own values):

```bash
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/config/dog-config.json" \
  -o "/data/workspace/skills/dog-strategy/config/dog-config.json"
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/dog-strategy/config/dog-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

This is the canonical source of truth — producer reads from here on every tick; runtime reads at startup.

### Step 4 — Required env vars

```bash
# Per fleet v2.0.9 rule: per-agent wallet env var (optional override;
# config.json is the canonical source).
export DOG_WALLET=<your-dog-wallet>

export SENPI_AUTH_TOKEN=...                           # required
export DOG_DECISION_MODEL=<your-preferred-model>              # bare model name; NO provider prefix
```

### Step 5 — Recreate the runtime + start the daemon

If you have an older runtime installed, delete it first — new scanner/action blocks require a fresh runtime create.

```bash
openclaw senpi runtime list | grep dog
openclaw senpi runtime delete <old-dog-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/dog-strategy/runtime.yaml
openclaw senpi runtime list
```

If you were running the v2.5 bash loop, stop it before launching the daemon:

```bash
pkill -f dog-scanner.py
# or, if cron-based: openclaw cron list | grep dog ; openclaw cron delete <id>

nohup python3 -u /data/workspace/skills/dog-strategy/scripts/dog-producer.py \
  > /tmp/dog-producer.log 2>&1 &
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Verification

```bash
ps aux | grep dog-producer
senpi-helpers list
tail -f /tmp/dog-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval). If you see `daemon_started` followed by no `daemon_tick_finished` events for 3+ minutes, the producer is stuck — check `tail /tmp/dog-producer.log` for error events.

## Changelog

### v3.0.0 — helpers-native plumbing migration

Plumbing-only migration from v2.5. NO thesis change. v2.5 scoring + universe + DSL preset preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces the v2.5 bash loop. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits.

| Layer | v2.5 | v3.0 |
|---|---|---|
| MCP transport | `mcporter` subprocess (cold-start 2.5-5s) | `SenpiClient` direct HTTPS (~280ms) |
| Signal emit | `create_position` direct call | `push_signal()` → runtime LLM gate |
| Scheduler | bash `while true; sleep 180` | `producer_daemon(interval_seconds=180)` |
| Reentrancy | hand-rolled fcntl flock | daemon's `scanner_lock` (stale-PID auto-recovery) |
| Daily cap | Python dynamic state file | `runtime.yaml risk.guard_rails` |
| Cooldown | Python `last_entry_ts` + `last_win_ts` | runtime `per_asset_cooldown_minutes` |
| Exit fee | MARKET (taker, 0.045%) | FEE_OPTIMIZED_LIMIT (maker-first, 0.015%) |
| Telemetry | scanner stdout JSON only | runtime audit_query + DSL events |

NO change to scoring components, MIN_SCORE, exhaustion gate, leverage tiers, margin, contrarian flip logic, or DSL preset.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
