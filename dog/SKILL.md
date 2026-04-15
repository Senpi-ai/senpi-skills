---
name: dog-strategy
description: >-
  DOG v2.0 — The Contrarian Pup (SM Exhaustion Fader). Multi-asset
  contrarian scanner targeting BTC, ETH, SOL, HYPE. Trades AGAINST
  Smart Money consensus when the move is exhausted. Wide DSL lets
  reversals develop. v2.0 is a complete direction flip from v1.0
  after fleet audit found the original momentum-following thesis was
  systematically entering after exhausted moves.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐕 DOG v2.0 — The Contrarian Pup

Smart Money goes one way. Dog goes the other. The crowd is already in — let them eat the unwind.

---

## Why v2.0 is a complete rewrite

Dog v1.0 was "The Loyal Consistent Performer" — a multi-asset SM consensus FOLLOWER. The fleet audit on 2026-04-10 found the v1.0 signal was perfectly inverted. Real performance was -$61 gross; if you'd flipped every trade, it would have been +$61. HYPE alone caused -$91 of the -$105 net loss. The SM consensus scanner was systematically entering AFTER exhausted moves — buying tops and selling bottoms.

**v2.0 flips the thesis.** Instead of chasing SM consensus, fade it. When SM consensus is overwhelmingly strong AND the move is already extended (4h price > 2-3% in the SM direction), trade the OPPOSITE direction. The crowd has already committed; the unwind is the alpha.

### Key changes from v1.0

| Aspect | v1.0 | v2.0 |
|---|---|---|
| Direction | Trade WITH SM consensus | Trade OPPOSITE to SM consensus |
| Move-exhaustion | -3 penalty (don't chase) | +1-2 BONUS (bigger move = better fade) |
| Early-move bonus | +1 (jump in early) | Removed (early moves have nothing to fade) |
| Leverage | 10x flat | 7x base, 10x at score 12+ |
| Min score | 10 (very tight) | 8 (contrarian signals fire more often) |
| DSL hold | 120 min hard timeout | 360 min hard timeout (reversals take time) |
| Phase 2 tier 1 | 3% ROE / 50% lock (quick scalp) | 5% ROE / 20% lock (let it develop) |

## What it does

Dog scans BTC, ETH, SOL, HYPE every 3 minutes via `leaderboard_get_markets`. For each asset:

1. **Identify SM dominant direction** — which side has the heaviest top-trader concentration
2. **Verify the move is exhausted** — 4H price moved > 2-3% in the SM direction (mean reversion setup)
3. **Verify the unwind hasn't started** — if 15m velocity is collapsing, the fade may already be in progress (still good)
4. **Score the contrarian setup** — concentration, exhaustion, velocity, funding alignment
5. **Fire the FADE** — open OPPOSITE direction at 7x or 10x leverage with FEE_OPTIMIZED_LIMIT
6. **Hand off to DSL** — wide DSL lets the reversal develop over hours

## Why the contrarian thesis works

Hyperliquid is dominated by leverage traders chasing momentum. When a coin moves 3% in 4 hours and SM consensus piles in 15%+, the crowd is already maximum exposed. The unwind happens for two reasons:

1. **Funding pressure** — extreme positioning generates extreme funding rates, forcing the late entrants to capitulate
2. **Mean reversion** — overextended moves draw counter-trend liquidity from contrarian traders

Dog's edge is being on the unwind side BEFORE the capitulation accelerates. The `weak_peak_cut` and `dead_weight_cut` mechanisms protect against premature fades that don't reverse, and the wide Phase 2 tiers let real unwinds compound.

## Scoring

| Signal | Points |
|---|---|
| SM concentration ≥ 20% (DEEP_CROWD) | 4 |
| SM concentration 15-20% (HEAVY_CROWD) | 3 |
| SM concentration 10-15% (MODERATE_CROWD) | 2 |
| Trader count ≥ 100 | 1 |
| Move exhaustion (4H ≥ 3% in SM direction) | 2 |
| Move exhaustion (4H ≥ 2% in SM direction) | 1 |
| 15m velocity COLLAPSING (< -1.0) | 3 |
| 15m velocity FADING (-1.0 to -0.3) | 2 |
| 15m velocity COOLING (-0.3 to 0) | 1 |
| 1h velocity decelerating | 1 |
| 1H price reversing toward fade direction | 1 |
| 4h SM contribution weakening | 1 |
| Funding pays the fade direction | 1 |
| US session bonus | 1 |

**Min score: 8.** Leverage tiers: 7x base, 10x at score 12+.

### Hard gates

- **SM minimums**: pct ≥ 3%, traders ≥ 20
- **15m velocity must be ≤ 0.1**: if SM is still actively building, the move isn't exhausting yet — skip
- **XYZ DEX banned**: contrarian thesis is for crypto majors only
- **No duplicate positions**: max 1 position at a time
- **Cooldown**: 120 min after any trade on the same asset

## Key settings

| Setting | Value | Why |
|---|---|---|
| Assets | BTC, ETH, SOL, HYPE | Liquid majors only |
| Max positions | 1 | Concentration |
| Margin per trade | 30% | Smaller bets, survive drawdowns |
| Leverage | 7x base, 10x at score 12+ | Conservative — contrarian needs room |
| Min score | 8 | Tunable in scanner constants |
| Per-asset cooldown | 120 min | Patience between trades |
| Max daily entries | 3 | Quality over quantity |

## DSL configuration (wide, patient)

| Phase | Setting | Value | Why |
|---|---|---|---|
| 1 | max_loss_pct | 15% | Tighter than fleet 25% — contrarian losses should be small |
| 1 | retrace_threshold | 8 | Standard |
| 1 | consecutive_breaches_required | 3 | Standard |
| Time cuts | hard_timeout | 360 min | Reversals take time to develop |
| Time cuts | weak_peak_cut | 90 min, min 2.0% | Cut if peak ROE never breaks 2% in 90 min |
| Time cuts | dead_weight_cut | 30 min | Cut early stallers |
| 2 | Tier 1 | +5% / 20% lock | Don't bank too early — let it run |
| 2 | Tier 2 | +10% / 40% lock | |
| 2 | Tier 3 | +15% / 60% lock | |
| 2 | Tier 4 | +20% / 75% lock | |
| 2 | Tier 5 | +30% / 85% lock | |

## Fleet-standard guardrails

- Self-executing via `create_position` (no external action layer needed)
- Dynamic P&L-aware daily cap (PR #176)
- `has_resting_orders()` auto-cancels stale maker orders >10 min (PR #177)
- Stale-date bug fix in `load_trade_counter()` (PR #177)
- Conviction-scaled leverage capped at 10x (fleet H12 audit)
- Contrarian exhaustion gate (PR #175) — requires the 4H move to be >2.5% before fading

## ⛔ Critical agent rules

1. **Install path** is `/data/workspace/skills/dog-strategy/`
2. **THE SCANNER DOES NOT EXIT POSITIONS** — DSL handles all exits
3. **MAX 1 POSITION AT A TIME**
4. **MAX 3 ENTRIES PER DAY** (dynamic cap may lower this on drawdown)
5. **120-minute cooldown** between entries on the same asset
6. **MIN_SCORE 8** — never lower
7. **10x leverage max** — never increase
8. **30% margin per trade** — never increase
9. **Never modify the contrarian direction logic** without testing
10. **Verify runtime on every session start**

## Setup

### Install path

The skill must live at `/data/workspace/skills/dog-strategy/`. Package contents:

```
dog/
├── README.md                 # User-facing summary
├── SKILL.md                  # This file (LLM-facing)
├── runtime.yaml              # OpenClaw runtime + DSL preset (wide for contrarian)
├── config/
│   └── dog-config.json       # Wallet, strategy ID, chat ID
└── scripts/
    ├── dog-scanner.py        # Main contrarian scanner (v2.0)
    └── dog_config.py         # Helper module
```

### Pull from GitHub

```bash
mkdir -p /data/workspace/skills/dog-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/runtime.yaml \
  -o /data/workspace/skills/dog-strategy/runtime.yaml

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/SKILL.md \
  -o /data/workspace/skills/dog-strategy/SKILL.md

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/config/dog-config.json \
  -o /data/workspace/skills/dog-strategy/config/dog-config.json

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/scripts/dog-scanner.py \
  -o /data/workspace/skills/dog-strategy/scripts/dog-scanner.py

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/scripts/dog_config.py \
  -o /data/workspace/skills/dog-strategy/scripts/dog_config.py
```

### Set wallet and chat ID

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/dog-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/dog-strategy/runtime.yaml
```

Or set them in `config/dog-config.json`. The scanner also supports environment variables: `DOG_WALLET` and `DOG_STRATEGY_ID`.

### Install runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/dog-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

### Verify with a manual scan

```bash
python3 /data/workspace/skills/dog-strategy/scripts/dog-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat (no fade signal) — contrarian setups are intentionally selective.

### Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/dog-strategy/scripts/dog-scanner.py >> /tmp/dog-loop.log 2>&1; sleep 180; done' > /tmp/dog-nohup.log 2>&1 &

ps aux | grep dog-scanner | grep -v grep
tail -5 /tmp/dog-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked.

## Best for

- Operators who believe momentum chasing is a losing edge on Hyperliquid
- Multi-asset diversification across BTC/ETH/SOL/HYPE
- Patient holds (1-6 hours per trade — reversals take time)
- Counter-trend traders who want a deterministic execution layer

## Not for

- Momentum followers (use Wolverine, Phoenix, or Condor)
- High-frequency scalping (Dog targets 2-3 trades per day max)
- Single-asset specialists (use Wolverine for HYPE, Polar for ETH, Kodiak for SOL)
- Anyone who wants the scanner to make exit decisions (DSL handles all exits)

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). The Contrarian Pup.


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
