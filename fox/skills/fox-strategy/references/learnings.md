# FOX v7 Learnings & Trading Discipline

> Forked from Wolf v4 proven session results. Same trading logic, independent strategy.

## Proven Session Results (from Wolf, Feb 23, 2026)

**20+ trades, 67% win rate, +$1,500 realized on $6.5k→$7k budget.**

### Top Winners
- HYPE SHORT: +$560 (Tier 1→4, +15.5% ROE, let DSL run)
- XRP SHORT #1: +$303 (Tier 3 in 19 min)
- ETH SHORT #2: +$274 (Tier 1→4, ~18% ROE)
- SNDK SHORT: +$237 locked (Tier 3, +19% peak ROE in 65 min)
- LIT SHORT: +$205
- APT SHORT: +$178 (stagnation close at +9.3% ROE)

### Key Losses
- NVDA LONG: -$114 (3/3 Phase 1 breaches, counter-trend)
- SILVER SHORT: -$72
- MON SHORT: -$62 (too few traders)
- MU SHORT: -$58 (dead weight, conv 0)

### Pattern
Winners move FAST. XRP Tier 3 in 19 min, XMR Tier 2 in 37 min, SNDK +19% ROE in 45 min. If not moving within 30 min, probably won't.

---

## Key Learnings

1. **Rank climb IS the entry signal — conviction is lagging.** Don't gate entries on conviction ≥4. By the time conviction is 4, the move is priced in.
2. **Catch movers at #50→#35→#20.** Find big movers climbing from deep positions before they reach top 15.
3. **PnL/contribution acceleration > trader count.** A fast-accelerating asset at rank #30 with 15 traders beats a stale rank #10 with 100 traders.
4. **Conviction collapse = instant cut.** ETH went conv 4→1 (220→24 traders) in 10 min. Cut for -$12 instead of -$100+.
5. **Don't anchor on past positions.** Evaluate every signal fresh.
6. **Concentrate, don't spread thin.** 6 positions averaging -6% = slow death. 2-3 high-conviction positions is the way.
7. **Hourly trend > SM signals.** SM conviction 4 on a 1-min bounce doesn't override a 2-week downtrend. NEVER go counter-trend on hourly.
8. **Tier 1 lock doesn't guarantee profit.** Lock is from high-water, not entry.
9. **Oversold decline rule.** Skip short entries when RSI < 30 AND extended 24h move (> -4%).
10. **Speed of move = quality signal.** If not moving in 30 min, it probably won't.

---

## Known Bugs & Footguns

1. **`dryRun: true` actually executes** — NEVER use dryRun.
2. **DSL transient API failures**: Clearinghouse queries can fail transiently. DSL v5.3.1 retries. Don't panic on 1 failure.
3. **Health check can't see XYZ positions**: `fox-health-check.py` doesn't query `dex=xyz`, causing false ORPHAN_DSL warnings.
4. **Multiple cron jobs can race**: Always deactivate DSL + disable cron when ANY job closes a position.
5. **Max leverage varies per asset**: Check `max-leverage.json`. E.g., WLFI is only 5x max.
6. **`close_position` is the tool to close** (not `edit_position` with action=close).
7. **XYZ positions**: Use `leverageType: "ISOLATED"`. The WALLET isn't cross/isolated — individual POSITIONS are.

---

## Trading Discipline Rules

- **Empty slot > mediocre position** — never enter just to fill a slot
- **Act on first IMMEDIATE_MOVER** — don't wait for confirmation scans
- **Mechanical DSL exits** — never override the DSL, let it do its job
- **Race condition prevention** — deactivate DSL + disable cron in the same action when any job closes
- **Dead weight rule** — SM conviction 0 + negative ROE for 30+ min = cut immediately
- **Rotation bias** — favor swapping into higher-conviction setups over holding stale positions
- **Budget discipline** — all sizing from fox-strategies.json, never hard-code dollar amounts
