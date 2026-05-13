# 🐕 Dog v3.0.0 — The Contrarian Pup (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/secret-skills).

**Plumbing-only migration from v2.5. NO thesis change.** v2.5 scoring + universe + DSL preset preserved verbatim. Scanner flips to in-process `SenpiClient`; daemon replaces the v2.5 bash loop. Runtime now owns execution, daily caps, cooldowns, and FEE_OPTIMIZED_LIMIT exits.

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

### Step 2 — Pull Dog v3.0.0

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

If you have a v2.5 runtime installed, delete it first — v3.0 introduces new scanner/action blocks that require a fresh runtime create.

```bash
openclaw senpi runtime list | grep dog
openclaw senpi runtime delete <old-dog-runtime-id>

openclaw senpi runtime create --path /data/workspace/skills/dog-strategy/runtime.yaml
openclaw senpi runtime list
```

If you were running the v2.5 bash loop, stop it before launching the v3.0 daemon:

```bash
pkill -f dog-scanner.py
# or, if cron-based: openclaw cron list | grep dog ; openclaw cron delete <id>

nohup python3 -u /data/workspace/skills/dog-strategy/scripts/dog-producer.py \
  > /tmp/dog-producer.log 2>&1 &
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Smoke test

```bash
tail -f /tmp/dog-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval).

If you see `daemon_started` followed by no `daemon_tick_finished` events for 3+ minutes, the producer is stuck — check `tail /tmp/dog-producer.log` for error events.

---

## Thesis (preserved from v2.5)

Multi-asset contrarian fader. Scans BTC, ETH, SOL, HYPE every 3 minutes via `leaderboard_get_markets`. For each asset:

1. **Identify SM dominant direction** — which side has the heaviest top-trader concentration
2. **Verify the move is exhausted** — 4H price moved ≥ 3.0% in the SM direction (mean reversion setup)
3. **Verify SM is still building** — 15m velocity > 0 (if SM already unwinding, the fade is mature/passing)
4. **Score the contrarian setup** — concentration + exhaustion + velocity + regime + persistence + funding
5. **Fire the FADE** — emit OPPOSITE direction at 7x or 10x leverage; runtime opens via FEE_OPTIMIZED_LIMIT
6. **Hand off to DSL** — wide DSL lets the reversal develop over hours

## What changed in v3.0 vs v2.5

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

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL, HYPE |
| Max positions | 1 |
| Margin per slot | $300 |
| Leverage | 7x base, 10x at score 12+ |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT (v3.0 win) |
| Tick interval | 180s (3 min) |
| MIN_SCORE (producer) | 8 |
| LLM min_confidence | 7 |
| Per-asset cooldown | 120 min |
| Daily entry cap | 3 |
| Daily loss limit | 15% |
| Drawdown halt | 25% |
| 4H exhaustion gate | 3.0% |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
