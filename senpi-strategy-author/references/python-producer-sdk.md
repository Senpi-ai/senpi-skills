# The `scan()` model — there is no producer SDK

Runtime 3.0 has **no Python SDK to import**. There is no client class, no daemon, no ingest wrapper —
the module that used to be documented here does not exist in the repo. An author writes **one function**,
`scan(inputs, ctx)`, against the frozen `ctx` surface the runtime hands in, returns a `list[dict]` of
candidate signals, and the runtime does everything else: it schedules the scanner, sizes the position,
executes the order, manages the DSL exit, dedups, reconciles, and delivers the signal internally. You
never open a network connection, never POST anything, never run a loop.

**Authoritative contract:** [`senpi-trading-runtime/references/scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md).
Copy from it; do not author `scan()` from memory. This file is only the lede + the minimal skeleton.

---

## What replaced what

| Old v2 mental model | Runtime 3.0 reality |
|---|---|
| Import a client, construct it from env | Nothing to import — the runtime injects `ctx` |
| A long-lived producer daemon looping every N seconds | A **single-pass, synchronous** `scan()` the runtime calls every `interval_seconds` — no `while True`, no `sleep`, no daemon |
| Push signals to an HTTP endpoint | **Return** a `list[dict]`; the runtime delivers them internally |
| Construct + manage an MCP client yourself | `ctx.senpi_mcp.call_tool(name, args)` — injected, **read-only** |
| Hand-rolled lockfile / tick cache / restart recipe | The supervisor owns liveness, dedup, reconcile, and rollback |
| Read params from a params file at boot | The recipe's `inputs:` map arrives as the first arg — `scan(inputs, ctx)` |

The single job of the author is: **read data, score it, return signals.** Sizing and execution are the
runtime's job, and every money/state-mutating MCP tool raises `PermissionError` if a scan tries to call
it (read-only boundary — see the contract).

---

## Minimal `scan.py` skeleton

Copied from [`scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md). Keep the
thesis math in a sibling **pure `scoring.py`** (no I/O, no MCP) so it is unit-testable; `scan.py` does
the reads + state, `scoring.py` does the numbers.

```python
# scanners/scan.py
import scoring  # pure logic, no I/O — unit-tested separately

def scan(inputs, ctx):
    # 1) READ — read-only MCP only (market / account / leaderboard / discovery / …)
    ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                 {"strategy_wallet": ctx.wallet})
    candles = ctx.senpi_mcp.call_tool("market_get_asset_data",
                                      {"asset": "xyz:SP500", "candle_intervals": ["4h"]})

    # 2) STATE — cross-tick dedup / rotation (transactional)
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    # 3) SCORE — pure functions
    picks = scoring.build_signals(inputs, candles, ch, recent)

    # 4) EMIT — plain dicts; the runtime sizes + executes them
    out = [{
        "asset": p["coin"],            # REQUIRED
        "direction": p["direction"],   # REQUIRED — "LONG" | "SHORT"
        "marginPct": p["margin_pct"],  # top-level PERCENT of withdrawable (fleet standard)
        "leverage": p["leverage"],     # top-level
        "data": {                      # validated against signal_data_schema
            "score": p["score"], "direction": p["direction"], "reasons": p["reasons"],
        },
        # "valid_for_seconds": 600,    # OPTIONAL per-signal TTL; else default_signal_validity_seconds
        # "signal_id": "...",          # OPTIONAL stable id; else the scaffold mints a uuid
    } for p in picks]

    # 5) PERSIST next-tick state (rolled back automatically if this tick errors/times out)
    if ctx.state is not None:
        ctx.state.append({"recent": recent})
    return out
```

**Signature is exactly two args: `scan(inputs, ctx)`.** On any failure, return `[]` (or a partial list)
— don't crash; the scaffold rolls back state and captures your stderr (use `print(..., file=sys.stderr)`,
there is no logging handle). For a full working example see the gold template at
`strategies/kodiak/main/scanners/scan.py`.

---

## The `ctx` surface (summary)

`ctx` is **frozen** — exactly these attributes, none settable/addable. Full detail in the contract.

| Member | What it is |
|---|---|
| `ctx.senpi_mcp.call_tool(name, args)` | Senpi MCP client, **read-only**. The only way to fetch data. |
| `ctx.state` | Transactional history store (or `None` when history is disabled). `.last()` / `.recent(n)` / `.append(dict)` / `len(...)`. |
| `ctx.wallet` | The runtime's wallet address (pass to `strategy_get_clearinghouse_state`, etc.) |
| `ctx.scanner_name` | This scanner's name (from the recipe) |
| `ctx.interval_seconds` | This scanner's tick cadence |

`inputs` (the first arg) is the recipe's `inputs:` map as a plain dict — read with `.get(...)` and your
own defaults. There is no `ctx.inputs`.

---

## Where to go next

| You need | Read |
|---|---|
| The full `scan()` + `ctx` contract, read-only MCP boundary, emit-one vs emit-all | [`scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md) |
| The signal dict you return (required keys, `data{}` vs `signal_data_schema`) | [`signal-schema.md`](signal-schema.md) |
| The `runtime.yaml` fields that feed `scan()` (`inputs`, `signal_data_schema`, `default_signal_validity_seconds`) | [`runtime-yaml.md`](../../senpi-trading-runtime/references/runtime-yaml.md) |
| The lifecycle around `scan()` (supervision, dedup, reconcile, state rollback) | [`runtime-concepts.md`](../../senpi-trading-runtime/references/runtime-concepts.md) |
| The build workflow for a whole strategy package | [`strategy-creation.md`](strategy-creation.md) |
