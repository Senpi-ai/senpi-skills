# `scan(inputs, ctx)` — the author contract

A strategy's Python module exports one function: **`scan(inputs, ctx)`**. The runtime calls it every
`interval_seconds`. It is **single-pass and synchronous** — no loop, no `sleep`, no daemon. It *reads*
data and *returns* a `list[dict]` of candidate signals; the runtime sizes, executes, and manages
exits. `scan()` never opens, closes, cancels, or schedules anything.

Keep the thesis math in a sibling pure **`scoring.py`** (no I/O, no MCP) so it is unit-testable;
`scan.py` does the reads + state, `scoring.py` does the numbers.

For the `runtime.yaml` fields that feed this (`inputs`, `signal_data_schema`,
`default_signal_validity_seconds`) see [runtime-yaml.md](runtime-yaml.md). For the lifecycle around
`scan()` (supervision, dedup, reconcile) see [runtime-concepts.md](runtime-concepts.md).

---

## Skeleton

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
        "marginPct": p["margin_pct"],  # top-level PERCENT of withdrawable
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

**Signature is exactly two args: `scan(inputs, ctx)`.** On any failure, return `[]` (or a partial
list) — don't crash; the scaffold rolls back state and captures your stderr.

---

## `inputs`

The recipe's `inputs:` map, as a plain dict (the function's first arg). This is how one shared
`scan.py` is reused across runtimes with different sizing/universe. Read with `.get(...)` and your own
defaults:

```python
whitelist = inputs.get("whitelist", DEFAULT_WHITELIST)
min_score = int(inputs.get("minScore", 4))
```

---

## The `ctx` surface

`ctx` is **frozen** — exactly these attributes, none settable/addable:

| Member | What it is |
|---|---|
| `ctx.senpi_mcp.call_tool(name, args)` | Senpi MCP client, **read-only** (see boundary below). The only way to fetch data. Value types: see "Market data types" below. |
| `ctx.state` | Transactional history store (or `None` when history is disabled). API below. |
| `ctx.wallet` | The runtime's wallet address (pass to `strategy_get_clearinghouse_state`, etc.) |
| `ctx.scanner_name` | This scanner's name (from the recipe) |
| `ctx.interval_seconds` | This scanner's tick cadence |
| `ctx.dry_run` | `True` when this tick is a validation run (`senpi validate`), `False` in production. See "Gates and `ctx.dry_run`" below — a gate that ignores it makes the scanner unprovable. |

> There is no `ctx.inputs` (inputs is the first arg) and no logging handle — use `print(..., file=sys.stderr)`; the supervisor captures the child's stderr.

### `ctx.state`

A bounded, transactional history store (bound = `state_history_max_count`; `None`/`0` ⇒ history
disabled, so guard with `if ctx.state is not None`).

| Call | Returns |
|---|---|
| `ctx.state.last()` | latest record (a dict) or `None` |
| `ctx.state.recent(n)` | the last `n` records (list) |
| `len(ctx.state)` | number of stored records |
| `ctx.state.append(record)` | push a record — **must be a dict** |

`ctx.state` advances **only on a clean tick**: an exception, timeout, or persist failure rolls back
the in-memory mutation, so state never advances on a failed tick. `append` can raise if history is
disabled — log-and-continue rather than crash (a stale next-tick read just means you may re-emit a
suppressed signal):

```python
if ctx.state is not None:
    try:
        ctx.state.append({"recent": recent})
    except Exception as exc:
        print(f"[scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
```

---

## Gates and `ctx.dry_run`

A scanner that returns early — outside its trading session, before a warm-up window, on a
day-of-week check — is doing the right thing in production. But a tick that returns **without having
read anything** proves nothing: from the outside it is indistinguishable from a healthy scanner with
no setups, which is exactly how a fatal bug behind a session gate stayed invisible for eighteen hours
and then lost most of a funded strategy on the first window that opened.

`senpi validate` reports that case as **UNPROVEN** rather than as a pass. To let validation see a real
read, consult `ctx.dry_run` in the gate:

```python
def scan(inputs, ctx):
    if not in_session() and not ctx.dry_run:
        return []                      # production: nothing to do outside the session
    candles = ctx.senpi_mcp.call_tool("market_get_asset_data", {...})
    ...
```

Returning `[]` is always fine — no setups is a legitimate answer. What matters is that the tick
*read* something first, so the fetch path is proven. Ignoring `ctx.dry_run` is safe: the scanner
still behaves correctly in production, it simply cannot be proven, and validation says so rather
than passing it.

## Read-only MCP boundary

`ctx.senpi_mcp` runs with the strategy's full `SENPI_API_KEY`, so the scaffold enforces a **read-only**
boundary. Every money/state-mutating tool is blocked **before any network connection opens** and
raises `PermissionError` (loud, fail-fast — never a silent `None`).

- **Reads always allowed:** `market_*`, `account_get*`, `leaderboard_*`, `discovery_*`,
  `strategy_get*`, `strategy_list`, `execution_get*`, `user_get*`, `arena_*`, `audit_*`, `get_*`,
  `ratchet_stop_get`/`list`/`events`, guides — everything not in the mutation set.
- **Mutations blocked (raise `PermissionError`):** `create_position`, `close_position`,
  `edit_position`, `cancel_order`, `send_usdc`, `transfer_spot_to_perps`, `strategy_create`,
  `strategy_create_custom_strategy`, `strategy_close`, `strategy_close_positions`, `strategy_update`,
  `strategy_pause`, `strategy_top_up`, `strategy_withdraw_funds`,
  `strategy_bridge_funds_from_hyperliquid_to_evm`, `ratchet_stop_add`/`edit`/`delete`,
  `user_claim_referral_rewards`.

The allowlist is scaffold-owned in source and **empty by default** — there is no env/operator/author
knob. As an author: **assume you cannot mutate anything.** Produce signals; the runtime executes.

---

## Market data types

**Read every numeric field through the fleet-standard `_f()` helper** (author guide's scoring
template) — `float` of a number is a no-op, so it is correct on every runtime version and every
data path. Hyperliquid serves candle `o/h/l/c/v` and price-map values as strings; most other
tools' numeric fields (budgets, balances, leaderboard rows) are strings too.

Runtimes **newer than 3.0.32** numeric-cast the two market tools at the `ctx.senpi_mcp` boundary:

| Section | Cast | Uncastable value |
|---|---|---|
| `market_get_asset_data` candles — `o/h/l/c/v` + `t` (`T`/`n` when valid) | strict | row **dropped** |
| `market_get_prices` — every `prices` value (`count` kept in sync) | strict | entry **dropped** |
| `asset_context`, `order_book` `px`/`sz`, `funding_history` rates | best-effort | left as-is |

- Drops are never silent: a `senpi_mcp_cast_dropped` scaffold event + a `_cast_dropped` marker on
  the payload. A series can come back **shorter / non-contiguous** — guard `len(candles)`, don't
  assume fixed `t` spacing.
- Originals: a single `_raw` key beside the cast sections.
- Every other tool's response is untouched, on every runtime version.

---

## The signal dict (return value)

Return a `list[dict]`, one per candidate. Keys:

| Key | Required | Notes |
|---|---|---|
| `asset` | ✅ | non-empty string |
| `direction` | ✅ | normalized to `LONG` / `SHORT` (case-insensitive in) |
| `marginPct` | — | **top-level**, PERCENT of withdrawable in (0,100]; the runtime sizes `(marginPct/100) × withdrawable`. Positive when present; a present-but-non-positive value is a **loud reject** |
| `leverage` | — | **top-level**, positive number when present |
| `data` | — | validated against the recipe's `signal_data_schema`: unknown key → reject, missing required key → reject, wrong type → reject (types `string`/`number`/`boolean`/`object`/`array`) |
| `valid_for_seconds` | — | per-signal TTL (relative); a non-positive/non-int falls back to `default_signal_validity_seconds` |
| `signal_id` | — | a stable string for your own dedup; **omit and the scaffold mints a uuid**. It is the intake dedup key |
| `signal_type` | — | optional label |

**The scaffold owns:** `produced_at`, `valid_until` (= `produced_at + (valid_for_seconds or
default_signal_validity_seconds)`), the wire envelope, delivery, and dedup. **Do not set
`valid_until`/`produced_at`.** You also don't normalize a `[0,1]` wire score — emit your raw score on
`data{}`.

`marginPct` (percent of withdrawable) and `leverage` are the canonical **top-level** sizing fields
(the runtime reads `signal.marginPct`/`signal.leverage` directly) — don't bury them inside `data{}`.

> **Per-signal sizing is exactly two top-level keys: `marginPct` and `leverage`.** Any other
> top-level key is **dropped with only a stderr warning**, and sizing falls back to the recipe's
> configured margin — so an invented sizing field does not fail loudly, it quietly sizes the
> position differently than you intended.

---

## `scoring.py` — keep the logic pure

Every example splits a sibling `scoring.py` with **no I/O, no MCP, no daemon** — just functions over
candles/numbers. `scan.py` fetches via `ctx.senpi_mcp` (value types: "Market data types" above)
and hands the candle lists to `scoring`. This ports
cleanly from older producers (the pure trend/scoring helpers were already unit-tested) and lets a
reader follow the thesis without mocking MCP.

---

## Emit-one vs emit-all

Both are valid — it's your thesis:

- **Emit ≤1 signal/tick** when the whole thesis is "one decision" (iguana picks the single strongest
  index trend; keep `slots: 1`).
- **Emit all gated candidates** and let the runtime's `slots`/`maxSlots` apply the ceiling
  (spider). The runtime owns slot accounting either way.

---

## Authoring checklist

- ✅ `scan(inputs, ctx)` is **single-pass + sync** — no `while True`, no `sleep`, no daemon.
- ✅ **Read-only MCP only** — never call a mutation tool (it raises `PermissionError`).
- ✅ Pure scoring in `scoring.py`; MCP + state in `scan.py`.
- ✅ Keep dedup / rotation / first-seen ledgers in `ctx.state` (`last()` → mutate → `append()`).
- ✅ Declare every `data{}` key in `signal_data_schema`; set `default_signal_validity_seconds`.
- ✅ Read every numeric field through `_f()` — no-op on numbers, required on strings.
- ✅ Put `marginPct` and `leverage` — the only two sizing keys — at the **top level**, not inside `data{}`.
- ✅ On any failure, **return `[]`** (or a partial list) — don't crash.
- ✅ If a gate can make `scan()` return **before it reads anything**, let `ctx.dry_run` bypass it — a
  tick that reads nothing validates as **UNPROVEN**, not as a pass.
- ✅ Prove it before you hand it over: `openclaw senpi validate <recipe-dir>` must return **PASS** —
  the directory holding that instance's `runtime.yaml`: the package root for a flat package (no
  `instances:` list), the instance's own dir once `strategy.yaml` lists instances. A root that lists
  instances holds no recipe of its own and refuses `[E_VALIDATE_NO_RECIPE]`; each instance needs its
  own pass.
- ❌ Don't set `valid_until`/`produced_at` — the scaffold owns the envelope (`signal_id` optional).
- ❌ Don't schedule the scanner yourself or POST signals — the runtime does it.
- ❌ Don't try to mutate `ctx` (it's frozen) or `append` a non-dict to `ctx.state`.
