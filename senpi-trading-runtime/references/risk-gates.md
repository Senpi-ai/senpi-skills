# Risk Gates Reference

Strategy-level entry gates evaluated **before every open**. Closes are not gated. Configured under `risk.guard_rails` in the strategy YAML — see [yaml-schema.md](yaml-schema.md) for the field list.

## Evaluation flow

Every signal that reaches `open-position` triggers a real-time gate check via MCP — no background polling, no cached verdicts. Each `checkGate()` call fetches fresh data, evaluates the configured gates, and returns one of:

- `OPEN` — proceed with the entry
- `COOLDOWN` — skip this entry (per-asset or consecutive-loss pause)
- `CLOSED` — halt entries entirely (loss/drawdown/cap breach)

**Priority:** `CLOSED > COOLDOWN > OPEN`. In default mode the runtime short-circuits on the first non-`OPEN` verdict. `evaluateAll` mode (used by `senpi runtime status` / `state` and the health API) runs every configured gate and returns per-gate detail.

## The five gates

| # | Gate | Verdict | Trigger | Reset |
|---|------|---------|---------|-------|
| 1 | **Daily Loss Halt** | `CLOSED` | `today_snapshot.pnl.delta_since_open` breaches `daily_loss_limit_usd` **or** `daily_loss_limit_pct` (OR logic) | UTC midnight |
| 2 | **Drawdown Halt** | `CLOSED` | PnL drawdown from peak ≥ `drawdown_halt_pct`. PnL-based — immune to deposits/withdrawals | Configurable via `drawdown_reset_on_day_rollover` (default `false` = ~24h carry) |
| 3 | **Consecutive Loss Cooldown** | `COOLDOWN` | Last `max_consecutive_losses` closed trades all have negative `realizedPnl` and the cooldown window has not expired | `cooldown_minutes` after the most recent loss close |
| 4 | **Per-Asset Cooldown** | `COOLDOWN` | The candidate asset was closed within `per_asset_cooldown_minutes` of now | Time-based — expires naturally |
| 5 | **Max Entries/Day** | `CLOSED` | `entries_today >= max_entries_per_day` (unless bypass + profit, see below) | UTC midnight |

**Opt-in fields:** Omitting a threshold disables the gate. Gate 3 requires **both** `max_consecutive_losses` and `cooldown_minutes`. The entire `risk:` block is optional — without it, all gates are `OPEN` (no-op stub).

**Default booleans (when `guard_rails` exists):** `bypass_max_entries_per_day_on_profit` and `drawdown_reset_on_day_rollover` default to `false`.

## Recommended default envelope

⚠️ Because the `risk:` block is fully optional and omitting a field disables that gate, a strategy that ships without `risk.guard_rails` runs with **no daily-loss halt, no drawdown halt, no cooldowns, and no entry cap** — only the DSL's `max_loss_pct` protects each individual position. That's rarely what you want. **Pair the `balanced` DSL preset with this guard-rail envelope** unless you have a reason to deviate — it's the "smart default" that works for most strategies:

```yaml
risk:
  data_retention_hours: 72
  guard_rails:
    daily_loss_limit_pct: 15          # halt new entries after a -15% day
    drawdown_halt_pct: 25             # circuit breaker on a -25% PnL drawdown from peak
    drawdown_reset_on_day_rollover: false
    max_consecutive_losses: 3         # + cooldown_minutes → pause after a losing streak
    cooldown_minutes: 60
    per_asset_cooldown_minutes: 240   # 4h between attempts on the same asset (anti-whipsaw)
    max_entries_per_day: 5            # caps fee-bleed / runaway over-trading
    bypass_max_entries_per_day_on_profit: false
```

Tune per strategy class: faders/scalpers want a **lower** `per_asset_cooldown_minutes` and **higher** `max_entries_per_day`; conviction holders want the opposite. The fail-safe below means an over-tight envelope only ever *suspends* trading — it never forces a bad entry.

**Fail-safe:** any risk MCP call that errors (network/timeout/missing snapshot) returns `CLOSED` for halt-class gates and `COOLDOWN` for asset checks — trading is suspended whenever risk state is unknown. There is no permissive fallback.

> ✅ **Multi-wallet funds — `daily_loss_limit_pct` is safe again (FIXED 2026-06-23).** *History:* the Daily-Loss-Halt gate computes its limit as `pct × today_snapshot.account_value.at_day_open`. For unified-account / portfolio-margin wallets, USDC collateral lives in **spot** and the perp-only `perpDay` series read **~$0**, so the limit computed to **`$0.00`** and *any* loss permanently halted the book (re-tripping every UTC day). It also corrupted the **drawdown** denominator (same `at_day_open`) — a ~7% real drawdown read as ~20%. Observed live across Wolf, Hydra, Mongoose. **Fix (senpi-hyperliquid-mcp #141, live on prod):** `today_snapshot` is now derived from Hyperliquid's overall **`day`** series (perp + spot), so `account_value.at_day_open` reflects true capital. Verified live: Mongoose `at_day_open` $406→$981, drawdown 17%→6%. The gate was re-enabled fleet-wide on the multi-wallet funds (restoring each fund's original `daily_loss_limit_pct`) after the fix shipped. **Note:** this fixed the *runtime gates* only — producers still size/dedup off the perp-only `strategy_get_clearinghouse_state`, which can still return `$0`/empty during the spot glitch; that path is covered separately by a producer read-sanity guard (skip the tick on an inconsistent read).

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
