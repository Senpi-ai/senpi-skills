# Jaguar — Hot-Streak Striker

**Runtime:** 1.0 (schema mislabeled — agent semver in version field; flag)  ·  **Asset:** Multi-asset  ·  **Status:** Live  ·  **Version:** 3.4.0

## Thesis

Jaguar v3.7 — runtime risk.guard_rails: cap losers, ride winners.

See `SKILL.md` and `runtime.yaml` for full scoring components, gates, and DSL configuration.

## Scoring components

Defined in the producer/scanner. See `runtime.yaml` `scanner:` section and the producer script in this folder for the current scoring weights and gates.

## Entry / Exit

- **Entry:** Producer-emitted signals scored against MIN_SCORE gate.
- **Exit:** DSL (Dynamic Stop-Loss) Phase 1 + Phase 2 trailing exits per `runtime.yaml` `dsl:` section.
- **Time cuts:** See `dsl:` config in runtime.yaml.

## Fleet rules applied

- Standard fleet drawdown gate.
- Fee budget tracking via shared infra.
- Producer reentrancy guard via fcntl lockfile (Runtime 2.0 only).

## Configuration

Operator-specific values configured at deploy time.

## Recent version notes

```
Jaguar v3.7 — runtime risk.guard_rails: cap losers, ride winners.
v3.4 + v3.5 + v3.6 (operator's held+pending dedup + config-driven
startingBudget) opened the floodgates: 7 entries in 90 min on
2026-05-06 vs original "1 amazing trade per day" striker thesis.
Producer-side dynamic cap was bypassed via STARTING_BUDGET hack.
Wrong layer for the fix.
v3.7 moves entry-cap policy to the RUNTIME via risk.guard_rails:
  - max_entries_per_day: 3 (hard cap when day is RED)
  - bypass_max_entries_per_day_on_profit: true (no cap when GREEN)
  - daily_loss_limit_pct: 10 (catastrophic intraday halt)
  - drawdown_halt_pct: 25 (lifetime circuit breaker)
  - max_consecutive_losses: 3 + cooldown_minutes: 60
  - per_asset_cooldown_minutes: 120 (matches Jaguar's existing cooldown)
Discipline: top 3 by score per day when losing. When winning today,
no cap — let the hot hand ride. Producer's get_dynamic_daily_cap
becomes redundant defense-in-depth; runtime is the authoritative gate.
STARTING_BUDGET hack can be reverted to $1000 (real value) once v3.7
is live.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
