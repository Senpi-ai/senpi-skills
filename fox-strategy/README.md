# FOX v0.1 — Sniper Trading for Hyperliquid

FOX is an autonomous trading strategy for Hyperliquid perpetuals via Senpi agents. It hunts for early-stage movers, enters fast, and cuts losers faster than anything else in the Senpi skill ecosystem.

**FOX is the data-driven evolution of WOLF.** Where WOLF casts a wide net, FOX is a sniper. Every rule in FOX exists because a real trade proved it necessary.

---

## FOX vs WOLF — Choosing the Right Skill

FOX and WOLF are independent strategies that can run side by side on the same Senpi agent without interference. They have separate scripts, state files, wallets, and cron jobs. Pick one, run both, or switch between them.

| | **WOLF v6** | **FOX v0.1** |
|---|---|---|
| **Philosophy** | Cast a wide net, catch many moves | Sniper mode — fewer trades, higher conviction |
| **Entry signals** | FIRST_JUMP + IMMEDIATE_MOVER, min 4 reasons | Scoring system (6+ pts), time-of-day weighting, rank jump minimums |
| **Market regimes** | BULLISH = long, BEARISH = short | Same + NEUTRAL regime support (both directions at higher threshold) |
| **Phase 1 timing** | 90min hard timeout, 45min weak peak, 30min dead weight | 30min / 15min / 10min — cuts losers 3x faster |
| **Max ROE loss** | ~30% (0.03/leverage floor) | ~20% (0.02/leverage floor) |
| **Conviction scaling** | Fixed Phase 1 rules for all trades | Score-based: high-conviction trades get more room, low-conviction get cut fast |
| **Position sizing** | Dynamic slots (3-6 based on daily PnL) | Tiered margin (entries 1-2 at $1,450, 3-4 at $950, 5-6 at $450) |
| **Re-entry** | No | Yes — if direction was right but timing was off, re-enters at 75% size |
| **Time-of-day** | No filter | +1 pt for 04-14 UTC, -2 pts for 18-02 UTC (evening trades need higher conviction) |
| **DSL version** | v4 (combined runner) | v5 (per-strategy, HL stop-loss sync, auto-cleanup) |
| **Cron architecture** | 7 crons | 8 crons (adds market regime refresh) |
| **Best for** | Traders who want broad market coverage and more at-bats | Traders who want fewer, higher-quality entries with aggressive risk management |

### Why FOX exists

WOLF v7 traded 14 live positions: 2 wins, 12 losses. But the direction was right 85% of the time — 11 of 13 trades moved the right way. The losses came from three problems: entering too late (small rank jumps), holding losers too long (90min timeouts), and giving back too much on stops (30% max loss).

FOX fixes all three. Every optimization is backed by data from those 14 trades. The tighter Phase 1 timing alone would have saved over $500 in losses. The time-of-day filter would have blocked all 6 evening trades that lost. The rank jump minimum would have filtered the small-jump entries that never worked.

---

## Architecture

8 scripts, 8 cron jobs, independent state. Depends on the DSL v5 skill for trailing stops.

| # | Job | Interval | Purpose |
|---|-----|----------|---------|
| 1 | Emerging Movers | 3min | Hunt FIRST_JUMP signals with v7 scoring |
| 2 | DSL v5 | 3min | Trailing stop exits + HL stop-loss sync |
| 3 | SM Flip Detector | 5min | Conviction collapse cuts |
| 4 | Watchdog | 5min | Per-strategy margin buffer, liquidation distances |
| 5 | Portfolio Update | 15min | Per-strategy PnL reporting |
| 6 | Opportunity Scanner | 15min | 4-pillar scoring with BTC macro context |
| 7 | Market Regime | 4h | BULLISH / BEARISH / NEUTRAL classification |
| 8 | Health Check | 10min | Orphan DSL detection, state validation |

### File Structure

```
scripts/
  fox_config.py              # Shared config (all scripts import this)
  fox-emerging-movers.py     # Primary scanner
  fox-opportunity-scan-v6.py # Secondary scanner
  fox-sm-flip-check.py       # SM conviction flip detector
  fox-monitor.py             # Watchdog
  fox-health-check.py        # State validation
  fox-market-regime.py       # Regime detector
  fox-setup.py               # Setup wizard

skills/
  fox-strategy/
    SKILL.md                 # Full strategy documentation
    references/              # Cron templates, state schemas, API reference
  dsl-dynamic-stop-loss/
    SKILL.md                 # DSL v5 documentation
    scripts/dsl-v5.py        # Trailing stop engine
```

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` should show `senpi`)
2. Create a strategy wallet via `strategy_create_custom_strategy`
3. Fund it via `strategy_top_up`
4. Run setup: `python3 scripts/fox-setup.py --wallet 0x... --strategy-id UUID --budget 6500 --chat-id 12345`
5. Create the 8 OpenClaw crons from `skills/fox-strategy/references/cron-templates.md`

To add a second strategy, run `fox-setup.py` again with a different wallet and budget.

---

## Key Innovations

**Scoring system.** Replaces WOLF's "minimum 4 reasons" with a weighted point system. FIRST_JUMP is worth 3 points, CONTRIB_EXPLOSION 2 points, BTC alignment 1 point. Minimum 6 points to enter. This lets a strong FIRST_JUMP with velocity enter immediately, while weak signals with many small reasons get filtered out.

**Conviction-scaled Phase 1.** Entry score determines how much room a position gets. A 10+ point entry gets 60 minutes and a 30% max loss. A 6-point entry gets 30 minutes and 20% max loss. High-conviction trades survive initial volatility. Low-conviction trades get cut fast.

**Re-entry logic.** When FOX cuts a Phase 1 position and the asset keeps moving in the original direction, it can re-enter within 2 hours at 75% size. Direction was right 85% of the time — re-entry captures the moves that timing initially missed.

**Time-of-day filtering.** 0% win rate across 6 evening trades. FOX penalizes entries between 18:00-02:00 UTC by 2 points, effectively requiring score 8+ to enter during those hours.

**DSL v5 with HL sync.** Stop losses are synced to Hyperliquid as native orders via `edit_position`. When price hits the stop, Hyperliquid executes instantly — no waiting for the next 3-minute cron tick.

---

## Running FOX and WOLF Together

FOX and WOLF are fully independent. They use separate config files (`fox-strategies.json` vs `wolf-strategies.json`), separate state directories, separate scripts, and separate cron jobs. You can run both on the same agent pointed at different strategy wallets.

The only shared resource is `market-regime-last.json`, which both can read. If both are running, whichever regime cron fires last writes the file — this is fine since they use the same regime logic.

---

## Changelog

### v0.1 (current)
- Forked from Wolf v7 + v7.1 data-driven optimizations
- Scoring system replaces min-reasons filter
- Phase 1 timing: 30/15/10min (was 90/45/30)
- Absolute floor tightened to 0.02/leverage (~20% max ROE loss)
- Conviction-scaled Phase 1 tolerance
- Time-of-day scoring (+1 morning, -2 evening)
- Rank jump minimum (≥15 or velocity >15)
- Green-in-10 floor tightening
- Re-entry logic for validated directions
- NEUTRAL regime support
- Market regime refresh cron (4h)
- Tiered margin system (6 flat entries)
- DSL v5 with Hyperliquid stop-loss sync
- BTC 1h bias alignment scoring
