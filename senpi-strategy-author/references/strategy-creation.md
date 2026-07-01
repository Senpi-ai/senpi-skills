# Strategy Creation — the fast path (Runtime 3.0)

**Read THIS doc, then build.** It's the self-contained 3.0 flow — you should not need to fetch other
files to produce a package that passes `scripts/validate_strategy.py`. (Deep references are linked at
the bottom for edge cases.)

A Senpi strategy is a **package**: `strategy.yaml` (the deploy manifest) + one or more instances, each
with a `runtime.yaml` and a `scanners/` dir holding `scan.py` + a sibling `scoring.py`. On **Runtime
3.0** the runtime spawns and supervises your **`scan(inputs, ctx)`** function every `interval_seconds`
and owns everything downstream — sizing, execution, the two-phase DSL exit, risk guard-rails, slots,
and cross-tick state. You write the *signal logic*; you do **not** write execution, stops, a daemon
loop, or risk checks.

> **The invariant — non-negotiable.** `scan()` is **single-pass, synchronous, read-only**. It READS
> (read-only MCP) and RETURNS a `list[dict]` of candidate signals. It NEVER calls `create_position` /
> `strategy_create_custom_strategy` / any mutation tool (they raise `PermissionError`), NEVER writes
> its own stop/exit logic, NEVER hand-rolls a `while True` / `sleep` / daemon loop. A "custom
> strategy" = custom *signal logic* in `scan()`, not a custom harness. On any failure, **return `[]`**
> — a raised exception rolls the whole tick back.
>
> **This invariant also applies to YOU, the authoring agent.** During authoring you do NOT call
> `strategy_create_custom_strategy`, `create_position`, or any execution/strategy-mutating MCP tool.
> **Authoring ends when the package exists on disk and `validate_strategy.py` passes.** Install is a
> separate operator step (Step 6), owned by **senpi-strategy-ops** (`deploy.py`).

---

## Package layout

Build the package at `strategies/<id>/`. `<id>` is the strategy's directory name AND its manifest
`id` — they must match (the validator enforces it). Single-instance is the common case:

```
strategies/<id>/
  strategy.yaml                 # deploy manifest: id, version, catalog, requires, instances[]
  <instance>/                   # one dir per instance (main, hedge, long, short, swing, scalp, …)
    runtime.yaml                # the runtime spec: external_scanner (inputs + signal_data_schema),
                                #   entry action, exit (DSL preset), risk guard_rails
    scanners/
      scan.py                   # exports scan(inputs, ctx) -> list[dict]; read-only MCP; single-pass
      scoring.py                # pure thesis math (no I/O/MCP); imported as `import scoring` — NO __init__.py
```

A package carries **no `SKILL.md`, no attribution file, no `config/*.json`** — those are not part of a
strategy package. Tunables live in each instance's `runtime.yaml` `inputs:` block, not in a config
file.

## The steps

- **Step 0** — Pre-flight: one parallel batch of read-only MCP calls.
- **Step 1** — Pick the archetype + gold-template package to clone, map it to a DSL preset.
- **Step 2** — Write `scan.py` + sibling `scoring.py` (clone the gold template; worked skeleton below).
- **Step 3** — Write `runtime.yaml` — fill-in-the-blank template below.
- **Step 4** — Pick the DSL preset block (all inlined below).
- **Step 5** — Write `strategy.yaml`, then validate the package (one command) — then it's done.
- **Step 6** — Hand the package to the operator to install via `deploy.py`. (You do not deploy.)

If the user gave you full autonomy, **pick the archetype + preset yourself**. Otherwise, **prompt the
user** to pick.

## Composition contract — the three name spaces

A package has **three** kinds of names. Confusing them is the most common authoring bug — and the most
common silent validation failure. Read this once before writing any code or YAML.

| Kind | Looks like | Where it comes from | Authors should… |
|---|---|---|---|
| **Runtime / env injection** | `${KODIAK_WALLET}`, `${TELEGRAM_CHAT_ID}` — **ALL_CAPS** inside `${…}`, only in `runtime.yaml` | Env-substituted by the runtime at load from the operator's environment (wallet, notifier config, …) | Use as-is in `runtime.yaml`. **Do not** read them in `scan.py`. **Do not** invent new ones for thesis logic. |
| **Input tunable** | Read inside `scan.py`/`scoring.py` via `inputs.get("minScore", 10)` | The runtime's `external_scanner` `inputs:` block — the operator-tunable defaults you ship | Declare under `inputs:` in `runtime.yaml`; read with `inputs.get(name, default)`. No `config.json`, no `load_params()`. |
| **Signal emission** | The `asset` / `direction` / `marginPct` / `leverage` keys and the keys of the `data={…}` map in each dict `scan()` returns | Produced by `scan()` each tick | These are what the runtime sizes/executes on. Every `data{}` key must be declared in the instance's `signal_data_schema`. |

**Rules of thumb (every authoring bug we've seen violates one of these):**

- **Never use `${UPPER_CASE}` to mean "a field the scan produces."** Reserve `${…}` for runtime/env
  injection only (always ALL_CAPS, only in `runtime.yaml`).
- **Tunables come from `inputs.get(...)`, never a config file.** There is no `config/*.json` and no
  `load_params()` — those were the retired producer model.
- **Sizing + `asset`/`direction` are TOP-LEVEL keys of the returned dict**, never nested inside
  `data={…}`. `marginPct` / `marginUsd` / `leverage` sit at the top level; the runtime reads them
  directly.
- **Every key your scan puts in `data={…}` must be declared** in that instance's
  `signal_data_schema`. Unknown key → reject; missing required key → reject; wrong type → reject.
  Producer-side keys and schema-declared fields are the same set.

## Step 0 — Pre-flight (one parallel batch, ~5s)

Make these read-only calls **in a single batch** to ground the build in the user's actual account +
current market state:

```
user_get_me            # who am I / auth sanity
account_get_portfolio  # available capital → informs marginPct sizing
strategy_list          # existing strategies (avoid wallet/name collisions)
market_get_funding_regime    # current regime — sanity-check a funding/contrarian thesis
market_list_instruments      # tradeable universe + per-asset context (copy tickers from here)
```

Everything here is read-only — none of it mutates state or creates a strategy. **Copy tickers from
`market_list_instruments`; never emit a ticker from memory** (a wrong ticker is a silent no-trade).

## Step 1 — Pick the archetype (+ gold template to clone), then map it to a DSL preset

Match your market thesis to an archetype below, then clone that archetype's **gold-template package**
under `strategies/<id>/`. Every entry is a real package on disk — clone its `<instance>/scanners/scan.py`
+ `scoring.py` + `runtime.yaml`, keep the archetype-defining MCP calls, rewrite `scoring.py` for your
thesis, and tune the `inputs:` block. **Never hand-roll from scratch.**

| Archetype (market thesis) | Clone this package | Instance | Also in this family |
|---|---|---|---|
| Multi-asset whitelist (conviction majors) | `bison` | `main` | hedgehog, hawk, salamander, sheep, sailfish, stag, tortoise, lynx, spider |
| Single-asset alpha hunter (hard-gated, one coin) | `kodiak` (SOL) | `main` | grizzly (BTC), polar (ETH), wolverine (HYPE); beaver/heron/hummingbird (onboarding), koala |
| Single-asset XYZ specialist (oil/metals/indices) | `dire` | `main` | lemur, bobcat, iguana, raccoon |
| Universe scan (trend-follow / sniper) | `condor` | `main` | cheetah, python |
| Market-neutral dispersion (two-book) | `octopus` | `long`+`short` | spider |
| Sector complex (breadth-gated basket) | `hornet` | `main` | — |
| Funding-regime fade | `dog` | `main` | pangolin, vulture, owl, lemon, egret |
| Copy-trade / cohort follower | `jackal` | `main` | remora, raptor, albatross, cuckoo |
| Leaderboard / hot-streak follower | `cheetah` | `main` | jaguar, roach, orca, meerkat |
| Event / new-listing (IPO arc, two-book) | `magpie` | `pre_listing`+`graduation` | lemur, falcon |
| Macro-thesis / regime classifier | `coyote` | `main` | lynx |
| Cross-asset lag | `mantis` | `main` | osprey (cross-venue) |

Other one-of-a-kind live packages (clone the named package directly, read its `scan.py` header for the
thesis): `piranha`/`marlin` (microstructure), `chameleon` (ratio pairs), `kestrel` (XYZ macro fader),
`turbine` (volume engine, two-book).

- **Know roughly what you want?** Pick the row whose thesis matches and clone that package.
- **Not sure / walking a user through it?** Start broad ("what should your agent *believe* about
  markets — ride trends, fade crowds, copy winners, pick one market, catch breakouts, or stay
  neutral?"), narrow one layer at a time, and land on a single package from the table above. Route
  beginners to the onboarding-tier forks (beaver/heron/hummingbird/hedgehog/hawk/salamander/albatross/
  lemur/bobcat/raccoon/tortoise/sheep/iguana) and level up to the fleet package once they've watched a run.

**Then map the archetype to a DSL preset** (default `balanced` when unsure):

| Archetype class | DSL preset |
|---|---|
| Trend-follower · single-asset alpha · striker / rank-jump · trader-follower | `let_winners_run` |
| Funding-fade · contrarian / crowding-unwind | `mean_reversion` |
| Volume-engine · high-frequency | `scalp` |
| Anything else, or unsure | `balanced` ⭐ |

## Step 2 — Write `scan.py` + sibling `scoring.py` (clone & edit)

Two files in `<instance>/scanners/`. **`scan.py` does reads + state; `scoring.py` does the numbers**
(pure — no I/O, no MCP, no clock). Import the sibling as `import scoring`; there is **NO `__init__.py`**
in the scanners dir (the sibling resolves via the scanner dir on `sys.path` — an `__init__.py` breaks
the model and the validator flags it).

**`scan.py` skeleton** (the authoritative contract is
[`../senpi-trading-runtime/references/scan-contract.md`](../senpi-trading-runtime/references/scan-contract.md);
`strategies/kodiak/main/scanners/scan.py` is the full worked reference):

```python
# scanners/scan.py
import sys
import time

import scoring  # pure logic sibling, no I/O — unit-tested separately; NO __init__.py

def scan(inputs, ctx):
    # ── tunables: read from the runtime.yaml `inputs:` block, with your own defaults ──
    asset = inputs.get("asset", "SOL")
    min_score = float(inputs.get("minScore", 10))
    margin_pct = float(inputs.get("marginPct", 20))   # PERCENT of withdrawable (0,100], top-level
    ttl = float(inputs.get("recentSignalTtlSeconds", 14400))
    now = time.time()

    # ── cross-tick memory: dedup / rotation via ctx.state (guard None) ──
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}
    au = asset.upper()
    last = recent.get(au)
    if last is not None and (now - last) < ttl:
        return []                                     # already fired recently — hold

    # ── READ: read-only MCP only. Guard EVERY call; degrade to [] on failure, never crash. ──
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset, "candle_intervals": ["15m", "1h", "4h"],
            "include_funding": True, "include_order_book": False,
        })
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[scan] market_get_asset_data({asset}) failed: {exc!r}", file=sys.stderr)
        return []
    data = md.get("data", md) if isinstance(md, dict) else None   # unwrap the {success,data,meta} envelope
    if not data:
        return []
    candles = data.get("candles", {}) or {}
    ctx_block = data.get("asset_context", {}) or {}
    funding = scoring._f(ctx_block.get("funding", 0))

    # ── SCORE: pure functions in scoring.py ──
    th = scoring.build_thesis(candles, funding, inputs)   # returns a thesis dict or None
    if not th or th["score"] < min_score:
        # still persist a per-tick result for observability (see below), then hold
        out = []
    else:
        recent[au] = now                              # mark fired (defence-in-depth vs runtime cooldown)
        # ── EMIT: plain dict. asset/direction + sizing are TOP-LEVEL; everything else in data{}. ──
        out = [{
            "asset": asset,                           # REQUIRED, top-level
            "direction": th["direction"],             # REQUIRED, top-level — "LONG" | "SHORT"
            "marginPct": margin_pct,                  # SIZING INTENT, top-level (fleet standard)
            "leverage": th["leverage"],               # top-level; runtime applies it
            "data": {                                 # must match signal_data_schema in runtime.yaml
                "score": th["score"], "leverage": th["leverage"], "direction": th["direction"],
                "reasons": th["reasons"],
            },
        }]

    # ── PERSIST next-tick state EVERY tick; rolled back automatically if this tick errors/times out ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent})
        except Exception as exc:  # noqa: BLE001 — history may be disabled; log-and-continue
            print(f"[scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
```

**`scoring.py`** is a plain module of pure functions over candle lists / numbers — no `ctx`, no MCP,
no `time`. Clone the gold template's `scoring.py` and swap the thesis math. Because it's pure you can
unit-test it directly on sample candles with no mocks (Step 5). See
`strategies/kodiak/main/scanners/scoring.py` for the full reference (indicators + a multi-factor
`build_thesis`).

### `ctx` surface (frozen — exactly these; nothing else)

| Member | What it is |
|---|---|
| `ctx.senpi_mcp.call_tool(name, args)` | Read-only MCP client — the ONLY way to fetch data. |
| `ctx.state` | Transactional history store (or `None` when history disabled). `last()` / `recent(n)` / `append(dict)` / `len()`. |
| `ctx.wallet` | The runtime's wallet address (pass to `strategy_get_clearinghouse_state`, etc.). |
| `ctx.scanner_name` | This scanner's name. |
| `ctx.interval_seconds` | This scanner's tick cadence. |

There is no `ctx.inputs` (inputs is the first arg) and no logging handle — `print(..., file=sys.stderr)`;
the supervisor captures the child's stderr.

### Read-only MCP boundary (what you can and can't call)

- **Reads always allowed:** `market_*`, `account_get*`, `leaderboard_*`, `discovery_*`, `strategy_get*`,
  `strategy_list`, `execution_get*`, `user_get*`, `arena_*`, `audit_*`, `get_*`, `ratchet_stop_get/list/events`,
  guides.
- **Mutations blocked (raise `PermissionError`, loud, before any network call):** `create_position`,
  `close_position`, `edit_position`, `cancel_order`, `send_usdc`, `transfer_spot_to_perps`,
  `strategy_create`, `strategy_create_custom_strategy`, `strategy_close`, `strategy_close_positions`,
  `strategy_update`, `strategy_pause`, `strategy_top_up`, `strategy_withdraw_funds`,
  `strategy_bridge_funds_from_hyperliquid_to_evm`, `ratchet_stop_add/edit/delete`,
  `user_claim_referral_rewards`.

**As an author: assume you cannot mutate anything.** Produce signals; the runtime executes.

### MCP response envelopes (so you don't guess the shape)

Every Senpi MCP tool returns the same outer envelope — `{ "success": bool, "data": {...}, "meta": {...} }`.
**Always unwrap with `resp.get("data", resp)`** (the fleet idiom — the fallback is harmless). The two
market-scan tools you'll reach for most:

```jsonc
// market_list_instruments()  — full tradeable universe (main DEX + XYZ)
{ "success": true, "data": { "instruments": [
    { "name": "BTC", "sz_decimals": 5, "max_leverage": 40, "is_delisted": false,
      "context": { "coin": "BTC", "funding": "0.0000125", "openInterest": "29013.5",
                   "prevDayPx": "77702.0", "markPx": "76803.0", "midPx": "76802.5" } }
  ] }, "meta": {...} }

// market_get_asset_data(asset="BTC", candle_intervals=["1h"], include_funding=true)  — one asset, deep
{ "success": true, "data": {
    "asset": "BTC",
    "candles": { "1h": [...] },                       // keyed by the intervals you requested
    "funding_history": [ { "coin": "BTC", "fundingRate": "0.0000125", "time": 1779120000018 } ],  // rows HOURLY
    "asset_context": { "coin": "BTC", "funding": "0.0000125", "openInterest": "...", "markPx": "..." }
  }, "meta": {...} }
```

So: `market_list_instruments` → `data["instruments"]` (a **list**); `market_get_asset_data` →
`data["asset_context"]` / `data["funding_history"]` / `data["candles"]`. All numeric fields arrive as
**strings** — cast with `float(...)` (the gold template's `scoring._f` helper does this safely).

### Exits are the runtime's job — you do NOT emit a close

`scan()` returns **entry** candidates only; **DSL owns every exit** (Step 4). A normal strategy has
**no `CLOSE_POSITION` action** at all. Do not invent one to "close on signal." (A producer-driven
signal-invalidation close — a separate `CLOSE_POSITION` action on its own scanner — is an advanced,
rarely-needed pattern; default and recommended: let DSL handle it. See
[`../senpi-trading-runtime/references/runtime-yaml.md`](../senpi-trading-runtime/references/runtime-yaml.md).)

## Step 3 — `runtime.yaml` (fill-in-the-blank template)

**One `runtime.yaml` per instance = one wallet.** Substitute every `<<<SLOT>>>`; leave everything else
as-is. `${WALLET_ENV}` / `${TELEGRAM_CHAT_ID}` env-substitute at load — don't hardcode them.
`strategies/kodiak/main/runtime.yaml` is the full worked reference.

```yaml
name: <<<RUNTIME_NAME>>>              # e.g. kodiak-main — unique; used in logs/state/telemetry
group: <<<PACKAGE_ID>>>              # e.g. kodiak — ties a package's runtimes together (optional)
version: 3.0.0                       # passthrough metadata; NOT validated. Package major lives in strategy.yaml
description: >
  <<<ONE_PARAGRAPH_THESIS>>>         # what it trades, the signal, the edge, leverage/DSL posture

strategy:                            # the wallet + sizing/risk identity
  wallet: "${<<<WALLET_ENV>>>}"      # e.g. ${KODIAK_WALLET} — env-substituted; MUST match strategy.yaml wallet_env
  slots: 1                           # max concurrent positions (runtime enforces the ceiling)
  default_leverage: 5                # fallback; scan emits per-signal leverage
  trading_risk: aggressive           # conservative | moderate | aggressive
  enabled: true

scanners:
  - name: position_tracker           # built-in — feeds the DSL exit engine. REQUIRED when exit.dsl_preset is set
    type: position_tracker
    interval_seconds: 10             # built-in scanners: integer seconds, floored at 7s
  - name: <<<SCANNER_NAME>>>         # e.g. kodiak_main_signals — the supervised external scanner
    type: external_scanner
    path: ./scanners                 # dir holding scan.py, resolved vs this runtime.yaml
    entrypoint: scan.py              # module exporting scan(inputs, ctx)
    interval_seconds: 180            # runtime calls scan() every N seconds
    timeout_seconds: 150             # per-tick wall-clock budget (default = interval_seconds)
    default_signal_validity_seconds: 1800   # REQUIRED — fallback signal TTL
    state_history_max_count: 100     # ctx.state bound (0/unset = history disabled)
    inputs:                          # author tunables → scan(inputs, …). NO config.json.
      asset: "SOL"
      minScore: 10
      marginPct: 20                  # PERCENT of withdrawable (0,100] — runtime sizes (marginPct/100)*withdrawable
      recentSignalTtlSeconds: 14400
    signal_data_schema:              # REQUIRED — one line per key your scan puts in data{}
      score: { type: number }
      leverage: { type: number }
      direction: { type: string }
      reasons: { type: array, required: false }   # add/remove to match your data{} exactly

actions:
  - name: position_tracker_action    # REQUIRED when DSL is on — feeds the exit engine
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]
  - name: <<<ACTION_OPEN>>>          # e.g. kodiak_main_entry
    action_type: OPEN_POSITION
    decision_mode: rule              # rule = pass-through (the scan already applied every filter). Or `llm` for a gate.
    scanners: [<<<SCANNER_NAME>>>]   # same name as the external_scanner above
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 60 }
    context:
      - { type: signal, scanner: <<<SCANNER_NAME>>> }

exit:                                # DSL trailing-stop engine — attach unless the user opts out
  engine: dsl
  interval_seconds: 30               # DSL poll cadence (integer, 5–3600)
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 60 }
  dsl_preset:
    # Default = balanced (shown). For let_winners_run / mean_reversion / scalp,
    # paste the matching block from Step 4 below — all four are inlined there.
    hard_timeout: { enabled: true, interval_in_minutes: 4320 }   # 72h outer bound
    weak_peak_cut: { enabled: true, interval_in_minutes: 360, min_value: 3.0 }
    phase1: { enabled: true, max_loss_pct: 15.0, retrace_threshold: 10, consecutive_breaches_required: 1 }
    phase2:
      enabled: true
      tiers:                         # MUST be ascending by trigger_pct; ROE %, not price %
        - { trigger_pct: 10,  lock_hw_pct: 0 }
        - { trigger_pct: 20,  lock_hw_pct: 30 }
        - { trigger_pct: 35,  lock_hw_pct: 50 }
        - { trigger_pct: 60,  lock_hw_pct: 70 }
        - { trigger_pct: 100, lock_hw_pct: 85 }

risk:                                # all durations are SECONDS
  data_retention_seconds: 345600     # integer 3600–604800 (here 4d)
  guard_rails:
    daily_loss_limit_pct: 10
    max_entries_per_day: 8
    max_consecutive_losses: 4
    cooldown_seconds: 3600           # min 60
    drawdown_halt_pct: 25
    per_asset_cooldown_seconds: 14400   # min 300

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
  dsl_lifecycle: true
```

**Slots to fill:** `RUNTIME_NAME`, `PACKAGE_ID`, thesis text, `WALLET_ENV` (matches `strategy.yaml`'s
`wallet_env`), `SCANNER_NAME` (external scanner + the action's `scanners:` + the context entry all use
this same name), `ACTION_OPEN`, the `inputs:` values, and the `signal_data_schema` keys (== every key
in your `data={…}`).

> **`decision_mode: rule` is the default** — the scan already applied every filter, so the action is a
> pass-through. Only use `decision_mode: llm` if you want a second LLM gate; then add
> `decision_model: ${<<<MODEL_ENV>>>}` (a BARE model name, no provider prefix), `min_confidence`, and a
> `decision_prompt` whose every `{{placeholder}}` resolves to a declared `context` entry.

## Step 4 — DSL presets (pick one; default `balanced`)

`balanced` is inlined in Step 3. All four are paste-ready below — drop the matching block straight under
`exit: { engine: dsl, interval_seconds: 30, ..., dsl_preset: ... }`. (`phase2.tiers` must be ascending
by `trigger_pct`; ROE %, not price %.)

**`let_winners_run`** — trend / momentum / single-asset / striker / trader-follower. Widest; time-cuts off.
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

**Always attach DSL** unless the user explicitly opts out — it's what protects a position if `scan()`
goes quiet or a fill lands late. Field-by-field tuning: [`dsl-configuration.md`](dsl-configuration.md)
(source presets: [`dsl-presets.yaml`](dsl-presets.yaml)).

## Step 5 — Write `strategy.yaml`, then validate — then authoring is done

**`strategy.yaml`** is the deploy manifest (one per package). Clone the gold template's and edit — the
required keys the validator checks: `id` (== package dir name), `version` (single source for catalog +
attribution), `requires.runtime: ">=3.0.0"`, and `instances[]` (each with `name`, `runtime`,
`wallet_env`). Distinct `wallet_env` per instance if multi-instance.

```yaml
# strategies/<id>/strategy.yaml
schema_version: 1

id: <<<PACKAGE_ID>>>                 # MUST equal the package directory name
version: "1.0.0"                     # single source for catalog + attribution

catalog:
  name: "<<<Display Name>>>"
  emoji: "🐻"
  tagline: "<<<one-sentence what-it-does>>>"
  belief_plain: "<<<plain-English thesis>>>"
  thesis: "<<<why this edge exists>>>"
  group: single-asset                # facets — copy from senpi-strategy-discover/references/glossary.yaml
  archetype: single_market
  asset_scope: single
  direction: long_short
  risk_level: aggressive
  tier: advanced
  leverage_max: 7
  max_slots: 1
  min_budget: 200
  assets: ["SOL"]                     # copy tickers from market_list_instruments
  tags: [sol, single-asset, momentum]

requires:
  runtime: ">=3.0.0"

defaults:
  auth_token_env: SENPI_AUTH_TOKEN

instances:
  - name: main
    runtime: main/runtime.yaml       # path relative to the package dir
    wallet_env: <<<WALLET_ENV>>>      # MUST equal the ${...} used in runtime.yaml strategy.wallet
    funding_share: 1.0
```

**Validate** (static — the manifest ↔ runtime ↔ package consistency the install relies on). One command:

```bash
python3 senpi-strategy-author/scripts/validate_strategy.py strategies/<id>
```

Exit 0 = valid. Also unit-test `scoring.py` on sample candles (it's pure — no mocks). **This is where
your job ends.** Do NOT install, do NOT run `deploy.py`, do NOT run any execution MCP — that's the
operator's step.

The validator enforces (so pre-check these): `id` == dir name; a `version`; `instances[]`; each
instance's `runtime.yaml` exists and its `wallet_env` appears as `${...}`; `scanners/scan.py` exists
and defines `scan(`; a sibling `scanners/scoring.py` exists; **no `scanners/__init__.py`**;
`data_retention_seconds` ∈ [3600, 604800]; `cooldown_seconds` ≥ 60; `per_asset_cooldown_seconds` ≥ 300;
every `.py` parses; no bare `@senpi/runtime` (use `@senpi-ai/runtime`).

## Step 6 — Hand off to the operator to install (you do NOT deploy)

Authoring is done; install is a separate, operator-run step owned by **senpi-strategy-ops** (`deploy.py`),
which creates + funds the wallet, renders + registers the runtime(s), then verifies the scanner ticks:

```bash
# resumable, in order — the operator runs these (substitute the strategy id + budget)
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget 500   # create + fund wallet(s)
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>                 # render + register runtime(s)
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                 # confirm scan() is ticking
```

---

## Gotchas (read before you ship — these cost roundtrips otherwise)

- **`scan()` is single-pass, sync, read-only.** No `while True`, no `sleep`, no daemon, no mutation
  MCP. On any failure, **return `[]`** — a raised exception rolls the whole tick back.
- **`asset`/`direction`/`marginPct`/`marginUsd`/`leverage` are TOP-LEVEL keys** of the returned dict,
  never inside `data={…}`. The runtime reads the top-level sizing fields directly; burying them there
  means they're ignored.
- **`marginPct` is a PERCENT of withdrawable (0,100]**, not a fraction — the runtime sizes
  `(marginPct/100) × withdrawable`. Emit `20`, not `0.20`. (`marginUsd` is the fixed-USD alternative;
  the fleet standard is `marginPct`.)
- **Every `data{}` key must be declared in `signal_data_schema`** — unknown key → reject, missing
  required key → reject, wrong type → reject.
- **Tunables come from `inputs.get(...)`.** There is no `config/*.json` and no `load_params()`. Declare
  them under `external_scanner.inputs` in `runtime.yaml`.
- **NO `__init__.py` in `scanners/`.** `scoring.py` is imported as `import scoring` — the sibling
  resolves via the scanner dir on `sys.path`; an `__init__.py` breaks it and the validator flags it.
- **`external_scanner` uses `interval_seconds` (integer), NOT `interval` (duration string).** Also:
  `inputs` (tunables) + `signal_data_schema` (output schema) — NOT `config` / `config.fields` /
  `outputs` (those are rejected).
- **Risk durations are SECONDS** (`cooldown_seconds` ≥ 60, `per_asset_cooldown_seconds` ≥ 300); DSL
  time-cut durations are MINUTES (`interval_in_minutes`). Don't mix the units.
- **`data_retention_seconds` ∈ [3600, 604800]** (1h–7d). Out of range fails validation.
- **DSL requires both** a `position_tracker` scanner AND a `POSITION_TRACKER` action subscribed to it —
  omit either and load fails.
- **Hyperliquid funding is hourly — annualize with `×24×365` (`×8760`), never `×3×365`.** The `funding`
  field on the asset context (and each `funding_history` row, spaced 1h apart) is the **per-hour** rate.
- **Runtime package is `@senpi-ai/runtime`** (with the `-ai`), never `@senpi/runtime`. `requires.runtime`
  is `">=3.0.0"`.
- **One runtime per wallet.** A package may bundle multiple runtimes (spider/turbine) but each is its
  own wallet.

## Deep references (only if the template above isn't enough)

- [Step 1's archetype → gold-template table](#step-1--pick-the-archetype--gold-template-to-clone-then-map-it-to-a-dsl-preset) — the full archetype catalog + gold-template packages to clone (inlined above)
- [`../senpi-trading-runtime/references/scan-contract.md`](../senpi-trading-runtime/references/scan-contract.md) — the authoritative `scan(inputs, ctx)` contract + `ctx` surface
- [`../senpi-trading-runtime/references/runtime-yaml.md`](../senpi-trading-runtime/references/runtime-yaml.md) — every `runtime.yaml` field + unit (the runtime's own schema — it wins over any helper doc)
- [`strategy-yaml-schema.md`](strategy-yaml-schema.md) — the `strategy.yaml` manifest schema
- [`dsl-configuration.md`](dsl-configuration.md) — DSL field reference + tuning ([`dsl-presets.yaml`](dsl-presets.yaml) = source presets)
- [`risk-gates.md`](risk-gates.md) — risk guard-rail semantics
- [`strategy-examples.md`](strategy-examples.md) — full worked examples
