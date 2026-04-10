# Fleet Hypotheses Log

Last updated: April 10, 2026
Updated by: Claude Code session (fleet analysis handoff from web chat)

---

## Active hypotheses

### H1: Fee fix alone flips 3-4 agents to net-positive
**Filed:** April 10, 2026
**Status:** AWAITING (Sarvesh's runtime release, expected April 14-15)
**Prediction:** Polar (+$101), Kodiak (+$33), Scorpion (~break-even), Lemon (improves to +$9). Based on 0% maker -> ~70% maker, fee rate 0.078% -> ~0.025%.
**How to score:** Pull each agent's net PnL 7 days after the fee fix merges. Compare to pre-fix baseline. If Polar and Kodiak are net-positive, H1 confirmed.
**Risk:** Actual maker fill rate may be lower than 70% — depends on market depth and timeout settings.

### H2: Phoenix time cuts (PR #147) reduce median hold from 3h to ~30 min
**Filed:** April 10, 2026
**Status:** AWAITING (PR merge + 24h measurement)
**Prediction:** With hard_timeout 45 min, weak_peak_cut 15 min, dead_weight_cut 20 min, most trades should resolve within 30 minutes. Gross PnL improves because trades close near signal resolution.
**How to score:** Pull Phoenix closed positions 48h after merge. Compute median hold time. Target: <45 min.
**Risk:** If the signal genuinely needs >45 min to resolve on some assets (BTC may trend longer than SOL), the new hard_timeout could cut winners.

### H3: Condor time cuts (PR #148) reduce median hold from 141 min to ~45 min
**Filed:** April 10, 2026
**Status:** AWAITING (PR merge + 24h measurement)
**Prediction:** Same thesis as H2 but for multi-asset signals. Signal window is 15-45 min for extreme velocity spikes.
**How to score:** Same method as H2. Target median hold: <60 min.

### H4: Roach-B scalp mode (already on main) captures >60% of peak ROE
**Filed:** April 10, 2026
**Status:** AWAITING (re-measurement after dual DSL tracker is killed)
**Prediction:** Pre-fix give-back was 62% of peak. With weak_peak_cut at 12 min and dead_weight_cut at 8 min, give-back should drop to <40%.
**How to score:** After health fix (kill duplicate DSL), measure avg peak ROE vs avg realized ROE over 20+ positions.
**Confound:** Dual DSL tracker must be fixed first or measurements are meaningless.

### H5: Thesis-exit-removed disease affects all v2.0 agents uniformly
**Filed:** April 10, 2026
**Status:** PARTIALLY CONFIRMED
**Evidence:** Phoenix, Wolverine, Condor all confirmed `_v2_no_thesis_exit: true` and exhibit the same pattern (trades drifting to hard timeout). All three diagnosed independently.
**Open question:** Are there other v2.0 agents with this flag that we haven't checked?

### H6: Agent self-reports are high-quality leads but not ground truth
**Filed:** April 10, 2026
**Status:** CONFIRMED (two instances)
**Evidence:**
1. Phoenix said "limits at mid-price getting shredded into 75 fills." Actual cause: executor hardcodes MARKET, no limit orders placed at all.
2. Condor said "hard_timeout: 180, weak_peak_cut disabled." Actual yaml: hard_timeout: 240, weak_peak_cut enabled at 90 min.
**Implication:** Always read yamls before drafting changes. Cross-check agent claims against repo source of truth.

---

## Retired hypotheses

### H0: "Lemon is the existence proof for the fleet"
**Filed:** April 10, 2026
**Status:** SUPERSEDED by Polar
**Original claim:** Lemon being the only profitable agent means it has the only working strategy.
**Correction:** Polar has stronger gross alpha and is only unprofitable due to fees. After fee fix, Polar is the better existence proof — it demonstrates that the Grizzly 3-mode lifecycle scanner generates real alpha when execution doesn't destroy it.

---

## Calibration log

### April 10, 2026 session predictions (from web chat)

**Correct (3/7):**
- Bison v1.2 not fee-bound (different category from strikers)
- Mantis v4.0 has signal issues (inversion test confirmed, marginally)
- Polar fills-per-position 5-8 confirming chunking

**Wrong (4/7):**
- "Lemon is the existence proof" — Polar is better
- "Kodiak will show same clipping pattern as Polar" — Kodiak's exits are excellent
- "Phoenix's inverted PnL would be marginally better" — it's 4x worse
- "Polar will show clean win/loss distribution" — noisy with extreme-conviction loser problem

**Calibration lesson:** Overweight architectural similarity. Same-lineage agents (Polar/Kodiak both Grizzly 3-mode) can have opposite outcomes due to underlying asset volatility differences.
