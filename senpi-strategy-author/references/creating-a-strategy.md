# Creating a Strategy from Scratch — Senpi Runtime 3.0

> **The one rule that governs everything below:** *every guess in this system fails silently.* A wrong MCP field → a scanner that ticks clean and emits nothing. A drifted DSL → an exit that doesn't fire. A made-up catalog facet → a strategy nobody is ever shown. So: **anchor on the references** (the MCP I/O guide, `dsl-presets.yaml`, the discovery `glossary.yaml`), and **confirm it actually operates** — never assume.

---

## 1. Mental model

A strategy is a **deployable package, not a skill.** You author two things — the **thesis** (what to believe, how to score it) and the **guardrails** (how to exit, how much risk). Runtime 3.0 owns everything operational: it **spawns and supervises** your scanner, calling `scan(inputs, ctx)` every `interval_seconds`, and owns sizing, execution, exits, slots, risk gates, state durability, and retries.

Your code is **one read-only, pure function**: it reads data and returns candidate signals. It **never** trades, sizes in dollars, loops, sleeps, or writes files. There is no daemon, no `push_signal`.

## 2. The package

```
strategies/<id>/
  strategy.yaml                 # identity, catalog facets, instances + funding
  <instance>/
    runtime.yaml                # the deterministic spec: inputs, entry action, DSL exit, risk
    scanners/
      scan.py                   # scan(inputs, ctx) -> list[dict]   (reads + emits)
      scoring.py                # pure thesis math — no I/O, unit-testable
```
One instance binds to one wallet. A long book + a short book, or a swing + a scalp leg, = **multiple instances**.

**Single-instance? Build it FLAT** — `strategy.yaml` + `runtime.yaml` + `scanners/` at the package root,
no `instances:` list and no `<instance>/` dir: the deployer (strategy-ops v2.4.0+) synthesizes the
canonical `main` instance for you. The nested `<instance>/` layout above is only *required* for
multi-instance strategies. Either way, `deploy.py validate strategies/<id>` tells you in one pass
whether the package is deploy-ready.

## 3. Division of labor — memorize this

| **You own** | **Runtime 3.0 owns** |
|---|---|
| Universe, signal, score | Scheduling + supervising `scan()` (restarts a crashed child) |
| Sizing **intent** (`marginPct` / weight) | Converting intent → **dollars** off the live (reconciled) account |
| Exit shape (a named DSL preset) | Execution, slot caps, position dedup |
| Risk limits (guard rails) | State durability (transactional), retries |
| Catalog facets | **Read-only enforcement** — any mutating tool raises `PermissionError` |

Two invariants fall out of this:
1. **`scan()` is read-only + pure + single-pass.** On *any* error, `return []` — never crash.
2. **You emit a sizing *intent* (`marginPct`/weight), not dollars.** The runtime computes `marginUsd` from the reconciled account value. Do **not** read the clearinghouse to size — that's the runtime's job in 3.0. **`marginPct` is a PERCENT in (0,100]** — `10` = 10%, sized `(marginPct/100) × withdrawable` (not a fraction: `0.10` = 0.1%).

## 4. The design space — the 7 decisions that define *any* strategy

Decide these in prose first; the files just encode them.

1. **Universe** — single asset · static basket · **dynamic** (rebuilt from `market_list_instruments`, volume floor + fresh-listing) · **derived** (names come from a leaderboard/cohort, not a list).
2. **Data** — candles (`market_get_asset_data`) · funding/OI (`market_get_funding_*`) · smart-money (`leaderboard_get_markets`, `discovery_*`) · cross-asset flow (`market_get_cross_asset_flows`).
3. **Edge** — trend-follow · mean-revert · breakout · relative-strength · copy/follow · cohort-divergence · event/new-listing · macro-thesis.
4. **Shape** — long-only / short-only / mixed-on-one-wallet = **1 instance** · independent long+short or distinct cadences = **multiple instances** (each its own wallet + `funding_share`).
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

> **Candle schema (`market_get_asset_data`):** each candle is keyed `t,o,h,l,c,v` (+ `T,s,i,n`) — short OHLCV, **string** values. Close is `c`; read fields as `float(candle["c"])`, not `candle["close"]` (no such key).

```python
def score(asset, candles, extra, inputs):    # candles/numbers in, thesis dict out
    if not _qualifies(...): return None
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
```yaml
name: <id>-<instance>          # REQUIRED linkage
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
instances:
  - { name: main, runtime: main/runtime.yaml, wallet_env: <ID>_WALLET, funding_share: 1.0 }
# multi-leg: one entry per instance; funding_share sums to 1.0; each a distinct wallet_env
```

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
- **Derived by `gen_catalog.py` (don't duplicate):** `assets`, `leverage_max`, `funding_split`, `cadence_seconds`/`time_horizon` (from cadence), `instance_count`, `max_slots`, `min_budget` (= `max(declared, 100 × instance_count)`).

## 9. Validate, smoke-test, deploy, confirm it *operates*

```
python3 senpi-strategy-author/scripts/validate_strategy.py strategies/<id>      # 0 errors
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget N            # wallet(s); $100/instance floor
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                        # re-run after interval_seconds
# teardown / redeploy:  close.py <id>  (flattens positions, returns funds)
```
**"running" ≠ "operating."** Don't trust `status: running`. Confirm the scanner has a **positive run count + a fresh `lastRunFinishedAt`** (`openclaw senpi state -r <id>-<instance> --json`), and that it **emits a non-empty set on a tick where it should** — `verify` proves it *ticked*, not that it produced a signal. This is an **agent-side check** — run it yourself; never ask the user "is it working?".

### The first smoke test — run it yourself, once, before you scale

The desk checks above catch *your* bugs. The first time the **live openclaw runtime runs your scanner** catches a different, higher-value class: the **contract / language mismatches between the authoring agent and the runtime** — a `runtime.yaml` key the runtime silently ignores, a `data{}` field it rejects, an MCP tool name/arg that doesn't exist, a `marginPct`/`leverage` the sizer reads differently. These surface *only* when the runtime itself executes your code, and they fail **silently**. So for every new strategy (and every new archetype), do this deliberately, by hand:

1. **Dry-run the plan** — `deploy.py create <id> --dry-run` + `deploy.py runtime <id> --dry-run`. Catches manifest / linkage / render errors with zero side effects (no wallet, no funds).
2. **Run `scan()` once against the live read-only MCP** — confirm it returns a **non-empty, correctly-shaped** list (right tool names, right field reads). Catches the MCP language gap at the desk.
3. **Deploy tiny, then read the runtime's OWN view of the first tick** — `create --budget <one-instance min>` → `runtime` → `openclaw senpi state -r <id>-<instance> --json`. Confirm the scanner **ran** *and the runtime **accepted** its signal* — not rejected for an undeclared `data{}` key, a non-positive `marginPct`, or a schema mismatch. The runtime reports those rejections in its state — **that is where a Claude↔openclaw language mismatch shows up loud** instead of as a silent `[]`.
4. **Green smoke test = one strategy went `scan` → signal → runtime-*accepted* → action, end to end.** A clean dry-run is **not** a green smoke test. Only after that do you scale — more budget, more instances, or porting siblings.

If anything mismatches, fix the **contract** (the field name, the `signal_data_schema`, the key the runtime expects), re-run the smoke test, *then* proceed. Budget time for this on every new strategy: **the first agent-run smoke test is where the contract meets reality.**

## 10. The author's checklist (the silent-failure guards)

- `scan()` single-pass + sync; read-only MCP only; `return []` on any error.
- Pure scoring in `scoring.py`; MCP + state in `scan.py`.
- **Never hardcode a ticker you didn't verify against the live list.** Every static `universe`/`asset`/`catalog.assets` entry must be a live HL instrument — a fake ticker silently no-trades (`market_get_asset_data` rejects it as an unknown coin — do not retry — and the scan skips it). Gate it: `validate_universe.py strategies/<id>` (and `deploy.py create` runs it as a preflight). Real index = `xyz:XYZ100`, *not* `xyz:NASDAQ`.
- **Two sub-DEXes, one wallet — spell the name the way each API spells it.** `leaderboard_get_markets` returns a *bare* token plus a separate `dex` field (`NVDA` + `dex: "xyz"`), while everywhere else the same name is written `xyz:NVDA`. Matching a bare token straight against a qualified one silently matches nothing, so a scanner that reads leaderboard rows must either check the row's `dex` or strip the `xyz:` prefix before comparing. Same wallet, two views: read positions per section — `for section in ("main", "xyz"): ch[section]["assetPositions"]` — never `assetPositions` off the top of `strategy_get_clearinghouse_state`, which is empty. The validator checks this by **positive evidence**: write the comparison however you like, but doing neither is flagged.
- Emit a **`marginPct` intent**, not dollars; `marginPct`/`leverage` top-level, not in `data{}`.
- Declare every `data{}` key in `signal_data_schema`.
- **Anchor on the references:** MCP fields → I/O guide; exit → a named preset; catalog facets → the glossary.
- Linkage: `group: <id>`, `name: <id>-<instance>`, `wallet_env` bound, `funding_share` sums to 1.0, package is `@senpi-ai/runtime`.
- Validate (0 errors) → deploy → **confirm it emits/operates**, not just "ticked."
- **Smoke-test the first deploy by hand** — dry-run → `scan()` once on live MCP → tiny deploy → confirm the runtime **accepted** a live signal (not just ticked) — *before* scaling. This is what catches the authoring-agent↔runtime language mismatches.

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
instances:
  - { name: main, runtime: main/runtime.yaml, wallet_env: US_REBOUND_WALLET, funding_share: 1.0 }
```

**`strategies/us-rebound/main/runtime.yaml`** (key parts; `exit:` = `let_winners_run` with the horizon override)
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

**`strategies/us-rebound/main/scanners/scoring.py`**
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

**`strategies/us-rebound/main/scanners/scan.py`**
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
validate_strategy.py strategies/us-rebound          # 0 errors
deploy.py create us-rebound --budget 200 ; deploy.py runtime us-rebound ; deploy.py verify us-rebound
```
…then confirm it **emits** on a tick where ≥4 names confirm — not just that it ticked.

**The portable lesson:** building any strategy = walk the 7 decisions → copy the matching archetype row → write the edge in `scoring.py` → name a DSL preset → fill the catalog facets from the glossary. The thesis fund's breadth+horizon, a scalp's tight preset, a follower's derived universe — those are *cells*, not different frameworks. And whatever you build, remember the creed: **every guess fails silently — anchor on the references and confirm it operates.**
