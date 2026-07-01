# Momentum-Guarded Strategy — Quick Start (Runtime 3.0)

End-to-end example package that exercises the major features of a Runtime 3.0 strategy:

- a `position_tracker` scanner (feeds the DSL exit engine on your Hyperliquid positions)
- an `external_scanner` running a supervised `scan(inputs, ctx)` that detects momentum breakouts and
  emits candidate signals — the runtime spawns and ticks it; **no producer daemon**
- a rule-mode `OPEN_POSITION` action (the scan already applied every filter; the runtime sizes + executes)
- risk guard rails (daily loss, max entries/day, consecutive-losses cooldown, drawdown halt, per-asset cooldown)
- the DSL exit engine with two-phase trailing stops + time-based cuts

Use it as a starting point for your own strategies. Tune values to taste.

For the `runtime.yaml` schema see [yaml-schema.md](yaml-schema.md) (and the runtime's own schema,
[`../../senpi-trading-runtime/references/runtime-yaml.md`](../../senpi-trading-runtime/references/runtime-yaml.md),
which outranks it). For the `scan(inputs, ctx)` contract see
[`../../senpi-trading-runtime/references/scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md).

---

## 1. Package layout

A strategy is a **package**, not a single YAML. This example is single-instance (`main`):

```
momentum-guarded/
  strategy.yaml                     # deploy manifest (id, version, requires.runtime: ">=3.0.0", instances[])
  main/
    runtime.yaml                    # the runtime spec below
    scanners/
      scan.py                       # exports scan(inputs, ctx) -> list[dict] (read-only, single-pass)
      scoring.py                    # pure momentum math (no I/O/MCP); sibling import, NO __init__.py
```

## 2. `strategy.yaml` (deploy manifest)

```yaml
schema_version: 1
id: momentum-guarded
version: "1.0.0"
catalog:
  name: "Momentum-Guarded"
  emoji: "🚀"
  tagline: "Risk-managed momentum breakouts with a two-phase DSL trailing exit."
  group: momentum
  risk_level: moderate
  min_budget: 100
requires:
  runtime: ">=3.0.0"
defaults:
  auth_token_env: SENPI_AUTH_TOKEN
instances:
  - name: main
    runtime: main/runtime.yaml
    wallet_env: MOMENTUM_WALLET
    funding_share: 1.0
```

## 3. `main/runtime.yaml`

```yaml
name: momentum-guarded-main
group: momentum-guarded
version: 3.0.0
description: >
  Risk-managed momentum strategy. A supervised scan.py gates breakout candidates
  (move magnitude, direction, liquidity, score) and emits conviction-sized signals;
  the runtime sizes + executes them; risk gates protect against daily loss, drawdown,
  and over-trading; the DSL exit engine trails accepted fills.

strategy:
  wallet: "${MOMENTUM_WALLET}"      # bound by deploy.py from the manifest wallet_env
  slots: 3
  margin_pct: 25                    # % of account per slot — scales with any budget
  trading_risk: moderate
  enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval_seconds: 10            # built-in scanner (integer seconds, floored at 7)

  - name: momentum_signals
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300           # per-thesis cadence (5 min) — NOT the 10s supervisor loop
    timeout_seconds: 180
    default_signal_validity_seconds: 900
    state_history_max_count: 100
    inputs:                         # author tunables → scan()'s first arg, read via inputs.get(...)
      timeframe: "1h"
      minMovePct: 1.5
      minDayVolume: 1000000
      minScore: 0.2
      marginPct: 25
    signal_data_schema:             # validates each emitted signal's data{} map
      score: { type: number }
      direction: { type: string }
      movePct: { type: number, required: false }
      timeframe: { type: string, required: false }
      reasons: { type: array, required: false }

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

  - name: momentum_entry
    action_type: OPEN_POSITION
    decision_mode: rule             # the scan already applied every filter
    scanners: [momentum_signals]
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options:
        ensure_execution_as_taker: true
        execution_timeout_seconds: 15
    context:
      - type: signal
        scanner: momentum_signals

risk:
  data_retention_seconds: 259200    # 72h
  guard_rails:
    daily_loss_limit_pct: 4
    max_entries_per_day: 6
    bypass_max_entries_per_day_on_profit: false
    max_consecutive_losses: 3
    cooldown_seconds: 5400          # 90m pause after consecutive losses (min 60)
    drawdown_halt_pct: 20
    drawdown_reset_on_day_rollover: false
    per_asset_cooldown_seconds: 2700  # 45m no re-entry on same asset (min 300)

exit:
  engine: dsl
  interval_seconds: 30
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 15
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 120
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 45
      min_value: 2
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 30
    phase1:
      enabled: true
      max_loss_pct: 2.5
      retrace_threshold: 7
      consecutive_breaches_required: 3
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 6,  lock_hw_pct: 35 }
        - { trigger_pct: 10, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 70 }
        - { trigger_pct: 20, lock_hw_pct: 85 }
```

## 4. The scanner — `main/scanners/scan.py`

The momentum thesis lives in a supervised `scan(inputs, ctx)`. It is **single-pass, synchronous, and
read-only** — it fetches data via `ctx.senpi_mcp.call_tool(...)`, scores with the sibling `scoring.py`,
and **returns** a `list[dict]` of candidate signals. The runtime sizes + executes them and manages the
DSL exit. Copy the skeleton from
[`../../senpi-trading-runtime/references/scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md).
Sketch:

```python
# main/scanners/scan.py
import sys
import scoring  # pure momentum math, no I/O — unit-tested separately

def scan(inputs, ctx):
    tf = inputs.get("timeframe", "1h")
    min_move = float(inputs.get("minMovePct", 1.5))
    min_score = float(inputs.get("minScore", 0.2))
    margin_pct = float(inputs.get("marginPct", 25))

    # 1) READ — read-only MCP only (a mutation tool raises PermissionError)
    try:
        markets = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {"limit": 100})
    except Exception as exc:                         # never crash the tick
        print(f"[momentum.scan] read failed: {exc!r}", file=sys.stderr)
        return []

    # 2) STATE — cross-tick dedup (optional; guard on ctx.state)
    seen = (ctx.state.last() or {}).get("seen", {}) if ctx.state is not None else {}

    # 3) SCORE — pure functions decide which breakouts qualify
    picks = scoring.qualifying_breakouts(markets, tf, min_move, min_score)

    # 4) EMIT — plain dicts; marginPct/leverage top-level, everything else in data{}
    out = [{
        "asset": p["asset"],
        "direction": p["direction"],                 # "LONG" | "SHORT"
        "marginPct": margin_pct,                      # PERCENT of withdrawable (fleet standard)
        "leverage": p["leverage"],
        "data": {"score": p["score"], "direction": p["direction"],
                 "movePct": p["move_pct"], "timeframe": tf, "reasons": p["reasons"]},
    } for p in picks]

    # 5) PERSIST next-tick state (rolled back automatically on a failed tick)
    if ctx.state is not None:
        try:
            ctx.state.append({"seen": seen})
        except Exception as exc:
            print(f"[momentum.scan] state append failed: {exc!r}", file=sys.stderr)
    return out
```

Keep the numeric thesis (move %, volume gate, score) in a pure `main/scanners/scoring.py` with **no I/O,
no MCP, no daemon** so it is unit-testable on sample candles.

## 5. Validate

```bash
python3 senpi-strategy-author/scripts/validate_strategy.py strategies/momentum-guarded    # 0 errors
```

Unit-test `scoring.py` directly (it's pure — no mocks).

## 6. Deploy — via senpi-strategy-ops (no daemon to launch)

A strategy package is deployed by **`senpi-strategy-ops` `deploy.py`**, which creates & funds the wallet,
renders `runtime.yaml` with that wallet, and registers the runtime. **The runtime supervises the scanner
in-process — you never launch a producer or daemon yourself.** Three short, resumable steps:

```bash
python3 senpi-strategy-ops/scripts/deploy.py create  momentum-guarded --budget 200   # 1. create + fund the wallet
python3 senpi-strategy-ops/scripts/deploy.py runtime momentum-guarded                # 2. register the autonomous runtime (DONE after this)
python3 senpi-strategy-ops/scripts/deploy.py verify  momentum-guarded                # optional: confirm a scan tick fired
```

- **`create`** creates one fresh wallet per instance, funds it (budget splits by `funding_share`, min
  $100 each), and polls to ACTIVE. If it prints `creating`, re-run the same command — it resumes and
  never re-creates a wallet. Prints `wallets-ready`.
- **`runtime`** renders each instance's `runtime.yaml` with its wallet and runs `openclaw senpi runtime
  create`. Rule-mode strategies need no `--decision-model`. **Once it prints `registered`, deployment is
  DONE — the strategy is live and scans on its own `interval_seconds`.** Do not sleep/poll for the first
  tick; that's normal strategy behavior, not part of deploy.
- **`verify`** (only if asked "is it scanning yet?") checks the `external_scanner` once. Right after
  `runtime` it reports `registered` (not ticked yet) — expected; re-run after the interval to see `live`.

Teardown is always through `close.py` (never a raw `strategy_close`), which stops the runtime **and**
closes the strategy (flattens positions, returns funds):

```bash
python3 senpi-strategy-ops/scripts/close.py momentum-guarded
```

## 7. Verify the strategy is live

```bash
python3 senpi-strategy-ops/scripts/status.py                       # what am I running? (+ health)
openclaw senpi runtime list                                         # runtime shows status: running
openclaw senpi status -r momentum-guarded-main --json              # per-runtime health verdict
openclaw senpi state  -r momentum-guarded-main --json              # last successful scan tick
openclaw senpi dsl positions                                        # positions the DSL is tracking
```

A strategy is **live** only when its runtime is running AND its `external_scanner` has a recent
successful tick (`deploy.py verify` confirms it, or `openclaw senpi state -r <name>`). If no positions
open after several ticks, the scan may simply have found no qualifying breakouts — check the scanner's
stderr in the runtime logs and confirm `inputs` thresholds aren't too tight. Full liveness procedure:
`senpi-strategy-ops/references/liveness-verification.md`.
