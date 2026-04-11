# Fleet Hypotheses Log

Last updated: April 11, 2026 (day 2)
Updated by: Claude Code day-2 session

---

## Active hypotheses

### H1: Fee fix alone flips 3-4 agents to net-positive
**Filed:** April 10, 2026
**Status:** AWAITING (Sarvesh's runtime release, expected April 14-15)
**Prediction:** Polar (+$101), Kodiak (+$33), Scorpion (~break-even), Lemon (improves to +$9), Roach (strong candidate due to 2.7:1 R/R).
**How to score:** Pull each agent's fee/vol bps 7 days after fix. Target: drop from ~7.8 bps to ~2.5 bps.
**Risk:** Actual maker fill rate may be lower than 70%.

### H2: Phoenix time cuts reduce median hold from 3h to ~30 min
**Filed:** April 10, 2026
**Status:** CONFIRMED — median hold dropped from ~3h to ~30 min. Weak peak cut firing at 15 min, hard timeout at 45 min. Deployment bug discovered (npx skills add overwrote ensureExecutionAsTaker patch) but time cuts themselves validated.
**How to score:** Pull Phoenix closed positions 48h after agent applies changes. Target: median hold <45 min.

### H3: Condor time cuts reduce median hold from 141 min to ~45 min
**Filed:** April 10, 2026
**Status:** LIVE — PR #148 merged, agent instructed
**How to score:** Same as H2. Target: median hold <60 min.

### H4: Roach-B scalp mode captures >60% of peak ROE
**Filed:** April 10, 2026
**Status:** UNBLOCKED — dual DSL tracker killed, health fixed
**How to score:** Measure avg peak ROE vs avg realized ROE over 20+ new positions.

### H5: Thesis-exit-removed disease affects all v2.0 agents
**Filed:** April 10, 2026
**Status:** CONFIRMED across Phoenix, Wolverine, Condor, Cheetah
**All four have `_v2_no_thesis_exit: true` and exhibit the same pattern.**

### H6: Agent self-reports are leads, not ground truth
**Filed:** April 10, 2026
**Status:** CONFIRMED (two instances, plus Cheetah may have self-modified DSL)

### H7: Signal inversion disease is fleet-wide on SM consensus scanners
**Filed:** April 10, 2026 (Claude Code session)
**Status:** CONFIRMED across 5 agents
**Evidence:** Grizzly v3.0 (81.8% inverted WR), Horribilis (-$53/+$53), Dog (-$61/+$61), Cheetah (-$175/+$175), Bald Eagle (-$48/+$48). All use multi-timeframe SM confirmation that enters after move exhaustion.
**Mechanism:** The confirmation requirement (4H + 1H + 15M alignment) means the scanner fires after the move is already done. The signal is a lagging indicator that works as a contrarian signal.
**Implication:** Direction flip is the fix. 4 agents flipped (PRs #156, #159), Vulture built from scratch as contrarian-native.

### H8: Direction flip produces profitable contrarian agents
**Filed:** April 10, 2026 (Claude Code session)
**Status:** LIVE — early mixed results
**Day-2 data:** Dog v2.0 is the strongest evidence FOR the thesis (+$19.08 realized, +$26.64 unrealized, contrarian shape confirmed with small losses and big winners). Cheetah v3.0 FAILED on HYPE (-$39.20, 40% WR — neither momentum nor contrarian SM works on HYPE in chop, retooled to funding fader). Grizzly v4.0 not firing (healthy, MOVE_EXHAUSTION filtering correctly, zero trades in 20h). Horribilis v2.0 flat (-$0.26 on 43 fills, contrarian trades offsetting in chop).
**Prediction:** At least 2 of the 5 contrarian agents (Grizzly v4.0, Horribilis v2.0, Cheetah v3.0, Dog v2.0, Vulture) will be net-positive within 7 days.
**How to score:** Pull PnL on all 5 contrarian agents 7 days post-flip. Any agent with positive gross PnL confirms the thesis. Net-positive confirms it overcomes fees.
**Risk:** Market regime may shift from ranging (where contrarian works) to trending (where it doesn't). Cheetah and Dog already trading — early results will be visible within 24-48h.
**This is the highest-risk, highest-reward experiment in the fleet.**

### H9: Polar market-driven exits improve winner capture
**Filed:** April 10, 2026 (Claude Code session)
**Status:** CONFIRMED DIRECTIONALLY — post-swap trades show dead_weight_cut at 33 min, Phase 1 at 36 min, weak_peak_cut at 60 min. Zero trades hitting 480-min timeout. New exit distribution is working as designed. First 4 trades in window were legacy (pre-swap).
**Prediction:** Polar's gross PnL per winning trade increases as winners are no longer killed by 180-min clock. Dead weight cut at 30 min catches losers faster than the old 180-min timeout.
**How to score:** Compare avg winner ROE before vs after. Target: avg winner improves by >20%.

### H10: Scorpion asset blacklist eliminates disproportionate losses
**Filed:** April 10, 2026 (Claude Code session)
**Status:** LIVE — PR #155 merged, agent confirmed
**Prediction:** Next 14 Scorpion positions show no losses >$25 (the XPL/LIT/FARTCOIN losses were $43-53 each). Win rate should improve from 64% to 70%+ as garbage assets are excluded.
**How to score:** Pull Scorpion's next 14 positions. No position should involve XPL, LIT, or FARTCOIN.

### H11: Orca DSL widening reduces winner clipping
**Filed:** April 10, 2026 (Claude Code session)
**Status:** LIVE — too early to score, only 13 new fills on Orca since change
**Prediction:** Percentage of exits via weak_peak_cut drops from 50% to <30%. Winners get 10 more minutes to develop.
**How to score:** Pull Orca exit distribution over next 20+ positions.

### H12: Leverage above 10x destroys edge via fee amplification
**Filed:** April 11, 2026
**Status:** CONFIRMED — Kodiak's 15x SOL SHORT lost $45.55 + $9.35 fees. Same pattern seen across Grizzly Horribilis (was 7x-20x, now capped 7x-10x). Higher leverage amplifies fees proportionally while signal quality stays constant.
**How to score:** Compare Kodiak loss-per-trade before (7/10/12/15x) vs after (7/10x cap).

### H13: HYPE funding rate extremes are a better signal than SM consensus for HYPE-specific trading
**Filed:** April 11, 2026
**Status:** LIVE (Cheetah v4.0)
**How to score:** Pull Cheetah v4.0 PnL after 10+ trades. Compare gross PnL to v3.0 contrarian (-$39.20 on 5 trades).

### H14: Striker breakout signals need higher thresholds in low-volatility/chop regimes
**Filed:** April 11, 2026
**Status:** LIVE (Roach-B tightened — MIN_SCORE 9→10, velocity floor 10→15, vol ratio 1.5→2.0)
**How to score:** Pull Roach-B peak ROE on next 10+ entries. Target: avg peak ROE >2% (vs 0.34% before tightening).

---

## Retired hypotheses

### H0: "Lemon is the existence proof for the fleet"
**Status:** SUPERSEDED by Polar
**Correction:** Polar has +$219 gross alpha — strongest in fleet. Lemon is profitable but Polar is the better proof that the signal layer works.

---

## Calibration log

### April 10, 2026 — web chat session (3/7 correct)

**Correct:**
- Bison v1.2 not fee-bound
- Mantis v4.0 has signal issues
- Polar fills-per-position 5-8 confirming chunking

**Wrong:**
- "Lemon is the existence proof" — Polar is better
- "Kodiak will show same clipping as Polar" — Kodiak's exits are excellent
- "Phoenix inverted PnL marginally better" — 4x worse
- "Polar clean win/loss distribution" — noisy with extreme-conviction entries

**Lesson:** Don't assume uniformity within same-lineage agents. SOL and ETH move differently.

### April 10, 2026 — Claude Code session (predictions logged, not yet scored)

- H8: at least 2/5 contrarian agents net-positive within 7 days
- H9: Polar avg winner ROE improves >20%
- H10: Scorpion no losses >$25 on next 14 positions
- H11: Orca weak_peak_cut exits drop from 50% to <30%

### April 11, 2026 — day-2 scoring (partial, where data available)

**Scored:**
- H2 (Phoenix time cuts): CONFIRMED — median hold dropped to ~30 min as predicted
- H9 (Polar market-driven exits): CONFIRMED DIRECTIONALLY — new exit distribution working, zero timeout exits
- H8 (Contrarian flip): MIXED early data — Dog strongly supports, Cheetah failed on HYPE, Horribilis flat in chop, Grizzly no data yet

**Still awaiting data:**
- H10 (Scorpion blacklist): not enough new positions yet
- H11 (Orca DSL widening): only 13 fills, need 20+ positions

**New predictions logged:**
- H12: Leverage >10x destroys edge (CONFIRMED same day by Kodiak data)
- H13: HYPE funding rate > SM consensus for HYPE trading (LIVE, Cheetah v4.0)
- H14: Striker thresholds need tightening in chop (LIVE, Roach-B)

**Scoring date: April 17, 2026**
