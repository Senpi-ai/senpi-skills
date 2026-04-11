# Fleet Dossiers — Predators Agent Profiles

Last updated: April 11, 2026 (day 2)
Updated by: Claude Code day-2 session

---

## Tier 1: Fee fix alone makes profitable (3 agents)

### Kodiak (SOL hunter)
- **Signal:** Grizzly 3-mode lifecycle on SOL. SM consensus + velocity.
- **Stats:** 56 positions, 55.4% WR, avg winner +$20.24, avg loser -$11.68. 1.73:1 payoff. Median hold: 45 min.
- **Exit quality:** 8/10 best winners captured peaks within pennies. Phase 2 tiers excellently calibrated.
- **Disease:** Fee only. $238.84 fees on -$119.56 net. Gross PnL: +$119.28. Fee fix recovers ~$153.
- **Day-2 update:** 15x leverage identified as a problem — recent 15x SOL SHORT lost $45.55 + $9.35 fees. Leverage now capped at 7x/10x max (PR #166, EXP-014). Same signal quality, fewer catastrophic losses from amplified retraces.
- **Status:** Leverage compressed. Waiting on Sarvesh fee fix.

### Polar (ETH hunter)
- **Signal:** Grizzly 3-mode lifecycle on ETH. Strong gross alpha (+$219.46).
- **Stats:** 56 positions, 26.8% WR, avg winner +$29.97, avg loser -$14.81. Median hold: 41.4 min.
- **Disease:** Fee primary ($372.19 paid), winner-clipping secondary. Hard timeout at 180 min was killing winners.
- **Day-2 update:** DSL retune confirmed directionally. Post-swap trades show dead_weight_cut at 33 min, Phase 1 at 36 min, weak_peak_cut at 60 min. Zero trades hitting 480-min timeout. New exit distribution working as designed. First 4 trades in window were legacy (pre-swap).
- **Status:** PR #158 confirmed working. Fee fix waiting on Sarvesh.

### Lemon (counter-trade, degen fader)
- **Signal:** Fades DEGEN/CHOPPY traders. 50% WR with 3.7:1 R/R ratio. Only profitable agent in fleet (+$3.79).
- **Stats:** 6 positions. Median hold: ~3h (all exits by hard timeout).
- **Disease:** Hard timeout clipping winners. Losers sitting in drawdown for full 3h.
- **Status:** PR #152 MERGED — hard_timeout 240→480, weak_peak_cut 90→60 (min_value 2.0), dead_weight_cut 30→20. Agent confirmed live.

---

## Tier 2: Fee fix + one fix (5 agents)

### Scorpion v2.0 (altcoin swarm hunter)
- **Signal:** Detects coordinated risk events across 5+ altcoins, trades best target. 64.3% WR on 14 positions.
- **Stats:** Avg winner +$21.64, avg loser -$33.16. Gross PnL: +$28.98.
- **Disease:** Low-liquidity assets (XPL -$53, LIT -$43). Other 12 trades: +$124 combined.
- **Status:** PR #155 MERGED — XPL, LIT, FARTCOIN blacklisted from trading (still counted in swarm detection). Agent confirmed live. Health: running, 38h idle because no signal met threshold (by design).

### Orca v2.0 (Gen-2 Striker, breakout detection)
- **Signal:** FIRST_JUMP with quality confirmation. Proven correct (inversion: +$4.29 vs -$4.29). 26 positions.
- **Stats:** Median hold: 15.3 min. 50% of exits are weak_peak_cut. 4/5 best winners clipped before maturity.
- **Disease:** DSL patience filters too tight for breakout signals.
- **Status:** PR #154 MERGED — hard_timeout 30→45, weak_peak_cut 15→25, dead_weight_cut 10→15. Agent confirmed live. Entry execution already fixed (maker-only, single-step fills).

### Phoenix v2.0 (contribution velocity divergence)
- **Signal:** SM profit velocity diverging from price. 4x correct on inversion test.
- **Stats:** 39 positions (360 fills), 48.7% WR. Median hold: ~3h. Gross PnL: -$71.29.
- **Disease:** Thesis-exit removed + 180-min hard timeout. Trades drift long after signal resolves.
- **Day-2 update:** Time cuts CONFIRMED working. Median hold dropped from ~3h to ~30 min. Weak peak cut firing at 15 min, hard timeout at 45 min. Cooldown working. Deployment bug discovered: npx skills add overwrote local ensureExecutionAsTaker patch.
- **Status:** PR #147 confirmed working. Fee fix waiting on Sarvesh. Deployment bug needs fix.

### Condor v2.0 (multi-asset alpha hunter)
- **Signal:** SM consensus + extreme velocity on BTC/ETH/SOL/HYPE. Gross PnL: -$0.40 (almost break-even).
- **Stats:** 9 positions (103 fills). Median hold: 141 min. 3/9 hit hard timeout.
- **Disease:** Time cuts too loose. Condor was wrong about its own config (reported 180, actual was 240).
- **Status:** PR #148 MERGED — hard_timeout 240→75, weak_peak_cut 90→40, dead_weight_cut 60→30. Agent instructed.

### Roach-B (striker-class momentum scalps)
- **Signal:** Rank-jump detection. 48 positions, 43.8% WR, R/R 1.29:1. Median hold: 12.1 min.
- **Stats:** Winners 15.2 min avg, losers 9.9 min avg. DSL cuts losers 35% faster than winners.
- **Disease:** Fee drag (entries maker, exits still taker). Edge razor-thin.
- **Health:** Dual DSL tracker issue RESOLVED (orphaned Gateway cron killed April 10).
- **Day-2 update:** 5 trades since fix, 0% net WR, avg peak ROE only 0.34%. Breakouts firing on noise in chop. Thresholds tightened in PR #166 (EXP-016): MIN_SCORE 9→10, velocity floor 10→15, vol ratio 1.5→2.0. Goal: fewer trades, higher quality breakouts.
- **Status:** Thresholds tightened. Waiting on fee fix for DSL exits.

---

## Tier 2b: Fee fix makes viable (1 agent)

### Wolverine v2.0 (HYPE alpha hunter)
- **Signal:** Extreme SM conviction + 15m/1h momentum on HYPE. Same thesis-exit disease as Phoenix.
- **Stats:** 8 positions (57 fills). Median hold: 180.3 min. 62.5% hit hard timeout. Gross PnL: -$0.39.
- **Disease:** `_v2_no_thesis_exit: true`. DSL handles 100% of exits.
- **Status:** Python-only (no runtime.yaml). DSL time cut instructions sent: weak_peak_cut 25, dead_weight_cut 35, hard_timeout 60. Fee fix waiting on Sarvesh.

---

## Contrarian flips (5 agents — new category, April 10)

### Cheetah v4.0 (HYPE funding fader — was worst in fleet)
- **Signal:** Was momentum (v1), then contrarian SM (v3.0), now funding rate fader (v4.0). Complete retool.
- **Disease (pre-flip):** Momentum scanner buying HYPE breakouts that immediately mean-reverted. Contrarian SM also failed — neither direction of SM consensus works on HYPE in chop.
- **Day-2 update:** v3.0 contrarian FAILED — 5 trades, 40% WR, -$39.20. Retooled to funding rate thesis (EXP-015, PR #166). New hypothesis: funding extremes on HYPE mean-revert, collecting funding + fading direction produces positive EV. Baseline: -$351.28 net (worst in fleet).
- **Status:** v4.0 LIVE — funding fader. Highest dollar-impact experiment if it works.

### Dog v2.0 (multi-asset contrarian)
- **Signal:** Was SM consensus (BTC/ETH/SOL/HYPE), now contrarian. Inversion: actual -$61, inverted +$61.
- **Disease (pre-flip):** HYPE caused -$91 of -$105 net loss. Entering after exhausted moves.
- **Day-2 update:** BEST PERFORMER in fleet this window. +$19.08 realized with +$26.64 unrealized. 5 positions, one big SHORT HYPE winner covers all losses. Contrarian shape confirmed: small losses, big winners. Strongest evidence for the direction-flip thesis (H8).
- **Status:** PR #159 confirmed working. Continue monitoring.

### Grizzly v4.0 (BTC contrarian — no pyramiding)
- **Signal:** Was SM consensus on BTC, now contrarian. Inversion: 81.8% WR if flipped on 11 trades.
- **Day-2 update:** Healthy, not firing. Zero trades in 20h. Scores hovering 5-7, MOVE_EXHAUSTION filtering correctly. Agent is waiting for a qualifying setup — this is by design.
- **Status:** PR #156 MERGED — agent confirmed live with dry-run. **A/B control vs Horribilis (single entry).**

### Grizzly Horribilis v2.0 (BTC contrarian — with pyramiding)
- **Signal:** Same as Grizzly v4.0 but pyramids into winners (up to 3 entries per position).
- **Day-2 update:** FLAT — -$0.26 on 43 fills. Contrarian trades offsetting each other in chop. Pyramiding not adding value — adds to positions that then reverse.
- **Status:** PR #156 MERGED — agent confirmed live. **A/B experiment vs Grizzly v4.0 (pyramiding).**

### Vulture v1.0 (NEW — multi-asset SM exhaustion fader)
- **Signal:** Purpose-built contrarian. Requires 4H price move >3% + strong SM consensus, then fades. BTC/ETH/SOL/HYPE.
- **Key difference from retrofits:** Contrarian-native scoring. Move-exhaustion is a BONUS, 1H reversal detection, 15M SM velocity fading. Not a direction flip on an existing scanner.
- **Status:** PRs #157 + #160 MERGED — replaced Fox v2.0. Directory renamed fox→vulture. Ready to deploy.

---

## Monitor (not enough data or needs more work)

### Bison v1.2 (conviction holder)
- **Signal:** 4H/1H alignment, requires multi-timeframe convergence. Very low frequency (5 positions lifetime).
- **Stats:** 25% WR closed, but open HYPE LONG from $38.59 at +$115 unrealized (+41.8% ROE, Tier 3 locked).
- **Key insight:** 2-of-3 pillar scoring degradation — v2.0 scoring allows entries when one pillar disagrees.
- **Recommendation from Bison:** Expand asset universe Top 10→Top 30-50 with strict 3-of-3 convergence.
- **Status:** Monitor. Architecture (low freq, wide DSL, patience) is the best in fleet. Sample too small.

### Spider (elite convergence scanner)
- **Stats:** 5 positions (70 fills), 60% WR. Gross PnL: -$8.32. Inversion marginal (+$8.32).
- **Disease:** BTC is the problem asset (-$48.82 gross). Both losers hit 180-min hard timeout.
- **Day-2 update:** Health bug found — account value calculation was double-counting main+xyz DEX balances. This inflated position sizing from ~$439 to ~$825. FARTCOIN loss was amplified by the bug. Agent self-patched the fix (EXP-017). Position sizing should normalize going forward.
- **Status:** Monitor post-fix. Too small sample to diagnose signal quality.

### Roach (original, Python cron)
- **Signal:** Same striker logic as Roach-B, but Python runtime with advanced velocity scoring. 39 positions.
- **Stats:** 41% WR, avg winner +7.02% ROE, avg loser -2.59% ROE. 2.7:1 R/R ratio (better than Roach-B).
- **Disease:** Pure fee drag. -$192 net on $200K+ volume. Losses evenly distributed, no single bad asset.
- **Health:** Clean — no duplicate DSL, maker entries working.
- **Day-2 update:** -$37.54 in measurement window. Bleeding on failed breakouts in chop. Better R/R ratio than Roach-B but still losing money.
- **Status:** Monitor. Waiting on fee fix, but continued losses in current regime.

### Sentinel (quality trader convergence)
- **Signal:** Inverted pipeline, 3x correct on inversion test. Highly predictive on ETH/TAO.
- **Disease:** Per-asset DSL mismatch — HYPE/BTC need wider stops.
- **Status:** Needs per-asset DSL tuning. Not yet implemented.

### Cobra v1.1 (arena sprint)
- **Stats:** 19 positions, 42.1% WR. Gross PnL: -$45.43. Even inverted, fees destroy it (-$96 net).
- **Disease:** Fee drag ($141.60) + historical stacking bug + weak signals. Architecture fixes applied.
- **Status:** Monitor post-fixes. Marginal signal.

### Owl (contrarian crowding-unwind)
- **Stats:** 2 positions (7 fills), both TAO, 5x leverage. 50% WR. Net: -$8.83.
- **Status:** Healthy, by design ultra-selective. v6.0 active. Self-diagnosed: needs 6-hour loss cooldown enforcement.

---

## Not candidates in current form

### Fox v2.0 → REPLACED by Vulture v1.0
- Even perfectly inverted, net PnL was -$1.96. Signal too weak for any fee level.

### Bald Eagle v2.0 (XYZ equities/commodities)
- **Stats:** 12 positions (only CL and BRENTOIL), 25% WR. Avg winner +$1.82, avg loser -$7.22.
- **Disease:** Same signal inversion as crypto agents — SM signal enters after oil moves exhaust.
- **Status:** Market hours enforcement applied. Direction flip shipped in PR #162.

### Mantis v4.0 (Vixen/Orca scanner)
- **Stats:** 15 positions, 46.7% WR. Signal marginally inverted. BLAST caused 150% of gross loss.
- **Status:** Needs asset whitelist + signal work. Lower priority.

---

## Healthy zero-trade agents (by design)

### Jaguar v2.0 — Striker-only, requires 9+ score with 15+ rank jump from outside Top 25. Running.
### Raptor v2.0 — Requires $5.5M+ Tier 2 momentum event from ELITE trader. Running.

---

## Paused agents (not in active fleet)

Hawk, Shark, Shark v2.0, Hydra, Viper, Anaconda, Scorpion v1, Jackal, Phoenix v1, Grizzly v2.1.1, Feral Fox, Fox v1, Orca v1, Jaguar v1, and several legacy versions.
