# Experiment Registry

Last updated: April 11, 2026 (day 2)
Updated by: Claude Code day-2 session

---

## Format

Each experiment gets a unique ID (EXP-NNN), a clear hypothesis, a single variable changed, a measurement plan, and a result field that gets filled after observation.

---

## EXP-001: Phoenix v2.0 time cuts
- **PR:** #147 (MERGED)
- **Variable:** hard_timeout 180→45, weak_peak_cut enabled at 15 min, dead_weight_cut at 20 min
- **Hypothesis:** Median hold drops from ~3h to ~30 min. Gross PnL improves.
- **Baseline:** 360 fills, -$250.67 net, -$71.29 gross. Median hold ~3h.
- **Status:** LIVE — agent instructed
- **Result:** CONFIRMED — median hold dropped from ~3h to ~30 min. Weak peak cut firing at 15 min, hard timeout at 45 min. Deployment bug: npx skills add overwrote local ensureExecutionAsTaker patch.

## EXP-002: Condor v2.0 time cuts
- **PR:** #148 (MERGED)
- **Variable:** hard_timeout 240→75, weak_peak_cut 90→40, dead_weight_cut 60→30
- **Hypothesis:** Median hold drops from ~141 min to ~45 min.
- **Baseline:** 103 fills, -$95.32 net, -$0.40 gross. Median hold 141 min.
- **Status:** LIVE — agent instructed
- **Result:** PENDING (score April 12)

## EXP-003: Fleet-wide FEE_OPTIMIZED_LIMIT
- **PR:** #149 (MERGED then REVERTED via #151)
- **Branch preserved:** fleet-fix/fee-optimized-limit-all-agents
- **Variable:** DSL exit order_type MARKET → FEE_OPTIMIZED_LIMIT across 20 yaml agents
- **Hypothesis:** Fee rate drops from ~7.8 bps to ~2.5 bps. 3-4 agents flip to net-positive.
- **Baseline:** Fleet-wide fee/vol 7.6-8.4 bps. Total fees ~$1.3K across active agents.
- **Dependency:** Sarvesh's runtime release (expected April 14-15)
- **Status:** STAGED — re-apply same hour runtime deploys
- **Result:** PENDING

## EXP-004: Roach-B scalp mode
- **Variable:** weak_peak_cut 12, dead_weight_cut 8, hard_timeout 25 (already on main)
- **Hypothesis:** Give-back drops from 62% of peak to <40%.
- **Baseline:** Avg peak ROE 3.92%, avg realized ROE 1.48%.
- **Status:** UNBLOCKED — dual DSL tracker killed April 10. Clean measurement now possible.
- **Result:** NOT IMPROVED — 5 trades, 0% net WR, avg peak ROE only 0.34%. Breakouts firing on noise. Tightened thresholds in PR #166 (MIN_SCORE 9→10, velocity floor 10→15, vol ratio 1.5→2.0).

## EXP-005: Lemon DSL retune
- **PR:** #152 (MERGED)
- **Variable:** hard_timeout 240→480, weak_peak_cut 90→60 (min_value 3.0→2.0), dead_weight_cut 30→20
- **Hypothesis:** Winners run longer (no more clock-based clipping). Losers cut faster.
- **Baseline:** 18 fills, +$3.79 net, +$12.09 gross. All exits by hard timeout.
- **Status:** LIVE — agent confirmed
- **Result:** PENDING (score April 12)

## EXP-006: Orca v2.0 DSL widening
- **PR:** #154 (MERGED)
- **Variable:** hard_timeout 30→45, weak_peak_cut 15→25, dead_weight_cut 10→15
- **Hypothesis:** Weak_peak_cut exits drop from 50% to <30%. Winners get time to develop.
- **Baseline:** 150 fills, -$49.83 net, +$4.29 gross. 50% exits via weak_peak_cut.
- **Status:** LIVE — agent confirmed
- **Result:** PENDING (score after 20+ positions)

## EXP-007: Scorpion v2.0 asset blacklist
- **PR:** #155 (MERGED)
- **Variable:** XPL, LIT, FARTCOIN blacklisted from trading (still in swarm detection)
- **Hypothesis:** No losses >$25 on next 14 positions. WR improves from 64% to 70%+.
- **Baseline:** 166 fills, -$46.89 net, +$28.98 gross. XPL/LIT caused $96 of $122 losses.
- **Status:** LIVE — agent confirmed
- **Result:** PENDING (score after 14 positions)

## EXP-008: Polar market-driven exits
- **PR:** #158 (MERGED)
- **Variable:** hard_timeout 180→480, weak_peak_cut enabled at 60 min, dead_weight_cut at 30 min
- **Hypothesis:** Avg winner ROE improves >20% as winners are no longer killed by 180-min clock.
- **Baseline:** 281 fills, -$152.73 net, +$219.46 gross. Winners clipped by timeout.
- **Status:** LIVE — agent confirmed
- **Result:** CONFIRMED DIRECTIONALLY — post-swap trades show dead_weight_cut at 33 min, Phase 1 at 36 min, weak_peak_cut at 60 min. Zero trades hitting 480-min timeout. First 4 trades in window were legacy (pre-swap).

## EXP-009: Grizzly v4.0 contrarian flip (no pyramiding)
- **PR:** #156 (MERGED)
- **Variable:** Direction flip + scoring alignment with Horribilis (MOVE_EXHAUSTION added, velocity simplified, leverage capped 10x)
- **Hypothesis:** WR improves from 18% toward 50%+ as contrarian entries fade exhausted moves correctly.
- **Baseline:** 91 fills, -$145.75 net. Inversion test: 81.8% WR if flipped.
- **A/B control:** Single entry (vs Horribilis pyramiding)
- **Status:** LIVE — agent confirmed with dry-run
- **Result:** NO DATA — zero trades in 20h. Scores hovering 5-7. MOVE_EXHAUSTION filtering correctly. Agent is healthy, waiting for qualifying setup.

## EXP-010: Grizzly Horribilis v2.0 contrarian flip (with pyramiding)
- **PR:** #156 (MERGED)
- **Variable:** Direction flip + scale-up logic inverted (SM must disagree for add)
- **Hypothesis:** Same as EXP-009 but pyramiding amplifies winners.
- **Baseline:** 116 fills, -$166.34 net. Inversion: -$53 actual, +$53 inverted.
- **A/B variable:** Pyramiding (up to 3 entries per position)
- **Status:** LIVE — agent confirmed
- **Result:** FLAT — -$0.26 on 43 fills. Contrarian trades offsetting each other. Pyramiding not adding value in chop.

## EXP-011: Cheetah v3.0 contrarian flip (HYPE)
- **PR:** #159 (MERGED)
- **Variable:** Direction flip + MOVE_EXHAUSTION + velocity simplification + same-dir cooldown
- **Hypothesis:** Inversion test showed +$175 inverted vs -$175 actual. Contrarian Cheetah should be net-positive.
- **Baseline:** 261 fills, -$323.48 net, -$175.15 gross. Worst in fleet.
- **Status:** SUPERSEDED by EXP-015 (Cheetah v4.0 funding fader)
- **Concern:** Cheetah may have self-modified DSL settings (reported different values than yaml). Verify.
- **Result:** FAILED — 5 trades, 40% WR, -$39.20. Neither momentum nor contrarian SM works on HYPE in chop. Retooled to funding rate thesis.

## EXP-012: Dog v2.0 contrarian flip (multi-asset)
- **PR:** #159 (MERGED)
- **Variable:** Direction flip + exhaustion inverted (now bonus) + leverage reduced + DSL widened
- **Hypothesis:** Inversion: -$61 actual, +$61 inverted. Contrarian Dog net-positive after fee fix.
- **Baseline:** 35 fills, -$105.24 net, -$61.34 gross.
- **Status:** LIVE — agent confirmed, took first trade (HYPE LONG fading SM SHORT)
- **Result:** EARLY POSITIVE — best performer in fleet this window. +$19.08 with +$26.64 unrealized. 5 positions, one big SHORT HYPE winner covers all losses. Contrarian shape confirmed (small losses, big winners).

## EXP-013: Vulture v1.0 (NEW — SM exhaustion fader)
- **PRs:** #157 + #160 (MERGED)
- **Variable:** Entirely new agent replacing Fox v2.0. Contrarian-native scoring with exhaustion gate (>3% 4H price move required).
- **Hypothesis:** Purpose-built contrarian outperforms retrofitted direction flips.
- **Baseline:** Fox v2.0 was -$92.63 net. Even inverted, -$1.96 (signal too weak for any fee level).
- **Status:** READY TO DEPLOY — code on main at vulture/
- **Result:** PENDING (score April 17 — needs deployment first)

## EXP-014: Kodiak leverage compression
- **PR:** #166
- **Variable:** leverage 7/10/12/15x → 7/10x
- **Hypothesis:** Same signal quality, fewer catastrophic losses from amplified retraces.
- **Baseline:** -$199.17 net, recent 15x trade lost $45.55 + $9.35 fees.
- **Status:** LIVE

## EXP-015: Cheetah v4.0 HYPE funding fader
- **PR:** #166
- **Variable:** Complete scanner retool from SM consensus to funding rate thesis.
- **Hypothesis:** Funding extremes on HYPE mean-revert, collecting funding + fading direction produces positive EV.
- **Baseline:** -$351.28 net (worst in fleet).
- **Status:** LIVE

## EXP-016: Roach-B striker threshold tightening
- **PR:** #166
- **Variable:** MIN_SCORE 9→10, velocity floor 10→15, vol ratio 1.5→2.0
- **Hypothesis:** Fewer trades, higher quality breakouts, peak ROE >2% on entries.
- **Baseline:** 5 trades with max peak ROE 0.57%.
- **Status:** LIVE

## EXP-017: Spider health fix (self-patched)
- **Variable:** Account value calculation fixed (was double-counting main+xyz).
- **Hypothesis:** Position sizing drops from ~$825 to ~$439, loss per trade normalizes.
- **Status:** LIVE (agent self-patched)

---

## Queued experiments (not yet implemented)

### EXP-Q1: Lemon 10x leverage A/B
- Test 10x vs current 5x leverage. Separate experiment from DSL retune.

### EXP-Q2: Sentinel per-asset DSL tuning
- Widen HYPE/BTC stops, keep ETH/SOL/TAO tight.

### EXP-Q3: Mantis v4.0 asset whitelist
- Restrict to top-50 volume assets.

### EXP-Q4: Wolverine mercy-kill logic
- Re-enable thesis exit with "mercy kill" threshold.

### EXP-Q5: Bison asset universe expansion
- Top 10 → Top 30-50 assets with strict 3-of-3 convergence.

---

## Baselines — snapshot at 18:45 UTC April 10, 2026

| Agent | Net PnL | Fees Paid | Volume | Fills | Gross PnL | Fee/Vol bps |
|-------|---------|-----------|--------|-------|-----------|-------------|
| Phoenix v2.0 | -$250.67 | $179.38 | $228,670 | 360 | -$71.29 | 7.84 |
| Condor v2.0 | -$95.32 | $94.91 | $123,638 | 103 | -$0.40 | 7.68 |
| Wolverine v2.0 | -$35.74 | $35.35 | $41,988 | 57 | -$0.39 | 8.42 |
| Lemon | +$3.79 | $8.30 | $10,531 | 18 | +$12.09 | 7.88 |
| Roach-B | -$119.40 | $126.61 | $151,293 | 274 | +$7.21 | 8.37 |
| Polar | -$152.73 | $372.19 | $488,820 | 281 | +$219.46 | 7.62 |
| Kodiak | -$119.56 | $238.84 | $301,532 | 206 | +$119.28 | 7.92 |
| Scorpion v2.0 | -$46.89 | $75.87 | $98,045 | 166 | +$28.98 | 7.74 |
| Cheetah | -$323.48 | $148.32 | $196,159 | 261 | -$175.16 | 7.56 |
| Dog | -$105.24 | $43.90 | $55,905 | 35 | -$61.34 | 7.85 |
| Grizzly v3.0 | -$145.75 | n/a | $105,799 | 91 | n/a | n/a |
| Grizzly Horribilis | -$166.34 | $113.29 | $148,445 | 116 | -$53.05 | 7.63 |
| Orca v2.0 | -$49.83 | $55.66 | $61,891 | 150 | +$4.29 | 8.99 |
| Roach | -$192.45 | n/a | $200,740 | 305 | n/a | n/a |

## Baselines — snapshot at 15:00 UTC April 11, 2026

*(To be populated with updated PnL data)*

---

## Scoring methodology

1. Log prediction BEFORE merge
2. Wait for measurement window (24h minimum, 48h preferred)
3. Pull data and score against prediction
4. Update calibration log in fleet-hypotheses.md
5. If prediction wrong, document why and what was learned

**Next scoring dates:**
- April 12: EXP-002, 005 (time cut + DSL retune experiments)
- April 14: EXP-012 (Dog contrarian, high trade frequency)
- April 14-15: EXP-003 (fee fix, depends on Sarvesh deploy)
- April 17: EXP-009, 013 (Grizzly A/B + Vulture, need more positions)
