---
name: senpi-strategy-composer
description: >-
  Turn a trading idea — or a lay thesis ("I want to be short the Iran war") —
  into a running, verified Senpi strategy, by driving the Strategy Composer: a
  registry-backed toolchain that GENERATES a complete valid strategy graph, checks
  it with one GREEN/RED verdict, stages a self-contained unit, and installs it onto
  the box. Use for "build a
  strategy", "create/design a strategy", "I have a trading idea", "make a strategy
  from this", or ANY strategy that needs a supervised exit (stop-loss / trailing
  stop / profit-lock). ALSO use the moment a user asks WHAT IS POSSIBLE — "what
  can I build?", "is X possible?", "can Senpi detect/trade Y?" — to fetch the node
  catalog (the world) and answer from it instead of guessing. Experimental POC
  successor to senpi-strategy-author. NOT for picking an existing strategy
  (senpi-strategy-discover) or monitoring/closing an already-running one (senpi-strategy-ops).
license: Apache-2.0
metadata:
  author: Senpi
  version: "0.2.0"
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
`install <staged-dir> --wallet 0x…`, `status <graph>`. (Ignore any `uvx --with … python -m
composer.cli` form you may see in
composer/AUTHORING.md — that is a dev-only convenience, not the claw invocation.)

## When to engage — and the boundary with the sibling skills

- **Build / design a new strategy, or a lay thesis to turn into one** → you, here.
- **"What can I build? Is X possible?"** → you, but FETCH THE CATALOG FIRST (below).
- **"Pick / recommend an existing strategy for me"** → `senpi-strategy-discover`.
- **Monitor, close, or manage an already-running strategy** → `senpi-strategy-ops`.
  (You now own the full build→check→deploy→install path here; ops still owns teardown.)
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

## Generate → check → deploy → install

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
3. **Check — GREEN now means INSTALLABLE:** `openclaw senpi composer check <graph>` → ONE verdict
   over five stages (validate · pure_fn tests · compile · smoke · **runtime_validate**). GREEN =
   ready to deploy AND install. RED = located, actionable `CMPxxx` errors (node / port / line, or a
   runtime.yaml dot-path + fix hint). The `runtime_validate` stage runs the REAL Zod + DSL validator
   over the emitted `runtime.yaml` and now enforces the checks that used to fail only when the
   runtime booted: protection-by-default (CMP120), position-tracking wiring (CMP112), duplicate
   scanner/action names (CMP113), unresolvable action-prompt placeholders (CMP114), unbound `${VAR}`
   (CMP115). Fix exactly what it names, re-check, repeat until GREEN — don't rewrite unrelated parts.
4. **Deploy — stage the PRISTINE, self-contained unit:**
   `openclaw senpi composer deploy <graph> -o <dir>/dist` — hard-gated on a GREEN check; stages a
   standalone strategy PACKAGE TREE (`strategy.yaml` + `main/runtime.yaml` with the DSL exit +
   `main/scanners/scan.py` + `main/scanners/strategy_primitives/` — a vendored primitives snapshot,
   so scan.py imports NO composer code and runs on a box with none of the checkout — + graph copy +
   `manifest.json` hashing every artifact plus the oracle verdict). Does NOT install. The staged
   tree is PRISTINE: the wallet is a `${..._WALLET}` placeholder, left intact. **NEVER hand-edit a
   staged unit** — install re-verifies every artifact against the manifest and REFUSES a tampered one
   (CMP201). To change anything, re-author → re-check → re-deploy.
5. **Wallet — create/verify it conversationally (MCP); install binds it:** the runtime binds ONE
   strategy wallet, which must be **ACTIVE**. Create or find it with the MCP tools
   `strategy_create_custom_strategy` (create) / `strategy_list` (read its `strategyWalletAddress` +
   status); funding/budget stays an agent↔user conversation, as always. This MCP call mints only the
   WALLET — it does NOT stand up the strategy (that path carries no DSL exit); the DSL-protected
   runtime comes from `composer install` binding the composed unit onto the wallet. Wait until the
   wallet reaches ACTIVE before installing.
6. **Install — the ONE sanctioned box-side path:**
   `openclaw senpi composer install <staged-dir> --wallet 0x…` (`--wallet` REQUIRED — a 0x… 40-hex
   address from `strategy_list`; pass a graph file plus `-o <staging-dir>` if you don't have the
   staged dir path). It copies the unit to an immutable, content-addressed dir under senpi-state,
   binds the wallet into the COPY (staged source stays pristine), and invokes
   `openclaw senpi runtime create -p` **itself**. This verb owns the entire box-side step. Re-install
   with the same content + wallet is a safe no-op (`ALREADY_INSTALLED`). On failure it prints a
   `CMP2xx` teaching error naming the exact next command — **READ it and DO what it says; do not
   improvise around the verb.**
7. **Verify + update:** `openclaw senpi runtime list` shows the installed runtime (id
   `<strategy>-<hash8>`). ONE running runtime per wallet. To UPDATE a strategy: re-author → re-check
   → re-deploy (new content ⇒ new hash) → delete the old runtime (`openclaw senpi runtime delete
   <id>`) → install the new one.
8. **Resume anytime:** `openclaw senpi composer status <graph>` — the file IS the state; it reports
   your lifecycle position and names the next verb. No journal to reconcile.

### Never improvise the install — each rule below is a real dev-box failure
- **No `--content` installs.** It drops the scanner dir the external scanner needs. `composer install`
  always uses PATH mode (`-p`) and calls the frozen consumer for you — never run `openclaw senpi
  runtime create` by hand.
- **No hand-crafted or hand-edited `runtime.yaml`** (staged or copied). The unit is generated and
  manifest-verified; editing it trips CMP201 (tampered unit) or CMP115 (leftover placeholder).
- **No raw `strategy_create*` + manual runtime setup when a staged unit exists.** Use the wallet the
  MCP call minted as `--wallet` for `composer install`; don't rebuild the strategy by hand.
- **No `pip install` to "fix" a missing import.** The unit vendors its primitives and is
  self-contained by construction — a missing import means a bad unit, so re-deploy, don't patch the box.
- **If install fails, read the `CMPxxx` error and follow its fix.** Don't route around the verb.

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
