## Step 8: Lessons from the Field

### What Works

- **Hourly trend alignment is everything.** Three shorts aligned with the hourly downtrend turned +$200 in unrealized gains. One long fighting a 2-week hourly downtrend lost $46 before being cut. Always check the hourly first.
- **DSL protects profits.** v4 per-tier retrace tightening locks more at higher tiers. Let the system work.
- **Sizing up slowly is correct.** Calibration trades at 30% size catch mistakes cheap.
- **The scanner is a filter, not a crystal ball.** Cross-scan tracking helps — rising `scoreDelta` increases confidence.
- **BTC macro filter prevents the worst losses.** Going LONG on alts during a BTC breakdown was the #1 source of catastrophic losses.
- **Trade journaling enables learning.** After 20+ trades with full scanner snapshots, you can see which pillar scores predict winners.
- **Exposure guard prevents correlated blowups.** Three same-direction positions in correlated alts = portfolio-level risk.
- **Limit orders beat polling crons** for conditional entries.
- **175 is a better threshold than 200** — with reduced position sizing for 175-199.
- **Cutting dead weight fast frees capital.** A position sitting misaligned at SM conviction 1 for 6 hours is -$97 you could've deployed elsewhere.
- **Ignoring counter-trend SM flip signals works.** Successfully ignored 3 consecutive HYPE FLIP_NOW signals that would have each lost money.
- **Best moves happen FAST (v6).** XRP hit Tier 3 in 19 minutes. XMR hit Tier 2 in 37 minutes. If a position isn't moving within 30 minutes of entry, it probably won't. Use this as a staleness filter.
- **Concentration beats diversification (v6).** 2-4 high-conviction positions beat 6 mediocre ones at small account sizes ($500-$5K). Fewer positions = healthier margin buffer (80.6% → 89.7% just by trimming from 4 to 2).
- **Checking real max leverage prevents missed opportunities (v6).** Scanner said HYPE was 4x, actual max was 10x. Always check `max-leverage.json`.
- **Race condition prevention eliminates phantom closes (v6).** The "disappearing position" mystery was caused by scanner closing a position, then DSL firing 2 min later and finding nothing. `close_position_safe()` fixes this.

### What Doesn't Work

- **SM flip signals without hourly trend confirmation.** Conviction 4 on a 1-minute bounce doesn't override a 2-week downtrend. This caused $346 in realized losses in one session.
- **Flipping based on conviction alone.** Conviction 4 with 130 traders ≠ conviction 4 with 400 traders. Always check trader count.
- **Rapid re-flipping.** BTC flipped 3 times in 2 hours = pure chop. Every flip costs ~1.3% of margin in fees. 3 flips = 4% margin burned for nothing.
- **Counter-trend bounce trades.** They look tempting — SM says LONG, there's a bounce, funding is favorable. But the hourly trend doesn't lie. A bounce inside a downtrend is a dead cat bounce 80% of the time.
- **Forcing trades in low-conviction environments.** If scanner scores are declining and risk flags are everywhere, hold cash.
- **Ignoring BTC macro trend.** If BTC is dumping, don't go long on alts no matter what the alt's scanner score says.
- **All positions in one direction.** Even if three different alts score well for LONG, opening all three gives you 3x the downside when the market turns.
- **Holding dead weight positions.** If SM conviction is 0, cut immediately. If conviction collapses from 4 to 1, cut immediately. Don't wait for DSL.
- **Trusting Tier 1 lock as a profit guarantee (v6).** The lock protects from the high water mark, not from your entry price. You can still lose money with a Tier 1 lock if price dumps below entry.
- **Entering on erratic rank history (v6).** Asset bouncing #34→#29→#11→#29→#12 is noise. Consistent improvement (#45→#30→#18→#10) is signal.
- **Diversifying into 6 mediocre positions (v6).** At small account sizes, this kills your margin buffer and spreads attention too thin. Concentrate on 2-4 high-conviction plays.
- **Using `dryRun: true` to test (v6).** It actually executes. There is no safe test mode.

### Phase 1 Retrace Threshold Tuning

- Conservative: 1.5-2% (tight, quick exits)
- Moderate: 2-3% (room to breathe)
- Aggressive: 3-4% (ride through volatility)

### Fee Awareness

Round-trip fees are ~0.18% of notional. At leverage:
- 5x: ~0.9% of margin
- 7x: ~1.26% of margin
- 10x: ~1.8% of margin

**Flip fee drag:** Each SM flip = close + open = full round-trip. At 7x, 3 flips on one asset = ~3.8% margin burned in fees. This is why the 30-minute cooldown and 3% cumulative flip cost cap exist.

---

