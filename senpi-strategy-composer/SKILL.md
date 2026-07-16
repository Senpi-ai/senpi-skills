---
name: senpi-strategy-composer
description: >-
  Turn a trading idea — or a lay thesis ("I want to be short the Iran war") —
  into a running, verified Senpi strategy, by driving the Strategy Composer: a
  registry-backed toolchain that GENERATES a complete valid strategy graph, checks
  it with one GREEN/RED verdict, and stages it for deploy. Use for "build a
  strategy", "create/design a strategy", "I have a trading idea", "make a strategy
  from this", or ANY strategy that needs a supervised exit (stop-loss / trailing
  stop / profit-lock). ALSO use the moment a user asks WHAT IS POSSIBLE — "what
  can I build?", "is X possible?", "can Senpi detect/trade Y?" — to fetch the node
  catalog (the world) and answer from it instead of guessing. Experimental POC
  successor to senpi-strategy-author. NOT for picking an existing strategy
  (senpi-strategy-discover) or installing/monitoring one (senpi-strategy-ops).
license: Apache-2.0
metadata:
  author: Senpi
  version: "0.1.0"
  status: experimental
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Composer — idea → verified strategy, driven by the composer

You do NOT hand-write scanners, YAML schemas, or DSL presets. The **composer** owns all
construction: you elicit the user's intent into a typed answers file, `composer new`
generates a complete, valid graph, and `composer check` issues the verdict. Your only
irreplaceable jobs are **the interview**, **passing the user's edge verbatim**, and
**reading the composer's verdicts back to the user honestly**. Everything about what nodes
exist and how they wire is fetched from the composer at point of use — never recalled.

## Invocation

Real form inside a claw:
```
openclaw senpi composer <verb> ...
```
Verbs: `catalog`, `describe <node>`, `new`, `check <graph>`, `deploy <graph>`,
`status <graph>`. (Ignore any `uvx --with … python -m composer.cli` form you may see in
composer/AUTHORING.md — that is a dev-only convenience, not the claw invocation.)

## When to engage — and the boundary with the sibling skills

- **Build / design a new strategy, or a lay thesis to turn into one** → you, here.
- **"What can I build? Is X possible?"** → you, but FETCH THE CATALOG FIRST (below).
- **"Pick / recommend an existing strategy for me"** → `senpi-strategy-discover`.
- **"Deploy / monitor / close a named strategy"** → `senpi-strategy-ops`.
- **Anything with a supervised exit is composed here**, never stood up with a raw MCP
  `strategy_create*` call (that path carries no supervised exit and registers no named
  strategy — a confirmed silent failure).
Ambiguous? Ask one disambiguating question before acting.

## Capability questions — fetch the world, don't guess

The moment the user asks what is possible, run `openclaw senpi composer catalog` (~1.2k
tokens: every node name + one-liner + params, grouped source / indicator / filter / scorer /
ranker / emitter / stateful). Answer strictly from it — it is the closed world of what can
be built. If they ask about a capability the catalog lacks, say so plainly (see Gap reports);
do not imply it is buildable. This is the earliest, cheapest way to set honest expectations.

## Elicitation → the answers file (this is your real work)

The composer needs the **7 decisions that are the type-signature of `scan()`**, captured in
an answers YAML file. This is a contract, not a script — draw it out one question at a time,
in plain language, mining the opening ask for anything already stated:

1. **Archetype** — `trend_momentum` | `thesis_fund` | `classifier` (unknown → off-map skeleton).
2. **Universe** — fixed `whitelist` (trend_momentum) · `long_basket`/`short_basket` legs
   (thesis_fund) · `discover: true` + filters (classifier derives it from the live board).
3. **Intervals** — candle intervals, e.g. `["1h","4h"]`.
4. **Cardinality** — `max_positions: 1` (single best) or `>1` (rank a pool, cap to slots).
5. **Memory** — `ttl_seconds` signal-dedup window.
6. **Risk + protection** — `margin_pct` (PERCENT of withdrawable, (0,100]) + `leverage`, and
   the **exit protection preset**: `protected_standard` (balanced, the DEFAULT) ·
   `protected_tight` (cut failures fast) · `protected_wide` (let a runner breathe). PROTECTION
   IS ON BY DEFAULT — every emitted strategy is a complete runtime with a DSL exit (two-phase
   trailing stop + time-cuts). Only pass `unmanaged: true` if the user KNOWINGLY wants no exit
   (naked positions that can run to liquidation) — `check` is RED (CMP120) otherwise, and the
   opt-out prints a loud warning. Optional guard rails: `drawdown_halt_pct`,
   `max_consecutive_losses`, `cooldown_seconds`, `max_entries_per_day`.
7. **Edge** — the thesis in the user's own plain words. **Carried VERBATIM; nothing parses
   it.** This is the one thing only you can supply — never sanitize or interpret it away.

**Users are not always traders.** A lay thesis ("against Trump", "the Iran war") is a valid
input — YOUR job is the translation layer: thesis → universe + direction (e.g. a macro
long/short → `thesis_fund` with `long_basket`/`short_basket`). Questions stay in plain
language; the answer domains come from the catalog, so you cannot elicit an impossible
requirement without the composer flagging it. Reflect each answer back before moving on;
replay the full captured spec and get a "yes" before generating.

Write the answers file to a scratch dir. Minimal shape (every field has a sane default, so a
short file is enough):
```yaml
archetype: thesis_fund
strategy: my_strat
universe: { long_basket: ["BTC","ETH"], short_basket: ["SOL"] }
intervals: ["4h"]
max_positions: 2
ttl_seconds: 240
exit_preset: protected_standard   # protection-by-default; omit to bind the default, or set tight/wide
risk: { margin_pct: 5.0, leverage: 3.0 }
edge: >
  The user's thesis, verbatim and specific.
requires: []        # name a capability that may not exist yet -> gap report, nothing built
```

## Generate → edit → verify

1. **Generate the anchor:**
   `openclaw senpi composer new my_strat --archetype thesis_fund --answers <file> -o <dir>`
   → a COMPLETE, VALID graph with mechanically-bindable values filled and bespoke slots
   marked `# TODO(edit)` (working defaults, so it validates unedited). `new` refuses to
   clobber a hand-edited graph without `--force`.
2. **Edit only what needs deciding** — the `TODO(edit)` slots (weights, thresholds), though
   you have full edit rights over the whole graph. For bespoke scoring/classification the
   registry can't express, add a **`kind: pure_fn` node**: typed `inputs:`/`outputs:`, exactly
   one top-level `def`, `math`/`statistics` only (no I/O, no ctx/MCP), and inline `tests:`.
   Need a node's real ports before wiring it? `openclaw senpi composer describe <node>` —
   never guess ports from the catalog one-liner.
3. **Check:** `openclaw senpi composer check <graph>` → ONE verdict over five stages
   (validate · pure_fn tests · compile · smoke · **runtime_validate**). GREEN = ready. RED =
   located, actionable `CMPxxx` errors (node / port / line, or a runtime.yaml dot-path for the
   `CMP1xx` runtime-unit class + fix hint). The `runtime_validate` stage runs the REAL Zod + DSL
   validator over the emitted `runtime.yaml` AND enforces protection-by-default (CMP120). Fix
   exactly what it names, re-check, repeat until GREEN — don't rewrite unrelated parts.
4. **Deploy (stage the COMPLETE unit):** `openclaw senpi composer deploy <graph> -o <dir>/dist`
   — hard-gated on a GREEN check; stages a standalone strategy PACKAGE TREE (`strategy.yaml` +
   `main/runtime.yaml` with the DSL exit + `main/scanners/scan.py` + graph copy + `manifest.json`),
   does NOT install. The unit is complete on its own — there is no host runtime.yaml to borrow.
   Hand the staged dir + the `id` to **senpi-strategy-ops**, which reads `strategy.yaml.instances[]`,
   mints one funded wallet per instance, and installs each runtime.yaml onto its wallet.
5. **Resume anytime:** `openclaw senpi composer status <graph>` — the file IS the state; it
   reports your lifecycle position and names the next verb. No journal to reconcile.

## Gap reports = the honest refusal

If the answers or an edit reference a data source / capability the registry lacks, `new` (or
`check`) writes nothing and prints a structured **gap report**: what was asked, what kind, why
it isn't buildable, and the nearest registry alternatives. Relay that to the user as-is — it
is the correct outcome for something not yet buildable, not a bug to code around. Tell them
what IS possible instead (from the catalog), and that adding the capability is a
registry-side change, not something to fake in a scanner. Same for ambiguity: if a brief
lacks something you can't responsibly guess (which assets, long/short, how much risk), ask —
don't invent a plausible-sounding specific.
</content>
