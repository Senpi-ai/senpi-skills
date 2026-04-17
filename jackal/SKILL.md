---
name: jackal-strategy
description: >-
  JACKAL v1.0 — The Smart Stalker. The fleet's first SECONDARY-SIGNAL
  agent. Observes other top-performing Senpi users' trades (via the new
  any-user-lookup MCP capability) and executes filtered,
  consensus-weighted trades with its own sizing and its own DSL.
  Maintains a rolling two-tier pool of tracked traders — Active Pool
  (~20-30 qualified) + Watchlist (~100-200 candidates) — scored by
  trajectory-velocity rather than absolute rank. Acts only when source
  quality + multi-trader consensus + independent Senpi TA confirmation
  all align. Gold signals (newly-promoted source + existing pool
  consensus within 2h) trigger max-sizing entries.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐺 JACKAL v1.0 — The Smart Stalker

The jackal watches bigger predators hunt. It doesn't chase kills blindly
— it waits for the moment when multiple hunters converge on the same
prey, confirms the kill is worth joining, and then acts with its own
instincts. Jackal is the fleet's first secondary-signal agent.

> **Note:** This replaces an earlier Jackal v2.0 concept (FOX v1.6
> config-override / First Jump pyramider). That design lives in
> `legacy-fox-pyramid-concept/` for reference.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/jackal-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 3 POSITIONS concurrent
### RULE 4: This is NOT a mirror strategy. Custom execution with independent filters.
### RULE 5: Never act on a signal without independent TA confirmation.
### RULE 6: Per-source exposure cap 40% — no single trader dominates.
### RULE 7: HARD_STOP circuit breaker triggers at -25% drawdown.

---

## Why This Agent Exists

Every other Senpi predator generates signals from MARKET data — price,
volume, SM positioning, funding. None use the actions of other
top-performing Senpi users as a signal source. That's a missing layer.

The new any-user-lookup MCP capability (`strategy_list` with `userIds`
filter, plus `strategy_get_clearinghouse_state` on any wallet) makes it
possible to observe what other Senpi users are doing in real time.
Jackal turns that observation into a trading signal.

Naive "copy the winner" fails because:
- Winners regress (pr0br000 went +$1,275 week 3 → -$500 week 4)
- Signals lag (by the time you copy, trade is half-over)
- Style-mismatches amplify risk (scalpers' signals aren't copyable)
- No filter layer means you copy losers too

Jackal solves these with a 4-layer funnel.

---

## Architecture: 4-layer funnel

```
LAYER 1 — ELIGIBILITY  (every 6h full refresh, 1h rescore)
  Trajectory-based scoring (not rank-based):
    pnl_trajectory  35%   — improving trend beats absolute level
    rank_velocity   25%   — rising fast catches emerging movers
    recent_outcomes 15%   — last 5 trades W/L pattern
    win_loss_ratio  10%   — classic asymmetry
    fee_efficiency  10%   — not overtrading
    consistency      5%   — positive days in last 14
  
  Hard disqualifiers:
    - fee_drag > 40% (overtrader)
    - avg_winner_hold < 2h (scalper — can't copy at our latency)
    - drawdown_24h > 10% (active blowup)
    - ratio < 1.5 (no edge)
    - <10 closed trades or <7 days old (insufficient history)

LAYER 2 — POSITION DISCOVERY  (every 3 min)
  For each Active Pool member: strategy_get_clearinghouse_state
  (with forceFetch=true for <60s latency)
  Diff against last-known → detect NEW positions
  Track first_seen_ts for age filtering

LAYER 3 — SIGNAL SCORING  (per detected position)
  score = 
    0.40 × source_quality      (avg quality_score of source traders)
  + 0.30 × consensus           (1.0x for 1 source, 1.8x for 2, 3.0x for 3+)
  + 0.30 × position_freshness  (peaks at 1h, declines past 4h)
  + GOLD_BONUS (+15)           (newly-promoted source + 2+ pool consensus)

LAYER 4 — INDEPENDENT CONFIRMATION + EXECUTION
  Senpi TA gate: 4h trend, 1h momentum, SM alignment
  If any fails: SKIP even at high score
  If passes: size by score tier (20/35/55% margin × 3/5/7x leverage)
  Own DSL manages exit — never wait for source to close
```

---

## Two-tier pool mechanics

**Watchlist (~100–200 traders):**
- Pulled from Arena leaderboard + top Senpi points leaderboard
- Polled for re-eligibility every 6h (full refresh) / 1h (rescore)
- Jackal does NOT act on their trades
- Entry/exit automatic — crosses score thresholds to promote

**Active Pool (~20–30 traders):**
- Quality score ≥ 70 for 6+ consecutive hours
- Polled every 3 min (scan) with forceFetch=true
- Jackal acts on their trades subject to consensus + TA filters
- Demoted if score < 50 for 12h OR 24h drawdown > 10%
- 48h re-promotion cooldown after demote

**Catching pr0br000 counterfactual (Arena Weeks 2-3):**

| Date | Rank | 7d PnL | quality_score | Tier |
|---|---:|---:|---:|---|
| Mar 28 | unranked | -$200 | below threshold | Watchlist low-priority |
| Apr 2 | 40 | +$400 | rising | Watchlist monitored |
| **Apr 3** | **20** | **+$800** | **crosses threshold** | **→ Active Pool** |
| Apr 4 | 8 | +$1,200 | high | Active (Jackal acts) |
| Apr 14 | 1 | +$3,100 peak | high | Active |
| **Apr 17** | **38** | **-$500 24h** | **drawdown trigger** | **→ Watchlist (demoted)** |

Jackal would have acted on pr0br000's trades from April 3 to April 16 —
catching WLD, HEMI, MON winners. Auto-demoted BEFORE the April 17 ZEC
disaster because the 24h drawdown trigger fires at -10%.

---

## The GOLD SIGNAL

The highest-conviction pattern: a trader who was just promoted from
Watchlist to Active Pool (within last 24h) opens a position that 2+
already-Active Pool members are also in. This means:
- A new rising star is agreeing with proven smart money
- Signal is BOTH new (emerging mover) AND confirmed (consensus)
- Historical win pattern aligns with existing pool

When Jackal detects a GOLD SIGNAL:
- Max sizing: 55% margin × 7x leverage
- Bypass score tiers (gold_signal_flag = true)
- Still requires TA confirmation — no TA override

This is where Jackal's asymmetric upside lives.

---

## Sizing tiers

| Score | Tier | Margin | Leverage |
|---|---|---:|---:|
| 65-74 | BASE | 20% | 3x |
| 75-84 | STRONG | 35% | 5x |
| **85+** | **GOLD** | **55%** | **7x** |

Never exceeds 7x. Patient hold profile — multi-day consensus signals
play out slowly. Leverage + fees would kill the edge.

---

## DSL — Consensus-aware patience

Jackal inherits Python's patience DSL profile but with adjustments:
- Wider Phase-1 floor (22% vs Python's 20%) — consensus signals need
  breathing room
- Looser early Phase-2 (+6%→20% lock vs Python's +5%→15%) — even
  consensus trades need runway

| Mechanism | Value | Rationale |
|---|---|---|
| hard_timeout | 4320 min (72h / 3 days) | Consensus trades typically play out in 1-3 days |
| weak_peak_cut | 480 min (8h) @ 3% min | Consolidations on consensus moves |
| dead_weight_cut | 240 min (4h) | Patient but not asleep |
| Phase 1 max_loss | 22% | Room for consensus shakeouts |
| Phase 1 retrace | 10% | |
| Phase 2 tier 1 | +6% → 20% lock | Loose first lock |
| Phase 2 tier 2 | +14% → 40% | |
| Phase 2 tier 3 | +28% → 62% | |
| Phase 2 tier 4 | +50% → 80% | |
| Phase 2 tier 5 | +100% → 90% | Monster trail |
| Phase 2 tier 6 | +200% → 94% | Ultra-rare (pr0br000-class) |

---

## Risk controls

| Control | Value |
|---|---|
| Max concurrent positions | 3 |
| Daily entry cap | 5 (dynamic by PnL) |
| Per-source exposure cap | 40% of budget |
| Per-asset cooldown (after exit) | 6h |
| Same-direction cooldown | 4h |
| Position age gate | 15 min – 8 hours (sweet spot 1h) |
| Consensus window | 2h (same coin+direction) |
| HARD_STOP drawdown | -25% |

---

## What Jackal Does NOT Do

1. **Does NOT use Senpi's native MIRROR strategy.** Those are passive
   single-trader copies. Jackal is a multi-source custom strategy that
   uses MCP read capability + own execution.

2. **Does NOT copy scalpers.** Traders whose avg winning hold is <2h
   get filtered out. Latency makes their edge uncopyable.

3. **Does NOT copy a trader's size or exit.** Own sizing (conviction
   score) and own DSL. A source trader holding through drawdown doesn't
   mean we hold.

4. **Does NOT require perfect source stability.** Sources can lose
   trades — they just can't dominate the pool. Per-source 40% cap
   + auto-demotion on drawdown protects against one-source reliance.

5. **Does NOT chase rank-1 traders who are peaking.** Trajectory scoring
   means cooling-off stars get demoted naturally.

---

## Install

```bash
mkdir -p /data/workspace/skills/jackal-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/runtime.yaml -o /data/workspace/skills/jackal-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/SKILL.md -o /data/workspace/skills/jackal-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/config/jackal-config.json -o /data/workspace/skills/jackal-strategy/config/jackal-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal-scanner.py -o /data/workspace/skills/jackal-strategy/scripts/jackal-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal_config.py -o /data/workspace/skills/jackal-strategy/scripts/jackal_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal_pool.py -o /data/workspace/skills/jackal-strategy/scripts/jackal_pool.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/jackal-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/jackal-strategy/runtime.yaml
```

## Install runtime + create scanner cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/jackal-strategy/runtime.yaml
openclaw senpi runtime list
# Create 3-minute cron: python3 /data/workspace/skills/jackal-strategy/scripts/jackal-scanner.py
```

## First-run behavior

On the first run, Jackal has no pool yet. The scanner will:
1. Fetch Arena leaderboard + Senpi points leaderboard (~100-200 candidates)
2. Score each candidate's recent trade history + PnL trajectory
3. Build initial Watchlist
4. No candidate will be promoted to Active Pool yet (requires 6h consistency)
5. First real trades typically start 6-24 hours after first run

This is expected and desired. Patience from setup.

---

## Operational cost

- Watchlist refresh: 6h cadence, ~100-200 MCP calls per refresh (batched over several minutes)
- Score refresh: 1h cadence, ~30-100 MCP calls per refresh
- Position scan: 3min cadence, ~20-30 MCP calls per scan
- Total: ~1.2 MCP calls/second sustained

---

## Changelog

### v1.0 (2026-04-17) — FIRST SHIP
- First secondary-signal agent in the fleet
- Two-tier pool architecture (Watchlist + Active Pool)
- Trajectory-based quality scoring (6 components, weighted composite)
- Emerging-mover detection via rank velocity
- GOLD SIGNAL pattern (new promotion + existing consensus)
- Consensus multiplier (3+ traders = 3.0x score boost)
- Independent Senpi TA confirmation gate
- Own sizing + own DSL (not native MIRROR)
- Hard disqualifiers: scalper filter, fee-drag cap, drawdown trigger
- Per-source 40% exposure cap
- 48h re-promotion cooldown
- Patient DSL profile (72h timeout, wide Phase 1)

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
