# Experiment Registry

Last updated: April 10, 2026
Updated by: Claude Code session (fleet analysis handoff from web chat)

---

## Format

Each experiment gets a unique ID (EXP-NNN), a clear hypothesis, a single variable changed, a measurement plan, and a result field that gets filled after observation.

---

## EXP-001: Phoenix v2.0 time cuts
- **PR:** Senpi-ai/senpi-skills#147
- **Branch:** fleet-fix/phoenix-v2.0-time-cuts
- **Variable changed:** hard_timeout 180->45, weak_peak_cut disabled->enabled at 15 min (min_value 1.5%), dead_weight_cut disabled->enabled at 20 min
- **Hypothesis:** Median hold time drops from ~3h to ~30 min. Gross PnL improves as trades close near signal resolution.
- **Baseline (pre-merge):** 39 positions, -$72.43 net, -$250.67 with fees. Median hold ~3h. 48.7% WR.
- **Measurement plan:** Pull Phoenix closed positions 24h and 48h after merge. Compute: median hold time, gross PnL, net PnL, WR, avg winner/loser.
- **Prediction logged:** Median hold <45 min. Gross PnL per trade improves by >30%.
- **Result:** PENDING

## EXP-002: Condor v2.0 time cuts
- **PR:** Senpi-ai/senpi-skills#148
- **Branch:** fleet-fix/condor-v2.0-time-cuts
- **Variable changed:** hard_timeout 240->75, weak_peak_cut 90->40, dead_weight_cut 60->30
- **Hypothesis:** Median hold time drops from ~141 min to ~45 min.
- **Baseline (pre-merge):** 9 positions, -$95.32 net. Median hold 141 min, avg 170 min.
- **Measurement plan:** Same as EXP-001 but for Condor.
- **Prediction logged:** Median hold <60 min. Gross PnL per trade improves.
- **Result:** PENDING

## EXP-003: Fleet-wide FEE_OPTIMIZED_LIMIT
- **PR:** Senpi-ai/senpi-skills#149 (DRAFT)
- **Branch:** fleet-fix/fee-optimized-limit-all-agents
- **Variable changed:** DSL exit order_type MARKET -> FEE_OPTIMIZED_LIMIT with ensure_execution_as_taker: true across 20 yaml agents
- **Hypothesis:** Maker fill rate goes from 0% to ~70%. Fee rate drops from ~0.078% to ~0.025%. 3-4 agents flip to net-positive.
- **Baseline:** Fleet-wide fees-to-volume 0.075-0.085%. Total fees paid ~$1.1K-1.3K across 13 active agents.
- **Measurement plan:** Within 1h of merge: capture baseline fees-to-volume per agent. Within 4h: check for maker fills. Within 24h: clean before/after comparison.
- **Prediction logged:** Polar +$101, Kodiak +$33, Scorpion ~break-even, Lemon +$9 (fee fix alone).
- **Dependency:** Sarvesh's order-type-configuration runtime release must deploy first (expected April 14-15).
- **Result:** PENDING

## EXP-004: Roach-B scalp mode (already on main)
- **Variable changed:** weak_peak_cut 12 min, dead_weight_cut 8 min, hard_timeout 25 min (already merged)
- **Hypothesis:** Give-back drops from 62% of peak to <40%.
- **Baseline:** Avg peak ROE 3.92%, avg realized ROE 1.48%, 62% give-back.
- **Measurement plan:** After dual DSL tracker health fix, measure over 20+ new positions.
- **Blocker:** Dual DSL tracker instances must be killed first (health issue). Performance measurement meaningless until then.
- **Result:** PENDING (BLOCKED on health fix)

---

## Queued experiments (not yet implemented)

### EXP-Q1: Lemon 10x leverage A/B
- Remove hard_timeout (let counter-trade unwinds run indefinitely)
- Add tighter trailing DSL
- Test 10x leverage vs current 5x
- Requires Python scanner changes (no runtime.yaml)

### EXP-Q2: Sentinel per-asset DSL tuning
- Widen HYPE/BTC stops, keep ETH/SOL/TAO tight
- May require scanner-level logic since runtime DSL is per-strategy not per-asset

### EXP-Q3: Mantis v4.0 asset whitelist
- Restrict to top-50 volume assets (remove BLAST, XPL, ZRO)
- Raise STRIKER_MIN_SCORE
- Consider signal direction flip

### EXP-Q4: Wolverine mercy-kill logic
- Re-enable thesis exit with a "mercy kill" threshold
- Target: close if 15m velocity goes deeply negative AND position has been open >25 min
- Requires Python scanner changes

---

---

## Baselines — snapshot at 18:45 UTC April 10, 2026

Captured from Predators MCP. These are the lifetime numbers at the moment the PRs merged. All future measurements diff against these.

| Agent | Net PnL | Realized | Unrealized | Fees Paid | Volume | Trades | Gross PnL | Fee/Vol bps |
|-------|---------|----------|------------|-----------|--------|--------|-----------|-------------|
| Phoenix v2.0 | -$250.67 | -$250.67 | $0 | $179.38 | $228,670 | 360 | -$71.29 | 7.84 |
| Condor v2.0 | -$95.32 | -$95.32 | $0 | $94.91 | $123,638 | 103 | -$0.40 | 7.68 |
| Wolverine v2.0 | -$35.74 | -$35.74 | $0 | $35.35 | $41,988 | 57 | -$0.39 | 8.42 |
| Lemon | +$3.79 | +$3.79 | $0 | $8.30 | $10,531 | 18 | +$12.09 | 7.88 |
| Roach-B | -$119.40 | -$119.40 | $0 | $126.61 | $151,293 | 274 | +$7.21 | 8.37 |
| Polar | -$152.73 | -$159.49 | +$6.76 | $372.19 | $488,820 | 281 | +$219.46 | 7.62 |
| Kodiak | -$119.56 | -$119.56 | $0 | $238.84 | $301,532 | 206 | +$119.28 | 7.92 |
| Scorpion v2.0 | -$46.89 | -$46.89 | $0 | $75.87 | $98,045 | 166 | +$28.98 | 7.74 |

**Key observations from baselines:**
- Fee/volume runs 7.6-8.4 bps across all agents — confirms 0% maker rate fleet-wide
- Polar, Kodiak, Scorpion, Roach-B all have positive gross PnL — strategy alpha exists, fees destroy it
- Condor and Wolverine are almost exactly break-even on gross — fees are the entire loss
- Phoenix is the only agent with genuinely negative gross PnL (-$71) — time cuts are the primary fix, not just fees

---

## Scoring methodology

For each experiment:
1. Log prediction BEFORE merge
2. Wait for measurement window (24h minimum, 48h preferred)
3. Pull data and score against prediction
4. Update calibration log in fleet-hypotheses.md
5. If prediction wrong, document why and what was learned
