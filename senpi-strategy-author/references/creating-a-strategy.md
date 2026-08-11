# Creating a Strategy from Scratch — Senpi Runtime 3.0

> **The one rule that governs everything below:** *every guess in this system fails silently.* A wrong MCP field → a scanner that ticks clean and emits nothing. A drifted DSL → an exit that doesn't fire. A made-up catalog facet → a strategy nobody is ever shown. So: **anchor on the references** (the MCP I/O guide, `dsl-presets.yaml`, the discovery `glossary.yaml`), and **confirm it actually operates** — never assume.

---

## 1. Mental model

A strategy is a **deployable package, not a skill.** You author two things — the **thesis** (what to believe, how to score it) and the **guardrails** (how to exit, how much risk). Runtime 3.0 owns everything operational: it **spawns and supervises** your scanner, calling `scan(inputs, ctx)` every `interval_seconds`, and owns sizing, execution, exits, slots, risk gates, state durability, and retries.

Your code is **one read-only, pure function**: it reads data and returns candidate signals. It **never** trades, sizes in dollars, loops, sleeps, or writes files. There is no daemon, no `push_signal`.

## 2. The package — FLAT, and that is the layout this whole guide builds

```
/data/workspace/strategies/<id>/  # the DURABLE root — never author inside a managed skill dir
  strategy.yaml                   # identity + catalog facets — and NO `instances:` list
  runtime.yaml                    # the deterministic spec: inputs, entry action, DSL exit, risk
  scanners/
    scan.py                       # scan(inputs, ctx) -> list[dict]   (reads + emits)
    scoring.py                    # pure thesis math — no I/O, unit-testable
```

**No `instances:` list, no `<instance>/` dir.** Every loader — the deployer, the author lint and the
runtime itself — synthesizes the canonical `main` instance from that root `runtime.yaml`, binding
`wallet_env` to the `${...}` the recipe already uses. That is what makes **one** path serve every
command in this guide: the package root is the target of `validate_strategy.py`,
`validate_universe.py`, `deploy.py validate`, `openclaw senpi validate` and `deploy.py create` alike.
The instance is still named `main`, so the recipe's linkage is `name: <id>-main`, `group: <id>` (§6).

**Location is load-bearing:** build under `/data/workspace/strategies/` (`SENPI_STRATEGIES_DIR`
overrides). A package created inside a managed skill directory (`/data/.openclaw/skills/…`) is
destroyed on that skill's next version bump — the skills-manager replaces the whole dir.

**The one exception — multi-instance — is a different shape, and §4's decision 4 is what picks it.**
One instance binds to one wallet, so an independent long book + short book, or a swing + a scalp leg,
needs one `<instance>/` dir per leg (each with its own `runtime.yaml` + `scanners/`) plus an explicit
`instances:` list in `strategy.yaml`. Schema and the instance-entry fields:
`references/strategy-yaml-schema.md`. **Unless decision 4 says multi-instance, build flat.**

If you do take the exception, **exactly two things in this guide change**, and nothing else:

| | flat (this guide) | multi-instance |
|---|---|---|
| `openclaw senpi validate` | `<pkg-root>`, once | `<pkg-root>/<instance>`, **one run per instance** |
| the runtime id (`-r`, and the recipe's `name:`) | `<id>-main` | `<id>-<instance>`, one per leg |

**Every other command still takes the package root or the bare id.** `validate_strategy.py` and
`deploy.py validate` read `strategy.yaml`, so they need the **root** — pointed at an instance dir,
`validate_strategy.py` fails `missing strategy.yaml`. `validate_universe.py` walks every
`runtime*.yaml` under whatever dir you give it, so the root covers all legs in one run.
`deploy.py create <id>` takes the bare id and funds every instance by `funding_share`. Only
`senpi validate` is per-instance, because it resolves ONE recipe and a multi-instance root holds none.

## 3. Division of labor — memorize this

| **You own** | **Runtime 3.0 owns** |
|---|---|
| Universe, signal, score | Scheduling + supervising `scan()` (restarts a crashed child) |
| Sizing **intent** (`marginPct`) | Converting intent → **dollars** off the live (reconciled) account |
| Exit shape (a named DSL preset) | Execution, slot caps, position dedup |
| Risk limits (guard rails) | State durability (transactional), retries |
| Catalog facets | **Read-only enforcement** — any mutating tool raises `PermissionError` |

Two invariants fall out of this:
1. **`scan()` is read-only + pure + single-pass.** On *any* error, `return []` — never crash.
2. **You emit a sizing *intent* (`marginPct`), not dollars.** The runtime converts it to a dollar amount off the reconciled account value. Do **not** read the clearinghouse to size — that's the runtime's job in 3.0. **`marginPct` is a PERCENT in (0,100]** — `10` = 10%, sized `(marginPct/100) × withdrawable` (not a fraction: `0.10` = 0.1%). **`marginPct` and `leverage` are the only two per-signal sizing keys** — any other top-level key is dropped with a stderr warning and sizing falls back to the configured margin.

## 4. The design space — the 7 decisions that define *any* strategy

Decide these in prose first; the files just encode them.

1. **Universe** — single asset · static basket · **dynamic** (rebuilt from `market_list_instruments`, volume floor + fresh-listing) · **derived** (names come from a leaderboard/cohort, not a list).
2. **Data** — candles (`market_get_asset_data`) · funding/OI (`market_get_funding_*`) · smart-money (`leaderboard_get_markets`, `discovery_*`) · cross-asset flow (`market_get_cross_asset_flows`).
3. **Edge** — trend-follow · mean-revert · breakout · relative-strength · copy/follow · cohort-divergence · event/new-listing · macro-thesis.
4. **Shape** — long-only / short-only / mixed-on-one-wallet = **1 instance** (build flat, §2) · independent long+short or distinct cadences = **multiple instances** (each its own wallet + `funding_share`) — that is §2's exception, and the only decision here that changes the layout.
5. **Cardinality** — one best pick (`slots: 1`) · all gated qualifiers (runtime caps via `slots`).
6. **Memory (`ctx.state`)** — none · signal-dedup (TTL) · first-seen ledger · rolling history (breadth / "adding-daily") · pool/cohort cache (daily refresh).
7. **Exit / risk / cadence** — a named **DSL preset** (§7), risk gates sized to style, `interval_seconds` 60→900, `decision_mode: rule` (default — `llm` only for a genuine gate).

## 5. Archetype patterns — the design space, pre-filled

Match the idea to a row, copy the settings, write the edge. The **Clone from** column names real
packages under `strategies/` with that shape — read their `scan.py`/`scoring.py`/`runtime.yaml`
as the working example (validated, current-idiom code; never resurrect old doc snippets).

| Archetype | Universe | Data | Edge | Shape | Card. | State | DSL preset / cadence | Clone from |
|---|---|---|---|---|---|---|---|---|
| Trend / momentum | dynamic/basket | candles | confirmed trend + RS | long or L/S | all | dedup | `let_winners_run` / slow | `lynx`, `bison` |
| Mean-reversion | majors basket | candles + RSI | fade extremes | L/S | all | dedup | `mean_reversion` / fast | `lemon`, `bald-eagle` |
| Breakout | basket | candles + range | range break / new high | long | all | dedup | `let_winners_run` / medium | `hawk`, `badger` |
| Trader-follower | **derived** (board) | `leaderboard_*`/`discovery_*` | mirror proven traders | L/S | all | dedup + baseline | `let_winners_run` / medium | `albatross`, `raptor` |
| Cohort-divergence | **derived** (realized-PnL cohorts) | `discovery_*` | smart-money vs crowd | L+S (2 inst.) | all | daily ledger + cohort cache | `let_winners_run` / slow | `whalehunter`, `egret` |
| Managed-futures | multi-class basket | candles | cross-asset trend, vol-parity | L+S | all | minimal | `let_winners_run` / slow | `caribou`, `ox` |
| Microstructure / flow | majors | funding/OI + candles | liquidation cascade / volume | L/S | one or all | dedup | `balanced` or `scalp` / fast | `piranha`, `camel` |
| Event / new-listing | **dynamic** (fresh) | board + candles | catch new listings early | long | all | first-seen ledger | `balanced` / medium | `magpie` |
| Thesis / macro | curated basket | candles + breadth | accumulate a narrative | long (or L/S) | all | breadth + horizon | `let_winners_run` + horizon / slow | `thesis-fund`, `rhino` |
| Parabolic (Stag-class) | single/few | candles | identified parabolic run | long | one | dedup | `parabolic_runner` / medium | `stag`, `kodiak` |

The *framework never changes* across rows — only these cells do. For more examples per archetype,
grep `strategies/catalog.json` by its `archetype` field (a closed set; every package is tagged).

## 6. Build it

### `scoring.py` — pure math (the edge)
No I/O, no MCP, no clock, no state — just functions over candles/numbers, so it unit-tests without mocks.

> **Candle schema (`market_get_asset_data`):** keys `t,o,h,l,c,v` (+ `T,s,i,n`). Close is `candle["c"]` — there is no `candle["close"]`. Values may arrive as **strings** — always read numerics through `_f()` below (`float` of a number is a no-op, so it's correct on every runtime version and every tool). Type contract: `senpi-trading-runtime/references/scan-contract.md` → "Market data types".

```python
def _f(v, d=0.0):
    """Defensive numeric read: no-op on numbers, casts strings, d on None/garbage.
    Gate on presence first — a fallback 0.0 reads as a real price."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return d

def score(asset, candles, extra, inputs):    # candles/numbers in, thesis dict out
    if not candles: return None
    close = _f(candles[-1]["c"])
    if not _qualifies(close, ...): return None
    return {"score": s, "direction": "LONG"|"SHORT", "reasons": [...]}
```

### `scan(inputs, ctx)` — reads + emits
Same skeleton for every archetype; the marked lines are where archetypes differ.
```python
import scoring

def scan(inputs, ctx):
    # (1) UNIVERSE — pick ONE:
    universe = inputs.get("universe", [])                    # static
    # universe = _dynamic_from_board(ctx, inputs)            # dynamic (market_list_instruments)
    # universe = _derived_from_leaderboard(ctx, inputs)      # derived (followers/cohort)

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}   # (6) MEMORY

    picks = []
    for asset in universe:
        data = ctx.senpi_mcp.call_tool("market_get_asset_data",   # (2) DATA — READ-ONLY
            {"asset": asset, "candle_intervals": ["4h", "1d"],
             "dex": "xyz" if asset.lower().startswith("xyz:") else ""})
        th = scoring.score(asset, data, None, inputs)             # (3) EDGE
        if th and th["score"] >= inputs.get("minScore", 5):
            picks.append({**th, "asset": asset})

    picks.sort(key=lambda p: p["score"], reverse=True)
    # (5) CARDINALITY: emit-all below, or `picks = picks[:1]` for one-best-pick
    out = [{
        "asset": p["asset"],
        "direction": p["direction"],                 # REQUIRED: LONG | SHORT
        "marginPct": inputs.get("marginPct", 10),    # SIZING INTENT — PERCENT in (0,100]; 10 = 10%
        "data": {                                    # must match signal_data_schema exactly
            "score": p["score"], "direction": p["direction"], "reasons": p["reasons"],
        },
    } for p in picks]

    if ctx.state is not None:
        try: ctx.state.append({"recent": recent})    # transactional — rolls back on a failed tick
        except Exception as e:
            import sys; print(f"[scan] state append failed: {e!r}", file=sys.stderr)
    return out
```
- **`ctx` is frozen:** `ctx.senpi_mcp.call_tool(name, args)` (read-only), `ctx.state` (`.last()/.recent(n)/.append()/len()`), `ctx.wallet`, `ctx.scanner_name`, `ctx.interval_seconds`. No logging handle — `print(..., file=sys.stderr)`.
- **Signal dict keys:** `asset`✅, `direction`✅ (LONG/SHORT), `marginPct` (intent), `leverage` (optional), `data{}` (validated against `signal_data_schema`), optional `valid_for_seconds` / `signal_id`. The scaffold owns `produced_at`/`valid_until`/dedup — don't set them.
- **Anchor every `call_tool` on the published MCP I/O reference** (`read_senpi_guide`). A guessed tool name, interval string, or output field = a silent dead scanner.

### `runtime.yaml` — the deterministic spec
At the package root (§2). Multi-instance: one per `<instance>/` dir, with `<instance>` in place of `main`.
```yaml
name: <id>-main                # REQUIRED linkage — `<id>-<instance>`; flat's instance IS `main`
group: <id>                    # REQUIRED linkage
version: 3.0.0
description: >                  # REQUIRED — plain-language thesis + how it works, 2-4 sentences.
  <What it trades, the actual edge/signal, when it enters, how it exits, and who it's for.>
  This is what the runtime REGISTERS and what the app reads back to explain and JUDGE the strategy
  later (senpi-portfolio surfaces it as the strategy's mandate — "is it doing its job?"). Write it as
  the strategy's own honest description of its job, not marketing. Every instance gets its own.
strategy:
  wallet: "${<WALLET_ENV>}"
  slots: 6                     # the runtime's hard ceiling on concurrent positions
  default_leverage: 3
  trading_risk: moderate
scanners:
  - { name: position_tracker, type: position_tracker, interval_seconds: 10 }
  - name: <id>_signals
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300
    timeout_seconds: 180
    state_history_max_count: 200          # 0/unset disables ctx.state
    inputs: { ... }                       # <-- ALL tunables live HERE (the old config.json), nowhere else
    signal_data_schema:                   # <-- declare EVERY key you put in data{}
      score: { type: number }
      direction: { type: string }
      reasons: { type: array, required: false }
actions:
  - { name: position_tracker_action, action_type: POSITION_TRACKER, decision_mode: rule, scanners: [position_tracker] }
  - name: <id>_entry
    action_type: OPEN_POSITION
    decision_mode: rule                   # 'rule' unless you genuinely need an LLM gate
    scanners: [<id>_signals]
    params: { order_type: FEE_OPTIMIZED_LIMIT, fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 60 } }
    context: [ { type: signal, scanner: <id>_signals } ]
exit:   { engine: dsl, dsl_preset: { ... } }   # <-- a named preset (§7)
risk:   { guard_rails: { drawdown_halt_pct: 25, daily_loss_limit_pct: 15, cooldown_seconds: 3600, ... } }
```

### `strategy.yaml` — the manifest
```yaml
schema_version: 1
id: <id>                       # == package dir name; == every runtime.yaml `group`
version: "1.0.0"               # the ONE version (catalog + MCP attribution derive from it)
catalog: { ... }               # section 8 — controlled vocabulary
requires: { runtime: ">=3.0.0" }     # @senpi-ai/runtime (with -ai). Confirm exact semver with the team.
defaults: { auth_token_env: SENPI_AUTH_TOKEN }      # env NAMES only, never values
# NO `instances:` — flat (§2). The `main` instance is synthesized from the root runtime.yaml,
# and its wallet_env is read back out of that recipe's `strategy.wallet: "${<ID>_WALLET}"`.
```
Multi-instance only (§2's exception): add `instances:` with one entry per leg —
`- { name: <leg>, runtime: <leg>/runtime.yaml, wallet_env: <ID>_<LEG>_WALLET, funding_share: 0.5 }` —
each a distinct `wallet_env`, `funding_share` summing to 1.0.

## 7. Exits — name a DSL preset, don't hand-roll

Pick one of five validated presets from `senpi-strategy-author/references/dsl-presets.yaml`, **copy its `dsl_preset:` block** into `exit:`, and change at most one field. `default: balanced`. Tiers are **uncapped** in 3.0.

| Preset | Use for | Shape (max-loss · first lock · top tier · time-cuts) |
|---|---|---|
| **`let_winners_run`** | trend · breakout · momentum · follower · cohort · thesis | -20% · none until +10% · rides to **+100%** (lock 85%) · off |
| **`balanced`** *(default)* | general / unsure | -15% · 6h weak-peak + 72h timeout · to +100% |
| **`mean_reversion`** | faders · contrarian · range | -15% tight retrace · **lock 30% at +5%** · time-cuts on |
| **`scalp`** | HFT · fee-sensitive | -8% · lock 50% at +5% · 90m timeout + dead-weight cut |
| **`parabolic_runner`** | *identified* parabolic setups only | -25% · none until +15% · rides to **+250%** · 14d timeout — **bleeds in chop** |

`max_loss_pct`/`retrace_threshold` are **ROE % (margin), not price %** (the engine divides by leverage). Unsure → `balanced`. `parabolic_runner` is a scalpel — only when you've *already* identified the setup.

## 8. Catalog — how users find you (a controlled vocabulary)

Discovery matches your strategy to users by the `catalog:` block. **Validation only *warns*** — wrong facets deploy fine but go silently unmatched. **Pull `senpi-strategy-discover/references/glossary.yaml` and use exact values.**

- **Controlled (author-set, from the glossary):**
  - `archetype` — **closed set of 6:** `trend_following` · `contrarian_fade` · `copy_trading` · `single_market` · `breakout_momentum` · `structural_neutral`.
  - `asset_classes` — **the one field the engine HARD-FILTERS on.** Tag *inclusively*; you assign the XYZ category (big-tech→`xyz_equities`, oil/metals→`commodities`, SP500→`indices`, SpaceX→`pre_ipo`). Values: `btc_eth` · `major_alts` · `universe_crypto` · `xyz_equities` · `commodities` · `indices` · `pre_ipo` · `none`.
  - `sub_style` (**extensible** — add value + gloss if nothing fits), `asset_scope` (`single|basket|universe|follows_traders`), `risk_level` (`conservative|moderate|aggressive`), `tier` (`starter|advanced`), `direction` (`long_only|short_only|long_short`).
- **Free text (no glossary — the LLM reads them):**
  - `belief_plain` — *what it does*, plain-language.
  - `thesis` — *when/who it's for* — **the only worldview hook** (how "I think the US rebounds" / "run me a hedge fund" finds you). For any thesis/macro/fund-style strategy this field is the whole point.
  - `tags` — free keywords.
- **Derived by `gen_catalog.py` (don't duplicate):** `assets`, `leverage_max`, `funding_split`, `cadence_seconds`/`time_horizon` (from cadence), `instance_count`, `max_slots`, `min_budget` (**computed** by `min_budget.py`), `wallet_count`, `min_budget_breakdown`.

## 9. Prove it runs, then deploy, then confirm it *operates*

```
python3 senpi-strategy-author/scripts/validate_strategy.py /data/workspace/strategies/<id>   # advisory lint
openclaw senpi validate /data/workspace/strategies/<id>                         # THE GATE — must be PASS. The package root (flat, §2)
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget N            # the whole path: wallet(s) ($10/wallet floor) → install → observed tick
openclaw senpi deploy status                                                    # read-only: the report; `overall: live` is the gate
# teardown / redeploy:  close.py <id>  (flattens positions, returns funds)
```
**"running" ≠ "operating."** Don't trust `status: running`. Confirm the scanner has a **positive run count + a fresh `lastRunFinishedAt`** (`openclaw senpi state -r <id>-main --json`, or `openclaw senpi scanner -r <id>-main` — `-r` is the runtime id, so `<id>-<leg>` per leg on §2's exception), and that it **emits a non-empty set on a tick where it should** — a `live` report proves it *ticked*, not that it produced a signal. Those reads are read-only, and so is `deploy.py verify <id>` (it composes them into a per-instance verdict and deploys nothing); the command that moves money is the resume, `deploy.py runtime <id>` / `create <id> --budget <usd>`. This is an **agent-side check** — run it yourself; never ask the user "is it working?".

### The gate — `senpi validate`, before any wallet exists

The desk checks above catch *your* bugs. A different and higher-value class only appears when **the runtime itself executes your code**: the contract / language mismatches between the authoring agent and the runtime — a `data{}` field it rejects, an MCP tool name that doesn't exist, a `marginPct` the sizer reads differently. These fail **silently**; a scanner with any of them ticks clean and trades nothing.

Finding them used to require a tiny deploy. It doesn't any more:

```
openclaw senpi validate /data/workspace/strategies/<id>
```
**Point it at the directory holding the `runtime.yaml`** — which, for the flat package §2 tells you to
build, is the package root. It resolves ONE recipe, and the root holds it. (If you built §2's
multi-instance exception instead, the root holds no recipe of its own: pointing there refuses
`[E_VALIDATE_NO_RECIPE]` and lists the instances, and you run it once per instance against
`.../<id>/<instance>`, per §2's table. Every package in the repo's `strategies/` catalog is that
kind — the flat package §11 builds is not, and neither is yours.)

It runs the **real loop** — same code path production uses — imports every scanner file, executes `scan()` once against live read-only data, counts what it actually read, and builds each returned candidate into the exact wire shape intake would receive, checking it against intake's own schema. **No wallet, no funding, no deploy.**

- **PASS** — the code loads, a real tick ran, it read live data, and its signals would be accepted. This is what a green smoke test used to mean, minus the money.
- **UNPROVEN** (exit 2) — it ran and **established nothing**: zero successful reads. Not a pass. Usually a gate in `scan()` returning early; have it consult `ctx.dry_run`. The finding names the line it returned from.
- **FAIL** (exit 1) — each finding carries `what` / `why` / `fix` computed against your package: the rejected field and why intake refuses it, the tool name the server doesn't expose, the exception with its file and line, an exception your own `except` swallowed.

Fix what it reports, re-run, and only hand to ops once it says PASS. **A clean lint is not a pass; only `senpi validate` is.**

Two things it deliberately does *not* prove, so don't over-claim on its behalf: branches this tick didn't take, and logic that depends on open positions (it runs against an empty account by default — which is exactly the state a freshly funded strategy starts in). After deploy, still confirm the strategy **operates** — `deploy.py verify <id>`, then a positive run count and an accepted signal in `openclaw senpi state -r <id>-main --json` (`<id>-<leg>` per leg on §2's exception).

## 10. The author's checklist (the silent-failure guards)

- `scan()` single-pass + sync; read-only MCP only; `return []` on any error.
- Pure scoring in `scoring.py`; MCP + state in `scan.py`.
- **Never hardcode a ticker you didn't verify against the live list.** Every static `universe`/`asset`/`catalog.assets` entry must be a live HL instrument — a fake ticker silently no-trades (`market_get_asset_data` rejects it as an unknown coin — do not retry — and the scan skips it). Check it: `validate_universe.py /data/workspace/strategies/<id>` (read-only; `deploy.py validate` reports the same thing, and `openclaw senpi deploy` REFUSES a dead name pre-money with `[E_UNIVERSE_NOT_LIVE]` — so that deploy funds no wallet, though on a redeploy it says nothing about a wallet the package already has; you find out faster here). Real index = `xyz:XYZ100`, *not* `xyz:NASDAQ`.
- Emit a **`marginPct` intent**, not dollars; `marginPct`/`leverage` top-level, not in `data{}`.
- Declare every `data{}` key in `signal_data_schema`.
- **Anchor on the references:** MCP fields → I/O guide; exit → a named preset; catalog facets → the glossary.
- Linkage: `group: <id>`, `name: <id>-main` (`<id>-<instance>` per leg when multi-instance), package is `@senpi-ai/runtime`. `funding_share` sums to 1.0 only when `instances:` is declared.
- **`strategy.wallet` must be the WHOLE value and UPPERCASE** — `"${MY_WALLET}"`, `[A-Z0-9_]` only. A lowercase (`${my_wallet}`) or mid-string (`pre${FOO}`) token passes **every** python lint, `deploy.py validate` included, and is then refused by the runtime when it loads the flat package.
- Lint (advisory) → **`openclaw senpi validate /data/workspace/strategies/<id>` = PASS (the gate)** → deploy → **confirm it emits/operates**, not just "ticked."
- **A gate in `scan()` must honour `ctx.dry_run`** — otherwise the tick reads nothing and validates as UNPROVEN, which is not a pass.
- **Never hand a strategy to ops on a clean lint alone.** The lint reads your package; only `senpi validate` runs it. That is what catches the authoring-agent↔runtime language mismatches — and it now costs nothing to find out.

---

## 11. Worked example — "US Rebound (Q3 2026)" thesis fund, end to end

**The 7 decisions:** Universe = curated US risk-on basket. Data = candles. Edge = accumulate names *confirming* the rebound (4h+1d uptrend, RSI room). Shape = long-only, 1 instance, emit-all. Memory = breadth + horizon. Exit = `let_winners_run` + a horizon timeout. **The defining feature of a thesis fund is its *invalidation*, not its entry:** a **breadth gate** (stand down if the rebound isn't broad) and a **horizon** (the bet has a deadline).

**`strategies/us-rebound/strategy.yaml`**
```yaml
schema_version: 1
id: us-rebound
version: "1.0.0"
catalog:
  name: "US Rebound — Q3 2026 Macro Thesis"
  emoji: "🇺🇸"
  archetype: trend_following          # accumulates on confirmation — honest mapping (no "thesis" archetype exists)
  sub_style: basket
  asset_classes: [xyz_equities, indices, btc_eth]   # HARD-FILTERED — every class it touches
  asset_scope: basket
  direction: long_only
  risk_level: moderate
  tier: advanced
  belief_plain: "Buys the U.S. stock-and-crypto complex while a 2026 growth rebound is confirming in price, and stands down if it isn't broad."
  thesis: "You believe U.S. growth reaccelerates in Q3 2026 — accumulate the U.S. risk-on complex while the rebound confirms across the board, on a hard deadline."
  tags: [macro, thesis, rebound, us-economy, risk-on]
requires: { runtime: ">=3.0.0" }
defaults: { auth_token_env: SENPI_AUTH_TOKEN }
# no `instances:` — flat, per §2. `main` is synthesized from the root runtime.yaml below,
# and its wallet_env comes from that recipe's `strategy.wallet: "${US_REBOUND_WALLET}"`.
```

**`strategies/us-rebound/runtime.yaml`** (key parts; `exit:` = `let_winners_run` with the horizon override)
```yaml
name: us-rebound-main
group: us-rebound
strategy: { wallet: "${US_REBOUND_WALLET}", slots: 9, default_leverage: 3, trading_risk: moderate }
scanners:
  - { name: position_tracker, type: position_tracker, interval_seconds: 10 }
  - name: us_rebound_signals
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 900
    timeout_seconds: 180
    state_history_max_count: 100
    inputs:
      universe: ["xyz:SP500","xyz:XYZ100","xyz:NVDA","xyz:AMD","xyz:MSFT","xyz:JPM","xyz:CAT","BTC","ETH"]
      minScore: 5
      breadthMin: 4
      marginPct: 10          # PERCENT of withdrawable in (0,100]; 10 = 10%
      rsiMaxLong: 72
      horizonEndIso: "2026-10-01T00:00:00Z"
    signal_data_schema:
      score: { type: number }
      direction: { type: string }
      reasons: { type: array, required: false }
      breadth: { type: number, required: false }
actions:
  - { name: position_tracker_action, action_type: POSITION_TRACKER, decision_mode: rule, scanners: [position_tracker] }
  - { name: us_rebound_entry, action_type: OPEN_POSITION, decision_mode: rule, scanners: [us_rebound_signals],
      params: { order_type: FEE_OPTIMIZED_LIMIT, fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 60 } },
      context: [ { type: signal, scanner: us_rebound_signals } ] }
exit:
  engine: dsl
  dsl_preset:                                   # let_winners_run, with the only thesis-specific tweak:
    hard_timeout: { enabled: true, interval_in_minutes: 144000 }   # ~100d -> the Q3-2026 deadline
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
risk:
  guard_rails: { drawdown_halt_pct: 22, daily_loss_limit_pct: 12, max_entries_per_day: 9, cooldown_seconds: 3600, drawdown_reset_on_day_rollover: true }
```

**`strategies/us-rebound/scanners/scoring.py`**
```python
def confirm_rebound(c4, c1d, inputs):
    """A name confirms the rebound: uptrend on both timeframes, with RSI room. Pure."""
    if len(c4) < 6 or len(c1d) < 6: return None
    if _trend(c4) != "UP" or _trend(c1d) != "UP": return None      # both agree -> a real leg up
    rsi = _rsi(c4)
    if rsi > inputs.get("rsiMaxLong", 72): return None             # don't chase the top
    score = 3 + (1 if _trend_strength(c4) > 0.6 else 0) + (1 if rsi < 60 else 0)
    return {"score": score, "direction": "LONG",
            "reasons": [f"4h_up_{_trend_strength(c4):.0%}", f"rsi_{rsi:.0f}"]}
# _trend / _trend_strength / _rsi: standard candle math, unit-tested.
```

**`strategies/us-rebound/scanners/scan.py`**
```python
import sys, time, scoring

def scan(inputs, ctx):
    # INVALIDATION 1 - horizon: past Q3 2026 -> stop accumulating.
    h = inputs.get("horizonEndIso")
    if h and time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) >= h:
        return []

    universe    = inputs.get("universe", [])
    min_score   = int(inputs.get("minScore", 5))
    breadth_min = int(inputs.get("breadthMin", 4))
    base_pct    = float(inputs.get("marginPct", 10))    # PERCENT in (0,100]; 10 = 10%

    confirmers = []
    for asset in universe:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data",
            {"asset": asset, "candle_intervals": ["4h", "1d"],
             "dex": "xyz" if asset.lower().startswith("xyz:") else ""})
        if not md: continue
        c = (md.get("data", md) or {}).get("candles", {})
        th = scoring.confirm_rebound(c.get("4h", []), c.get("1d", []), inputs)
        if th and th["score"] >= min_score:
            confirmers.append({**th, "asset": asset})

    breadth = len(confirmers)
    if breadth < breadth_min:            # INVALIDATION 2 - breadth: not broad -> don't add
        return []

    conv = min(1.5, 1.0 + 0.1 * (breadth - breadth_min))   # broader rebound -> higher conviction, capped
    return [{
        "asset": p["asset"], "direction": "LONG",
        "marginPct": round(base_pct * conv, 4),            # INTENT - runtime sizes the dollars
        "data": {"score": p["score"], "direction": "LONG", "reasons": p["reasons"], "breadth": breadth},
    } for p in confirmers]
```

**Ship it:**
```
validate_strategy.py strategies/us-rebound          # advisory lint (this one DOES take the package dir)
openclaw senpi validate strategies/us-rebound      # THE GATE — PASS before ops. The package root: flat, per the manifest above
deploy.py create us-rebound --budget 200 ; openclaw senpi deploy status   # `overall: live` is the gate
```
…then confirm it **emits** on a tick where ≥4 names confirm — not just that it ticked.

**The portable lesson:** building any strategy = walk the 7 decisions → copy the matching archetype row → write the edge in `scoring.py` → name a DSL preset → fill the catalog facets from the glossary. The thesis fund's breadth+horizon, a scalp's tight preset, a follower's derived universe — those are *cells*, not different frameworks. And whatever you build, remember the creed: **every guess fails silently — anchor on the references and confirm it operates.**
