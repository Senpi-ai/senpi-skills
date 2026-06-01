# 🐆 CHEETAH — Multi-Signal Confluence Sniper

Patient trader-follower that refuses to enter unless every confluence signal agrees.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Multi-signal confluence sniper. Refuses to trade unless ALL major signals align: SM consensus + velocity + acceleration + dual price confirmation + volume spike + quality-trader alignment + rank climb. Score 10/15 floor. Top-100 SM leaderboard universe. XYZ banned. Patience is the edge.

While most rotation-style agents take any setup that crosses a single threshold, Cheetah waits for the full stack of confirmations to line up on the same asset at the same time. The producer scores the top-100 SM leaderboard each tick, and the runtime LLM gate provides a second-pass veto before any position is opened.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | Top 100 SM leaderboard (XYZ banned) |
| Tick interval | 20 min (1200s) |
| MIN_SCORE | 10 (down from v5.2's 11) |
| LLM min_confidence | 7 |
| Max positions | 1 |
| Margin per slot | $250 (30% of starting budget) |
| Leverage tiers | 3x / 5x / 7x / 8x (score-tiered) |
| Max entries per day | 8 |
| Per-asset cooldown | 240 min (4h) |
| Post-close cooldown | 240 min (producer-side backstop) |
| Daily loss limit | 25% |
| Drawdown halt | 25% |
| drawdown_reset_on_day_rollover | false |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder ("let winners run")

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +10% | 0% |
| T1 | +20% | 25% |
| T2 | +30% | 40% |
| T3 | +50% | 60% |
| T4 | +75% | 75% |
| T5 (apex) | +100% | 85% |

Phase 1: max_loss 15% / retrace 10 / 1 consecutive breach.
Time cuts (v7.2.0): hard_timeout 720min ENABLED (12h fail-safe); weak_peak_cut + dead_weight_cut **DISABLED**. The 100-trade analysis showed the 60m/90m cuts were forcing exits before trenders could accelerate — the two big HYPE wins (+13.9%, +8.9%) only landed via the 12h timeout. T0 lock=0 lets a +10% mover retrace fully before any profit lock; the rare +50–100% move makes the book.

## Scanner pattern

This strategy uses the **trader-follower / hot-streak** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `discovery_get_top_traders`, `leaderboard_get_markets`, `leaderboard_get_trader_positions`.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/cheetah-producer.py | Long-lived daemon |
| scripts/cheetah_config.py | SDK probe + SenpiClient wrapper |
| config/cheetah-config.json | Operator-tunable defaults |

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

### Step 2 — Pull the Cheetah skill

```bash
mkdir -p /data/workspace/skills/cheetah-strategy/{config,scripts,state}

for f in runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/$f" \
    -o "/data/workspace/skills/cheetah-strategy/$f"
done
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/config/cheetah-config.json" \
  -o "/data/workspace/skills/cheetah-strategy/config/cheetah-config.json"
for f in cheetah-producer.py cheetah_config.py; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/$f" \
    -o "/data/workspace/skills/cheetah-strategy/scripts/$f"
done
mkdir -p /data/workspace/skills/cheetah-strategy/references
curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/references/skill-attribution.md" \
  -o "/data/workspace/skills/cheetah-strategy/references/skill-attribution.md"
```

## Configure

**Set wallet, strategyId, chatId in `config/cheetah-config.json`** — canonical source.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 10
}
```

### Required env vars

| Env var | Purpose |
|---|---|
| `CHEETAH_WALLET` | Strategy wallet (must match runtime.yaml). Per-agent; no `STRATEGY_ADDRESS` fallback. |
| `SENPI_AUTH_TOKEN` | Bearer token for MCP + signal POST. |
| `CHEETAH_DECISION_MODEL` | Bare model name (no provider prefix), e.g. `gemini-3.1-pro-preview`. Set at runtime-create time only. |

### Optional env vars (sensible defaults)

| Env var | Default | Purpose |
|---|---|---|
| `SENPI_MCP_URL` | `https://mcp.prod.senpi.ai/mcp` | Direct MCP endpoint |
| `SENPI_RUNTIME_API_HOST` | `127.0.0.1` | Runtime signal POST host |
| `SENPI_RUNTIME_API_PORT` | `8787` | Runtime signal POST port |
| `OPENCLAW_WORKSPACE` | `/data/workspace` | Skill mount root |

## Install the runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime list   # confirm status: ACTIVE
```

## Run the producer (long-lived daemon — replaces cron)

The producer is a long-lived daemon. **Do NOT add an openclaw cron entry** — that would spawn duplicate daemons. If you're upgrading from a cron-era version, delete the existing cheetah-producer cron first:

```bash
openclaw cron list | grep cheetah
openclaw cron delete <cheetah-cron-id>
```

Start the daemon (pick one supervision style):

```bash
# Option A — supervised by tini in a docker-managed container:
exec tini -- python3 -u /data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py

# Option B — nohup background process (simple, no auto-restart):
nohup python3 -u /data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py \
  > /tmp/cheetah-producer.log 2>&1 &
```

## Verification

Watch the daemon log for one minute:

```bash
tail -f /tmp/cheetah-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -5
```

Expected: every line shows `status=ok`.

| Status | Meaning | What to do |
|---|---|---|
| `ok` | Tick succeeded | Healthy |
| `skipped_locked` | Lock collision (likely double-locking) | Confirm no inner `scanner_lock` was added inside `main()` |
| `error` | `fn` raised | Read the `error` field |
| `timeout` | `fn` took too long | Tune `tick_timeout` in producer's `__main__` block |

`daemon_self_terminated_no_runtime` is normal when the runtime is deleted.

State files (`state/entry-log.jsonl`, `state/scan-history.json`, `state/quality-cache.json`, `state/cooldowns.json`, `state/trade-counter.json`) live under `state/<wallet-hash>/` — wallet-isolated.

## Changelog

### v7.2.0 (2026-06-01) — DSL exit overhaul: disable time cuts, let winners run

A 100-trade-by-score analysis showed entries are sound — win rate scales
cleanly with score (40.0% @ 10 → 41.7% @ 11 / n=84 → 50.0% @ 12 → 55.6% @ 13)
— but **Avg ROE was negative across EVERY bracket** (−1.90% / −0.35% / −0.20%
/ −0.12%). Diagnosis: exits, not entries. "Death by a thousand cuts" — tight
Phase 2 locks + 60m/90m time-cuts made it mechanically impossible to hold for
a big win. The two HYPE longs that paid (+13.9%, +8.9%) only landed because
they hit the 12h **hard timeout**, not a trailing stop or dead_weight_cut.
Fix (mirrors live deploy): `weak_peak_cut` and `dead_weight_cut` **disabled**;
`hard_timeout` (12h) kept as the sole time fail-safe; Phase 2 ladder already
on the "let winners run" shape (T0 +10% / lock 0). Exit-mechanics only — NO
change to scoring, MIN_SCORE, leverage, margin, or risk gates. Also syncs the
README/SKILL DSL tables, which were stale (still showed the pre-2026-05-21
+5%/35% fleet-standard ladder).

### v7.0.0 — Plumbing-only migration (no thesis change)

v6.1's scoring tables, leverage tiers, dedup logic, post-close cooldown, runtime DSL preset all preserved verbatim.

- `cheetah-producer.py` and `cheetah_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` (PID-aliveness auto-recovery) instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn` (per-tick LLM cost)
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host; provides the `{success,data,error}` envelope and `GET /state` for daemon liveness probes).
- `runtime.yaml` unchanged. `external_scanner.name: cheetah_signals` matches the producer's `client.push_signal(scanner=...)`.

### v6.x (preserved)

- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits per-trade telemetry
- **MIN_SCORE 10** (v5.2's 11 produced 8 days dormant; restored to 10)
- Held-asset dedup (3-layer)
- Post-close cooldown (producer-side backstop for the runtime `per_asset_cooldown` known-silent-bug)
- All v5.2 scoring + leverage tiers + leverage-safety clamp preserved EXACTLY

### Migrating from v6.x

```bash
cd /data/workspace/skills/cheetah-strategy

# 1. Install the senpi-trading-runtime skill (one-time per host) — Step 1 above.

# 2. Pull the new producer + config files (Step 2 above curl block).

# 3. Bump the runtime plugin to >= 1.1.0 if not already on it:
cat /data/.openclaw/extensions/runtime/package.json | grep version
# Minimum required version:
#   1.1.0

# 4. Stop the old producer cron (the v7.0.0 producer is a daemon now):
openclaw cron list | grep cheetah
openclaw cron delete <cheetah-cron-id>

# 5. Start the daemon (see "Run the producer" above).

# 6. runtime.yaml unchanged — no need to drop+recreate the runtime.
#    If you DO recreate it, do so only when there are no open positions
#    (orphan-position bug: runtime swap can leave baseline positions
#    without DSL coverage).
```

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
