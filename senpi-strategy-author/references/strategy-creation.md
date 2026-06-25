# Strategy Creation — the fast path

**Read THIS doc, then build. It's self-contained — you should not need to fetch other files to produce a working strategy.** (Deep references are linked at the bottom for edge cases only.)

A Senpi strategy is **a Python producer that emits signals + a `runtime.yaml` that tells the runtime how to act on them.** The runtime owns execution, exits (DSL), and risk. You write the *signal logic*; you do **not** write execution, stops, or a daemon loop.

> **The invariant — non-negotiable.** The producer ONLY emits signals via `push_signal`. It NEVER calls `create_position`/`strategy_create_custom_strategy`, NEVER writes its own stop/exit logic, NEVER hand-rolls a daemon loop or risk checks. A "custom strategy" = custom *signal logic* in a standard producer, not a custom harness. If you're calling `create_position` in the producer, stop — that's the runtime's job.
>
> **This invariant also applies to YOU, the authoring agent.** During authoring you do NOT call `strategy_create_custom_strategy`, `create_position`, `estimate_custom_strategy_positions_opening`, or any other execution/strategy-mutating MCP tool. **Authoring ends when the file bundle exists on disk.** Deployment is a separate operator step (Step 6) — running an execution MCP during authoring just creates a real, empty strategy that someone has to clean up.

---

## The steps

- **Step 0** — Pre-flight: one parallel batch of read-only MCP calls.
- **Step 1** — Pick the archetype + example agent (from [`producer-patterns.md`](producer-patterns.md)), map it to a DSL preset.
- **Step 1.5** — Write `SKILL.md` + lay out the bundle directory.
- **Step 2** — Write the producer (`scripts/<skill>-producer.py`) — complete worked example below.
- **Step 3** — Write `runtime.yaml` — fill-in-the-blank template below.
- **Step 4** — Pick the DSL preset block (all 4 inlined).
- **Step 5** — Verify the bundle (one-liner) — then it's done.
- **Step 6** — Hand the bundle to the operator to deploy. (You do not deploy.)

If the user gave you full autonomy, **pick the archetype + preset yourself**. Otherwise, **prompt the user** to pick.

## Composition contract — the three name spaces

A skill bundle has **three** kinds of names. Confusing them is the most common authoring bug — and the most common silent compose-check failure downstream. Read this once before writing any code or YAML; everything below assumes you understand the distinction.

| Kind | Looks like | Where it comes from | Authors should… |
|---|---|---|---|
| **Runtime / env injection** | `${WALLET_ADDRESS}`, `${TELEGRAM_CHAT_ID}`, `${<SKILL>_DECISION_MODEL}` — **ALL_CAPS** inside `${…}`, only in `runtime.yaml` | Injected by the runtime at start-up from the operator's environment (wallet, notifier config, decision-model name, etc.) | Use as-is in `runtime.yaml`. **Do not** define them in the producer. **Do not** invent new ones for thesis logic. |
| **Config parameter** | Read inside the producer from `config/<skill>-config.json` (or via a config helper module like the example agent's `<skill>_config.py`) | The operator-tunable defaults you ship in `config/<skill>-config.json` | Define in the config JSON; reference in the producer. `runtime.yaml` does **not** typically refer to these — they're for the producer's own gating logic. |
| **Scanner emission** | The `asset` / `direction` / `score` / `signal_type` kwargs and the top-level keys of the `data={…}` kwarg on `client.push_signal(…)` | Produced by the producer each tick | These are the fields the runtime / actions / exit DSL see. If `runtime.yaml` (or an action condition) needs to reference one, write it as `scanner.score`, `scanner.data.<field>`, etc. — **never** as `${UPPER_CASE}`. |

**Rules of thumb (every authoring bug we've seen violates one of these):**

- **Never use `${UPPER_CASE}` to mean "a field the scanner produces."** Reserve `${…}` for runtime/env injection only (and it's always ALL_CAPS).
- **Never invent emission names like `${rsi_value}` and hope the runtime knows them.** Emit via `push_signal(data={"rsi_value": …})` and reference as `scanner.data.rsi_value` if `runtime.yaml` needs it.
- **The top-level `push_signal` kwargs (`asset`, `direction`, `score`, `signal_type`) are the runtime's stable contract.** Keep them top-level; never nest them inside `data={…}` — the runtime rejects that with `INVALID_REQUEST`.
- **Every key your producer puts in `data={…}` must be declared** in `runtime.yaml` under `scanners.config.fields`. Producer-side keys and YAML-declared fields are the same set; mismatches fail validation.

## Step 0 — Pre-flight (one parallel batch, ~5s)

Make these read-only calls **in a single batch** (not three scattered waves) to ground the build in the user's actual account + current market state:

```
user_get_me            # who am I / auth sanity
account_get_portfolio  # available capital → informs margin_pct sizing
strategy_list          # existing strategies (avoid wallet/name collisions)
market_get_funding_regime    # current regime — sanity-check a funding/contrarian thesis
market_list_instruments      # tradeable universe + per-asset context (see shape in Step 2)
```

Everything here is read-only — none of it mutates state or creates a strategy.

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

## Step 1.5 — Lay out the bundle + write `SKILL.md`

**Create the bundle at `/data/workspace/skills/<skill-name>/`** (the skills dir — **NOT** `/data/workspace/strategies/`). `<skill-name>` is your strategy's directory name, e.g. `funding-harvest-1779475145`. Standard layout:

```
/data/workspace/skills/<skill-name>/
├── SKILL.md                       # frontmatter + operator spec (write this now)
├── README.md                      # install/deploy steps for the operator
├── runtime.yaml                   # Step 3
├── config/<skill-name>-config.json
├── scripts/<skill>-producer.py    # Step 2
├── scripts/<skill>_config.py      # boilerplate — copy from the cloned example
└── references/skill-attribution.md
```

**`SKILL.md` frontmatter** — match this real structure (note `version` + `requires` live under `metadata`, not top-level):

```yaml
---
name: <skill-name>                  # e.g. funding-harvest-1779475145
description: >-
  One-paragraph thesis: what it trades, the signal, the edge, leverage/DSL posture.
license: MIT
metadata:
  author: <operator>
  version: "1.0.0"                  # YOUR agent semver — distinct from runtime.yaml's schema version
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---
```

Below the frontmatter, add a 5-line operator spec table (asset/universe, signal, tick cadence, leverage, DSL preset) + the file inventory above. This is what stops the agent from emitting a stray `README.md`-only deviation — `SKILL.md` is a first-class deliverable, not an afterthought.

## Step 2 — The producer (complete worked example — clone & edit)

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

WALLET = os.environ["<SKILL>_WALLET"]     # e.g. FUNDING_HARVEST_WALLET — per-agent env var
SCANNER_NAME = "<scanner_name>"           # MUST match the external_scanner name in runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet — multi-wallet-host safe

client = SenpiClient()
mcp = tick_cache(client)                  # per-tick memoization; call MCP tools through this

# ── Your tunables (the only part you change per thesis) ──────────────
MIN_FUNDING_HOURLY = 0.00005              # gate: |hourly funding| this large = "elevated"
MIN_OI_USD = 3_000_000                    # skip illiquid markets

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        # 1. PULL. Every MCP tool returns {success, data, meta} — unwrap with
        #    .get("data", resp). market_list_instruments → data["instruments"].
        resp = mcp("market_list_instruments")
        instruments = resp.get("data", resp).get("instruments", [])

        # 2. SCORE / GATE. This worked example is a funding-fade: short the
        #    asset with the most-positive funding (crowded longs paying up).
        best = None
        for inst in instruments:
            ctx = inst.get("context") or {}
            funding = float(ctx.get("funding") or 0)          # HOURLY rate (string → float)
            oi_usd = float(ctx.get("openInterest") or 0) * float(ctx.get("markPx") or 0)
            if oi_usd < MIN_OI_USD or abs(funding) < MIN_FUNDING_HOURLY:
                continue
            if best is None or abs(funding) > abs(float((best.get("context") or {}).get("funding") or 0)):
                best = inst

        # 3. EMIT — only when a signal qualifies. Fade: positive funding → SHORT.
        if best is not None:
            ctx = best["context"]
            funding = float(ctx["funding"])
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset=best["name"],                                  # TOP-LEVEL — never inside data
                direction="SHORT" if funding > 0 else "LONG",        # TOP-LEVEL
                score=min(1.0, abs(funding) / 0.0005),               # 0..1 confidence
                signal_type="FUNDING_FADE",                          # always pass explicitly
                data={                                               # must match config.fields in runtime.yaml
                    "funding_annualized_pct": round(abs(funding) * 8760 * 100, 2),  # HOURLY → ×8760
                    "oi_usd": round(float(ctx["openInterest"]) * float(ctx["markPx"])),
                },
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

This runs as-is (swap the `<...>` names + the two tunables for your thesis). The config/wrapper module (`scripts/<skill>_config.py`) just exposes the `SenpiClient` — copy it verbatim from the cloned example; it's boilerplate.

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

## Step 3 — `runtime.yaml` (fill-in-the-blank template)

**Substitute every `<<<SLOT>>>`; leave everything else as-is.** The four slots are: the runtime name, the scanner name (must equal `SCANNER_NAME` in your producer), your `data` field declarations, and the decision-model env var. `${WALLET_ADDRESS}` / `${TELEGRAM_CHAT_ID}` resolve from the operator's environment at deploy — don't hardcode them.

```yaml
name: <<<RUNTIME_NAME>>>          # e.g. funding-harvest-1779475145-tracker
version: 1                       # plugin SCHEMA version (always 1) — NOT your agent semver
description: <<<ONE_LINE_THESIS>>>   # e.g. "Fade the most-crowded funding on liquid perps."

strategy:
  wallet: "${WALLET_ADDRESS}"
  slots: 1                       # max concurrent positions
  margin_pct: 20                 # % of account budget per slot — scales with any budget (preferred over fixed margin_per_slot USD)
  enabled: true

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"

scanners:
  - name: position_tracker       # REQUIRED for exit management — leave verbatim
    type: position_tracker
    interval: 10s
  - name: <<<SCANNER_NAME>>>      # e.g. funding_harvest_signals — MUST equal SCANNER_NAME in the producer
    type: external_scanner       # push-driven — do NOT set interval
    outputs: { signals: true, context: false }
    config:
      fields:                    # one line per key your producer puts in `data`
        <<<DATA_FIELD_1>>>: { type: number, required: true }   # e.g. funding_annualized_pct
        <<<DATA_FIELD_2>>>: { type: number, required: true }   # e.g. oi_usd  (add/remove to match your data{})

actions:
  - name: <<<ACTION_OPEN>>>      # e.g. funding_harvest_entry
    action_type: OPEN_POSITION
    decision_mode: llm           # llm gate; or `rule` for pass-through
    decision_model: ${<<<DECISION_MODEL_ENV>>>}   # e.g. ${FUNDING_HARVEST_DECISION_MODEL} — BARE model name, no provider prefix
    scanners: [<<<SCANNER_NAME>>>]                # same name as the external_scanner above
    min_confidence: 7
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 15 }
    context:
      - { type: signal, scanner: <<<SCANNER_NAME>>> }
    decision_prompt: |
      Decide whether to open this position. Approve only if the signal is clean.
      {{signal_<<<SCANNER_NAME>>>}}

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
    cooldown_seconds: 5400
    drawdown_halt_pct: 20
    per_asset_cooldown_seconds: 2700
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

## Step 5 — Verify the bundle (one-liner) — then authoring is done

Static checks only — the producer parses, the YAML loads, the files exist. **This is where your job ends.** One exec call:

```bash
cd /data/workspace/skills/<skill-name>/ && \
  python3 -c "import ast; ast.parse(open('scripts/<skill>-producer.py').read())" && \
  python3 -c "import yaml; yaml.safe_load(open('runtime.yaml'))" && \
  ls -la scripts config runtime.yaml SKILL.md
```

If that exits clean, the bundle is complete. **Do not start the daemon, do not call `runtime create`, do not run any execution MCP** — that's the operator's step.

## Step 6 — Hand off to the operator to deploy (you do NOT deploy)

Authoring is done; deployment is a separate, operator-run step. Tell the operator (or put in the README) the sequence — substitute wallet + chat first, then:

```bash
# 1. Register the runtime from the bundle
openclaw senpi runtime create --path /data/workspace/skills/<skill-name>/runtime.yaml
openclaw senpi runtime list          # confirm it registered
openclaw senpi status

# 2. Launch the producer daemon (long-lived; ticks internally). disown so a
#    shell/SIGTERM exit doesn't kill it. Env vars must be set in this shell.
nohup python3 -u /data/workspace/skills/<skill-name>/scripts/<skill>-producer.py \
  > /tmp/<skill>-producer.log 2>&1 &
disown
```

Post-deploy liveness (operator runs these once it's up):

```bash
ps -ef | grep <skill>-producer | grep -v grep        # exactly one process
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
