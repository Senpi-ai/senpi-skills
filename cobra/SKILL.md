---
name: cobra-strategy
description: >-
  COBRA v1.1 — Arena Sprint Predator. Trades the single most dominant
  SM asset with maximum conviction. Fee-optimized maker orders.
  Wider DSL lets positions breathe. Designed to win Arena weeks.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐍 COBRA v1.1 — Arena Sprint Predator

SM dominates. Cobra strikes. One asset. Maximum conviction.

## What Makes Cobra Different

Every other Predator agent is built for long-term edge — patient entry, perfect scores, wide DSL. Cobra is built to win Arena weeks.

The Arena rewards ROE% over 7 days. The winning strategy is 2-4 massive conviction trades that ride the dominant SM trend. The #1 Arena agent won with +62% ROE on just 12 trades.

### Fee Lesson from the Fleet

The Predators data tells a clear story about fees:

| Agent | Trades | Fees | Profit | Profit:Fee Ratio |
|---|---|---|---|---|
| Condor v2.0 | 5 | $2.71 | +$37.43 | **13.8:1** |
| Cheetah | 53 | $23.12 | +$40.58 | 1.8:1 |
| Polar | 170 | $239.27 | +$38.65 | 0.16:1 |

Cobra targets Condor's efficiency: few trades, big impact, minimal fees.

### Cobra's approach:
- **Only trades the #1 SM asset** — never scatters across multiple assets
- **$400 margin per trade** (40% of budget) for concentrated ROE impact
- **FEE_OPTIMIZED_LIMIT orders** — maker first, taker fallback at 30s. ~50% fee savings vs market orders.
- **Wide DSL** — 180min timeout, 8% retrace, 45min dead weight. Let positions breathe.
- **3 entries per day max** — quality over quantity
- **10x leverage on majors** (BTC/ETH/SOL/HYPE), 5x on others

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/cobra-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION at a time.
### RULE 4: MAX 3 ENTRIES PER DAY. 90-minute cooldown. 120-minute per-asset cooldown.
### RULE 5: ONLY trade the #1 SM dominant asset. Never scatter.
### RULE 6: XYZ equities are BANNED at parse level.
### RULE 7: USE FEE_OPTIMIZED_LIMIT for all entries. Never market orders.

## Scanner Logic (2 API calls)

```
1. leaderboard_get_markets (limit=20)
   → Find #1 non-XYZ asset by SM concentration
   → Gate: Must be >10% SM dominance

2. Score the dominant asset:
   - SM Dominance: >15% = +3, >10% = +2, >5% = +1
   - Contribution Surge: >5% = +2, >2% = +1
   - 4H Trend Confirms: >0.5% in direction = +1
   - 1H Trend Confirms: >0.2% in direction = +1
   - Deep Consensus: >200 traders = +1

   MIN_SCORE: 5/8
   
   → If score >= 5: ENTER with $400 margin at 10x (majors) or 5x
   → Order type: FEE_OPTIMIZED_LIMIT (maker first, taker fallback 30s)
```

## DSL Configuration (Fee-Conscious, Position-Breathing)

**Phase 1 (Loss Protection):**
- Max loss: -15%
- Retrace from high water: 8% (wide — let volatile assets breathe)
- 3 consecutive breaches required
- Phase 1 max time: 45 minutes

**Phase 2 (Profit Locking — let winners run):**
- +5% → lock 25% of high water
- +10% → lock 45%
- +15% → lock 60%
- +20% → lock 75%
- +30% → lock 85%
- +50% → lock 92%

**Timeouts:**
- Hard timeout: 180 minutes (3 hours — positions need time)
- Weak peak cut: 60 minutes at +3% minimum
- Dead weight cut: 45 minutes

## Why This Wins Arena

1. **Concentration**: $400 on one trade. A 10% move at 10x = +$400 = **40% ROE** on the $1K account.

2. **Fee efficiency**: FEE_OPTIMIZED_LIMIT saves ~50% vs market orders. Over a week, that's the difference between +5% and +8% ROE.

3. **Breathing room**: 180-minute timeout and 8% retrace means positions ride through volatility instead of getting stopped out on noise and re-entering (which churns fees).

4. **SM alignment**: Only trading when SM dominance is extreme (>10%). When 300+ top traders are positioned the same way, the trend usually has hours to develop — not minutes.

5. **Fewer trades**: 3/day max, 90-min cooldown. Over 7 days that's 21 max entries. Only need 2-3 big winners.

## Execution Details (Scanner-Level, Not YAML)

Entry: `FEE_OPTIMIZED_LIMIT` with `ensureExecutionAsTaker: true` and `executionTimeoutSeconds: 30`
- Places an ALO (Add Liquidity Only) maker order
- If not filled in 30 seconds, cancels and resubmits as market order
- Maker fees are ~0.02% vs taker fees of ~0.05%
- On a $400 × 10x = $4,000 notional position, this saves ~$1.20 per trade

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/cobra-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/cobra-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/cobra-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (90s, main).
CRITICAL: The cron must be configured to call `create_position` via Senpi MCP when the scanner outputs a signal with an entry block. If the cron just runs the scanner and exits, trades will never execute.
Send: "🐍 COBRA v1.1 online. Arena predator. Fee-optimized. Hunting the #1 SM rotation."

## License
MIT — Built by Senpi (https://senpi.ai).
