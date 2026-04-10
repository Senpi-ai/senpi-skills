# Fleet Dossiers — Predators Agent Profiles

Last updated: April 10, 2026
Updated by: Claude Code session (fleet analysis handoff from web chat)

---

## Tier 1: Fee fix alone makes profitable

### Kodiak (SOL hunter)
- **Signal:** Grizzly 3-mode lifecycle on SOL. SM consensus + velocity.
- **Stats (lifetime as of April 10):** 56 positions, 55.4% WR, avg winner +$20.24, avg loser -$11.68. 1.73:1 payoff ratio with majority win rate. Median hold: 45 min. 3.8 fills/position.
- **Exit quality:** 8 of 10 best winners captured peaks within pennies. Phase 2 tiers are excellently calibrated. Unlike Polar, Kodiak is NOT clipping winners.
- **Exit cause distribution:** Phase 1 breaches 60%, Phase 2 trailing 32.5%, hard timeout 7.5%.
- **Disease:** Fee only. $238.84 in fees paid on -$119.56 net. Fee fix recovers ~$153, flipping to ~+$33.
- **Status:** No yaml changes needed beyond FEE_OPTIMIZED_LIMIT. runtime.yaml hard_timeout: 45 min.

### Polar (ETH hunter)
- **Signal:** Grizzly 3-mode lifecycle on ETH. Strong gross alpha but extreme-conviction entries at local tops/bottoms.
- **Stats:** 56 positions, 26.8% WR, avg winner +$29.97, avg loser -$14.81. Median hold: 41.4 min. 5.0 fills/position.
- **Exit quality:** Winners are being severely clipped by Phase 2 tiers. One winner would have gone +113.5% ROI vs captured +82.0%. DSL is too tight on the upside.
- **Disease:** Fee primary, winner-clipping secondary. $367.86 in fees paid. Fee fix recovers ~$235, flipping to ~+$101.
- **Status:** Phase 1: ship fee fix. Phase 2: consider widening Phase 2 tiers. runtime.yaml hard_timeout: 180 min.

### Lemon (counter-trade, degen fader)
- **Signal:** Fades DEGEN/CHOPPY traders losing >10% ROE at 10x+ leverage. 50% WR with 3.7:1 R/R ratio.
- **Stats:** 6 positions, 3W/3L. Avg winner +$5.48, avg loss -$1.45. Median hold: ~3h (all exits by hard timeout).
- **Exit quality:** Timer-based only. Clipping winners (BTC short missed $263 of additional downside). Losers sat in drawdown for full 3h when they should have been cut early by DSL.
- **Disease:** Fee (minor, $8.44 total). Primary issue: hard_timeout is the only exit mechanism.
- **Status:** Python-only (no runtime.yaml). Needs: remove hard_timeout, add tighter trailing DSL, test 10x leverage A/B.
- **Open item:** Scanner doesn't persist addresses of faded traders. Needs lemon_config.py patch.

---

## Tier 2: Fee fix + one surgical change

### Sentinel (inverted pipeline scanner)
- **Signal:** Rising assets -> verify quality traders. Inversion test: actual -$38, inverted -$119. Signal is 3x correct.
- **Stats:** Highly predictive on ETH (70% WR) and TAO (100% WR). Entire negative PnL isolated to HYPE (-$20.46) and BTC (-$22.50) where volatility triggers DSL stops on correct trades.
- **Disease:** Per-asset DSL mismatch. HYPE/BTC need wider stops; ETH/SOL/TAO can keep tight stops.
- **Status:** runtime.yaml hard_timeout: 240 min. Needs per-asset DSL tuning (not yet supported in runtime — may need scanner-level logic).

### Phoenix v2.0 (contribution velocity divergence)
- **Signal:** SM profit velocity diverging from price. Inversion: actual -$72, inverted -$295. Signal is 4x correct.
- **Stats:** 39 positions, 48.7% WR. Avg winner +$11.93, avg loser -$14.95. Median hold: ~3h.
- **Disease:** Thesis-exit removed (`_v2_no_thesis_exit: true`). Trades drift to 180-min hard timeout long after 15-min signal resolves.
- **Status:** PR #147 ships time cuts (hard_timeout 180->45, weak_peak_cut enabled at 15 min, dead_weight_cut at 20 min).
- **Fees:** $179.38 paid. Fee fix recovers ~$115.

### Wolverine v2.0 (HYPE alpha hunter)
- **Signal:** Extreme SM conviction + 15m/1h momentum spikes on HYPE. Same thesis-exit disease as Phoenix.
- **Stats:** 8 positions, 57 fills. Median hold: 180.3 min. 62.5% of trades hit hard timeout.
- **Disease:** `_v2_no_thesis_exit: true`. DSL handles 100% of exits. Needs mercy-kill logic.
- **Status:** Python-only (no runtime.yaml). Target DSL values: weak_peak_cut 25 min, dead_weight_cut 35 min, hard_timeout 60 min. Must be applied via DSL CLI at deploy time.
- **Fees:** $35.35 paid. Fee fix recovers ~$23.

### Condor v2.0 (multi-asset alpha hunter)
- **Signal:** SM consensus + 15m/1h extreme velocity on BTC/ETH/SOL/HYPE. Same thesis-exit disease.
- **Stats:** 9 positions, 57 fills. Median hold: 141 min, avg: 170 min. 3 of 9 hit hard timeout.
- **Disease:** Time cuts too loose (hard_timeout was 240 min, weak_peak_cut 90 min, dead_weight_cut 60 min).
- **Status:** PR #148 tightens to 75/40/30 min. Condor was wrong about its own config during self-diagnosis.
- **Fees:** $94.91 paid. Fee fix recovers ~$61.
- **Critical methodological note:** Always read yaml directly. Condor reported hard_timeout: 180 when actual was 240.

### Roach-B (Python cron, custom scoring)
- **Signal:** Striker-class momentum scalps. Median time-to-peak: 11.28 minutes. 62% give-back from peak.
- **Stats:** 84% of positions exit in Phase 1. Avg peak ROE 3.92%, avg realized ROE 1.48%.
- **Disease:** DSL was too loose for scalp signals. Already fixed on main: weak_peak_cut 12 min, dead_weight_cut 8 min, hard_timeout 25 min.
- **Status:** Scalp-mode values already shipped. Needs fee fix + re-measurement.
- **Health issue (from transcript):** Dual DSL tracker instances detected. Needs runtime fix before performance re-measurement.

---

## Tier 3: Needs more work

### Mantis v4.0 (Vixen/Orca scanner)
- **Signal:** Marginally inverted. Original gross: -$11.33, inverted: +$11.33. But fee drag destroys both directions.
- **Stats:** 15 positions, 46.7% WR. Median hold: 15.6 min. BLAST caused 150% of total gross loss.
- **Disease:** Asset universe problem. Signal works on mid/large-caps, gets chopped on low-liquidity pairs.
- **Status:** Needs whitelist to top-50 volume assets, raise STRIKER_MIN_SCORE, consider signal flip.

### TBD — Owl, Spider, Orca v2.0 not yet interrogated

---

## Hold and monitor

### Bison v1.2 (conviction holder)
- **Signal:** 4H/1H signal agreement. Very low frequency.
- **Stats:** 5 positions, 1W/3L closed, 1 open HYPE covering all realized losses. Avg closed hold: 14.18h.
- **Disease:** Not fee-bound ($9.41 total fees). Sample too small to diagnose. Open HYPE position has +$116 unrealized.
- **Status:** Monitor. Don't change anything until sample size reaches 15+ closed positions.

---

## Not yet profiled (need interrogation)

- Scorpion v2.0 — near break-even after fee fix (~+$2). Needs examination.
- Spider — $57.44 in fees, -$51.23 net. Needs interrogation.
- Orca v2.0 — $52.70 in fees, -$49.83 net. Needs interrogation.
- Owl — $4.25 in fees, -$8.83 net. Low activity.
- Grizzly Horribilis — $113.29 in fees, -$166.34 net. Conviction class, needs examination.
- Cobra, Jaguar, Raptor, Dog, Fox — not profiled in the April 10 session.
- Hawk — single best signal, 20x leverage too high.
- Barracuda — funding decay scanner, not profiled.
- Bald Eagle — XYZ equities/commodities, not profiled.
- 4 zero-trade agents — need identification and health check.
