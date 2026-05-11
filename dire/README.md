# Dire — BRENTOIL XYZ Specialist

**Runtime:** 1.0  ·  **Asset:** BRENTOIL (XYZ)  ·  **Status:** Live  ·  **Version:** 1.5.0

## Thesis

Dire v1.7.0 — fleet-wide DSL ratchet T0/T1 patch (2026-05-02).

See `SKILL.md` and `runtime.yaml` for full scoring components, gates, and DSL configuration.

## Scoring components

Defined in the producer/scanner. See `runtime.yaml` `scanner:` section and the producer script in this folder for the current scoring weights and gates.

## Entry / Exit

- **Entry:** Producer-emitted signals scored against MIN_SCORE gate.
- **Exit:** DSL (Dynamic Stop-Loss) Phase 1 + Phase 2 trailing exits per `runtime.yaml` `dsl:` section.
- **Time cuts:** Disabled per single-asset rule.

## Fleet rules applied

- Standard fleet drawdown gate.
- Fee budget tracking via shared infra.
- Producer reentrancy guard via fcntl lockfile (Runtime 2.0 only).

## Configuration

Operator-specific values configured at deploy time.

## Recent version notes

```
Dire v1.7.0 — fleet-wide DSL ratchet T0/T1 patch (2026-05-02).
T0 lock 25→35, T1 trigger 10→8 — closes T0→T1 dead zone for oil reversals.
v1.6.0 base — "hit fewer, win bigger" patch (scanner-only changes; DSL
preset unchanged). v1.0 sample (16 entries / 11 closes / -$140 realized)
showed signal works (Apr 29 +57% peak runner) but score 9-10 floor let
through low-conviction entries that closed at protective exits. v1.6
raises minScore 9 → 11 AND requires all 5 soft confirmations (Volume +
OI velocity + SM premium + Price cleanliness in addition to 4TF/SM hard
gates). Adds FP-001 quiet hours (skip 00-04 UTC unless apex 12+) and
FP-002 hard rule in SKILL.md (Claude Code conversation sessions must
NOT call trading MCP tools — only producer cron may enter). Ratchet-
engine state-sync bug observed Apr 29-May 1 is upstream Senpi backend
(separate Erik ferry); v1.5 DSL architecture remains correct.
```

## Related

- Top-level repo README: `../README.md`
- Runtime spec: `../senpi-trading-runtime/`
- DSL plugin: `../dsl-dynamic-stop-loss/`
