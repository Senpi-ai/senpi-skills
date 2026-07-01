# Signal schema reference (Runtime 3.0)

The value your `scan(inputs, ctx)` returns is a **`list[dict]`**, one dict per candidate signal. There is
no wire envelope to build and nothing to POST — the runtime reads your dicts directly, sizes and executes
them, and owns the delivery envelope. This doc is the authoritative field reference for that dict.

**Source of truth:** the "signal dict" section of
[`senpi-trading-runtime/references/scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md).
Copy from it; do not invent keys. For the return-value *skeleton* and the `ctx` surface, see
[`python-producer-sdk.md`](python-producer-sdk.md).

---

## The signal dict (return value)

Return a `list[dict]`, one per candidate. Keys:

| Key | Required | Notes |
|---|---|---|
| `asset` | ✅ | non-empty string |
| `direction` | ✅ | normalized to `LONG` / `SHORT` (case-insensitive in) |
| `marginPct` | — | **top-level**, a PERCENT of withdrawable (0,100] — the **fleet standard** (~97 of 102 scanners). The runtime sizes `(marginPct/100) × withdrawable`. |
| `marginUsd` | — | **top-level** alternative to `marginPct`: a fixed USD amount (not a percent); a positive number when present — a present-but-non-positive value is a **loud reject** |
| `leverage` | — | **top-level**, positive number when present |
| `data` | — | validated against the recipe's `signal_data_schema`: unknown key → reject, missing required key → reject, wrong type → reject (types `string`/`number`/`boolean`/`object`/`array`) |
| `valid_for_seconds` | — | per-signal TTL (relative); a non-positive/non-int falls back to `default_signal_validity_seconds` |
| `signal_id` | — | a stable string for your own dedup; **omit and the scaffold mints a uuid**. It is the intake dedup key |
| `signal_type` | — | optional label |

**The scaffold owns:** `produced_at`, `valid_until` (= `produced_at + (valid_for_seconds or
default_signal_validity_seconds)`), the wire envelope, delivery, and dedup. **Do not set
`valid_until`/`produced_at`.** You also don't normalize a `[0,1]` wire score — emit your raw score on
`data{}`.

Sizing is **top-level**, not buried inside `data{}`. Emit **`marginPct`** (the fleet standard) *or*
`marginUsd` — plus `leverage`; the runtime reads `signal.marginPct`/`signal.marginUsd`/`signal.leverage`
directly.

---

## The `data{}` block — validated against `signal_data_schema`

`data` is the scanner-specific field bag. In Runtime 3.0 it is validated against the instance's
**`signal_data_schema`**, declared under the `external_scanner` in that instance's **`runtime.yaml`** (not
in any separate config-fields block). Every key your `scan()` puts in `data{}` must be declared there:

- A key not declared in `signal_data_schema` → **reject**.
- A key declared `required` but missing → **reject**.
- A wrong-typed value → **reject** (types: `string` / `number` / `boolean` / `object` / `array`).

Keep `asset`, `direction`, `marginPct`/`marginUsd`, and `leverage` at the **top level** — do not also put
them inside `data{}`. `data{}` is for the thesis payload (scores, momentum, reasons) you want carried
alongside the signal.

### Example `signal_data_schema` (from `strategies/kodiak/main/runtime.yaml`)

Declared under the `external_scanner` in `runtime.yaml`:

```yaml
    signal_data_schema:
      score: { type: number }
      leverage: { type: number }
      direction: { type: string }
      trend4h: { type: string, required: false }
      trendStrength4h: { type: number, required: false }
      trend1h: { type: string, required: false }
      mom15mPct: { type: number, required: false }
      mom1hPct: { type: number, required: false }
      mom4hPct: { type: number, required: false }
      fundingRate: { type: number, required: false }
      oiTrend: { type: string, required: false }
      btcMom1hPct: { type: number, required: false }
      rsi: { type: number, required: false }
      smPctOfTopTraders: { type: number, required: false }
      smTraderCount: { type: number, required: false }
      smCc15m: { type: number, required: false }
      smAligned: { type: boolean, required: false }
      reasons: { type: array, required: false }
```

Fields without `required: false` are mandatory in every emitted `data{}`. Declare `required: false` for
anything optional.

### Matching `scan()` return (from `strategies/kodiak/main/scanners/scan.py`)

```python
return [{
    "asset": asset,
    "direction": th["direction"],
    "marginPct": margin_pct,          # SIZING INTENT — top level; runtime sizes the dollars
    "leverage": leverage,             # conviction-tiered (5/6/7); top level; runtime applies it
    "data": {                         # validated against signal_data_schema
        "score": th["score"], "leverage": leverage, "direction": th["direction"],
        "trend4h": th["trend_4h"], "trendStrength4h": th["trend_strength_4h"], "trend1h": th["trend_1h"],
        "mom15mPct": th["mom_15m"], "mom1hPct": th["mom_1h"], "mom4hPct": th["mom_4h"],
        "fundingRate": th["funding"], "oiTrend": "rising" if th["oi"] > 0 else "unknown",
        "btcMom1hPct": th["btc_mom_1h"], "rsi": th["rsi"],
        "smPctOfTopTraders": th["sm_pct"], "smTraderCount": th["sm_traders"],
        "smCc15m": th["sm_cc15m"], "smAligned": th["sm_aligned"],
        "reasons": th["reasons"],
    },
}]
```

Every `data{}` key above (`score`, `leverage`, `direction`, `trend4h`, …, `reasons`) is declared in the
`signal_data_schema` block — that one-to-one correspondence is what the validator enforces.

---

## Failure & dedup behavior

- On any error, **return `[]`** (or a partial list) — don't crash. The scaffold rolls back next-tick state
  and captures your stderr.
- Omit `signal_id` and the scaffold mints a uuid; provide a stable `signal_id` when you want your own
  intake dedup key.
- Cross-tick dedup / rotation state lives in `ctx.state` (`.last()` → mutate → `.append(dict)`), which
  advances only on a clean tick. See [`python-producer-sdk.md`](python-producer-sdk.md) for the `ctx`
  surface and [`scan-contract.md`](../../senpi-trading-runtime/references/scan-contract.md) for the full
  transactional-state semantics.

---

## Emit-one vs emit-all

Both are valid — it's your thesis:

- **Emit ≤1 signal/tick** when the whole thesis is "one decision" (single-asset conviction agents; keep
  `slots: 1`).
- **Emit all gated candidates** and let the runtime's `slots`/`maxSlots` apply the ceiling. The runtime
  owns slot accounting either way.
