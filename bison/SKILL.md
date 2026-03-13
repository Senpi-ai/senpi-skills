---
name: bison-strategy
description: >-
  BISON v1.1 — Conviction holder for top 10 Hyperliquid assets by volume. Enters on 4h trend structure
  + 1h momentum + SM alignment convergence. Holds through pullbacks with DSL High Water Mode
  (mandatory). Re-evaluates thesis every 5 min — exits when conviction breaks, not when price
  retraces. Wide Phase 1 floors, no time-based exits, infinite trailing at 85% of peak.
  v1.1: daily entry cap only enforced when day PnL is negative. When profitable, reloads
  in batches of 3 — BISON keeps trading as long as it's making money.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.1"
  platform: senpi
  exchange: hyperliquid
---

# BISON — Conviction Holder

Top 10 assets by volume. Multi-signal thesis entry. Thesis-based re-evaluation exits. Wide DSL bands that tighten as profit grows.

## MANDATORY: DSL High Water Mode

**BISON MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any BISON position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "tiers": [
    {"triggerPct": 10,  "lockHwPct": 0,  "consecutiveBreachesRequired": 3},
    {"triggerPct": 20,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 30,  "lockHwPct": 40, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50,  "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 75,  "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 100, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. This defeats the entire purpose of BISON. Always verify the state file contains these fields after creation.**

Phase 1 conviction-scaled floors (also mandatory in every state file):

| Entry Score | absoluteFloorRoe | Time Exits |
|---|---|---|
| 6-7 | -25 | All disabled (0) |
| 8-9 | -30 | All disabled (0) |
| 10+ | 0 (unrestricted) | All disabled (0) |

## How BISON Trades

### Entry
Scans top 10 assets by volume every 5 minutes. Builds a conviction thesis from:
- 4h trend structure (higher lows / lower highs) — **required**
- 1h trend agreement — **required**
- 1h momentum ≥ 0.5% in direction — **required**
- Smart money alignment — **hard block if opposing**
- Funding direction, volume trend, OI growth, RSI — boosters

Minimum score: 8. Conviction-scaled margin: 25% base, 31% at score 10, 37% at score 12+.

### Hold
Every 5-minute scan re-evaluates held positions FIRST, before looking for new entries. The thesis is intact as long as:
- 4h trend structure hasn't flipped
- SM hasn't flipped against the position
- Funding hasn't gone extreme against the position
- Volume hasn't dried up for 3+ consecutive hours

If ANY of these break → output `thesis_exit` → agent closes the position.

### Exit (DSL)
DSL High Water Mode handles mechanical exits. Wide early, tight late:
- +10% ROE: no lock (confirms trade working)
- +50% ROE: lock 60% of high water (+30% ROE locked)
- +100% ROE: lock 85% of high water — infinite trail from here, no ceiling

A trade at +500% ROE has its stop at +425% ROE. The 85% geometry holds forever.

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 5 min | isolated | Thesis re-evaluation + new entry scan |
| DSL v5.3.1 | 3 min | isolated | High Water Mode trailing stops |

Both crons MUST be isolated sessions with `agentTurn` payload. Use `NO_REPLY` for idle cycles.

## Notification Policy

**ONLY alert the user when:**
- Position OPENED (asset, direction, thesis reasons, margin)
- Position CLOSED — either by DSL (breach) or thesis exit (which signal broke)
- Risk guardian triggered
- Critical error

**NEVER alert for:**
- Scanner found nothing
- Thesis re-evaluation passed (position still valid)
- DSL routine check
- Any reasoning or narration

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 3 per batch; unlimited batches when day PnL ≥ 0. Hard cap when negative. |
| Absolute floor | 3% notional (~30% ROE at 10x) |
| G5 per-position cap | 8% of account |
| G2 drawdown halt | 25% from peak |
| Daily loss limit | 10% |
| Cooldown after 3 consecutive losses | 120 min |
| Stagnation TP | 15% ROE stale for 2 hours |

## Bootstrap Gate

On EVERY session, check if `config/bootstrap-complete.json` exists. If not:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦬 BISON is online. Scanning top 10 for conviction thesis. DSL High Water Mode active. Silence = no conviction."

## Files

| File | Purpose |
|---|---|
| `scripts/bison-scanner.py` | Thesis builder + thesis re-evaluator |
| `scripts/bison_config.py` | Shared config, MCP helpers, state I/O |
| `config/bison-config.json` | All configurable variables with DSL High Water tiers |
| DSL v5.3.1 (shared skill) | Trailing stop engine — MUST be configured with High Water Mode |
