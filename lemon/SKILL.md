---
name: lemon-strategy
description: >-
  Degen fader. Counter-trades CHOPPY/DEGEN consensus on 12 crypto
  majors plus 4 XYZ macro assets when 15m velocity is exhausting.
  MACRO_TREND_GATE blocks crypto fades during BTC trends; decorrelated
  XYZ assets keep firing.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🍋 LEMON v2.0.0 — Degen Fader

## v2.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v1.3 fade scoring, MACRO_TREND_GATE, XYZ unban, leverage tiers (5x/7x/10x), MARGIN_PCT 30%, MIN_SCORE 9 all preserved verbatim. Migration: mcporter → SenpiClient, scanner-side `create_position` → producer `push_signal()` with LLM-gated runtime action, Python state files for daily cap → `runtime.yaml risk.guard_rails`, MARKET exits → FEE_OPTIMIZED_LIMIT.

The crowd is wrong. Lemon trades against the worst traders on the Hyperfeed when their consensus is fading.

## Thesis

Hyperliquid attracts a lot of low-quality, high-leverage retail traders ("degens"). The Senpi MCP `discovery_get_top_traders` tool classifies traders by Trading Consistency Score (TCS) — the bottom buckets are CHOPPY and DEGEN. These traders consistently lose money over time. **Counter-trading their consensus when their momentum is fading is a positive-edge strategy.**

Lemon scans 12 liquid crypto majors every 5 minutes for setups where:

1. **Smart money is concentrated** on one direction (≥3% pct of top traders gain, ≥20 traders)
2. **The 15m velocity is fading** (the move is exhausting — `contribution_pct_change_15m ≤ 0.1`, ideally negative)
3. **The 4H price is overextended** in the SM direction (mean-reversion setup)
4. **The 1H price is starting to reverse** toward the fade direction

When all gates pass and the score reaches MIN_SCORE (8), Lemon enters in the **OPPOSITE** direction to the SM consensus. Wide DSL gives the reversal time to develop.

## v1.1 changes from v1.0 (fleet hardening)

- Scanner calls `create_position` internally via mcporter (Wolverine self-executing pattern)
- `feeOptimizedLimitOptions` with FEE_OPTIMIZED_LIMIT order type
- Conviction-scaled leverage: 14+→20x, 12+→15x, 10+→10x, 8+→7x
- Margin at 50% per trade
- `has_resting_orders()` with reduceOnly filter prevents position stacking
- Hyperfeed multi-window contribution velocity (15m, 1h) scoring
- Move-exhaustion penalty/bonus structure
- No thesis exit — DSL manages all exits
- `MAX_DAILY_ENTRIES = 3`, `COOLDOWN_MINUTES = 120`
- `XYZ_BANNED = True` — equities don't follow the same crowding dynamics

## Tracked assets

```
BTC, ETH, SOL, HYPE, AVAX, DOGE, LINK, XRP, ADA, NEAR, UNI, AAVE
```

12 liquid crypto majors. XYZ DEX equities are explicitly banned — Lemon's thesis doesn't apply to macro-driven instruments.

## Pipeline

```
1. fetch_sm_data() — leaderboard_get_markets, filter to TRACKED_ASSETS, normalize fields
2. For each asset:
   - Skip if currently held
   - Skip if on per-asset cooldown
   - evaluate_fade(asset, sm_data) — score the contrarian setup
3. If candidates exist, sort by score, pick best
4. execute_entry() — call create_position via mcporter
5. Mark cooldown, increment trade counter
```

## Scoring (max ~16 points)

### Hard gates (any failure = reject)

- `pct_of_top_traders_gain ≥ 3%`
- `trader_count ≥ 20`
- `contribution_pct_change_15m ≤ 0.1` (SM is no longer building, the move is exhausting)
- Asset must be in `TRACKED_ASSETS`
- Not currently held
- Not on per-asset cooldown
- XYZ DEX banned

### Scoring components

| Signal | Points |
|---|---|
| DEGEN_PILE: SM concentration ≥ 20% | 4 |
| HEAVY_CROWD: 12-20% | 3 |
| CROWDED: 7-12% | 2 |
| LEANING: 3-7% | 1 |
| 15M_COLLAPSING: contrib_15m < -2.0 | 3 |
| 15M_FADING: contrib_15m -2.0 to -0.5 | 2 |
| 15M_COOLING: contrib_15m -0.5 to -0.1 | 1 |
| 1H_FADING: contrib_1h < -0.5 | 1 |
| OVEREXTENDED 4H (≥3% in SM direction) | 2 |
| EXTENDED 4H (≥1.5% in SM direction) | 1 |
| 1H_REVERSING toward fade direction | 1 |
| 4H_SM_WEAKENING (4h contrib < -1.0) | 1 |
| FUNDING_PAYS_FADE (funding direction matches fade) | 1 |
| US_SESSION (13-21 UTC) | 1 |

**Min score: 8.** Leverage tiers: 7x base, 10x at score 10+, 15x at score 12+, 20x at score 14+ (capped by fleet `MAX_LEVERAGE = 20`).

## Key settings

| Setting | Value | Why |
|---|---|---|
| Tracked assets | 12 crypto majors | Liquid, follow crowding dynamics |
| Max positions | 1 | Concentration |
| Margin per trade | 50% | High conviction commits high capital |
| Min score | 8 | Tunable; higher = fewer/better trades |
| Per-asset cooldown | 120 min | Patience between trades |
| Max daily entries | 3 (dynamic cap-aware) | Quality over quantity |
| Leverage tiers | 7x / 10x / 15x / 20x | Fleet conviction-scaled |
| MAX_LEVERAGE | 20 | Fleet hard cap (H12 audit) |
| XYZ banned | True | Equities don't follow the thesis |

## DSL configuration

Wide tiers for fade trades that need time to mean-revert:

| Setting | Value | Notes |
|---|---|---|
| `hard_timeout` | 480 min (8h) | Mean reversion can take hours |
| `weak_peak_cut` | 60 min, min 2.0% | Cut if peak ROE never breaks 2% in 60 min |
| `dead_weight_cut` | 20 min | Cut early stallers |
| `phase1.max_loss_pct` | 15% | Tighter than fleet 25% — fades fail fast |
| `phase2.tier_1` | +5% / 20% lock | Don't bank too early |
| `phase2.tier_2` | +10% / 40% lock | |
| `phase2.tier_3` | +15% / 60% lock | |
| `phase2.tier_4` | +20% / 75% lock | |
| `phase2.tier_5` | +30% / 85% lock | |
| `phase2.tier_6` | +50% / 92% lock | Maximum protection on huge wins |

## Fleet-standard guardrails

- Self-executing via `create_position` (no external action layer needed)
- Dynamic P&L-aware daily cap (PR #176)
- `has_resting_orders()` auto-cancels stale maker orders >10 min (PR #177)
- Stale-date bug fix in `load_trade_counter()` (PR #177)
- Per-asset cooldown (120 min)
- Conviction-scaled leverage capped at 20x

## ⛔ Critical agent rules

1. **Install path** is `/data/workspace/skills/lemon-strategy/`
2. **THE SCANNER DOES NOT EXIT POSITIONS** — DSL handles all exits
3. **MAX 1 POSITION at a time**
4. **MAX 3 ENTRIES PER DAY** (dynamic cap may lower this on drawdown)
5. **Thesis is FADING, not following** — Lemon trades OPPOSITE to SM consensus by design. Direction inversion is intentional.
6. **No XYZ DEX trades** — equities don't follow the crowding/exhaustion thesis
7. **120-minute per-asset cooldown** — never bypass
8. **Verify runtime on every session start**

## Setup

### Pull from GitHub

```bash
mkdir -p /data/workspace/skills/lemon-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/runtime.yaml \
  -o /data/workspace/skills/lemon-strategy/runtime.yaml

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/SKILL.md \
  -o /data/workspace/skills/lemon-strategy/SKILL.md

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/config/lemon-config.json \
  -o /data/workspace/skills/lemon-strategy/config/lemon-config.json

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/scripts/lemon-scanner.py \
  -o /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/scripts/lemon_config.py \
  -o /data/workspace/skills/lemon-strategy/scripts/lemon_config.py
```

### Set wallet and chat ID

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/lemon-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/lemon-strategy/runtime.yaml
```

Or set in `config/lemon-config.json`. Environment variables: `LEMON_WALLET`, `LEMON_STRATEGY_ID`.

### Install runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/lemon-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

### Verify

```bash
python3 /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py
```

Expected: clean exit, JSON output with `"_lemon_version": "1.1"`. Most likely first run shows a heartbeat (no fade signal).

### Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py >> /tmp/lemon-loop.log 2>&1; sleep 300; done' > /tmp/lemon-nohup.log 2>&1 &

ps aux | grep lemon-scanner | grep -v grep
tail -5 /tmp/lemon-loop.log
```

5-minute cadence (slower than other agents because fade setups are rarer).

## Best for

- Operators who believe momentum chasing on Hyperliquid is a losing edge
- Multi-asset diversification across 12 crypto majors
- Mean-reversion / contrarian strategies
- Patient holds (1-8 hours per trade — reversals take time)
- Operators who want a deterministic execution layer

## Not for

- Momentum followers (use Phoenix, Wolverine, Condor, or Raptor)
- Single-asset specialists (use Wolverine for HYPE, Polar for ETH, Kodiak for SOL)
- High-frequency scalping
- XYZ DEX equities (use Bald Eagle or Kestrel)
- Anyone who wants the scanner to make exit decisions (DSL handles all exits)

## Track record

Lemon has been one of two consistently green agents in the Senpi Predators fleet (37 red / 2 green as of 2026-04-14). The thesis works because the underlying behavior pattern (degens piling into exhausted moves) is structural, not market-regime-dependent.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). The Degen Fader.
