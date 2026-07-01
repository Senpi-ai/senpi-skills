# `runtime.yaml` Schema Reference (Runtime 3.0)

Schema reference for a Runtime 3.0 `runtime.yaml` (one file = one wallet's strategy). Defines every
top-level section, every field, wiring rules, template variables, and validation errors.

> **The runtime's own schema outranks this doc.** When this page and
> [`../../senpi-trading-runtime/references/runtime-yaml.md`](../../senpi-trading-runtime/references/runtime-yaml.md)
> disagree, the runtime schema wins — copy field names/units from there, never from memory.

A `runtime.yaml` is deployed by `senpi-strategy-ops` `deploy.py` (which renders the wallet env and runs
`openclaw senpi runtime create`) — you do not launch anything yourself. For a complete end-to-end example
see [momentum-guarded-strategy.md](momentum-guarded-strategy.md). For DSL exit-engine details see
[dsl-configuration.md](dsl-configuration.md). For ready-to-use position-tracker variants see
[strategy-examples.md](strategy-examples.md). For the `scan(inputs, ctx)` author contract see
[`../../senpi-trading-runtime/references/scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md).

---

## Top-level sections

| Key | Required | Purpose |
|-----|----------|---------|
| `name` | yes | Runtime id (unique; used in logs/state/telemetry). |
| `version` | no | Passthrough metadata, **accepted but never validated**. Use `3.0.0` by convention; the package major version is the real boundary. |
| `group` | no | Optional label tying a package's runtimes together; stamped on telemetry. |
| `description` | no | Free-form description. |
| `strategy` | yes | Wallet, slots, risk profile, leverage — see [Define Your Strategy](#define-your-strategy). |
| `scanners` | conditional | At least one scanner is required when any action depends on one. See [Connect Your Signal Source](#connect-your-signal-source). |
| `actions` | conditional | Trade entry and lifecycle actions. Required when signal-driven. See [Define How Trades Are Entered](#define-how-trades-are-entered). |
| `exit` | no | DSL exit engine config. See [Configure Exit Management](#configure-exit-management). |
| `risk` | no | Guard-rail gates. See [Configure Risk Protection](#configure-risk-protection). |
| `notifications` | no | Telegram notifications. See [Set Up Notifications](#set-up-notifications). |

> The schema is **passthrough** — unknown keys are accepted but ignored. Use `strategy:` (singular);
> plural `strategies:` is rejected.

---

## Building Blocks

### Define Your Strategy

The `strategy` block tells the runtime which wallet to trade and how much capital to allocate.

| Field | What it does | Required |
|-------|-------------|----------|
| `wallet` | Your wallet address (use `${WALLET_ADDRESS}` to keep it in env) | **yes** |
| `slots` | Max concurrent positions | no |
| `margin_per_slot` | Fixed USD margin per position | no |
| `margin_pct` | Margin as % of account (positive, max 100) | no |

> **Prefer `margin_pct` over `margin_per_slot`.** Operators fund a strategy with any budget (≥ $100, no upper bound), so a fixed USD `margin_per_slot` either over- or under-commits depending on their balance. `margin_pct` sizes each slot as a share of the actual account and scales correctly with any budget. Keep `slots × margin_pct ≤ 100` so total committed margin stays within the account.
| `trading_risk` | Risk profile: `conservative`, `moderate`, or `aggressive` | no |
| `default_leverage` | Leverage multiplier | no |
| `leverage_multipliers` | Per-risk-level leverage overrides: `{ conservative?, moderate?, aggressive? }` | no |
| `enabled` | Set to `false` to pause without uninstalling | no |

### Set Up Notifications

The `notifications` block toggles Telegram alerts for position opens, closes, stop-loss updates, and
errors. It is **optional** — most packages omit it and let the operator wire delivery at deploy time.
For the exact field set (`dsl_lifecycle`, `dsl_notify_sl_updates`, `action_lifecycle_notifications`,
`gateway_url`, `gateway_token`, and the chat-id field) and defaults, copy from the runtime schema:
[`../../senpi-trading-runtime/references/runtime-yaml.md`](../../senpi-trading-runtime/references/runtime-yaml.md#notifications-block)
(the `notifications` section). The block is passthrough, so a typo'd key is silently ignored — use the
exact names shown there. Delivery over the HTTP gateway needs `OPENCLAW_GATEWAY_TOKEN` (or the block's
`gateway_token`) or notifications are dropped silently.

### Configure Risk Protection

Risk gates are checked before every trade entry. If any gate is not "OPEN", the entry is skipped.

All `risk` durations are in **seconds** (the `*_minutes`/`*_hours` forms were removed in 3.0). Copy
field names/bounds from the runtime schema — do not emit a `_minutes` field, it will be silently ignored.

```yaml
risk:
  data_retention_seconds: 259200    # PnL history retention; integer 3600-604800 (1h-7d)
  guard_rails:
    daily_loss_limit_pct: 4           # Stop if daily loss exceeds 4%
    max_entries_per_day: 6            # Max 6 entries per UTC day
    bypass_max_entries_per_day_on_profit: false  # At cap: allow more if today_snapshot.pnl.delta_since_open > 0
    max_consecutive_losses: 3         # Pause after 3 losers in a row
    cooldown_seconds: 5400            # Pause duration after consecutive losses (min 60)
    drawdown_halt_pct: 20             # Stop if drawdown from peak exceeds 20%
    drawdown_reset_on_day_rollover: false  # Keep drawdown tracking across days
    per_asset_cooldown_seconds: 2700  # No re-entry on same asset for 45 min (min 300)
```

`max_entries_per_day` is enforced using MCP trade and position times as **Unix seconds** vs UTC midnight in seconds (see [`risk-gates.md`](risk-gates.md)). With `bypass_max_entries_per_day_on_profit: true`, at the cap the runtime consults `today_snapshot.pnl.delta_since_open` (shared with daily-loss snapshot when configured, else one lazy MCP call).

All guard rail fields are optional — only configure the gates you want active.

### Connect Your Signal Source

Scanners detect trading opportunities. A signal-driven strategy needs two scanners: a `position_tracker`
(feeds the DSL exit engine) and an `external_scanner` (your supervised `scan(inputs, ctx)`).

**Position tracker** (required whenever `exit.dsl_preset` is set):
```yaml
- name: position_tracker
  type: position_tracker
  interval_seconds: 10       # built-in scanners take integer seconds (floored at 7)
```

**External scanner** — the runtime spawns a scaffold that calls your `scan(inputs, ctx)` every
`interval_seconds` and delivers the returned signals. **There is no push/ingest model and no producer
daemon** — the runtime does the scheduling. Example (from `strategies/kodiak/main/runtime.yaml`):
```yaml
- name: kodiak_main_signals
  type: external_scanner
  path: ./scanners             # dir holding the scan module, resolved vs the runtime.yaml dir
  entrypoint: scan.py          # module exporting scan(inputs, ctx)
  interval_seconds: 180        # tick cadence — per-thesis (e.g. 300/900s), NOT the 10s supervisor loop
  timeout_seconds: 150         # per-tick wall-clock budget (default = interval_seconds)
  default_signal_validity_seconds: 1800   # REQUIRED — fallback signal TTL
  state_history_max_count: 100 # ctx.state bound (0/unset = history disabled)
  inputs:                      # author tunables → scan()'s first arg; read via inputs.get(...)
    asset: "SOL"
    minScore: 10
    marginPct: 20
    leverageTiers: [[13, 7], [11, 6], [10, 5]]
  signal_data_schema:          # REQUIRED — validates each signal's data{} map
    score: { type: number }
    leverage: { type: number }
    direction: { type: string }
    reasons: { type: array, required: false }
```

| Field | Required | Notes |
|---|---|---|
| `path` | yes | scan-module directory, resolved against the `runtime.yaml` dir |
| `entrypoint` | yes | module file exporting `scan(inputs, ctx)` (e.g. `scan.py`) |
| `interval_seconds` | no (default 30) | integer, positive; per-thesis tick cadence |
| `timeout_seconds` | no (default = `interval_seconds`) | integer, positive; per-tick budget |
| `default_signal_validity_seconds` | yes | integer, positive; fallback signal TTL (no magic default) |
| `state_history_max_count` | no (default 0) | integer ≥ 0; `ctx.state` bound |
| `inputs` | no | author tunables → the scan's first arg (this is where thresholds/universe/leverage live, **not** in `strategy.yaml`) |
| `signal_data_schema` | yes | non-empty map; per-`data`-key `{ type, required? }`, `type` ∈ `string`/`number`/`boolean`/`object`/`array` |

Rules for external scanners:
- Set an integer `interval_seconds` (the runtime polls; there is no `interval` duration-string form).
- Provide non-empty `path` **and** `entrypoint`.
- Provide `default_signal_validity_seconds` and a non-empty `signal_data_schema`.
- Tunables go under `inputs:`; output shape goes under `signal_data_schema:`. **The retired v2 keys
  (`outputs`, the old `config` field map, `depends_on`) were removed** — they load-error on an
  `external_scanner`.

### Define How Trades Are Entered

Actions decide what to do with the signals the scanner emits. The **fleet-standard** entry is
`decision_mode: rule` — the `scan()` already applied every filter, so the runtime just sizes and executes
each emitted signal (no LLM). From `strategies/kodiak/main/runtime.yaml`:

```yaml
- name: kodiak_main_entry
  action_type: OPEN_POSITION
  decision_mode: rule                    # the scan already applied every filter
  scanners: [kodiak_main_signals]
  params:
    order_type: FEE_OPTIMIZED_LIMIT
    fee_optimized_limit_options:
      ensure_execution_as_taker: true
      execution_timeout_seconds: 60
  context:
    - type: signal
      scanner: kodiak_main_signals
```

`decision_mode: llm` is available for LLM-gated entries; it adds `decision_model`, `min_confidence`, and
a `decision_prompt` with `{{placeholder}}` tokens. Most packages don't use it — the thesis lives in
`scoring.py`, not a prompt.

| Field | What it does |
|-------|-------------|
| `name` | Action identifier (required; unique — keys telemetry pairing + per-action state) |
| `action_type` | Registered action type (required): `OPEN_POSITION`, `CLOSE_POSITION`, or `POSITION_TRACKER` |
| `decision_mode` | `rule` (execute emitted signals — the fleet default), `llm` (an LLM decides), or `no_decision` (disabled) |
| `scanners` | Which scanner(s) trigger this action |
| `params.order_type` | `MARKET` or `FEE_OPTIMIZED_LIMIT` (+ `fee_optimized_limit_options`) |
| `context` | Context entries injected as `{{placeholders}}` — see [Template Variables](#template-variables) |
| `decision_model` | (llm mode) LLM model id. **Bare model name only — no provider prefix.** See [Model name format](#model-name-format). |
| `min_confidence` | (llm mode) minimum LLM confidence (0–10) to execute (default 1) |
| `decision_prompt` | (llm mode) prompt template with `{{placeholders}}` |

The runtime picks the LLM backend at boot; the package YAML never pins a provider. For a `decision_mode:
llm` action the operator supplies the model at deploy time (`deploy.py runtime <id> --decision-model M`) —
rule-mode strategies need none.

#### Model name format (llm mode only)

Pass the **bare** model name to `decision_model`. The runtime forwards it to the OpenClaw `llm-task` gateway, which adds its own provider prefix when routing. Passing a prefixed name causes a double-prefix and the gateway responds with `500 Unknown model`.

| | Example |
|---|---|
| Valid | `gemini-2.5-pro`, `claude-sonnet-4-20250514`, `gemini-3.1-pro-preview` |
| Invalid | `google/gemini-2.5-pro`, `anthropic/claude-sonnet-4-20250514` |

### Configure Exit Management

The DSL exit engine monitors your open positions and closes them based on configurable rules.

**Top-level `exit` fields:**

| Field | What it does | Default |
|-------|-------------|---------|
| `engine` | Exit engine identifier; typically `dsl` | — |
| `interval_seconds` | DSL polling interval (integer, 5–3600) | `30` |
| `order_type` | Order type for DSL-initiated closes: `MARKET` or `FEE_OPTIMIZED_LIMIT` | `MARKET` |
| `fee_optimized_limit_options.ensure_execution_as_taker` | Ensure fill as taker if limit times out | — |
| `fee_optimized_limit_options.execution_timeout_seconds` | Limit-order timeout (integer, 1–300) | — |
| `dsl_preset` | DSL exit profile (see below) | — |

**Time-based cuts** close positions that aren't performing:
- `hard_timeout`: Close after N minutes no matter what
- `weak_peak_cut`: Close if peak ROE% never reaches `min_value` after N minutes
- `dead_weight_cut`: Close if position is at or below break-even after N minutes

**Phase 1** (fixed floor + trailing):
- `max_loss_pct`: Maximum allowed loss before closing (e.g. 2.5 = close at -2.5%)
- `retrace_threshold`: Once ROE% reaches this level, a trailing floor activates
- `consecutive_breaches_required`: How many consecutive ticks below floor before closing

**Phase 2** (dynamic high-water tiers):
Each tier locks in a percentage of your high-water mark as a floor:
```yaml
tiers:
  - { trigger_pct: 6,  lock_hw_pct: 35 }   # At +6% ROE, lock 35% of peak
  - { trigger_pct: 10, lock_hw_pct: 55 }   # At +10% ROE, lock 55% of peak
  - { trigger_pct: 15, lock_hw_pct: 70 }   # At +15% ROE, lock 70% of peak
  - { trigger_pct: 20, lock_hw_pct: 85 }   # At +20% ROE, lock 85% of peak
```

Tiers must be listed in ascending order by `trigger_pct`.

---

## Template Variables

These apply to `decision_mode: llm` actions. In a `decision_prompt`, use `{{placeholder}}` to inject data. The placeholder name is derived from your `context` entries:

| Context entry | Placeholder name | What it contains |
|---------------|-----------------|------------------|
| `{ type: signal, scanner: "X" }` | `{{signal_X}}` | Latest signal data from scanner X |
| `{ type: context, scanner: "X" }` | `{{context_X}}` | Retained context artifact from scanner X |
| `{ type: strategy, value: "X" }` | `{{strategy_X}}` | Strategy-level parameter X |
| `{ type: asset-trend, value: "X" }` | `{{asset_trend_X}}` | Asset trend data for X |

Additionally, any key under an action's execution `params` map is available as a placeholder (e.g. a `my_val: "hello"` entry → `{{my_val}}`).

Every `{{placeholder}}` in your prompt **must** resolve to a declared context entry or param key. If it doesn't, the YAML will fail validation.

---

## Environment Variables

Use `${VAR}` or `${VAR:-default}` in any string value to reference environment variables:

```yaml
strategy:
  wallet: "${KODIAK_WALLET}"               # Required: bound by deploy.py from the manifest wallet_env
```

The wallet env name **must match** the instance's `wallet_env` in `strategy.yaml` — `deploy.py runtime`
renders the fresh wallet into it. If the env var is not set, `${VAR}` resolves to an empty string; use
`${VAR:-fallback}` to provide a default.

---

## Required Wiring Rules

These are hard requirements — if you violate them, the YAML will fail to load:

1. **If you use exit management (`dsl_preset`), you must include a position tracker:**
   - A scanner with `type: position_tracker`
   - An action with `action_type: POSITION_TRACKER` subscribed to that scanner

2. **If you have an `OPEN_POSITION` action, you must have a `scanners` block** with at least one scanner defined.

3. **`external_scanner` requires `path` + `entrypoint` + `default_signal_validity_seconds` + a non-empty `signal_data_schema`.** It must NOT carry the retired v2 keys (`outputs`, the old `config` field map, `depends_on`) or a duration-string `interval` — those were removed and load-error.

4. **Scanner names AND action names must be unique** across the entire YAML.

5. **Every `{{placeholder}}` in a decision_prompt must resolve** to a context entry or params key.

6. **Phase 2 tiers must be sorted ascending** by `trigger_pct`.

7. **`max_loss_pct` is required** when phase1 is enabled — set it in `dsl_preset.phase1.max_loss_pct` or at the preset root.

8. **Every `data{}` key the scan emits must be declared in `signal_data_schema`** — an undeclared key is a loud reject.

---

## Validation Errors & Fixes

| Error you see | What's wrong | How to fix |
|---------------|-------------|-----------|
| "name is required at the top" | Missing `name` field | Add `name: my-strategy` at top level |
| "strategy must have a wallet" | Missing or empty wallet | Add `strategy: { wallet: "0x..." }` |
| "when using actions that depend on scanners, you must also add a non-empty 'scanners:' block" | OPEN_POSITION action but no scanners | Add a `scanners:` block with at least one scanner |
| "Duplicate scanner name(s)" / action names | Two scanners (or actions) share a name | Rename the duplicate |
| "`interval` (duration string) was replaced by `interval_seconds`" | Duration-string `interval` on a scanner | Use integer `interval_seconds` |
| "external_scanner requires a non-empty path / entrypoint" | Missing `path` or `entrypoint` | Add both |
| "external_scanner requires a non-empty signal_data_schema map" | Missing the output schema | Add the per-`data`-key `signal_data_schema` |
| "external_scanner requires default_signal_validity_seconds" | Missing the fallback TTL | Add `default_signal_validity_seconds` |
| "`config` / `outputs` …" on external_scanner | You set retired v2 keys | Move tunables to `inputs`, output shape to `signal_data_schema`; delete `outputs` |
| "DSL requires … position-tracker scanner / POSITION_TRACKER action" | Exit management without a tracker | Add the `position_tracker` scanner **and** the `POSITION_TRACKER` action |
| "max_loss_pct is required" | Phase1 enabled without max_loss_pct | Add `max_loss_pct` to phase1 or preset root |
| "'strategies' (plural) is no longer supported" | You used `strategies:` | Use `strategy:` (singular) |
