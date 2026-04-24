# Turbine v2.0 — Volume Generation Engine (Runtime v2-native)

**PRIVATE — Internal use only. Not for public distribution.**

## Mission

Generate high HL perpetuals volume at minimum HL-fee cost. Net to Senpi driven by builder-fee recycling. Trading P&L target: **breakeven**, small positive lean acceptable.

## Economics (target regime)

| Metric | Target | Notes |
|---|---|---|
| Daily volume | $1.4M (Phase 1) → $3M+ (Phase 2) | Phase 1 validates maker mechanics; Phase 2 scales after success |
| HL fees (all-maker) | ~$170/day @ $1.4M · ~$360/day @ $3M | At XYZ-weighted ~1.2 bp round-trip on XYZ, 2.88 bp on HL main |
| Builder fees (to Senpi) | ~$490/day @ $1.4M · ~$1,050/day @ $3M | ~3.5 bp round-trip observed in v1 onchain |
| Net to Senpi | +$320/day → +$690/day | Before trading P&L |
| Trading P&L target | ±$50/day (breakeven) | Funding-fade lean provides small positive drift |

## How v2 differs from v1

| Layer | v1 | v2 |
|---|---|---|
| Architecture | Scanner-driven state machine; manual `create_position` + `close_position` | v2 runtime plugin; producer emits signals, runtime owns lifecycle |
| Exits | Scanner calls `close_position` with `ensureExecutionAsTaker: true`, 5-min timeout → frequently falls through to taker | DSL engine with `ensure_execution_as_taker: false`; ALOs cancel rather than take |
| Entry | Single asset (BTC) | Rotation across 9 assets, XYZ-weighted 70/30 |
| Leverage | 10x | 5x (lower per-tick P&L noise on a $1,500 budget) |
| Slots | 1 | 3 parallel |
| Direction | Alternating LONG/SHORT | Funding-regime-aware: SHORT LONG_CROWDED / LONG SHORT_CROWDED / alternate on FLAT |
| Cycle time | 90s–15 min (variable; spec diverged from reality) | Runtime-enforced `hard_timeout: 15 min` |

The single most important v2 change: **`ensure_execution_as_taker: false` on both entry AND exit.** v1 was bleeding fees on every timeout-fallthrough; v2 refuses to take.

## How it works

Each cron tick (60s):

1. Producer acquires reentrancy lock (fcntl)
2. Queries strategy wallet's open positions via MCP
3. For each empty slot (up to `max_slots`):
   - Advances rotation index, picks next asset from weighted list
   - Queries spread + funding regime
   - Skips if spread > threshold (main 5 bps, XYZ 15 bps)
   - Chooses direction (funding-fade if crowded, alternate on flat)
   - Emits signal via `openclaw senpi external-scanner ingest`
4. Runtime's LLM gate passes signal through (hard-skip only on malformed data)
5. Runtime opens position with `FEE_OPTIMIZED_LIMIT`, 120s ALO, `ensure_execution_as_taker: false`
6. Runtime's DSL engine manages the position lifecycle
7. After 15 min (`hard_timeout`), DSL closes with `FEE_OPTIMIZED_LIMIT`, 120s ALO, never-taker
8. Producer picks up the empty slot on the next tick

## Safety controls

- **`ensure_execution_as_taker: false`** — hard floor on fee leakage. Unfilled ALOs cancel rather than fall through to taker. Exception: after `hard_timeout` if even the final maker attempt fails, one taker close is accepted (structural floor, not default path).
- **Runtime `daily_loss_limit_pct: 3`** — 3% = $45 on $1,500. Halts strategy if cumulative realized losses exceed.
- **Runtime `drawdown_halt_pct: 10`** — 10% = $150 drawdown halt. Full-stop for the Phase 1 test; investigate and resume if legit.
- **Spread gate** — don't emit if book too wide (main > 5 bps, XYZ > 15 bps).
- **Held-asset skip** — producer never emits a signal for an asset already held.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime config. DSL, risk guardrails, external_scanner, LLM pass-through gate |
| `scripts/turbine-producer.py` | Cron producer. Picks asset, queries regime, emits signal |
| `scripts/turbine_config.py` | MCP helpers, config loader, session state I/O |
| `config/turbine-config.example.json` | Deployment template (copy to `turbine-config.json`, fill in live values) |

## Deployment environment

Required:
- `STRATEGY_ADDRESS` or `TURBINE_WALLET` — Turbine wallet address
- `STRATEGY_ID` or `TURBINE_STRATEGY_ID` — strategy id
- `WALLET_ADDRESS` — same wallet (for runtime.yaml substitution)
- `TURBINE_DECISION_MODEL` — LLM for the pass-through gate. Use cheapest available (e.g. `gemini-2.5-flash`, `claude-haiku-4-5-20251001`). This is a pass-through, not a thesis evaluator.
- `TELEGRAM_CHAT_ID` — notification channel

Optional:
- `TURBINE_MAX_SLOTS` (default 3)
- `TURBINE_MARGIN_USD` (default 500)
- `TURBINE_LEVERAGE` (default 5)
- `TURBINE_MAX_SPREAD_MAIN` (default 5 bps)
- `TURBINE_MAX_SPREAD_XYZ` (default 15 bps)

## Cron

```
* * * * * /usr/bin/env python3 /path/to/turbine/scripts/turbine-producer.py >> /path/to/state/producer.log 2>&1
```

Every 60 seconds. Reentrancy-guarded so a long-running tick can't overlap the next one.

## Phase 1 pass/fail criteria

**Pass** = over 24h of running:
- Maker fill rate ≥ 90% on both entries and exits (check `executionAsMaker` per fill)
- Trading P&L within ±$50/day
- At least 200 round-trip cycles completed
- No daily-loss-halt or drawdown-halt trips

**Fail** (any of):
- Maker fill rate < 80% (DSL engine isn't honoring `ensure_execution_as_taker: false`, or timeout too short)
- Trading P&L outside ±$150/day (strategy exposed to drift we didn't model)
- Producer cron failures (e.g. concurrent runs, lock contention, MCP timeouts > 10% of ticks)

If Phase 1 passes, Phase 2 is: scale slot count and/or margin, expand asset list, measure scaling efficiency. If it fails, we diagnose root cause before scaling.
