# Memory

Long-term context across sessions.

## Account
- Strategy ID: 8b224b6f-d5d5-4246-8495-a10421796c64
- Wallet: 0x5eed9cf409f480019683f314f0ae84b12ca035a0
- Started: $2,999 → Current: ~$2,471 (as of Mar 8 evening)
- Mar 8 day: -$61 (-2.4%), 11 trades opened, 2W/9L

## VIPER Strategy (SHUT DOWN Mar 5)
- Was: Range bounce trading, 9 crons
- Shut down due to underperformance (10.6% drawdown, low activity)
- Cron IDs preserved for potential re-enable
## OWL Strategy v4 (DEPLOYED Mar 7)
- Contrarian crowding-unwind: trade AGAINST extreme positioning when exhaustion signals appear
- Skill dir: /data/workspace/skills/owl-strategy/
- Script: owl-hunt-v4.py (replaced owl-hunt.py v3)
- **v4 KEY CHANGES (Mar 7):**
  - Point-based scoring: min 8 points to enter (was binary 2-signal)
  - Crowding bar: 0.65 (was 0.50), persistence: 8 counts (was 5), funding: 25% ann (was 15%)
  - ≥2 structural signals required (was 1)
  - 4h RSI divergence (+3 pts), 4h alignment gate, time-of-day modifier
  - 9-tier DSL (from FOX): 5/10/20/30/40/50/65/80/100% ROE triggers
  - Phase 2 retrace: 1.2% ROE (was 1.5%)
  - Conviction-scaled Phase 1: score 8-9 (45/20/12min), 10-11 (60/25/15), 12+ (75/30/20)
  - Absolute floor: 0.06/leverage (was 0.10), 6% ROE max loss
  - Green-in-10: enabled (tighten floor 50% if never positive in 10min)
  - ALO entries: FEE_OPTIMIZED_LIMIT + ensureExecutionAsTaker
  - ALO profit exits: FEE_OPTIMIZED_LIMIT (SL stays MARKET)
  - Loss cooldown: 4hrs per asset (was 2)
  - Stagnation TP: auto-close if ROE ≥8% and HW stale 60min
  - Max slots: 3 (was 5)
- Crons: Hunt(15m main), DSL(3m isolated), OI Tracker(5m isolated), Risk Guardian(5m isolated)
- Cron model: configured model (was previous model which was blocked)
- Account: $2,532 (Mar 7), down from $2,999 start

## OWL Multi-Scanner (DEPLOYED Mar 8)
- Added 2 new scanners alongside contrarian hunt (v4)
- **Momentum Scanner** (owl-momentum.py, every 5min, main session):
  - Uses Hyperfeed SM concentration (leaderboard_get_markets)
  - BTC correlation filter (1 entry per correlated group)
  - Fading momentum filter, anti-chasing gate
  - Tighter Phase 1: 0.04/lev, 30min timeout
- **Correlation Lag Scanner** (owl-correlation.py, every 5min, main session):
  - BTC/ETH leader moves → scan lagging alts
  - 1.5% 1h or 2% 4h threshold on leader
  - Very fast cut: 8min dead weight, 30min stagnation TP
- All scanners share: 3-slot limit, same wallet/strategy, 9-tier DSL, ALO entries, 4hr cooldown
- Same-direction limit: max 2 positions same direction
- 6 total crons: Hunt, Momentum, Correlation, DSL, OI Tracker, Risk Guardian

## Stop Architecture Fix (Mar 8 ~17:30 UTC)
- **CRITICAL LESSON**: 0.04/lev floor at 8x = 0.5% from entry = noise territory. SOL peaked +4.6% ROE then reversed to SL.
- Floor widened: 0.04 → 0.06/lev (0.75% at 8x) for momentum + correlation
- weakPeakCut raised: 3% → 5% ROE (slow builders need room)
- T1 warmup: exchangeSlDelayMin=5 — no exchange SL for first 5min (prevents noise trips)
- Momentum timeouts normalized to match contrarian: 45/20/12 (was 30/15/10)
- DSL cron updated with weakPeakRoeThreshold=5 and exchangeSlDelayMin support

## DSL v5 (Shared)
- Upgraded to DSL v5 (official Senpi skill) on Mar 3 ~17:07 UTC
- DSL v5 script: /data/workspace/scripts/dsl/dsl-v5.py (auto-discovers positions from clearinghouse)
- DSL v5 state dir: /data/workspace/dsl/8b224b6f-d5d5-4246-8495-a10421796c64/
- DSL v5 tiers: 10/20/30/50/75/100% ROE triggers with per-tier retrace tightening
- DSL v5 leverage fix (commit 323702e): retrace is ROE-based, divided by leverage for price floors (e.g. 3% ROE at 7x = 0.43% price retrace)
- DSL v5 key improvements: auto-reconcile (no init_dsl.py needed), built-in edit_position SL sync, auto-cleanup on close
- Old DSL (dsl-combined.py) retired — state dir was state/wolf-8b224b6f/
- 8 crons: Scanner(15m), Bouncer(2m), BreakDetector(2m), RiskExit(5m), Health(10m), DSL(3min), PositionSummary(2h)
- API gotchas: strategyWalletAddress (not strategy_wallet), marginAmount (not margin), limitPrice (not price), xyz: prefix required for XYZ coins

## Exchange SL Ratchet (v2.1)
- Implemented: 2026-03-02 ~22:14 UTC
- DSL combined runner now calls `edit_position` with `stopLoss: {price, orderType: "MARKET"}` on each tier upgrade
- Hyperliquid enforces SL in real-time (milliseconds vs 3-min polling gap)
- T1 warmup: 5-min delay before setting exchange SL on first tier (prevents spike triggers)
- T2+ set immediately
- State tracks: `exchange_sl_price`, `exchange_sl_tier`, `tier0_reached_at`
- Wallet stored in DSL state files for `edit_position` calls
- Motivation: Lost ~$80 on DOGE polling gap (peak 10.1%, closed -2.8%)

## ⚠️ DSL v5: State File After Every Entry — MANDATORY FOR ALL POSITIONS
- **RULE (Mar 6): DSL must be on for EVERY position, always. Check on every update/heartbeat. No exceptions.**
- **RULE (Mar 6): ALWAYS notify user (Telegram 5183731261) when:**
  - A position is OPENED (any method — manual, OWL, any strategy)
  - A position is CLOSED (DSL stop, TP/SL hit, manual)
  - DSL advances to a new tier (e.g. T1→T2, T2→T3)
  - DSL sets or ratchets an exchange SL
- **Lesson:** NVDA was open since Feb 26 with NO DSL and NO notifications. User caught it manually on Mar 6. Could have given back all gains with zero protection.
- After EVERY successful `create_position`, write a DSL v5 state file to `/data/workspace/dsl/8b224b6f-d5d5-4246-8495-a10421796c64/{ASSET}.json`
- xyz assets: use `xyz--SYMBOL.json` filename
- v5 auto-discovers positions from clearinghouse and reconciles — but state file must exist for DSL to track
- absoluteFloor: LONG = entry*(1-0.10/leverage), SHORT = entry*(1+0.10/leverage)  ← WIDENED from 5% to 10% ROE (Mar 4 analysis: 7 premature stops left $1,128 on table)
- **CRITICAL: triggerPct/lockPct are PERCENTAGES not decimals** — use 10/20/30 NOT 0.10/0.20/0.30. upnl_pct = (upnl/margin)*100, so triggerPct=10 means 10% ROE
- init_dsl.py is RETIRED — create state file directly as JSON
- DSL Phase 1 retraceThreshold: 0.05 (was 0.03) — gives more room for S/R bounce noise
