# 🐻 GRIZZLY v6.0.0 — BTC Alpha Hunter (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

Single-asset BTC trend-following scanner. Trades **WITH** smart money consensus when BTC's multi-timeframe momentum aligns. Same Kodiak-family pattern as Wolverine (HYPE), Polar (ETH), and Kodiak (SOL) — tuned for BTC's slower cadence.

## What Grizzly does

- **Producer** (`grizzly-producer.py`) emits BTC entry signals via `openclaw senpi external-scanner ingest`. NO execution code in Python.
- **Runtime LLM gate** (configured per `runtime.yaml`) is pass-through — producer has applied every filter; LLM only catches malformed signals and converts to `OPEN_POSITION`.
- **risk.guard_rails** enforces daily caps, drawdown halt, consecutive-loss halt, per-asset cooldown — declarative in `runtime.yaml`, no Python state to drift.
- **DSL** uses `FEE_OPTIMIZED_LIMIT` (maker-first, 60s taker fallback) on entries AND exits. Saves ~0.020-0.030% per maker-filled close.
- **Trade chain DB** emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade.

## Thesis — Trend Continuation on BTC

From Kodiak's lifetime top-3 SOL winners (+$133 / +$87 / +$78):

> "The absolute highest predictor of a massive directional swing is when the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a single direction, AND the Smart Money leaderboard is heavily lopsided in that exact same direction."

Grizzly v6 applies that exact thesis to BTC. BTC is slower and heavier than SOL, so thresholds are tightened — but the pattern is identical.

**This is NOT a contrarian agent.** Grizzly trades with the trend and with the smart money. SM-opposes is a hard block.

## Six entry gates (all must pass)

1. **4h trend != NEUTRAL** — must have macro structure
2. **4h structural strength ≥ 0.75** — at least 4 of 5 candles must align (Kodiak v5.1 fix)
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

`MIN_SCORE = 12` (config-overridable). v5.8 "hit fewer, win bigger" — only conviction (12-13) and apex (14+) tiers fire.

## Conviction-scaled leverage

| Score | Tier | Leverage |
|---|---|---|
| 14+ | apex | 10x |
| 12+ | conviction | 10x |
| (below MIN_SCORE) | — | n/a |

BTC's wick risk is lower than alts, so apex levering to 10x is empirically safe within the family pattern.

## DSL preset (preserved verbatim from v5.9)

Time-cuts ALL DISABLED — single-asset family pattern. Phase 1 + Phase 2 own all exits via price action.

| Phase 2 Tier | Trigger | Lock % of HW |
|---|---|---|
| T0 | +5% margin ROE | 35% |
| T1 | +8% | 45% |
| T2 | +15% | 65% |
| T3 | +20% | 80% |
| T4 | +30% | 90% (apex lock) |
| T5 | +50% | 94% (monster trail) |

**Validated end-to-end on 2026-04-30:** BTC LONG ran +33% peak ROE through Tier 5 cleanly. Venue SL fired at $78,592 = +29.9% ROE / +$76.36 realized. Proves Senpi DSL ratchet works on BTC main-DEX through every tier.

## Fleet patches

- **FP-001 quiet hours** — skip 00:00-04:00 UTC unless apex score ≥ 14. BTC overnight liquidity is thinnest; sub-apex entries wait until 04:00.
- **FP-002 hard rule** — user-conversation Claude sessions are read-only. Only the producer cron and DSL engine are write paths.
- **FP-003 require-all-confirmations** — every soft confirmation (4TF + SM + Funding + Volume + OI) must fire, not just summed score.

## Install (operator path)

**Prerequisite — verify your plugin is on `runtime-phase-2`:**

```bash
openclaw senpi external-scanner ingest --help
```

Should show `--address`, `--scanner`, `--payload` flags. If not, you're on stable v1.0.97 which lacks v2 architecture. Install the phase-2 build first:

```bash
cd /tmp && rm -rf senpi-trading-runtime
git clone -b runtime-phase-2 https://github.com/Senpi-ai/senpi-trading-runtime.git
cd senpi-trading-runtime && npm run build
openclaw plugins uninstall runtime
openclaw plugins install ./
```

**Then install the skill:**

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml -o /data/workspace/skills/grizzly-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/SKILL.md -o /data/workspace/skills/grizzly-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/config/grizzly-config.json -o /data/workspace/skills/grizzly-strategy/config/grizzly-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-producer.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly_config.py
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

## Create the runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list
```

## Schedule the producer cron

```bash
openclaw cron add \
  --name senpi-producer-grizzly_signals-<wallet-suffix> \
  --interval 3m \
  --message "python3 /data/workspace/skills/grizzly-strategy/scripts/grizzly-producer.py"
```

`<wallet-suffix>` = last 4 hex chars of your strategy wallet, lowercased.

## Verify liveness

```bash
openclaw senpi runtime list                 # status: running
openclaw senpi state --runtime <id>         # both scanners have non-zero runCount
openclaw cron list                          # producer cron registered
```

First producer scan should print JSON with `_grizzly_producer_version: "6.0.0"`.

## Key settings

| Setting | Value | Notes |
|---|---|---|
| Asset | BTC | Single-asset focus |
| Max positions | 1 | No parallel bets |
| Margin per slot | $500 | 50% of $1k baseline |
| Leverage | 10x apex/conviction, 7x default | Score-scaled, fleet cap |
| MIN_SCORE | 12 | Config-overridable |
| Per-asset cooldown | 60 min | Post-exit cooldown |
| Daily loss limit | 10% | Halt entries fail-closed |
| Drawdown halt | 25% | Carries across day rollover |

## Troubleshooting

**Producer fires but `INGEST_FAILED` in stderr:** Check the rc / stderr / stdout / payload now logged on every failure (Vulture v3.1.1 forensic-logging pattern). Most common cause is the host being on stable v1.0.97 instead of phase-2 — verify with `openclaw senpi external-scanner ingest --help`.

**Heartbeats constantly with `BLOCKED:` reasons:** Normal in chop. Grizzly fires 1-3 trades per day on average. Macro V-recovery gate especially blocks fresh reversals.

**No `4TF_aligned` reason on candidates:** 5m direction must agree with the setup direction. If 5m is choppy/opposing, this gate fails and FP-003 (require-all-confirmations) blocks emission even at score >= 12.

**Scanner imports fail:** Both `grizzly-producer.py` AND `grizzly_config.py` must be in `scripts/`.

## What changed from v5.x

- **Architecture:** v1 full-agency Python scanner → v2 producer + LLM gate + native risk + DSL maker exits
- **3-mode state machine** (HUNTING/RIDING/STALKING) → DROPPED. Runtime tracks position lifecycle.
- **`evaluate_reload`** → DROPPED. DSL owns position management; no Python reload logic.
- **`get_dynamic_daily_cap`** → DROPPED. `risk.guard_rails.max_entries_per_day` enforces.
- **`has_resting_orders`** → DROPPED. Runtime tracks open orders.
- **`create_position` execution** → DROPPED. Runtime LLM gate executes via `OPEN_POSITION` action.
- **MARKET exits** → REPLACED. `FEE_OPTIMIZED_LIMIT` on entries AND exits.

All scoring, gates, thresholds, and DSL preset preserved verbatim.

## Family

Grizzly is the BTC member of the Kodiak family:

- **Kodiak** — SOL alpha hunter
- **Wolverine** — HYPE alpha hunter
- **Polar** — ETH alpha hunter
- **Grizzly** — BTC alpha hunter ← this one

All four share the same architecture, tuned per asset (BTC ~3x slower than SOL → tighter momentum thresholds, lower RSI bounds).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). BTC Alpha Hunter.
