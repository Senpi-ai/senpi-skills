# Strategy Creation — the fast path

**Read THIS doc, then build. It's self-contained — you should not need to fetch other files to produce a working strategy.** (Deep references are linked at the bottom for edge cases only.)

A Senpi strategy is **a Python producer that emits signals + a `runtime.yaml` that tells the runtime how to act on them.** The runtime owns execution, exits (DSL), and risk. You write the *signal logic*; you do **not** write execution, stops, or a daemon loop.

> **The invariant — non-negotiable.** The producer ONLY emits signals via `push_signal`. It NEVER calls `create_position`/`strategy_create_custom_strategy`, NEVER writes its own stop/exit logic, NEVER hand-rolls a daemon loop or risk checks. A "custom strategy" = custom *signal logic* in a standard producer, not a custom harness. If you're calling `create_position` in the producer, stop — that's the runtime's job.

---

## The 5 steps

1. **Pick your archetype + example agent** from [`producer-patterns.md`](producer-patterns.md), then **map it to a DSL preset** (heuristic below). Clone the example agent's three files as your starting point.
2. **Write the producer** (`scripts/<skill>-producer.py`) on the bundled SDK — skeleton below.
3. **Write `runtime.yaml`** — complete minimal template below.
4. **Write `config/<skill>-config.json`** — operator-tunable defaults (wallet, chatId, model via env).
5. **Verify** the daemon is alive + ticking.

If the user gave you full autonomy, **pick the archetype + preset yourself**. Otherwise, **prompt the user** to pick.

## Step 1 — Pick the archetype (+ example agent), then map it to a DSL preset

**The archetype catalog and the example agent to clone for each live in [`producer-patterns.md`](producer-patterns.md) — that's the single source of truth, so this doc never duplicates (and never drifts from) the agent roster.** Go there to choose:

- **Know roughly what you want?** Skim the archetype catalog and clone the example agent under the closest one.
- **Not sure?** Walk the [decision tree](producer-patterns.md#decision-tree--help-a-user-pick-their-first-strategy) — it takes a user from "I don't know" (or "just give me a good one") through what they believe about markets to a concrete archetype + example agent.

**Then map the archetype to a DSL preset** (default `balanced` when unsure):

| Archetype class | DSL preset |
|---|---|
| Trend-follower · single-asset alpha · striker / rank-jump · trader-follower | `let_winners_run` |
| Funding-fade · contrarian / crowding-unwind | `mean_reversion` |
| Volume-engine · high-frequency | `scalp` |
| Anything else, or unsure | `balanced` ⭐ |

## Step 2 — The producer (copy this skeleton)

```python
# scripts/<skill>-producer.py
import os, sys
from pathlib import Path

_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next((p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()), _sdk_candidates[0])
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache, producer_daemon

WALLET = os.environ["<SKILL>_WALLET"]
SCANNER_NAME = "<scanner_name>"           # MUST match the external_scanner name in runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet — multi-wallet-host safe

client = SenpiClient()
mcp = tick_cache(client)                  # per-tick memoization; call MCP tools through this

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        # 1. Pull whatever your thesis needs (cached per tick). EVERY MCP tool
        #    returns the envelope {success, data, meta} — your payload is under
        #    "data". Always unwrap with .get("data", resp) (see shapes below):
        resp = mcp("market_list_instruments")
        instruments = resp.get("data", resp).get("instruments", [])
        # ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET).get("data", {})
        # 2. Score / gate per your thesis. 3. Emit ONLY when a signal qualifies:
        if signal_ready:
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset="BTC", direction="LONG",   # asset & direction are TOP-LEVEL (never inside data)
                score=0.85,                       # 0..1 confidence
                signal_type="<YOUR_TYPE>",
                data={"your_factor": 1.23},       # must match config.fields in runtime.yaml
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,                 # your tick cadence
        name=LOCK_NAME,
        wallet=WALLET,                        # daemon self-terminates if the runtime is
        scanner=SCANNER_NAME,                 # deleted OR the scanner is renamed
    )
```

The config/wrapper module (`scripts/<skill>_config.py`) just exposes the `SenpiClient`; copy it from the example agent — it's boilerplate.

### MCP response envelopes (so you don't guess the shape)

Every Senpi MCP tool returns the same outer envelope — `{ "success": bool, "data": {...}, "meta": {...} }`. **Always read your payload from `data`** (`resp.get("data", resp)` is the fleet idiom — the fallback is harmless if a future runtime ever unwraps for you). The two market-scan tools you'll reach for most:

```jsonc
// market_list_instruments()  — full tradeable universe (main DEX + XYZ)
{ "success": true, "data": { "instruments": [
    { "name": "BTC", "sz_decimals": 5, "max_leverage": 40, "is_delisted": false,
      "context": { "coin": "BTC", "funding": "0.0000125", "openInterest": "29013.5",
                   "prevDayPx": "77702.0", "dayNtlVlm": "1492870202.9", "premium": "-0.00026",
                   "oraclePx": "76825.0", "markPx": "76803.0", "midPx": "76802.5", "dayBaseVlm": "19315.1" } }
  ] }, "meta": {...} }

// market_get_asset_data(asset="BTC")  — one asset, deep
{ "success": true, "data": {
    "asset": "BTC",
    "candles": { "1h": [...] },                       // keyed by the intervals you requested
    "funding_history": [ { "coin": "BTC", "fundingRate": "0.0000125", "premium": "-0.0003", "time": 1779120000018 } ],  // rows are HOURLY
    "asset_context": { "coin": "BTC", "funding": "0.0000125", "openInterest": "...", "markPx": "...", "oraclePx": "..." },
    "oi_velocity": { "current_oi": ..., "oi_trend": "BUILDING" }  // may be null
  }, "meta": {...} }
```

So: `market_list_instruments` → `data["instruments"]` (a **list**); `market_get_asset_data` → `data["asset_context"]` / `data["funding_history"]` / `data["candles"]`. All numeric fields arrive as **strings** — cast with `float(...)`.

### Exits are the runtime's job — you do NOT add a close leg

The producer emits **entry** signals only; **DSL owns every exit** (Step 4). That's the invariant — a normal strategy has **no `CLOSE_POSITION` action** at all. Do not invent one to "close on signal," and note there is **no `signal_type_filter` field** — actions bind to scanners via the `scanners:` list, not by signal type. (Producer-driven signal-invalidation closes are an advanced, rarely-needed pattern: a separate `CLOSE_POSITION` action subscribed to its **own** scanner that the producer pushes exit signals to — see [`yaml-schema.md`](yaml-schema.md). Default and recommended: let DSL handle it.)

## Step 3 — `runtime.yaml` (complete minimal template)

```yaml
version: 1                       # plugin SCHEMA version (always 1) — NOT your agent semver

strategy:
  wallet: "${WALLET_ADDRESS}"
  slots: 1                       # max concurrent positions
  margin_per_slot: 200           # USD per position (or use margin_pct)
  enabled: true

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"

scanners:
  - name: position_tracker       # REQUIRED for exit management
    type: position_tracker
    interval: 10s
  - name: <scanner_name>         # MUST match SCANNER_NAME in the producer
    type: external_scanner       # push-driven — do NOT set interval
    outputs: { signals: true, context: false }
    config:
      fields:                    # every key your producer puts in `data`
        your_factor: { type: number, required: true }

actions:
  - name: <skill>_entry
    action_type: OPEN_POSITION
    decision_mode: llm           # llm gate; or `rule` for pass-through
    decision_model: ${<SKILL>_DECISION_MODEL}   # BARE model name, no provider prefix
    scanners: [<scanner_name>]
    min_confidence: 7
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 15 }
    context:
      - { type: signal, scanner: <scanner_name> }
    decision_prompt: |
      Decide whether to open this position. Approve only if the signal is clean.
      {{signal_<scanner_name>}}

exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
    # Default = balanced (shown). For let_winners_run / mean_reversion / scalp,
    # paste the matching block from Step 4 below — all four are inlined there.
    phase1: { enabled: true, max_loss_pct: 15.0, retrace_threshold: 10, consecutive_breaches_required: 1 }
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 10,  lock_hw_pct: 0 }
        - { trigger_pct: 20,  lock_hw_pct: 30 }
        - { trigger_pct: 35,  lock_hw_pct: 50 }
        - { trigger_pct: 60,  lock_hw_pct: 70 }
        - { trigger_pct: 100, lock_hw_pct: 85 }
    hard_timeout: { enabled: true, interval_in_minutes: 4320 }   # 72h outer bound
    weak_peak_cut: { enabled: true, interval_in_minutes: 360, min_value: 3.0 }

risk:
  guard_rails:
    daily_loss_limit_pct: 4
    max_entries_per_day: 6
    max_consecutive_losses: 3
    cooldown_minutes: 90
    drawdown_halt_pct: 20
    per_asset_cooldown_minutes: 45
```

## Step 4 — DSL presets (pick one; default `balanced`)

`balanced` is inlined in Step 3. All four are paste-ready below — drop the matching block straight under `exit: { engine: dsl, interval_seconds: 30, dsl_preset: ... }`. (`phase2.tiers` must be ascending by `trigger_pct`; ROE %, not price %.)

**`let_winners_run`** — trend / momentum / single-asset / striker / trader-follower. Widest; no time-cuts.
```yaml
phase1: { enabled: true, max_loss_pct: 20.0, retrace_threshold: 8, consecutive_breaches_required: 1 }
phase2:
  enabled: true
  tiers:
    - { trigger_pct: 10,  lock_hw_pct: 0 }
    - { trigger_pct: 20,  lock_hw_pct: 25 }
    - { trigger_pct: 30,  lock_hw_pct: 40 }
    - { trigger_pct: 50,  lock_hw_pct: 60 }
    - { trigger_pct: 75,  lock_hw_pct: 75 }
    - { trigger_pct: 100, lock_hw_pct: 85 }
```

**`balanced`** ⭐ default — general-purpose / unsure. (Same block as Step 3.)
```yaml
hard_timeout: { enabled: true, interval_in_minutes: 4320 }        # 72h outer bound
weak_peak_cut: { enabled: true, interval_in_minutes: 360, min_value: 3.0 }
phase1: { enabled: true, max_loss_pct: 15.0, retrace_threshold: 10, consecutive_breaches_required: 1 }
phase2:
  enabled: true
  tiers:
    - { trigger_pct: 10,  lock_hw_pct: 0 }
    - { trigger_pct: 20,  lock_hw_pct: 30 }
    - { trigger_pct: 35,  lock_hw_pct: 50 }
    - { trigger_pct: 60,  lock_hw_pct: 70 }
    - { trigger_pct: 100, lock_hw_pct: 85 }
```

**`mean_reversion`** — faders / contrarian / range unwinds. Tight; banks the snapback fast; time-cuts on.
```yaml
hard_timeout: { enabled: true, interval_in_minutes: 2880 }        # 48h
weak_peak_cut: { enabled: true, interval_in_minutes: 120, min_value: 2.0 }
phase1: { enabled: true, max_loss_pct: 15.0, retrace_threshold: 6, consecutive_breaches_required: 1 }
phase2:
  enabled: true
  tiers:
    - { trigger_pct: 5,  lock_hw_pct: 30 }
    - { trigger_pct: 10, lock_hw_pct: 50 }
    - { trigger_pct: 15, lock_hw_pct: 65 }
    - { trigger_pct: 25, lock_hw_pct: 80 }
    - { trigger_pct: 40, lock_hw_pct: 90 }
```

**`scalp`** — high-frequency, fee-sensitive. Tightest; fast locks + short timeouts.
```yaml
hard_timeout: { enabled: true, interval_in_minutes: 90 }
dead_weight_cut: { enabled: true, interval_in_minutes: 45 }
phase1: { enabled: true, max_loss_pct: 8.0, retrace_threshold: 5, consecutive_breaches_required: 1 }
phase2:
  enabled: true
  tiers:
    - { trigger_pct: 5,  lock_hw_pct: 50 }
    - { trigger_pct: 10, lock_hw_pct: 70 }
    - { trigger_pct: 15, lock_hw_pct: 85 }
```

**Always attach DSL** unless the user explicitly opts out — it's what protects a position if the producer goes quiet or a fill lands late. Field-by-field tuning: [`dsl-configuration.md`](dsl-configuration.md).

## Step 5 — Verify

```bash
ps -ef | grep <skill>-producer | grep -v grep        # exactly one process
senpi-helpers list                                    # TICKS ≥ 1, ERRORS = 0
grep daemon_tick_finished /tmp/<skill>-producer.log | tail -3   # "status":"ok"
```

---

## Gotchas (read before you ship — these cost roundtrips otherwise)

- **`asset` and `direction` are TOP-LEVEL `push_signal` args, never inside `data`.** Putting them in `data` makes the runtime store two copies and rejects with `INVALID_REQUEST`.
- **`decision_model` takes a BARE model name** (`gemini-2.5-pro`, `claude-sonnet-4-20250514`) — no provider prefix, or the gateway returns `500 Unknown model`.
- **`runtime.yaml` `version:` is always `1`** (plugin schema major). Your agent semver lives in SKILL.md frontmatter — NOT here.
- **Runtime package on main is `@senpi-ai/runtime`** (with the `-ai`), never `@senpi/runtime`.
- **Hyperliquid funding is hourly — annualize with `×24×365` (`×8760`), never `×3×365`.** The `funding` field on the asset context (and each `funding_history` row, spaced 1h apart) is the **per-hour** rate — verified against Hyperliquid's Info API: both return `0.0000125` for BTC at baseline (HL's `0.01%/8h ÷ 8`). So annualized % = `abs(funding) × 8760 × 100`. Annualizing `×3×365` (an 8-hour / Binance-style assumption) understates funding by ~8x. Owl's producer is the correct reference; don't copy an 8h convention.
- **One runtime per wallet.** Installing a second for the same wallet is rejected — delete the first.
- **This doc is canonical — don't reverse-engineer conventions from live fleet producers.** Some shipped producers still carry legacy conventions (e.g. the old `×3×365` funding annualization) pending fleet reconciliation. Trust the patterns here; don't fetch another agent's source to "double-check" — you'll just copy a bug.

## Deep references (only if the template above isn't enough)

- [`producer-patterns.md`](producer-patterns.md) — full archetype catalog + example links
- [`python-producer-sdk.md`](python-producer-sdk.md) — full SDK (batch, parallel, cache, errors)
- [`yaml-schema.md`](yaml-schema.md) — every `runtime.yaml` field
- [`dsl-configuration.md`](dsl-configuration.md) — DSL field reference + tuning
- [`risk-gates.md`](risk-gates.md) — risk guard-rail semantics
- [`strategy-examples.md`](strategy-examples.md) — full worked examples
