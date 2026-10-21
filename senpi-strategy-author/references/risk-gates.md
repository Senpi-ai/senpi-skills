# Risk Gates Reference

Strategy-level entry gates evaluated **before every open**. Closes are not gated. Configured under `risk.guard_rails` in the strategy YAML — see `senpi-trading-runtime/references/runtime-yaml.md` (the authoritative schema) for the field list.

## Evaluation flow

Every signal that reaches `open-position` triggers a real-time gate check via MCP — no background polling, no cached verdicts. Each `checkGate()` call fetches fresh data, evaluates the configured gates, applies the halt latch (below), and returns one of:

- `OPEN` — proceed with the entry
- `COOLDOWN` — skip this entry (per-asset or consecutive-loss pause)
- `CLOSED` — halt entries entirely (loss/drawdown/cap breach)

**Priority:** `CLOSED > COOLDOWN > OPEN`. In default mode the runtime short-circuits on the first non-`OPEN` verdict. `evaluateAll` mode (used by `senpi runtime status` / `state` and the health API) runs every configured gate and returns per-gate detail.

## The five gates

| # | Gate | Verdict | Trigger | Reset |
|---|------|---------|---------|-------|
| 1 | **Daily Loss Halt** | `CLOSED` | `today_snapshot.pnl.delta_since_open` breaches `daily_loss_limit_usd` **or** `daily_loss_limit_pct` (OR logic) | Latched until UTC midnight |
| 2 | **Drawdown Halt** | `CLOSED` | PnL drawdown from peak ≥ `drawdown_halt_pct`. PnL-based — immune to deposits/withdrawals | Latched. `drawdown_reset_on_day_rollover: true` releases it at UTC midnight; `false` (default) never auto-resets — the user decides |
| 3 | **Consecutive Loss Cooldown** | `COOLDOWN` | Last `max_consecutive_losses` closed trades all have negative `realizedPnl` and the cooldown window has not expired | `cooldown_seconds` after the most recent loss close |
| 4 | **Per-Asset Cooldown** | `COOLDOWN` | The candidate asset was closed within `per_asset_cooldown_seconds` of now | Time-based — expires naturally |
| 5 | **Max Entries/Day** | `CLOSED` | `entries_today >= max_entries_per_day` (unless bypass + profit, see below) | UTC midnight |

**Opt-in fields:** Omitting a threshold disables the gate. Gate 3 requires **both** `max_consecutive_losses` and `cooldown_seconds`. The entire `risk:` block is optional — without it, all gates are `OPEN` (no-op stub).

**Default booleans (when `guard_rails` exists):** `bypass_max_entries_per_day_on_profit` and `drawdown_reset_on_day_rollover` default to `false`.

## Halt latch (gates 1 and 2)

`delta_since_open` and `from_peak_pct` are mark-to-market: they move with unrealized PnL, so a strategy near its limit reads breached / not-breached on every price bounce. Gates 1 and 2 therefore **latch**: once a check sees a genuine breach (not a fail-closed read), the gate stays `CLOSED` on every later check, even when the live number is back under the limit.

- **Release:** the daily loss halt releases on the first successful check whose `today_snapshot.meta.utc_day_start_iso` is a later UTC day. The drawdown halt releases the same way only when `drawdown_reset_on_day_rollover: true`; with `false` it never auto-resets. Editing a threshold does not release a latch.
- **Persistence:** the latch is saved per strategy in the runtime state dir (`<stateDir>/{address}/risk-halt-latch.json`) and survives gateway restarts and updates; the release rules above are unchanged. Restarting the gateway or re-running `update` does not clear it.
- **A latched halt is the user's call.** Report it (name the gate, quote its `reason`) and wait for the user's decision. Do not try to clear the halt or resume entries on their behalf without their explicit approval. The levers, each a recipe edit plus `openclaw senpi update <pkg> --apply` and each only on the user's explicit yes: set `drawdown_reset_on_day_rollover: true` (the drawdown latch then releases at the next UTC day), or remove the rail (`drawdown_halt_pct` / `daily_loss_limit_pct`), which drops its latch — the strategy then trades with no such halt at all. Never redeploy to a fresh wallet to escape a halt.
- **Status:** while latched, the gate reports `CLOSED` with `evaluationOk: true` and a reason starting `<Gate> latched …` that quotes the breach that tripped it. `metrics` keeps the live inputs (`pnlDelta` / `drawdownFromPeakPct`) and adds `latched`, `latchedAt`, `latchedUtcDay`, `latchReleasesAt` (null = no auto-release) and `latchTrippedReason`.
- **Events:** one `runtime.paused` when the latch is set; `runtime.resumed` only when it releases (or when its rail is removed from the YAML, which drops the latch).
- Gate 5 (max entries/day) is not latched.

## Recommended default envelope

⚠️ Because the `risk:` block is fully optional and omitting a field disables that gate, a strategy that ships without `risk.guard_rails` runs with **no daily-loss halt, no drawdown halt, no cooldowns, and no entry cap** — only the DSL's `max_loss_pct` protects each individual position. That's rarely what you want. **Pair the `balanced` DSL preset with this guard-rail envelope** unless you have a reason to deviate — it's the "smart default" that works for most strategies:

```yaml
risk:
  data_retention_seconds: 259200    # 72h
  guard_rails:
    daily_loss_limit_pct: 15          # halt new entries after a -15% day
    drawdown_halt_pct: 25             # circuit breaker on a -25% PnL drawdown from peak
    drawdown_reset_on_day_rollover: false
    max_consecutive_losses: 3         # + cooldown_seconds → pause after a losing streak
    cooldown_seconds: 3600
    per_asset_cooldown_seconds: 14400 # 4h between attempts on the same asset (anti-whipsaw)
    max_entries_per_day: 5            # caps fee-bleed / runaway over-trading
    bypass_max_entries_per_day_on_profit: false
```

Tune per strategy class: faders/scalpers want a **lower** `per_asset_cooldown_seconds` and **higher** `max_entries_per_day`; conviction holders want the opposite. The fail-safe below means an over-tight envelope only ever *suspends* trading — it never forces a bad entry.

**Fail-safe:** any risk MCP call that errors (network/timeout/missing snapshot) returns `CLOSED` for halt-class gates and `COOLDOWN` for asset checks — trading is suspended whenever risk state is unknown. There is no permissive fallback.

## Gate 5 timestamp arithmetic

`max_entries_per_day` is enforced by counting opens since UTC midnight. The MCP fields involved — `discovery_get_trader_history.openTime`/`closeTime` and `discovery_get_trader_state` position `startTime` — are **Unix epoch seconds**, and the runtime compares them against **UTC midnight expressed in seconds** (same unit end-to-end). Don't pass milliseconds anywhere in this path.

## `bypass_max_entries_per_day_on_profit`

When `true`, gate 5 stays `OPEN` at the cap if `today_snapshot.pnl.delta_since_open > 0` (strict — exact zero does not bypass). Same field as gate 1.

Snapshot acquisition is optimized to one MCP call per check at most:

- If gates 1 or 2 are configured, gate 5 **reuses** the snapshot already fetched by them.
- If neither is configured, gate 5 performs **at most one** `getPnlHistoryWithSnapshot` call, and **only when the cap is hit** (no prefetch on every check).
- If a shared snapshot fetch was already attempted and failed, gate 5 does **not** retry — it **fails closed**.
- Unknown or missing snapshot during bypass also **fails closed**.

## Fail-closed principle

Any MCP risk API call that fails (network error, timeout, missing snapshot) returns `CLOSED` for halt-class gates and `COOLDOWN` for asset-specific checks. Trading is suspended whenever risk state is unknown — there is no permissive fallback.

## Health surfacing

Risk surfaces as `components.risk` on `RuntimeHealthStatus` and `RuntimeSystemState` with two independent dimensions:

- **Risk Engine Health** — is risk evaluation working?
  - `healthy`: all gates evaluated successfully
  - `unhealthy`: any gate evaluation failed and fallback was used (`evaluationOk === false`)
  - `disabled`: risk not configured
- **Trading Eligibility** — can this strategy open a new trade? `OPEN` / `COOLDOWN` / `CLOSED` / `N_A` per gate, with effective eligibility = `CLOSED > COOLDOWN > OPEN`.

Overall `RuntimeHealthStatus.health` is **not** affected by risk — risk is informational only. A `CLOSED` gate does not mark the runtime unhealthy; it simply blocks new entries.

## Audit log

Every `checkGate()` invocation appends a `gate_check` JSONL entry:

- Path: `<stateDir>/{address}/risk-guard/audit.jsonl`
- Daily snapshots at: `<stateDir>/{address}/risk-guard/snapshot-{date}.json`

Entry shape:

```json
{
  "ts": "2026-04-11T10:30:00.000Z",
  "event": "gate_check",
  "address": "0xabc...",
  "source": "runtime",
  "meta": { "gate": "OPEN", "reason": null, "candidateAsset": "BTC" },
  "evaluations": [
    { "guardrail": "daily_loss_halt", "result": "pass", "evaluationOk": true, "fallbackApplied": false, "failureKind": "none" }
  ]
}
```

Field notes:

- `source`: `"runtime"` (trade flow) or `"cli"` (status/state inspection)
- `evaluations`: per-gate results, present when `evaluateAll: true`. Each entry carries `evaluationOk`, `fallbackApplied`, and `failureKind` for reliability tracking.

Gate state transitions (`OPEN ↔ COOLDOWN ↔ CLOSED`) trigger Telegram notifications when notifications are configured.

## CLI surfaces

`senpi runtime status` (quick view):

```
Risk Health: healthy
Risk Gates:
  Daily Loss Halt:        OPEN
  Drawdown Halt:          OPEN
  Consecutive Loss:       COOLDOWN — 3 losses, cooldown until 12:00 UTC
  Per-Asset Cooldown:     N_A — no candidate asset
  Max Entries/Day:        CLOSED — 8/8 entries today
```

`senpi runtime state` (debug view) adds `evaluationOk`, raw deltas, limits, and streak counts per gate.

## Interaction with actions

- `open-position` calls `riskGuard.checkGate(address, { candidateAsset })` before submitting.
  - `OPEN` → proceed
  - `COOLDOWN` → skip this asset
  - `CLOSED` → skip all entries this tick
- `close-position` does **not** consult risk — closes always proceed if a position exists.
