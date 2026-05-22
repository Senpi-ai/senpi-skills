# Signal schema reference

> **Read only if [`strategy-creation.md`](strategy-creation.md) doesn't cover your edge case.** That doc inlines the `push_signal(...)` contract you need to build. This is the exhaustive wire-format reference — reach for it for the `POST /signals` envelope, per-item error codes, or unusual `data` field types.

Full description of the `SignalItem` shape consumed by
`senpi-trading-runtime`'s `POST /signals` endpoint, as exposed by
`SenpiClient.push_signal(...)` and `push_signals([...])`.

For usage recipes, read [`../SKILL.md`](../SKILL.md). This doc is the
authoritative field reference.

Source of truth on the runtime side: in the `senpi-trading-runtime` repo,
`src/runtime-api/routes/signals.schema.ts` and `external-scanner-receiver.ts`.

---

## Top-level `SignalItem` fields (routing + classification)

These are arguments to `push_signal(...)`, or top-level keys in each item of
`push_signals([...])`. They live **outside** the `data` block and are used by
the runtime for routing, locking, and downstream dispatch.

| Field | Required | Type | Notes |
|---|---|---|---|
| `address` | yes | `str` | Strategy wallet address. 0x-prefixed. Lowercased server-side. |
| `scanner` | yes | `str` | Scanner id declared as `external_scanner.name` in `runtime.yaml`. |
| `asset` | for signal-emitting single ingests | `str` | Hyperliquid-canonical ticker, **uppercase** (e.g. `"MAVIA"`, `"TST"`). No runtime-side normalizer. |
| `direction` | optional | `"LONG" \| "SHORT" \| None` | Strict — runtime's `normalizeDirection` rejects anything else with `INVALID_REQUEST`. |
| `score` | optional | `float` in `[0, 1]` | Top-level confidence. Used by `decision-engine.ts` as `Math.round(highestScore * 10)` → 1..10 confidence integer. **Schema rejects values > 1.** |
| `signal_type` | optional | `str` | Per-signal override of the scanner's `defaultSignalType`. |

## Payload — the `data` block

`data` is the scanner-specific field bag. The runtime validates it against
the `config.fields` block declared on the corresponding `external_scanner`
in `runtime.yaml`.

- Field names not declared in `config.fields` are rejected with `INVALID_REQUEST`.
- Field types are enforced per `config.fields[name].type` (e.g. `string`,
  `number`, `enum`).
- `config.fields[name].required: true` means presence is mandatory; otherwise
  the field is free-form-optional.

`data` becomes `signal.meta` downstream.

### Example `runtime.yaml` declaration

```yaml
external_scanners:
  - name: my_signals
    config:
      fields:
        funding_bps:        { type: number,  required: true }
        regime:             { type: string,  required: false }
        margin_usd:         { type: number,  required: false }
        leverage:           { type: number,  required: false }
```

### Matching producer call

```python
client.push_signal(
    address=WALLET,
    scanner="my_signals",
    asset="MAVIA",                   # routing — top level
    direction="SHORT",                # routing — top level
    score=0.42,                       # top-level confidence (0..1)
    data={                            # scanner-specific — `data`
        "funding_bps": 18,
        "regime": "neutral",
        "margin_usd": 50.0,
        "leverage": 4,
    },
)
```

---

## ⚠️ The split between routing and `data`

**Routing fields live at the top level. Scanner-specific fields live in `data`.**

The split exists because the runtime uses routing fields for serialization,
locking, and dispatch (e.g. the per-(address, scanner) ingest lock keys on
`address`+`scanner`). The downstream signal object stores `address`, `scanner`,
`asset`, `direction`, `score`, `signal_type` as discrete top-level fields, and
the entire `data` object as `signal.meta`.

**If you put `asset` or `direction` inside `data`**, the runtime stores two
copies — one at the canonical top-level slot (which becomes `null` because
you didn't pass it there) and one inside `meta`. Downstream consumers read
inconsistently:

- The decision-engine + DSL exit engine read top-level → see `null` → reject as `INVALID_REQUEST`.
- Some scanners' field validators read `meta` → see the value → think it's fine.

That divergence is what triggered the `INVALID_REQUEST` rejections in the
Pangolin TST incident on 2026-05-05.

The wrapper does **not** auto-relocate misplaced fields — it forwards exactly
what you pass. The runtime rejects with `INVALID_REQUEST` and the helper
surfaces that rejection as a `SenpiClientError`.

---

## Validation: what the helper checks vs. what the runtime checks

| Check | Where | Behavior |
|---|---|---|
| Items list non-empty | helper | `SenpiClientError` raised before any HTTP call |
| `address` is `str`, starts with `0x`, length ≥ 4 | helper | `SenpiClientError` raised before any HTTP call |
| `scanner` is non-empty `str` | helper | `SenpiClientError` raised before any HTTP call |
| `score` ∈ `[0, 1]` (top-level) | helper + runtime | Helper raises pre-flight; runtime rejects with `INVALID_REQUEST` if it slipped past |
| `direction` ∈ `{"LONG", "SHORT", null}` | runtime | Per-item `INVALID_REQUEST` |
| `asset` is uppercase Hyperliquid ticker | runtime | Per-item `INVALID_REQUEST` if unknown |
| `data` keys match `config.fields` | runtime | Per-item `INVALID_REQUEST` for unknown keys or wrong types |
| Wallet is registered in this runtime | runtime | Per-item `NOT_FOUND` |
| Scanner exists in `runtime.yaml` | runtime | Per-item `NOT_FOUND` |
| Per-batch item count ≤ `api.maxItemsPerSignalsRequest` | runtime | Envelope-level `400 INVALID_REQUEST` |
| Body ≤ 256 KB | runtime | Envelope-level `413 INVALID_REQUEST` |

---

## Response envelope (senpi-stack shape)

`POST /signals` returns the senpi-stack envelope:

**Success (HTTP 200):**
```json
{
  "success": true,
  "data": {
    "results": [
      {
        "success": true,
        "address": "0xabc…",
        "scanner": "my_signals",
        "data": { "timestamp": 1714492800000, "signalCount": 1, "contextUpdated": false }
      },
      {
        "success": false,
        "address": "0xdef…",
        "scanner": "my_signals",
        "error": { "code": "NOT_FOUND", "message": "No running runtime…" }
      }
    ]
  }
}
```

**Envelope error (HTTP 4xx/5xx):**
```json
{
  "success": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Exceeded api.maxItemsPerSignalsRequest=10; got 12."
  }
}
```

The wrapper raises `SenpiClientError` for:
- Any per-item failure (raises with the first item's code + a histogram of all codes).
- Any envelope-level error (raises with the runtime's human message).
- Any shape skew (legacy `{ results: [...] }` envelope, empty body, malformed JSON).

The wrapper returns the parsed dict on full success (all per-item entries
have `success: true`).

Per-item error codes (`code` inside `error: { … }`):

| Code | Cause |
|---|---|
| `INVALID_REQUEST` | Schema rejection — bad `data` keys, mistyped field, asset/direction in data |
| `NOT_FOUND` | Wallet has no running runtime, or scanner doesn't exist in `runtime.yaml` |
| `UNAVAILABLE` | Runtime ingest failed internally (timeout, transient runtime error) |

---

## Single ingest vs. batch

`push_signal(...)` — convenience wrapper around `push_signals([...])` for one
item. Use it when you have one signal at a time.

`push_signals([...])` — batch. Use when you have ≥ 2 signals to emit in one
HTTP round-trip. The runtime is **not atomic** — successful items in the
batch are ingested even when the helper raises on partial failure. If you
need per-item outcome (e.g. update producer-side state per accepted signal),
either:

- Push one at a time (per-signal commit), or
- Catch `SenpiClientError` and inspect the message — it includes
  `failed_count` and a `failed_by_code` histogram.

The runtime's per-(address, scanner) ingest lock means batching items with
the same `(address, scanner)` is the most efficient pattern (no lock
contention). Items targeting different `(address, scanner)` pairs run in
parallel inside the runtime regardless of whether they were sent as a batch
or N requests.
