---
name: scorpion-strategy
description: >-
  SCORPION v2.0 — Altcoin Swarm Hunter. Detects coordinated risk-off
  events where 5+ altcoins simultaneously attract SM concentration,
  then trades the highest-conviction target within the swarm.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦂 SCORPION v2.0 — Altcoin Swarm Hunter

The swarm forms. Scorpion picks the weakest prey.

## The Thesis

When SM goes risk-off, altcoins dump in a correlated swarm. The top traders
on Hyperliquid make their biggest returns not on BTC/ETH, but on altcoin
SHORTs: LIT, TAO, MON, FARTCOIN, VVV, ZRO, CC.

On April 3, 2026, trader 0x039c was up **99.8% in 4 hours** with 33 positions.
Biggest winners were all altcoin SHORTs: LIT +$11.6K, FARTCOIN +$6.3K,
TAO +$4.9K. The same day, the SM leaderboard showed 7+ altcoins simultaneously
at >2% SM SHORT concentration — a clear coordinated swarm.

**No current agent detects this pattern.** Our agents evaluate assets individually.
Scorpion detects the *meta-pattern* first — then picks the best target.

### The Data Advantage

| Signal | What it means | Who uses it |
|---|---|---|
| SM concentration on BTC | Smart money is trading BTC | Grizzly, Cobra |
| SM concentration on HYPE | Smart money is trading HYPE | Cheetah |
| SM concentration on ETH | Smart money is trading ETH | Polar |
| **5+ altcoins all SM SHORT** | **Coordinated risk-off event** | **Only Scorpion** |

## ⛔ CRITICAL AGENT RULES
### RULE 1: Install path is `/data/workspace/skills/scorpion-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION at a time.
### RULE 4: MAX 3 ENTRIES PER DAY. 90-minute cooldown. 120-minute per-asset cooldown.
### RULE 5: Must detect swarm (5+ altcoins) BEFORE evaluating individual targets.
### RULE 6: XYZ equities are BANNED from trading.
### RULE 7: Major assets (BTC, ETH, SOL, HYPE) are EXCLUDED — those are Cobra's territory.
### RULE 8: USE FEE_OPTIMIZED_LIMIT for all entries.

## Scanner Logic (1 API call)

```
1. leaderboard_get_markets (limit=100)

STEP 1 — SWARM DETECTION:
   → Count non-major, non-XYZ altcoins with SM >2% in each direction
   → If SHORT count >= 5: SHORT swarm confirmed
   → If LONG count >= 5: LONG swarm confirmed
   → If neither: NO TRADE (no coordinated event)

STEP 2 — TARGET SELECTION (only if swarm confirmed):
   → Score each altcoin in the swarm:
     - Swarm Size: >=7 = +2, >=5 = +1
     - SM Concentration: >10% = +2, >5% = +1
     - Price Confirmation: >1% in direction = +2, >0.5% = +1
     - Trader Count: >=50 = +1
     - Contribution Velocity: >3% = +1

   MIN_SCORE: 5/8

   → Pick highest-scoring target
   → ENTER with $350 margin, 5x leverage, FEE_OPTIMIZED_LIMIT
```

## DSL Configuration (Altcoin-Optimized)

Altcoins are more volatile than majors. They need wider stops and more time.

**Phase 1 (Loss Protection):**
- Max loss: -20% (wider than Cobra's -15% — alts bounce harder)
- Retrace from high water: 10% (alts retrace 5-8% before continuing)
- 3 consecutive breaches required
- Phase 1 max time: 45 minutes

**Phase 2 (Profit Locking — let altcoin trends develop):**
- +5% → lock 20% (very loose — let it run)
- +10% → lock 40%
- +15% → lock 55%
- +20% → lock 70%
- +30% → lock 82%
- +50% → lock 90% (huge winners get maximum room)

**Timeouts:**
- Hard timeout: 240 minutes (4 hours — altcoin moves are slower to develop)
- Weak peak cut: 90 minutes at +3% minimum
- Dead weight cut: 45 minutes

## Why This Works

1. **Pattern detection > single-asset detection.** When 7 altcoins simultaneously
   attract SM SHORT concentration, something systemic is happening. Individual
   signals might be noise. Correlated signals are conviction.

2. **Untapped altcoin alpha.** No other Predator agent trades LIT, TAO, MON,
   FARTCOIN, VVV, ZRO, or CC. These are the assets generating the most SM PnL
   right now. We're leaving the highest-ROE trades on the table.

3. **Higher move magnitude.** BTC moves 0.2% in 4 hours. LIT moves 3.1%. At the
   same leverage, the altcoin trade has 15x the ROE impact.

4. **Complementary to Cobra.** Cobra trades the #1 SM asset (usually a major).
   Scorpion trades the #1 altcoin within a confirmed swarm. They never overlap.

## ROE Math

$350 margin × 5x leverage = $1,750 notional position
LIT drops 3% (today's actual move): $1,750 × 0.03 = $52.50 profit
$52.50 / $1,000 account = **5.25% ROE per trade**

With 3 entries/day over 5 remaining Arena days = 15 potential trades.
Need 4 winners at 5%+ each = **+20% weekly ROE** with room for losers.

## Runtime Setup
```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/scorpion-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/scorpion-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/scorpion-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

## Bootstrap Gate
Verify runtime + status + scanner cron (90s, main).
CRITICAL: The cron must be configured to call `create_position` via Senpi MCP
when the scanner outputs a signal with an entry block.
Send: "🦂 SCORPION v2.0 online. Swarm hunter. Watching for coordinated altcoin risk-off."

## License
MIT — Built by Senpi (https://senpi.ai).
