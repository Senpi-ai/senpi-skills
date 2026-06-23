# `runtime.yaml` — the runtime config schema

A `runtime.yaml` defines **one runtime — one wallet's strategy**: what to watch (`scanners`), how to
decide (`actions`), how to size/gate (`strategy`, `risk`), and how to exit (`exit`). The runtime loads
the file, resolves environment variables, validates the schema, applies cross-section invariants, then
wires the scanners, actions, and DSL exit engine.

For *how* the runtime behaves at runtime see [runtime-concepts.md](runtime-concepts.md). For the
author-side `scan(inputs, ctx)` + signal shape see [scan-contract.md](scan-contract.md).

> **One wallet per `runtime.yaml`.** A strategy **package** (`strategy.yaml`) may bundle more than one
> runtime — e.g. spider's `swing` + `scalp` legs are two `runtime.yaml` files under one `strategy.yaml`.
> See [Multi-runtime packages](#multi-runtime-packages).
>
> The runtime engine accepts any YAML path (`runtime create -p <path>`); the engine's own examples name
> the file `recipe.yaml`. `runtime.yaml` is the Senpi package convention and is fully compatible.

---

## Annotated example

```yaml
name: iguana-tracker              # REQUIRED — runtime id (unique; used in logs/state/telemetry)
version: 2.0.0                    # OPTIONAL — passthrough metadata, NOT validated
group: iguana                    # OPTIONAL — ties a package's runtimes together; stamped on telemetry
description: >
  IGUANA — XYZ macro index trend …

strategy:                        # REQUIRED — the wallet + sizing/risk identity
  wallet: "${IGUANA_WALLET}"     # REQUIRED — env-substituted at load
  slots: 1                       # max concurrent positions (the runtime enforces the ceiling)
  margin_pct: 20                 # % of account per slot (or margin_per_slot for a fixed USD amount)
  default_leverage: 3
  trading_risk: conservative     # conservative | moderate | aggressive
  enabled: true

scanners:
  - name: position_tracker       # built-in — feeds the DSL exit engine (REQUIRED when exit.dsl_preset is set)
    type: position_tracker
    interval_seconds: 10         # built-in scanners: integer seconds, floored at 7s

  - name: iguana_signals         # the supervised external scanner
    type: external_scanner
    path: ./scanners             # dir holding the scan module (resolved vs the runtime.yaml dir)
    entrypoint: scan.py          # module exporting scan(inputs, ctx)
    interval_seconds: 300        # runtime calls scan() every N seconds
    timeout_seconds: 180         # per-tick wall-clock budget (default = interval_seconds)
    default_signal_validity_seconds: 600   # REQUIRED — fallback signal TTL
    state_history_max_count: 200 # ctx.state bound (0/unset = history disabled)
    inputs:                      # author tunables → scan(inputs, …)
      whitelist: ["xyz:SP500", "xyz:XYZ100"]
      minScore: 4
    signal_data_schema:          # REQUIRED — validates each signal's data{} map
      score: { type: number }
      direction: { type: string }
      reasons: { type: array, required: false }

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER   # REQUIRED when DSL is on
    decision_mode: rule
    scanners: [position_tracker]
  - name: iguana_entry
    action_type: OPEN_POSITION
    decision_mode: rule             # rule | llm | no_decision
    scanners: [iguana_signals]
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 30 }
    context:
      - { type: signal, scanner: iguana_signals }

exit:                            # DSL trailing-stop engine (optional but typical)
  engine: dsl
  interval_seconds: 60           # DSL poll cadence (integer, 5–3600)
  dsl_preset:
    hard_timeout:  { enabled: true, interval_in_minutes: 2880 }
    weak_peak_cut: { enabled: true, interval_in_minutes: 480, min_value: 3.0 }
    phase1: { enabled: true, max_loss_pct: 12.0, retrace_threshold: 8, consecutive_breaches_required: 1 }
    phase2:
      enabled: true
      tiers:                     # MUST be sorted ascending by trigger_pct
        - { trigger_pct: 5,  lock_hw_pct: 0 }
        - { trigger_pct: 10, lock_hw_pct: 40 }
        - { trigger_pct: 50, lock_hw_pct: 85 }

risk:
  data_retention_seconds: 345600 # integer 3600–604800 (7d)
  guard_rails:
    daily_loss_limit_pct: 12
    max_entries_per_day: 2
    max_consecutive_losses: 3
    cooldown_seconds: 5400       # min 60
    drawdown_halt_pct: 20
    per_asset_cooldown_seconds: 21600   # min 300

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
  dsl_lifecycle: true
```

---

## Top-level fields

| Field | Required | Notes |
|---|---|---|
| `name` | ✅ | Runtime id (min 1 char; unique; used in logs/state/telemetry) |
| `version` | — | Passthrough metadata, **accepted but never validated** (the package major version is the boundary) |
| `description` | — | Human-readable |
| `group` | — | Optional label tying a package's runtimes together; stamped on telemetry |
| `strategy` | ✅ | Wallet + sizing/risk identity (see below) |
| `scanners` | ✅ if signal-driven | Scanner definitions |
| `actions` | ✅ if signal-driven | Action definitions |
| `exit` | — | DSL exit engine |
| `risk` | — | Risk guard-rails |
| `notifications` | — | Telegram + lifecycle toggles |

> The schema is **passthrough** — unknown keys are accepted but ignored. Do not rely on undeclared
> fields (e.g. there is no `budget` field). Use `strategy:` (singular); plural `strategies:` is rejected.

### Environment substitution

String values are resolved before validation: `${VAR}` → `process.env.VAR` (empty if unset);
`${VAR:-default}` → falls back to the literal `default`. Resolved values may hold secrets — never log
the resolved config.

---

## `strategy` block

| Field | Required | Constraints |
|---|---|---|
| `wallet` | ✅ | min 1 char; the runtime's wallet address (use `${...}`) |
| `slots` | — | concurrent position slots |
| `margin_per_slot` | — | fixed margin per slot (USD) |
| `margin_pct` | — | margin as % of account; > 0, ≤ 100 |
| `default_leverage` | — | default leverage multiplier |
| `leverage_multipliers` | — | per-risk-level leverage `{ conservative?, moderate?, aggressive? }` |
| `trading_risk` | — | `conservative` \| `moderate` \| `aggressive` |
| `enabled` | — | whether the runtime is active |

---

## `scanners` block

Each entry needs a unique `name` and a `type`. Registered types: `position_tracker`,
`external_scanner`, plus built-ins (`emerging_movers`, `momentum`, `oi_tracker`, `prescreener`,
`liquidation_watchdog`, `market_regime`, `sm_flip`, `opportunity`).

- **Built-in scanners** take `interval_seconds` (integer, **floored at 7s**), and may use `depends_on`
  (`scanner`, `required`, `max_age_seconds`, `on_missing`/`on_stale`), `blocked_assets`, `config`.
- **`position_tracker`** — typical: `{ name: position_tracker, type: position_tracker, interval_seconds: 10 }`.

### `external_scanner` field set

The runtime spawns a scaffold child that calls your `scan(inputs, ctx)` on `interval_seconds` and
delivers the returned signals — there is no push/ingest model.

| Field | Required | Notes |
|---|---|---|
| `path` | ✅ | non-empty; scan-module directory, resolved against the `runtime.yaml` dir |
| `entrypoint` | ✅ | non-empty; module file exporting `scan(inputs, ctx)` |
| `interval_seconds` | — (default 30) | integer, positive; tick cadence |
| `timeout_seconds` | — (default = `interval_seconds`) | integer, positive; per-tick budget |
| `default_signal_validity_seconds` | ✅ | integer, positive; fallback signal TTL (no magic default) |
| `state_history_max_count` | — (default 0) | integer ≥ 0; `ctx.state` bound; 0/unset = history disabled |
| `inputs` | — | author tunables → `scan()`'s first arg |
| `signal_data_schema` | ✅ | non-empty map; per-`data`-key `{ type, required? }`, `type` ∈ `string`/`number`/`boolean`/`object`/`array` |

**Rejected on `external_scanner`** (loud load error — these were removed):

| Field | Replacement |
|---|---|
| `interval` (duration string) | `interval_seconds` (integer) |
| `params` | `inputs` |
| `config` / `config.fields` | `inputs` (tunables) + `signal_data_schema` (output schema) |
| `outputs` | removed — an external scanner emits one output (signals) |
| `blocked_assets` | filter candidates inside `scan()` |
| `depends_on` | not supported on `external_scanner` |

---

## `actions` block

Each entry needs a unique `name` and an `action_type` ∈ `OPEN_POSITION` | `CLOSE_POSITION` |
`POSITION_TRACKER`. `decision_mode` ∈ `rule` | `llm` | `no_decision`.

| Field | Notes |
|---|---|
| `scanners` | scanner names this action subscribes to |
| `context` | context entries (discriminated union, below) |
| `params` | key-values merged into the prompt interpolation context |
| `decision_model` | LLM model id (for `decision_mode: llm`) |
| `min_confidence` | 0–10; min LLM confidence to execute (default 1) |
| `decision_prompt` | template with `{{placeholder}}` tokens (llm mode) |

**Context entries** → placeholders: `{ type: signal, scanner: X }` → `{{signal_X}}`;
`{ type: context, scanner: X }` → `{{context_X}}`; `{ type: strategy, value: X }` → `{{strategy_X}}`;
`{ type: asset-trend, value: X }` → `{{asset_trend_X}}`. Each `params` key → `{{key}}`. Every
`{{placeholder}}` in `decision_prompt` must resolve to a declared context entry or a `params` key.

**`OPEN_POSITION` execution `params`:** `order_type` ∈ `MARKET` | `FEE_OPTIMIZED_LIMIT`;
`fee_optimized_limit_options.ensure_execution_as_taker` (bool);
`fee_optimized_limit_options.execution_timeout_seconds` (1–300).

**`POSITION_TRACKER`** is minimal — `decision_mode: rule`, `scanners: [position_tracker]` — and feeds
the DSL exit engine.

---

## `exit` block (DSL engine)

Schema only; for the exit *behavior* (phases, floors, breach counting, time cuts) see
[runtime-concepts.md](runtime-concepts.md).

| Field | Default | Constraints |
|---|---|---|
| `engine` | — | typically `dsl` |
| `interval_seconds` | 30 | integer, 5–3600; DSL poll interval |
| `order_type` | `MARKET` | `MARKET` \| `FEE_OPTIMIZED_LIMIT` |
| `fee_optimized_limit_options` | — | `ensure_execution_as_taker`, `execution_timeout_seconds` (1–300) |
| `dsl_preset` | — | the exit profile (below) |

**`dsl_preset`:**

- Time cuts (preset root, siblings of phase1/phase2): `hard_timeout` (`interval_in_minutes` > 0);
  `weak_peak_cut` (`interval_in_minutes` > 0, `min_value` > 0); `dead_weight_cut`
  (`interval_in_minutes` > 0). Each requires those fields when `enabled: true`. Durations are in
  **minutes** (the DSL preset's own unit — unchanged).
- `phase1`: `enabled` (default true); when enabled, `max_loss_pct` (>0, ≤100),
  `retrace_threshold` (>0), `consecutive_breaches_required` (int ≥1).
- `phase2`: `enabled` (default true); when enabled, non-empty `tiers` **sorted ascending by
  `trigger_pct`**. Each tier: `trigger_pct` (>0, ≤100), `lock_hw_pct` (0–100). Phase-2
  `retrace_threshold`/`consecutive_breaches_required` and tier `retrace`/`breaches` are **rejected**.
- `max_loss_pct` resolution: `dsl_preset.max_loss_pct` → `phase1.max_loss_pct` → 1% if
  `phase1.enabled: false` → else error.

---

## `risk` block

All durations are in **seconds** (the `*_minutes`/`*_hours` forms were removed). Gates are checked on
every `OPEN_POSITION`; if `risk` is absent, none are evaluated.

| Field | Constraints |
|---|---|
| `data_retention_seconds` | integer, 3600–604800 |
| `guard_rails.daily_loss_limit_pct` | ≥ 0 |
| `guard_rails.max_entries_per_day` | integer ≥ 1 |
| `guard_rails.bypass_max_entries_per_day_on_profit` | bool (default false) |
| `guard_rails.max_consecutive_losses` | integer ≥ 1 |
| `guard_rails.cooldown_seconds` | min 60 |
| `guard_rails.drawdown_halt_pct` | 0–100 |
| `guard_rails.drawdown_reset_on_day_rollover` | bool (default false) |
| `guard_rails.per_asset_cooldown_seconds` | min 300 |

---

## `notifications` block

| Field | Default | Notes |
|---|---|---|
| `telegram_chat_id` | — | Telegram chat id |
| `gateway_url` | `http://127.0.0.1:18789` | gateway URL for HTTP delivery |
| `gateway_token` | `$OPENCLAW_GATEWAY_TOKEN` | auth token for HTTP gateway |
| `dsl_lifecycle` | true | DSL open/close notifications |
| `dsl_notify_sl_updates` | false | stop-loss update notifications |
| `action_lifecycle_notifications` | — | action open/close notifications |

If `telegram_chat_id` is set but MCP transport is unavailable, `OPENCLAW_GATEWAY_TOKEN` (or
`gateway_token`) must be set or notifications are silently dropped.

---

## Load-time invariants

A `runtime.yaml` that passes the schema still fails at load if any of these break:

1. Signal-driven actions (`OPEN_POSITION`/`CLOSE_POSITION`, or any non-empty `scanners`) require a
   non-empty top-level `scanners` block.
2. If `exit.dsl_preset` is set: at least one `position_tracker` scanner **and** at least one
   `POSITION_TRACKER` action, and that action must subscribe to a position-tracker scanner (empty
   `scanners` = subscribes to all).
3. Scanner names unique; **action names unique** (they key telemetry pairing + per-action state).
4. Every `decision_prompt` placeholder resolves to a declared `context` entry or a `params` key.
5. `phase2.tiers` sorted ascending by `trigger_pct`.
6. `fee_optimized_limit_options` requires `order_type: FEE_OPTIMIZED_LIMIT` (else ignored with a warning).

---

## Multi-runtime packages

A strategy **package** (`strategy.yaml`) may declare more than one `runtime.yaml`. Two common shapes,
tied together by a shared `group:` label:

- **Shared scanner, different sizing/exit (turbine):** both runtimes point `path:` at the same scan
  module; per-leg margin/slots live in each runtime's `inputs:` and the exit profile in each `exit:`.
  One signal stream, two legs.
- **Separate theses, separate wallets (spider):** each leg is its own `runtime.yaml` + its own scanner
  + its own `ctx.state`; no cross-leg shared state.

Each `runtime.yaml` is still one wallet. Funding split across the wallets is an operator dial, not a
field in `runtime.yaml`.

---

## Validation error quick-reference

| Error (prefix) | Fix |
|---|---|
| "name is required at the top" | Add `name:` |
| "strategy must have a wallet" | Add `strategy: { wallet: "0x…" }` |
| "when using actions that depend on scanners" | Add a non-empty `scanners:` block |
| "Duplicate scanner name(s)" / action names | Rename the duplicate |
| "`interval` (duration string) was replaced by `interval_seconds`" | Use integer `interval_seconds` |
| "interval_seconds must be an integer >= 7" | Raise built-in scanner cadence to ≥ 7 |
| "external_scanner requires a non-empty path / entrypoint" | Add `path:` + `entrypoint:` |
| "external_scanner requires a non-empty signal_data_schema map" | Add the per-`data`-key schema |
| "external_scanner requires default_signal_validity_seconds" | Add the fallback TTL |
| "`config` / `outputs` …" on external_scanner | Move to `inputs` + `signal_data_schema`; delete `outputs` |
| "DSL requires … position-tracker scanner / POSITION_TRACKER action" | Add the missing scanner/action |
| "max_loss_pct is required" | Set it on the preset root or `phase1` |
| "'strategies' (plural) is no longer supported" | Use `strategy:` (singular) |
