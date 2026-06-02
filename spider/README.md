# 🕷️ SPIDER v5.0 — Two-Persona Style Hunter

Two autonomous style legs on two wallets, served by **one** producer script.
**Not a copy-trader** — each leg scores its own universe to a *style* and
pushes signals; the runtime owns the LLM gate (pass-through), DSL exits, and
all `risk.guard_rails`.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

| Leg | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `swing` | Tech & AI multi-day momentum, **LONG only** | `SPIDER_SWING_WALLET` | `runtime-swing.yaml` | `spider_swing_signals` |
| `scalp` | Macro & majors fast mean-reversion, **BOTH dirs** | `SPIDER_SCALP_WALLET` | `runtime-scalp.yaml` | `spider_scalp_signals` |

The `SPIDER_LEG` env var (`swing` | `scalp`) selects which leg a given daemon
is. Each leg binds to its own wallet, runtime YAML, DSL, and risk envelope.
See [SKILL.md](SKILL.md) for the full thesis, scoring tables, and risk gates.

## Thesis

**SWING — Tech & AI multi-day momentum.** Multi-day trend rider. Holds a
small basket of the strongest names in a semiconductor/AI + high-beta-alt
universe (`xyz:NVDA/AMD/INTC/MRVL/MU/TSM` + `SUI/ONDO/HYPE/NIL/GRASS/ZEC`),
LONG only, scored on 4h+1h trend structure + 24h relative strength.
Conviction leverage clamped 10x. Wide *let-winners-run* DSL — sits through
drawdowns while the multi-timeframe trend holds.

**SCALP — Macro & majors fast mean-reversion.** Fades short-timeframe stretch
and rides the snap-back across majors (`BTC/ETH/SOL/HYPE`) + energy
(`xyz:BRENTOIL/xyz:CL`). BOTH directions (long-biased), strict 5x, minutes-to-
hour holds, tight *fast-capture* DSL with `dead_weight_cut` + `weak_peak_cut`
ON. **Fee-sensitive** — high turnover.

## Key parameters

| Parameter | swing | scalp |
|---|---|---|
| Direction | LONG only | LONG + SHORT |
| Tick interval | 300s | 60s |
| `minScore` (raw) | 5 | 4 |
| Max slots | 3 | 4 |
| Margin per slot | 28% | 15% |
| Leverage cap | 10x → venue max | 5x → venue max |
| `max_entries_per_day` | 4 | 30 (fee-sensitive) |
| `per_asset_cooldown` | 4h | 10m |
| `daily_loss_limit_pct` | 15 | 10 |
| `drawdown_halt_pct` | 25 | 20 |
| DSL phase1 max_loss | 22% | 7% |
| DSL `hard_timeout` | 7d (staleness) | 2h |
| Time cuts | all OFF | weak_peak + dead_weight ON |
| Entry / exit order | FEE_OPTIMIZED_LIMIT | FEE_OPTIMIZED_LIMIT |

## Leverage clamping

Desired leverage = the leg cap (`swingMaxLeverage` 10 / `scalpMaxLeverage` 5).
The producer then clamps to each asset's **Hyperliquid venue max**, read from
`market_list_instruments` → `instruments[].max_leverage` (one call per tick).
This prevents emitting an unfillable order — e.g. `GRASS` and `NIL` cap at
**3x**, so a swing 10x desire is clamped to 3x for those names. The runtime
decision gate also rejects any leverage above the leg cap (clamp-breach
defense).

## Files

| File | Purpose |
|---|---|
| `runtime-swing.yaml` | Swing-leg runtime spec (wallet, DSL, risk, LLM gate) |
| `runtime-scalp.yaml` | Scalp-leg runtime spec |
| `scripts/spider-producer.py` | Leg-aware producer daemon (one script, both legs) |
| `scripts/spider_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/spider-swing-config.json` | Swing-leg tunables |
| `config/spider-scalp-config.json` | Scalp-leg tunables |

## Install

The two legs are **two daemons on two wallets**, each with its own runtime
YAML. Steps 0–2 are one-time per host; steps 3–4 are run once per leg.

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

The senpi-trading-runtime plugin won't bind its API port (`127.0.0.1:8787`)
unless `plugins.entries.runtime` is present in `/data/.openclaw/openclaw.json`.
Without it the plugin logs `No plugin config found — skipping registration`
and producer `signal_post` calls fail with `[Errno 111] Connection refused`.
Confirm or add:

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

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the
senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Spider (both legs)

```bash
mkdir -p /data/workspace/skills/spider-strategy/{config,scripts,state,references}
for f in scripts/spider-producer.py scripts/spider_config.py \
         runtime-swing.yaml runtime-scalp.yaml \
         config/spider-swing-config.json config/spider-scalp-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/spider/$f" \
    -o "/data/workspace/skills/spider-strategy/$f"
done
```

### Step 3 — Required env vars

Both legs share the auth token, decision model, and (optional) Telegram chat
id. Each leg binds its own wallet:

```bash
export SENPI_AUTH_TOKEN=...
export SPIDER_DECISION_MODEL=<your-preferred-model>   # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                            # optional, for trade notifications

export SPIDER_SWING_WALLET=<your-swing-wallet>         # or set wallet in config/spider-swing-config.json
export SPIDER_SCALP_WALLET=<your-scalp-wallet>         # or set wallet in config/spider-scalp-config.json
```

Each leg also accepts an optional `SPIDER_SWING_STRATEGY_ID` /
`SPIDER_SCALP_STRATEGY_ID` (falls back to the `strategyId` field in the
matching config file).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (per your host's runtime-register
flow), then launch one producer daemon per leg. `SPIDER_LEG` selects the leg;
the daemon reads the matching config, scanner name, and wallet env var.

```bash
# SWING daemon
SPIDER_LEG=swing \
SPIDER_SWING_WALLET=$SPIDER_SWING_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
nohup python3 -u /data/workspace/skills/spider-strategy/scripts/spider-producer.py \
  > /tmp/spider-swing-producer.log 2>&1 &
disown

# SCALP daemon
SPIDER_LEG=scalp \
SPIDER_SCALP_WALLET=$SPIDER_SCALP_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
nohup python3 -u /data/workspace/skills/spider-strategy/scripts/spider-producer.py \
  > /tmp/spider-scalp-producer.log 2>&1 &
disown
```

`disown` detaches each daemon from the shell job table so a shell/OpenClaw
exit (SIGTERM to children) doesn't kill it. Env vars are passed inline so each
daemon's environment is self-contained.

## Verification

```bash
tail -f /tmp/spider-swing-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
tail -f /tmp/spider-scalp-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (swing 300s / scalp 60s). Each daemon logs
under `[spider-v5:swing]` / `[spider-v5:scalp]` on stderr.

## Changelog

### v5.0.0 — Two-persona style-hunter rebuild

Full thesis **replacement** of the v4.0 single-leg "patient anchor sniper."
v5.0 is two autonomous style legs on two wallets served by one
leg-parameterized producer (`SPIDER_LEG` selects `swing` | `scalp`):

- **SWING** — Tech & AI multi-day momentum, LONG only, 4h+1h trend + 24h
  relative strength, conviction 10x clamp, wide let-winners-run DSL.
- **SCALP** — Macro & majors fast mean-reversion, BOTH directions, short-TF
  stretch + RSI extreme with a 1h knife-guard trend filter, strict 5x, tight
  fast-capture DSL.
- **Not a copy-trader** — each leg scores its own universe; the runtime LLM
  gate is pass-through and the runtime owns execution + DSL + risk.
- Leverage clamped to each asset's Hyperliquid venue max via
  `market_list_instruments` (GRASS/NIL cap 3x).
- XYZ (HIP-3) equities/energy handled with `dex="xyz"`;
  `get_positions` sums account value across both the main and xyz sub-DEX
  views.
- Per-leg race-window dedup (`state/recent-signals-<leg>.json`, 180s).

The old single-leg `runtime.yaml` / `config/spider-config.json` /
`config/spider-config.example.json` are removed.

### v4.0.0 — Plumbing-only migration from v3.0.2 (no thesis change)

Producer ported onto `senpi_runtime_helpers` (in-process `SenpiClient`,
long-lived `producer_daemon`). Superseded by v5.0.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
