# Is my position protected? — rung-2 DSL triage

> **The protection front door is `composer status`.** For any "is my strategy protected?" question,
> run `composer status <target>` (via the `senpi_strategy` tool) FIRST — it renders every open
> position's protection in plain ROE and is relayed verbatim. This doc is **rung 2**: reach for the
> raw `senpi dsl …` commands below only when `composer status` flags a protection anomaly it cannot
> explain (e.g. a stranded/unmanaged runtime, an `UNAVAILABLE` live section, or a surface divergence),
> or when you need to understand what an underlying `dsl` field means. Relay the raw output verbatim.

**The raw trap this exposes:** in `senpi dsl positions -r <runtime-id>`, every open position must
appear, each with a `floorPrice` (its live stop). **An open position *missing* from that list is
UNPROTECTED** — an unprotected position shows up as an *absence*, not a warning, so you have to look
for what's *not* there. Read it from the runtime; don't ask the user to confirm.

## Mental model — why this is usually simple

DSL (Dynamic Stop-Loss) is the autonomous trailing-stop engine. **If a strategy's `runtime.yaml` has an
`exit: engine: dsl` block, its `position_tracker` scanner auto-starts DSL on every position the strategy
opens** — you never arm it per trade. So a position is protected when three things hold, and the checks
below confirm each:

1. **Configured** — the strategy has `exit: engine: dsl` (with a `dsl_preset`).
2. **Monitor alive** — the DSL monitor is enabled and ticking.
3. **Tracked + synced** — the position is in the tracked set, with a floor, and its stop reached the venue.

If the strategy has **no** `exit:` block, there is **no engine stop at all** — every one of its positions
is unprotected by design. That's the first thing to rule out.

## The one command that answers it

```
senpi dsl positions -r <runtime-id>          # [-a <addr>] [--json]
```
Lists every position DSL is protecting — each with `phase`, `currentROE`, `currentTierIndex`, and
**`floorPrice`** (the live stop). Every open position should appear here with a `floorPrice`.

## Confirm nothing is missing (the reconciliation — do NOT skip)

`dsl positions` lists only *tracked* positions, so compare it against what's actually open:

1. **Open positions** for the strategy wallet → MCP `strategy_get_clearinghouse_state` (or
   `account_get_portfolio`, filtering `positions` by the strategy's `strategyWalletAddress`).
2. **Tracked positions** → `senpi dsl positions -r <id>`.
3. **Every open asset must appear in the tracked set.** Open-but-not-tracked = **UNPROTECTED** → flag it
   loudly. Causes: the strategy has no `exit: engine: dsl`, the `position_tracker` missed the open, or the
   position was opened outside the strategy.

## Per-position detail

```
senpi dsl inspect <ASSET> -r <id>            # e.g. senpi dsl inspect BTC
```
Full state for one position: **phase** (1 = fixed floor + retrace from high-water; 2 = tier locks),
computed **floor/stop price**, active **tier**, and ROE. Use it to confirm the stop is where you expect.

## Strategy + monitor health

The authoritative monitor-health read is **`composer status`** (front door). At rung 2 the raw digest is:

```
senpi status -r <id>
```
Its DSL monitor line reports whether DSL is enabled, active-position count, and monitor liveness:
- DSL not enabled / no DSL line → **the strategy has no exit engine — positions are unprotected.** Check
  the emitted `runtime.yaml` `exit:` block (inspect-only — do not hand-edit it).
- monitor stopped or a stale next-tick → DSL isn't evaluating, so positions won't be trailed or cut.

Do not re-interpret the raw field beyond this; `composer status` is the authority on protection semantics.

## Why a position closed

```
senpi dsl closes -r <id>                     # [-l N]
```
Archived positions with the close **reason** (floor breach / tier / `hard_timeout` / `weak_peak_cut` /
`dead_weight_cut`) and realized ROE.

## The "stop never reached the exchange" alarm  (runtime with the event surface)

A position can be *tracked* yet its stop never actually posted to Hyperliquid (an `editPosition` threw).
On a runtime that ships the agent event surface:
- `senpi events -r <id> --name dsl.sl_sync_failed --level error` → **⚠ tracked, but the venue never got
  the stop** — effectively unprotected until it re-syncs. Treat as urgent.
- `senpi explain <ASSET> -r <id>` → that position's DSL narrative (`dsl.created` → `dsl.sl_updated` →
  `dsl.tier_advanced` → `dsl.closed`).

> These two need a runtime build that includes the agent event surface (`senpi events`/`explain`). On
> older runtimes they return "unknown command" — fall back to `dsl positions` / `inspect` / `status`
> above, which are always available.

## Verdict rubric (what to tell the user)

| Verdict | When |
|---|---|
| ✅ **PROTECTED** | open, appears in `dsl positions` with a `floorPrice`, monitor healthy, no recent `dsl.sl_sync_failed` |
| ⛔ **UNPROTECTED** | open but **absent** from `dsl positions`, or the strategy has no `exit: engine: dsl` |
| ⚠ **STOP NOT ON VENUE** | tracked, but a recent `dsl.sl_sync_failed` for that asset |

## Config reference — what "DSL is set up" looks like

```yaml
exit:
  engine: dsl
  interval_seconds: 30            # how often DSL re-evaluates each position
  dsl_preset:
    phase1: { max_loss_pct: 3.0, retrace_threshold: 10, consecutive_breaches_required: 3 }
    phase2: { tiers: [ { trigger_pct: 10, lock_hw_pct: 50 }, { trigger_pct: 20, lock_hw_pct: 70 } ] }
    # optional time cuts: hard_timeout / weak_peak_cut / dead_weight_cut
```
Full engine semantics: `senpi guide dsl`, and `references/runtime-concepts.md` (DSL Exit Engine).
