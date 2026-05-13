---
name: otter-strategy
description: >-
  OTTER v2.0.0 — Open Interest Velocity Hunter (senpi_runtime_helpers
  migration). Plumbing-only port from v1.0. NO thesis change. NO scoring
  change. Producer ports onto `senpi_runtime_helpers` (in-process
  SenpiClient + direct HTTP POST to runtime /signals + producer_daemon
  long-lived loop). Trades the rate of change of OI on Hyperliquid
  perps — when 1h OI delta is >= 5% AND price moves in the same
  direction by >= 0.5%, that's fresh leveraged positioning with
  directional conviction (TOP-LEFT or TOP-RIGHT quadrant of the
  OI/price matrix). Otter follows the flow for 1-3 hours then exits.
  Conviction-scaled leverage (5/7/10 by score) per the fleet-winning
  pattern.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦦 OTTER v1.0 — Open Interest Velocity Hunter

A new fleet archetype. Built v2-runtime-native from day 1 (no v1 to
migrate from). Otter is the FIRST fleet agent to use OI velocity as a
primary trading signal — every other agent (Mamba, Condor, Barracuda,
Pangolin) reads OI as a snapshot filter (size threshold) but none track
delta over time.

---

## Thesis (one paragraph)

Open Interest is the total notional of open perpetual contracts on an
asset. Spot trading doesn't generate OI — only fresh perp positions do.
So OI growth = real new leveraged capital deployed. When OI grows
materially (>= 5% in 1h) **and** price moves in the same direction by
>= 0.5%, that's fresh institutional/sophisticated positioning with
directional conviction. Otter rides that flow for 1-3 hours, then exits
on DSL hard timeout. The 4-quadrant interpretation:

| OI direction | Price direction | Interpretation | Otter trade |
|---|---|---|---|
| **OI ↑** | Price ↑ | New LONGS entering with conviction | LONG (follow flow) |
| **OI ↑** | Price ↓ | New SHORTS entering with conviction | SHORT (follow flow) |
| OI ↓ | Price ↑ | SHORT covering — exhaustion | SKIP (Pangolin/Owl territory) |
| OI ↓ | Price ↓ | LONG unwinding — exhaustion | SKIP (Pangolin/Owl territory) |

Otter only trades the TOP-LEFT and TOP-RIGHT quadrants — fresh
leveraged positioning with directional conviction.

---

## What it inherits from fleet winners

| Winning pattern | Source | Applied here |
|---|---|---|
| Conviction-scaled leverage (5/7/10) | Polar v2.4, Bald Eagle v3.0, Cheetah v5.2 | MIN_SCORE 9 → 5x; 11-12 → 7x; 13+ → 10x |
| MIN_SCORE 9+ high bar | Polar v2.4, Cheetah v5.2 | MIN_SCORE 9 |
| Multi-signal confluence (winning lane in fleet) | Cheetah v5.2 (+1.6%, 11 trades, sniper) | OI velocity + price alignment + spread + SM bonus |
| Quantitative MCP signal (not subjective TA) | Pangolin (funding), Bald Eagle (XYZ SM), Mantis (cross-flows) | OI delta computed from `market_list_instruments` history |
| Maker-only execution | Bald Eagle v3.0, Pangolin v2.0 | FEE_OPTIMIZED_LIMIT entries (no taker fallback) |
| 240min per-asset cooldown | Polar v2.4, Pangolin v2.0, Roach v2.0 | per_asset_cooldown_minutes 240 |
| Producer + LLM pass-through gate + DSL exits | Roach v2, Pangolin v2 | v2-native from day 1 |

---

## Differentiation from the fleet

| Agent | Signal | How Otter differs |
|---|---|---|
| **Pangolin** | funding rate (cost of carry) | Otter trades growth of positioning, not cost. Pangolin holds 24-48h; Otter holds 1-3h. |
| **Mantis** | BTC cross-asset lag | Otter doesn't care about BTC — each asset's OI tells its own story. Asset-agnostic. |
| **Bald Eagle** | XYZ SM concentration | Otter is crypto-only. XYZ has different OI dynamics; that's Bald Eagle's lane. |
| **Roach / Vulture / Cheetah** | SM concentration breakouts | SM = WHO is positioned. OI velocity = HOW MUCH new leverage is entering. Independent signals. |
| **Owl** | crowding unwind | Otter SKIPS unwinding (bottom 2 quadrants). Otter trades the GROWTH; Owl trades the FADE. |

---

## Producer behavior

Runs every 5 minutes via cron. On each tick:

1. **Reentrancy guard:** acquires `state/<wallet-hash>/producer.lock`. fcntl LOCK_EX | LOCK_NB.
2. **Read account value** via `strategy_get_clearinghouse_state` for sizing.
3. **Fetch the universe** via `market_list_instruments` (~60 crypto perps, XYZ filtered out at parse time).
4. **Update OI history**: append current sample {ts, oi, mark_px} per asset to `state/<wallet-hash>/oi-history.json`. Trim to last 60 samples per asset (5h rolling window at 5min cadence).
5. **Compute deltas** per asset with sufficient history:
   - 1h OI delta % (12 samples back)
   - 4h OI delta % (48 samples back, optional confirmation)
   - 1h price delta % (12 samples back)
6. **Apply hard gates** (TOP quadrants only):
   - 1h OI delta >= 5% (fresh leveraged flow)
   - 1h price delta aligned with OI delta direction (same sign), magnitude >= 0.5%
   - 4h OI delta >= 0% (no recent net unwinding masking the 1h growth)
   - OI USD >= $1M (liquidity floor)
7. **Score** each survivor:
   - 1h OI Δ tier: 4 (5-10%), 5 (10-20%), 6 (>20%)
   - 4h OI Δ confirmation: +2 if >= 10% same direction
   - 4h OI Δ contradiction: -2 if < 0
   - 1h price Δ magnitude: +1 (>1%), +2 (>2%)
   - SM concentration aligned: +2 if `leaderboard_get_markets` shows top traders in same direction
   - Spread check (`market_get_asset_data` orderbook): +1 (≤5 bps), +2 (≤2 bps); REJECT if >5 bps
   - Time-of-day modifier: +1 active window / -2 chop zone
8. **MIN_SCORE 9** to fire.
9. **Per-asset 240min cooldown** (defense-in-depth alongside runtime).
10. **Conviction-scaled leverage:** 5x at 9-10, 7x at 11-12, 10x at 13+.
11. **Emit top candidate** via `client.push_signal()` (direct HTTP POST to the runtime API).

NO execution code. NO position-tracking. NO DSL state. The runtime owns all of that.

---

## Bootstrap behavior

On first deploy, OI history is empty. First **12 cron ticks (1 hour)** build the rolling window with no signals emitted — output reports `note:"bootstrapping_history"`. After that, 1h delta is computable. After **48 ticks (4 hours)**, 4h confirmation also active.

This is normal and expected. New deployments need 1h to warm up; alert agents that 0 trades in the first hour is by design.

---

## Entry flow

```
Producer daemon (5 min tick)
  ↓ Update rolling OI history (60 samples × ~60 assets = ~5h × 60 = 300 cells)
  ↓ Detect 1h OI Δ >= 5% with price aligned (TOP quadrant)
  ↓ Score >= 9 (multi-factor confluence)
  ↓ client.push_signal(scanner="otter_signals", ...)
Runtime
  ↓ Schema-validates fields against runtime.yaml
  ↓ LLM gate (decision_model = ${OTTER_DECISION_MODEL})
  ↓ Pass-through unless malformed
  ↓ OPEN_POSITION via FEE_OPTIMIZED_LIMIT (maker-only, 60s, NO taker fallback)
DSL (runtime-managed, exits via maker-first)
  ↓ Phase 1 max_loss 12% (≈ 2.4% price stop at 5x)
  ↓ Phase 2 tiers (5/30, 10/55, 15/75, 20/85)
  ↓ hard_timeout 240min (4h positioning thesis horizon)
  ↓ weak_peak_cut 60min @ 1.5%, dead_weight_cut 90min
  ↓ Exit via FEE_OPTIMIZED_LIMIT (maker-first, 60s, taker fallback)
```

---

## Required env vars

The runtime YAML uses these substitutions:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID for notifications |
| `${OTTER_DECISION_MODEL}` | Bare model name for LLM gate. NO provider prefix. |

The producer reads:

| Var | Purpose | Default |
|---|---|---|
| `OTTER_WALLET` | Wallet (must match runtime YAML's wallet). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary name | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `otter_signals` |
| `OTTER_MARGIN_PCT` | Fraction of account value per slot | `0.25` |
| `OTTER_MIN_OI_DELTA_PCT` | Override 1h OI Δ floor | `5.0` |

---

## Producer install (on OpenClaw host)

Source path in the senpi-skills repo: `otter/`. Install destination on the OpenClaw host: `/data/workspace/skills/otter-strategy/`. The two names differ — repo uses `otter`, install dir uses the SKILL.md `name` field.

```bash
# 1. Pull the skill files (source: senpi-skills/main/otter/)
mkdir -p /data/workspace/skills/otter-strategy/{scripts,config}

for f in runtime.yaml scripts/otter-producer.py scripts/otter_config.py config/otter-config.json; do
  curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/otter/$f \
    -o /data/workspace/skills/otter-strategy/$f
done

# 2. Install the runtime
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
OTTER_DECISION_MODEL=<your-preferred-model> \
  openclaw senpi runtime create --path /data/workspace/skills/otter-strategy/runtime.yaml

# 3. Launch the producer daemon (5 min tick).
# OTTER_WALLET (NOT generic STRATEGY_ADDRESS) is required by the producer.
SENPI_AUTH_TOKEN=<your-token> \
OTTER_WALLET=0x... \
  nohup python3 -u /data/workspace/skills/otter-strategy/scripts/otter-producer.py \
  > /tmp/otter-producer.log 2>&1 &

# Note: first 12 ticks (1 hour) will report "bootstrapping_history" — by design.

# 4. Verify
openclaw senpi runtime list                              # expect: otter-tracker running
openclaw senpi status --runtime otter-tracker
senpi-helpers list                                       # daemon visible with recent LAST_TICK
senpi-helpers health otter-<wallet-suffix>               # exit 0 = healthy
senpi-helpers stats otter-<wallet-suffix> --hours 1      # signals posted + error histogram

# Inspect producer state:
ls -la /data/workspace/skills/otter-strategy/state/<wallet-hash>/
# Expect: producer.lock + oi-history.json with mtime in last few minutes.
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots (max concurrent positions) | 2 |
| Margin per slot | 25% of account value (default ~$250 on $1k starting) |
| Default leverage | 5x |
| Conviction-scaled leverage | 5x (score 9-10), 7x (11-12), 10x (13+) |
| Daily loss halt | 8% |
| Drawdown halt | 20% |
| Max entries per day | 8 |
| Max consecutive losses | 4 |
| Per-asset cooldown | 240 min |
| Asset universe | Top crypto perps with OI >= $1M (XYZ banned — different OI dynamics) |

---

## Scoring (max ~17 points)

| Component | Points | Rationale |
|---|---|---|
| 1h OI Δ tier | 4-6 | Primary signal — bigger flow = more conviction |
| 4h OI Δ confirmation (>= 10% same dir) | +2 | Sustained build, not a one-tick spike |
| 4h OI Δ contradiction (< 0) | -2 | Recent unwind contradicts 1h growth |
| 1h price Δ magnitude (>1% / >2%) | +1 / +2 | Strong price confirmation of flow |
| SM concentration aligned | +2 | Smart money positioned with the flow |
| Spread (≤5 / ≤2 bps) | +1 / +2 | Tighter spread = better fill quality |
| Time-of-day | -2 to +1 | UTC active hours bonus / chop penalty |

**MIN_SCORE 9** to fire. Hard gate: 1h OI Δ >= 5% (raw signal floor).

---

## Expected behavior

| Metric | Expected |
|---|---|
| First hour after deploy | 0 trades (bootstrapping history) |
| Days with no qualifying flow | Common (most days no asset has 5%+ OI growth + aligned price) |
| Trades per day | 0-3 |
| Avg hold | 30-180 min |
| Hard exit | 240 min |
| Win rate target | 55-65% |

**Silence is correct.** OI velocity events are infrequent — the agent should be quiet most of the time. Don't improvise.

---

## What's intentionally NOT here

- **No XYZ assets.** XYZ OI dynamics differ (commodities/equities OI is dominated by hedgers, not leveraged speculators). Bald Eagle's territory.
- **No bottom-quadrant trades.** OI ↓ + price-aligned moves are exhaustion signals; that's Pangolin/Owl's lane.
- **No taker fallback on entries.** OI velocity isn't urgent — if maker can't fill in 60s, the trade isn't ripe.
- **No funding-rate filter.** Funding is independent of OI velocity; would over-filter.
- **No counter-quadrant trades.** Otter follows flow, doesn't fade it.

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
