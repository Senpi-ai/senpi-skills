# 🐻 GRIZZLY — BTC Alpha Hunter

Single-asset BTC trend-following scanner. Trades **WITH** smart money consensus when BTC's multi-timeframe momentum aligns.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Grizzly hunts BTC trend continuations where 4h/1h/15m/5m momentum is unified and the Smart Money leaderboard is heavily lopsided in the same direction. The thesis is borrowed verbatim from Kodiak's lifetime top-3 SOL winners and re-tuned for BTC's slower, heavier cadence:

> "The absolute highest predictor of a massive directional swing is when the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a single direction, AND the Smart Money leaderboard is heavily lopsided in that exact same direction."

**This is NOT a contrarian agent.** Grizzly trades with the trend and with the smart money. SM-opposes is a hard block. The agent is part of the Kodiak family — same architecture as Wolverine (HYPE), Polar (ETH), Kodiak (SOL), Dire (BRENTOIL) — with thresholds tightened for BTC. MIN_SCORE is 12 ("hit fewer, win bigger"); only conviction and apex tiers fire. FP-001 quiet hours block sub-apex entries 00-04 UTC; FP-003 require-all-confirmations means every soft confirmation (4TF + SM + Funding + Volume + OI) must fire individually, not just sum past the score floor.

## Six entry gates (all must pass)

1. **4h trend != NEUTRAL** — must have macro structure
2. **4h structural strength ≥ 0.75** — at least 4 of 5 candles must align
3. **1h matches 4h** — short-timeframe must agree with macro
4. **15m momentum aligned** — `MIN_MOM_15M = 0.05` (BTC-tuned)
5. **Base-tech floor** — strong 15m magnitude OR 5m alignment
6. **Macro V-recovery gate** — block fades within 1.25% of 24h extreme (prevents fading fresh V-bottoms)

Plus a **SM hard block** (will not trade against SM consensus) and **RSI hard gates** (block LONG > 70, block SHORT < 30 — BTC-tuned).

## Scoring (~17 max points)

- 4h trend foundation: 3 pts
- 1h confirmation: 2 pts
- 15m strong: 1 pt
- 4TF aligned (5m too): 1 pt
- SM aligned: 2 pts (+1 if strongly tilted >65%)
- SM 15m fresh: 1 pt (-3 if stale)
- Funding pays: 2 pts (-1 if crowded)
- Funding regime aligned: 1 pt (-1 if fighting)
- Funding persistent ≥6h: 1 pt
- Volume confirmed: 1 pt (-1 if weak)
- Volume rising: 1 pt
- OI accelerating: 2 pts (-1 if draining)
- RSI room: 1 pt
- 4h strong move: 1 pt
- Move exhaustion: -2 / -1 penalty

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | BTC (single-asset) |
| Tick interval | 180s |
| MIN_SCORE | 12 |
| Leverage tiers | 10x conviction (score 12+) / 10x apex (score 14+) |
| Max entries per day | enforced via `risk.guard_rails.max_entries_per_day` |
| Per-asset cooldown | 60 min |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 14+ bypasses) |
| Margin per slot | $500 (50% of $1k baseline) |
| Max positions | 1 |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

BTC's wick risk is lower than alts, so apex levering to 10x is empirically safe within the family pattern.

### DSL Phase 2 ladder

Time-cuts ALL DISABLED — single-asset family pattern. Phase 1 + Phase 2 own all exits via price action.

| Phase 2 Tier | Trigger | Lock % of HW |
|---|---|---|
| T0 | +5% margin ROE | 35% |
| T1 | +8% | 45% |
| T2 | +15% | 65% |
| T3 | +20% | 80% |
| T4 | +30% | 90% (apex lock) |
| T5 | +50% | 94% (monster trail) |

Validated end-to-end on 2026-04-30: BTC LONG ran +33% peak ROE through Tier 5 cleanly. Venue SL fired at $78,592 = +29.9% ROE / +$76.36 realized.

## Fleet patches

- **FP-001 quiet hours** — skip 00:00-04:00 UTC unless apex score ≥ 14. BTC overnight liquidity is thinnest; sub-apex entries wait until 04:00.
- **FP-002 hard rule** — user-conversation Claude sessions are read-only. Only the producer cron and DSL engine are write paths.
- **FP-003 require-all-confirmations** — every soft confirmation (4TF + SM + Funding + Volume + OI) must fire, not just summed score.

## Scanner pattern

This strategy uses the **Single-asset alpha hunter (Kodiak family)** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `market_get_asset_data` for BTC.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Runtime spec (external_scanner, risk guard rails, DSL) |
| `scripts/grizzly-producer.py` | Long-lived daemon emitting BTC entry signals |
| `scripts/grizzly_config.py` | SDK probe + SenpiClient wrapper |
| `config/grizzly-config.json` | Operator-tunable defaults (wallet, minScore, quiet hours) |

## What Grizzly does

- **Producer** (`grizzly-producer.py`) emits BTC entry signals via `SenpiClient.push_signal()` (direct HTTP POST). NO execution code in Python.
- **Runtime LLM gate** (configured per `runtime.yaml`) is pass-through — producer has applied every filter; LLM only catches malformed signals and converts to `OPEN_POSITION`.
- **risk.guard_rails** enforces daily caps, drawdown halt, consecutive-loss halt, per-asset cooldown — declarative in `runtime.yaml`, no Python state to drift.
- **DSL** uses `FEE_OPTIMIZED_LIMIT` (maker-first, 60s taker fallback) on entries AND exits. Saves ~0.020-0.030% per maker-filled close.
- **Trade chain DB** emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade.

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

### Step 2 — Pull Grizzly

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state,references}
for f in scripts/grizzly-producer.py scripts/grizzly_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/$f" \
    -o "/data/workspace/skills/grizzly-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export GRIZZLY_WALLET_ADDRESS=<your-grizzly-wallet>
export SENPI_AUTH_TOKEN=...
export GRIZZLY_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Start the daemon

```bash
# Stop any prior cron
openclaw cron list | grep grizzly
openclaw cron delete <grizzly-cron-id>

# Start daemon (long-lived, no cron)
nohup python3 -u /data/workspace/skills/grizzly-strategy/scripts/grizzly-producer.py \
  > /tmp/grizzly-producer.log 2>&1 &
```

## Configure

Edit `/data/workspace/skills/grizzly-strategy/config/grizzly-config.json`:

```json
{
  "wallet": "0xYourStrategyWallet",
  "startingBudget": 1000.0,
  "minScore": 12,
  "requireAllConfirmations": true,
  "quietHoursStartUtc": 0,
  "quietHoursEndUtc": 4,
  "quietHoursApexBypassScore": 14
}
```

`runtime.yaml` resolves `${WALLET_ADDRESS}` and `${TELEGRAM_CHAT_ID}` from environment. Also requires `${GRIZZLY_DECISION_MODEL}` env var with the bare model name (no provider prefix).

## Verification

```bash
tail -f /tmp/grizzly-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (180s interval). Tick `duration_ms` should be ~1-3s.

```bash
openclaw senpi runtime list                 # status: running
openclaw senpi state --runtime <id>         # both scanners have non-zero runCount
senpi-helpers list                          # producer daemon registered with recent LAST_TICK
senpi-helpers health grizzly-<wallet-suffix>  # exit 0 = healthy
```

## Troubleshooting

**Producer fires but `INGEST_FAILED` in stderr:** Check the rc / stderr / stdout / payload now logged on every failure (forensic-logging pattern). Most common cause is the host runtime plugin being on a pre-1.1.0 build — verify the runtime API responds at 127.0.0.1:8787 with `curl -s http://127.0.0.1:8787/health`.

**Heartbeats constantly with `BLOCKED:` reasons:** Normal in chop. Grizzly fires 1-3 trades per day on average. Macro V-recovery gate especially blocks fresh reversals.

**No `4TF_aligned` reason on candidates:** 5m direction must agree with the setup direction. If 5m is choppy/opposing, this gate fails and FP-003 (require-all-confirmations) blocks emission even at score >= 12.

**Scanner imports fail:** Both `grizzly-producer.py` AND `grizzly_config.py` must be in `scripts/`.

## Family

Grizzly is the BTC member of the Kodiak family:

- **Kodiak** — SOL alpha hunter
- **Wolverine** — HYPE alpha hunter
- **Polar** — ETH alpha hunter
- **Grizzly** — BTC alpha hunter ← this one

All four share the same architecture, tuned per asset (BTC ~3x slower than SOL → tighter momentum thresholds, lower RSI bounds).

## Changelog

### v7.0.0 — `senpi_runtime_helpers` migration

Plumbing-only migration. NO thesis change. v6.0.0's six-gate validation, scoring, leverage tiers, MIN_SCORE 12, FP-001 quiet hours, FP-003 requireAllConfirmations gate all preserved verbatim.

- `grizzly-producer.py` and `grizzly_config.py` migrate to `senpi_runtime_helpers`:
  - MCP calls go via `SenpiClient.mcp_call()` (direct HTTPS) instead of `mcporter` subprocess
  - Signal emission goes via `SenpiClient.push_signal()` (direct HTTP POST)
  - Reentrancy lock owned by `producer_daemon.scanner_lock` instead of hand-rolled `fcntl`
  - Tick scheduling owned by `producer_daemon` (long-lived process) instead of openclaw cron + `agentTurn`
- Requires the `senpi-trading-runtime` skill (preinstalled on the OpenClaw host).
- `runtime.yaml` unchanged from v6.x.
- Dead fields stripped from signal payload; `signal_type="GRIZZLY_BTC_TREND"` passed explicitly to `push_signal()` so audit logs + LLM decision context stay correctly tagged (avoids relying on the runtime YAML's `defaultSignalType` fallback).

### What changed from v5.x

- **Architecture:** v1 full-agency Python scanner → v2 producer + LLM gate + native risk + DSL maker exits
- **3-mode state machine** (HUNTING/RIDING/STALKING) → DROPPED. Runtime tracks position lifecycle.
- **`evaluate_reload`** → DROPPED. DSL owns position management; no Python reload logic.
- **`get_dynamic_daily_cap`** → DROPPED. `risk.guard_rails.max_entries_per_day` enforces.
- **`has_resting_orders`** → DROPPED. Runtime tracks open orders.
- **`create_position` execution** → DROPPED. Runtime LLM gate executes via `OPEN_POSITION` action.
- **MARKET exits** → REPLACED. `FEE_OPTIMIZED_LIMIT` on entries AND exits.

All scoring, gates, thresholds, and DSL preset preserved verbatim.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). BTC Alpha Hunter.
