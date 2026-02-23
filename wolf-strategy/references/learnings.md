## Key Learnings & Gotchas

### Bugs & Footguns

| Issue | Impact | Fix |
|---|---|---|
| `dryRun: true` actually executes | Real money lost on "test" trades | **NEVER use dryRun** |
| DSL v4 auto-closes are real | Will close live positions if state file is near breach | Read state files before running |
| Phantom close race condition | Multiple jobs close same position | Deactivate DSL state + disable cron on ANY close |
| DSL reads `DSL_STATE_FILE` env only | Positional args silently ignored | Always set env var in cron command |
| Scanner leverage is conservative | May reject valid assets | Always check `max-leverage.json` |
| DSL `triggerPct` not `threshold` | Wrong field name = wrong behavior | v4 expects percentage (5), not decimal (0.05) |
| `lockPct` not `retracePct` | Schema mismatch | Use `lockPct` in state files |
| Direction flip must update `absoluteFloor` | Old floor causes instant breach | Recalculate floor on every flip |

### Trading Discipline

1. **Empty slot > mediocre position.** Skipped 30+ IMMEDIATEs with neg velocity, erratic history, or low reason count.
2. **Concentration beats diversification.** 2 slots averaging +10% >>> 6 slots averaging -6%.
3. **Hourly trend > SM signals.** Conviction 4 on a 1-min bounce doesn't override a 2-week downtrend.
4. **Cut SM conviction 0 immediately.** Don't wait. Every minute = missed opportunity.
5. **Rank climb IS the entry signal.** Conviction is lagging. By conviction 4, the move is priced in.
6. **XYZ assets: ignore trader count.** New instruments with low participation. Use reason count + rank velocity.
7. **Erratic rank history = SKIP always.** Zigzag pattern is noise, not signal.
8. **Don't anchor on past positions.** Evaluate every signal fresh.
9. **Cross-margin buffer matters.** 2 crypto positions = ~90% buffer. 4 positions = ~80%. Fewer = safer.
10. **Tier 1 doesn't guarantee profit.** Lock is from HW, not entry. Price can still dump below entry.

---

