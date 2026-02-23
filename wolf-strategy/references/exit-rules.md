## Exit Rules

### 1. DSL v4 Mechanical Exit (Primary)

DSL handles all trailing stop exits automatically. See [Position Management](#position-management--dsl) for tier details.

### 2. SM Conviction Collapse

| Condition | Action |
|---|---|
| Conviction drops to 0 | **Instant cut** — no waiting |
| Conviction drops 4→1 with 100+ traders leaving | **Instant cut** |
| Conviction 1-2, no momentum, negative ROE | Cut within minutes |

**Real example:** ETH conviction 4→1 (220→24 traders in 10 min) — cut for -$12 instead of letting it bleed to -$100+.

### 3. Dead Weight Rule

If a position has:
- SM conviction 0
- Negative ROE
- Open for 30+ minutes

→ **Instant cut.** Don't wait for DSL to handle it.

**Real example:** MU sat at -2.9% with SM conv 0. Cut it, freed slot for XPL which ran +47.8%.

### 4. Race Condition Prevention

**Critical:** Multiple cron jobs (DSL, SM flip, scanner rotation) can all try to close the same position simultaneously.

**Fix:** When ANY job closes a position:
1. Set DSL state file `active: false` immediately
2. Disable the DSL cron job immediately
3. Log which job performed the close

---

