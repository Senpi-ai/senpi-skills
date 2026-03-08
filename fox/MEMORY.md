# Memory

Long-term context across sessions.

## Rules
- **NEVER estimate balance** — always pull real-time from Senpi before reporting

## User: Jason Goldberg
- Telegram chat ID: 5183731261
- **Senpi account: M192203** (migrated from M4 on 2026-03-02)
- Embedded wallet: `0xD98d0464125d8a8A359E98c0659E225c27C865d0`
- Budget: ~$3,232 after Day 1, target $4,000 by 2026-03-06
- Full autonomy granted — act decisively, NEVER ask permission, just execute and report
- Timezone: Eastern (Miami)

## Current Mission — COPY TRADING (Pivoted 2026-03-08)
- **Autonomous FOX trading STOPPED** — 7 days, ~51 trades, 15% WR, -$459 (-15.3%)
- **3 copy trading strategies deployed** ($1,200 total, $1,340 reserve):
  - Copy#1 "The Consistent": 0xaea8...e59b, $500, 99.3% WR, GP:147.6
  - Copy#2 "The Diversified": 0xd6e5...5b42, $400, 91% WR, GP:100.2
  - Copy#3 "The Bear": 0x418a...8888, $300, 25% SL, 312% weekly ROI
- Strategy IDs: 129a0292, 3853db6b, 827a7ad8
- State file: `/data/workspace/copy-strategies.json`
- Monitor cron: every 15min (Copy Trading Monitor)
- Market Regime cron still active (1h)
- All 6 FOX scanner/support crons DISABLED (not deleted)

## Day 2 Results (2026-03-04 — FOX v0.1→v0.2) — COMPLETE
- **16 trades, ~$203+ gross, ~$120+ net** (balance $3,010→~$3,130, +4%)
- Best: ZEC SHORT +$266 (T3), GOLD SHORT +$48 (T2), PUMP(2) +$37, PUMP(1) +$19, MU +$11
- Morning (02-08 UTC): 6 trades, strong — ZEC T3 monster + GOLD T2
- Afternoon (13-15:30 UTC): 7 trades, mixed — mostly Phase 1 losses
- Evening (20:24-22:35 UTC): 3 trades — AVAX -$27 (Phase 1 SL), PUMP(2) +$37, PAXG (unknown PnL)
- **v0.2 upgrade mid-day**: vel>10 mandatory gate, flat $950 margin
- 21:06-22:36 UTC: 5+ strong SHORT signals blocked by stale BULLISH regime (DOGE vel=35, MU vel=15, ASTER vel=20)
- **Regime lag is #1 problem**: v0.3 MUST add 1h refresh or evening hard stop
- **DSL v5 BUG**: Script auto-deletes state files when clearinghouse query timing races — need fix
- **ALWAYS use Senpi PnL** — estimates off by up to $78

## Day 1 Results (2026-03-02)
- **7 trades, +$233 realized** (2W/1BE/4L)
- Winners: ADA +$164, COPPER +$192 (both T3 floor exits)
- Losers: GOLD -$35, XRP -$60, BNB -$28 (all Phase 1 cuts)
- Balance: ~$3,232 (need ~$768 in 3 days)
- Evening session weak — no qualifying SHORT FJs after BNB
- Phase 1 dead weight cuts saved significant capital

## Senpi MCP Config
- **Endpoint**: `https://mcp.prod.senpi.ai/mcp` (production)
- **Auth**: Token hardcoded in mcporter config — M192296 (Fox account)
- **Config**: `/data/.openclaw/config/mcporter.json`
- **Strategy flow**: Use `strategy_create_custom_strategy` — auto-bridges funds from embedded wallet
- **Positions format**: `[{"coin":"ASSET","direction":"SHORT","leverage":10,"marginAmount":1450,"leverageType":"CROSS"}]`
- XYZ assets: coin=`xyz:ASSET`, leverageType=`ISOLATED`

## FOX v0.1 Migration (2026-03-04)
- Migrated from Wolf v7.2 to Fox v0.1
- New account: M192296, embedded wallet: `0xBE79774ac130b15F880162fd5f33A4B267B6a152`
- Transferred $2,009.57 from old M192203 wallet → new wallet
- Balance: ~$3,010
- State files: `fox-strategies.json`, `fox-trade-counter.json` (Fox namespace)
- Fox scripts don't exist yet — using Wolf scripts (`emerging-movers.py`, etc.) with Fox state files
- 8 Wolf crons disabled, 7 Fox crons active (DSL v5 is dynamic)
- Fox SKILL.md at `/data/workspace/skills/fox-strategy/SKILL.md` (reference only, no scripts)

## FOX v1.0 Changes (2026-03-08, after v0.9 3x floor SL in 25min)
- **ROOT CAUSE**: Phase 1 retraceThreshold 0.03 (3% ROE) at 10x = 0.3% price tolerance. Normal noise triggers breach every check. 3 breaches × 3min = auto-close in 9min. Exchange SL at -15% ROE was NEVER reached.
- **retraceThreshold → 0.15** (matches absoluteFloor, 15% ROE = 1.5% price at 10x)
- **consecutiveBreachesRequired → 10** (30min sustained breach, not 9min)
- **Removed deadWeight and weakPeak timeouts** — these also closed trades prematurely
- **hardTimeout kept at 90min** as max time limit
- DSL now truly catastrophic-only: closes via tier system, exchange SL (-15% ROE), or 90min hard timeout
- Counter reset to 0/3 for v1.0 testing

## FOX v0.9 Changes (2026-03-07, after v0.8 failure)
- **Reverted to 10x default** — 20x doubled absolute fees ($71 vs $30). Experiment failed.
- **Removed time block** — user override, trade any hour
- **kPEPE blacklisted** — Senpi uppercases to KPEPE, invalid on Hyperliquid
- **Post-entry verification** — check clearinghouse state after every entry for position size correctness
- **DSL state 'asset' field** — MUST include, script crashes without it
- **Position doubling bug**: v0.8 XRP had 27,892 units vs expected 13,946. Investigate.

## FOX v0.8 Changes (2026-03-07, REVERTED after 1 trade)
- 20x leverage doubled fees ($71 on XRP vs ~$30 at 10x). Net -$76.48 on $0.002 price move.
- **Fees are % of notional — leverage doesn't help, it hurts on quick exits.**

## FOX v0.7 Changes (2026-03-07, user-approved)
- **Max 3 trades/day** (was 2 in v0.6)
- **Removed 7x min leverage gate** — 5x assets (LIT, ZRO, ASTER, XMR, TAO) now eligible
- Use asset's native max leverage (min 3x to enter)
- Default 10x or asset max, whichever lower. Score 8+ can use up to asset max.

## FOX v0.6 Changes (2026-03-07)
- **Catastrophic-only SL**: absoluteFloor 0.15/leverage (-15% ROE). NO tight phase 1 floor.
- **Hard time block**: NO entries UTC 18:00-03:59 (replaces evening penalty)
- **XPL blacklisted**: builder fee trap (5.1% fee rate on $950 position)
- **Max 2 trades/day** (later bumped to 3 in v0.7)
- **Longer DSL timeouts**: Score 10+: 90min hard (was 60)

## FOX v0.2 Changes (2026-03-04, based on Day 2 W/L analysis)
- **vel>10 MANDATORY** — was +2 pts bonus, now hard gate. 100% of winners had vel>10, only 56% of losers.
- **Flat $950 margin** — no more tiered system. Reduces variance, caps max loss ~$50/trade.

## FOX v0.5 Changes (2026-03-06, user-approved)
- **Entry confirmation gate**: Non-explosive signals must show 0.3% price move in our direction before entry. Tracked across scans in fj-last-seen.json. Drop after 3 scans or 0.5% adverse. Explosive (score≥10) still instant.
- **Score minimum 7** (was 6). 8 for NEUTRAL. 5 for re-entries.
- **XYZ liquidity filter**: XYZ assets require score ≥10 (wider spreads, choppier)
- **DSL tiers are floors not exits**: Hold through tiers, only exit on floor breach
- **Momentum check**: Built into confirmation gate (0.3% move = momentum confirmed)
- Target: 40% win rate. Math: 6 losses × $47.50 = $285, need 4 wins × $71+ to profit.

## FOX v0.4 Changes (2026-03-06, user-approved)
- **Much wider DSL floors** — 6-7: 0.05/lev (~50% ROE), 8-9: 0.06/lev (~60% ROE), 10+: 0.07/lev (~70% ROE)
- **vel>7 mandatory gate** (was >10) — opens more entries, vel>10 still gives +2 pts bonus
- **Evening penalty reduced** — -1 pt (was -2) for 18:00-01:59 UTC
- **Regime staleness 1h** (was 2h) — faster flip to NEUTRAL when regime data ages
- Rationale: 22/25+ trades across Days 3-4 hit floor SL. Direction 85% correct but floors too tight to survive initial chop.

## FOX v0.3 Changes (2026-03-05, user-approved)
- **Regime refresh 1h** (was 4h) — prevents stale regime mislabeling
- **Wider DSL floors** — 6-7: 0.03/lev, 8-9: 0.035/lev, 10+: 0.04/lev (50% wider than v0.2)
- **FJ persistence filter** — must appear on 2 consecutive 3min scans. State: `fj-last-seen.json`
- **NEUTRAL direction flexibility** — both LONG/SHORT allowed if score≥8 when bull<60 or conf<50
- Day 3 problem: 7/9 trades hit absolute floor SL immediately — floors were too tight at 0.02/lev

## WOLF v6.2 Rules (Sniper Mode)
- FJ-only (CE accepted only if also FJ)
- Must start from rank ≥20 (catch early, not peaks)
- Skip if 4h price change >2% (too late)
- No top-10 entries, no counter-trend, no erratic, no neg velocity
- Min 4 reasons, min 7x leverage
- Max 3 entries/day
- Market regime: candle-based (BTC+ETH on 1h/4h/1d EMAs)
- DSL v5: 9-tier aggressive, Phase 1 absoluteFloor (0.03/leverage), 90min hard timeout, 45min weak peak cut, 25min dead weight cut
- DSL state: `dsl/{strategyId}/{ASSET}.json`, cron per-strategy (dynamic create/remove)

## Key Technical Learnings
- `strategy_get_clearinghouse_state` param: `strategy_wallet=` (not strategyWalletAddress)
- `create_position` requires `orders` array; `strategy_create_custom_strategy` requires `positions` array
- `close_position` is the close tool (not edit_position)
- XYZ positions need `leverageType: "ISOLATED"` and `dex="xyz"` in queries
- Cross-margin wallets CAN hold longs + shorts simultaneously (not same coin)
- DSL state: `active` (boolean), `triggerPct`/`lockPct` (not threshold/retracePct)
- DSL v4 auto-closes on breach — don't test against live positions
- Race condition: when ANY job closes → immediately deactivate DSL state file
- Scanner needs `PYTHONUNBUFFERED=1` to avoid stdout buffering
- Max leverage is per-asset: check `/data/workspace/max-leverage.json`
- BUG: `create_position` with `dryRun:true` ACTUALLY EXECUTES
- Telegram: numeric chat IDs only, not @usernames
- File sending: copy to `/tmp/` first, workspace paths rejected

## DSL Setup Checklist (MANDATORY after every entry)
1. `DSL_STATE_DIR=/data/workspace/dsl` (NOT including strategy ID — script appends it)
2. Script path: `skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py` (NOT root of skill dir)
3. State file fields MUST include: `size`, `wallet`, `phase`, `highWaterPrice`, `currentTierIndex`, `tierFloorPrice`, `currentBreachCount`, `consecutiveFetchFailures`, `lastPrice`, `lastSyncedFloorPrice`, `slOrderId`, `pendingClose`
4. Phase1 fields: `absoluteFloor` (number), `retraceThreshold`, `consecutiveBreachesRequired`, `hardTimeoutMinutes`, `weakPeakTimeoutMinutes`, `deadWeightTimeoutMinutes`, `greenIn10Enabled`
5. Phase2 fields: `retraceThreshold`, `consecutiveBreachesRequired`
6. **RUN THE SCRIPT MANUALLY** after creating state + cron to verify it works. NEVER assume it's fine.
7. Check the output shows `sl_synced: true` before moving on.

## User Preferences
- **Always alert on DSL tier upgrades** — send Telegram notification every time a position hits a new tier

## Critical Operations
- **ALWAYS close strategies after closing positions** — `strategy_close` via mcporter after every position close. Funds stay locked in strategy wallet until explicitly closed. Forgetting this = capital stuck, can't open new trades.
- After DSL/agent closes a position → immediately `mcporter call senpi.strategy_close strategyId={id}` → update wolf-strategies.json (clear wallet/strategyId)

## Trading Wisdom (Proven)
- **Monsters make the money**: 2-3 Tier 3-4 trades = entire day's profit. Everything else is noise.
- **Over-trading kills**: Feb 24: 32 trades, -$146. Top 5 alone = +$1,546. The other 27 subtracted.
- **Counter-trend LONGs in selloffs = #1 killer**: Every retro confirms this.
- **Phase 1 stagnation is #2 killer**: Assets that don't move in 30min probably won't. Cut early.
- **Best moves happen FAST**: Tier 3 in 19min, Tier 4 in 21min. Speed of initial move = quality signal.
- **Rank climb LAGS price**: Enter on FIRST jump, not after confirmation at top.
- **Empty slot > mediocre position**: Discipline to skip saves more than entries make.
- **PnL/contribution velocity > trader count**: Fast-accelerating rank #30 beats stale rank #10.
- **5x leverage doesn't work**: DSL tiers too tight at low leverage. Min 7x hard gate.
- **Fees are % of notional — leverage doesn't help**: Doubling leverage doubles fees proportionally. Only way to beat fees: hold longer for bigger moves, fewer trades, ALO on both sides.
- **Floor SL is the #1 killer**: 30/38 trades (79%) across Days 1-5 hit floor SL. Direction was right 85% of the time. The stop is the problem, not the signal.

## DSL v5 Migration (2026-03-03)
- Migrated from DIY DSL to official DSL v5 skill at `/data/workspace/skills/dsl-dynamic-stop-loss/`
- State files: `dsl/{strategyId}/{ASSET}.json` (xyz assets: `xyz--ASSET.json`)
- DSL cron: per-strategy, dynamic lifecycle (create on position open, remove on strategy_inactive)
- Script: `dsl-v5.py` — self-contained, handles clearinghouse reconciliation, HL SL sync, cleanup
- **Key feature: Phase 1 absolute floor** — exchange SL set immediately at entry
  - SHORT: `entry × (1 + 0.03/leverage)`, LONG: `entry × (1 - 0.03/leverage)`
  - Caps max loss at ~30% ROE — VALIDATED on TSLA trade ($65 loss vs $239-334 without)
- 9-tier aggressive config, phase1 retrace 3%/3 breaches, phase2 retrace 1.5%/2 breaches
- Old crons removed: "DSL Integrity Check", "DSL Combined"
- Old files deprecated: `scripts/dsl-exchange-sl.js`, `scripts/dsl-combined.py`

## WOLF v7 Migration (2026-03-03)
- **Major upgrade**: Scoring system replaces "min 4 reasons", NEUTRAL regime support, tiered margin
- **New market regime refresh**: Every 4h cron job, saves to `market-regime-last.json`
- **Scoring system**: FIRST_JUMP +3pts (mandatory), IMMEDIATE_MOVER +2pts, contribVelocity>10 +2pts, CONTRIB_EXPLOSION +2pts, others +1pt. Min 6pts to enter.
- **NEUTRAL regime**: Both LONG/SHORT allowed if score ≥8 (BEARISH=SHORT only, BULLISH=LONG only unchanged)
- **NEUTRAL definition**: market_bull<60 AND market_bear<60, OR overall confidence<50
- **BTC 1h bias**: +1 bonus point for aligned signals (BTC 1h BULLISH + signal LONG, or BTC 1h BEARISH + signal SHORT)
- **Tiered margin**: Entries 1-2=$1450/$1500, 3-4=$950/$1000, 5-6=$450/$500. Max 6/day (no dynamic slots)
- **wolf-trade-counter.json updated**: marginTiers array, maxEntriesPerDay=6, removed dynamicSlots
- **Scanner cron updated**: ID 3f0dd5b0, new v7 mandate with all scoring/regime/tier logic
- **Market regime cron created**: ID 6486acb9, 4h schedule

## WOLF v7.1 Optimizations (2026-03-03, based on 14-trade analysis)
- **Tighter absolute floor**: 0.02/leverage (20% max ROE loss, was 0.03/30%)
- **Aggressive Phase 1 timing**: 30min hard cap (was 90), 15min weak peak (was 45), 10min dead weight (was 25)
- **Green-in-10 rule**: If never positive ROE within 10min → tighten floor to 50% of original distance
- **Time-of-day scoring**: +1 pt for 04:00-14:00 UTC, -2 pts for 18:00-02:00 UTC (evening trades 0% win rate)
- **Rank jump minimum**: ≥15 jump OR contribVelocity > 15 (small jumps had 0% win rate historically)
- Evidence: 2/13 winners both went green within minutes; all losers that started negative stayed negative; evening trades 0/6 winners

## WOLF v7.2 Optimizations (2026-03-03, direction analysis)
- **Key finding**: 11/13 trades (85%) had the RIGHT direction. Lost $785 on correct-direction trades.
- **Left $8,000+ on table**: NEAR dropped 52% after our exit (-$334), FARTCOIN dropped 71% (-$239)
- **Problem was timing/stops, not direction selection**

### Conviction-Scaled Phase 1 Tolerance
| Score | Absolute Floor    | Hard Timeout | Weak Peak | Dead Weight |
|-------|-------------------|-------------|-----------|-------------|
| 6-7   | 0.02/lev (20%)    | 30 min      | 15 min    | 10 min      |
| 8-9   | 0.025/lev (25%)   | 45 min      | 20 min    | 15 min      |
| 10+   | 0.03/lev (30%)    | 60 min      | 30 min    | 20 min      |

### Re-Entry Rule
- Check trade counter for recently exited trades (within 2h)
- Asset must still show strong signals in SAME direction
- Price must have moved FURTHER in our direction (confirming thesis)
- contribVelocity > 5, must be in top 20
- 75% of normal margin (reduced size for 2nd attempt)
- Score requirement: 5 pts (relaxed from 6, FJ not required)
- Skip if first attempt lost >15% ROE (too volatile)

## Day 5 Results (2026-03-07 — FOX v0.5→v0.6→v0.7) — COMPLETE
- **3 trades (00:00-03:00 UTC), 0W/3L** — net **-$73.87** (-2.56%)
- Gross PnL was only -$4.62 — **fees ($78.51) are 17x the gross loss**
- XRP LONG -$32.34 (floor SL 92min), XPL SHORT -$22.05 ($48.57 fees!), SOL SHORT -$19.48 (floor SL 12min)
- All 3 were score-13 explosive signals — highest quality still losing
- Balance: $2,884.92 → ~$2,811.33 ($2,821.40 total with $10.07 USDC)
- v0.6 deployed 04:02 UTC: catastrophic-only SL, time block 18:00-03:59, XPL blacklisted
- v0.7 deployed 14:09 UTC: max 3/day, removed 7x min leverage gate
- **Fees are %-based on notional NOT flat** — higher leverage does NOT improve fee ratio
- v0.8 comprehensive optimization plan proposed (see memory/2026-03-07.md)

## Day 4 Results (2026-03-06 — FOX v0.3.1)
- **9 trades, 2W/7L** — net ~-$140.94
- Balance: $3,074.61 → $2,933.67 (-4.6%)
- Winners: XRP SHORT +$16.89, XYZ100 SHORT +$49.96 (Tier 1!)
- **7/9 trades hit floor SL** — dominant failure mode continues (22/25+ across Days 3-4)
- **6+ hour dead period** (10:30-16:34 UTC): zero qualifying entries
- **3 strong LONG signals regime-blocked** by stale market-regime-last.json (dated 2026-01-10!): kPEPE (v=10.88), GOLD (v=11.53, persistent), PAXG (v=19.13, explosive)
- **ALO fee optimization added**: saves ~$7.60/trade (~$68/day at 9 trades). `FEE_OPTIMIZED_LIMIT` + `ensureExecutionAsTaker: true` for entries, MARKET for stops.
- **CRITICAL BUG IN MY BEHAVIOR**: Sent repeated false "DSL error" alerts when DSL was working perfectly. User very annoyed. MUST NOT append anything after HEARTBEAT_OK on slot-guard skips.

## Day 3 Results (2026-03-05 — FOX v0.2→v0.3)
- **10 trades, +$33.96 confirmed net** (2W: MU +$40, PUMP +$18.44; 8 floor SL exits including XRP -$24.48)
- Balance: $3,010 → $3,074.61 (+$64.61, +2.1%)
- Morning (00:15-01:12 UTC): 4 trades, 1W (MU +$40), 3 XYZ floor SLs
- Mid-morning (07:37-10:27 UTC): 5 trades, 1W (PUMP +$18.44), 4 floor SLs
- Evening (23:07 UTC): XRP SHORT score-10 monster (vel=250, jump 37) — floor SL in 6min, -$24.48
- **Market dead 10:30-20:12 UTC** (10+ hours, zero qualifying FJs)
- GOOGL score-14 entered twice, both hit floor SL
- v0.3 upgrade at 20:09 UTC: wider floors, 1h regime, FJ persistence, NEUTRAL direction
- v0.3.1 hotfix at 21:14 UTC: removed top-10 block, explosive skip persistence, relaxed 4h for score≥10
- **8/10 trades hit floor SL** — dominant failure mode across all score levels
- **DSL v5.1 tier keys**: Must use `triggerPct`/`lockPct` (not `trigger`/`floor`)

## Cron Architecture (Copy Trading Mode — 2026-03-08)
- Copy Trading Monitor (15min, isolated) — portfolio tracking for copy strategies
- Market Regime (1h, isolated) — BTC macro intel
- All FOX trading crons DISABLED: Scanner 3min, Opp Scanner 15min, SM Flip 5min, Watchdog 5min, Health Check 10min, Portfolio 15min

## OLD Cron Architecture (8 jobs — v7, DISABLED)
- Scanner (3min, main session) — emerging movers with v7 scoring system
- DSL v5 (3min, per-strategy, isolated) — trailing stop management via dsl-v5.py (dynamic cron)
- SM Flip (5min, isolated) — smart money flip detection
- Watchdog (5min, isolated) — liquidation buffer monitoring
- Health Check (10min, isolated) — DSL/position reconciliation
- Portfolio (15min, isolated) — balance/position reporting
- **Market Regime Refresh (4h, isolated)** — regime classification to market-regime-last.json
- Daily Retro (23:55 ET, isolated) — trade review + learnings
