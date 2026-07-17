---
name: senpi-strategy-composer
description: >-
  Turn a trading idea — or a lay thesis ("I want to be short the Iran war") —
  into a running, verified Senpi strategy, AND operate it end to end, by driving
  the Strategy Composer: a registry-backed toolchain that GENERATES a complete
  valid strategy graph, checks it with one GREEN/RED verdict, stages a
  self-contained unit, installs it onto the box, and tears it down safely. Use for
  "build a strategy", "create/design a strategy", "I have a trading idea", "make a
  strategy from this", "which strategy should I run", "I want a <theme> strategy",
  or ANY strategy that needs a supervised exit (stop-loss / trailing stop /
  profit-lock) — AND for operating one you built here: "what's my strategy doing?",
  "close/stop my strategy", "update it". ALSO use the moment a user asks WHAT IS
  POSSIBLE — "what can I build?", "is X possible?", "can Senpi detect/trade Y?" — to
  fetch the node catalog (the world) and answer from it instead of guessing. The
  composer now owns the whole path: author → check → deploy → fund → install →
  status → close. Experimental POC successor to senpi-strategy-author,
  senpi-strategy-discover, and senpi-strategy-ops (all superseded).
license: Apache-2.0
metadata:
  author: Senpi
  version: "0.3.0"
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
`fund <staged-dir> --budget N`, `install <staged-dir> [--wallet 0x…]`, `status <graph> [--state-dir …]`, `close <target>`.
(Ignore any `uvx --with … python -m composer.cli` form you may see in
composer/AUTHORING.md — that is a dev-only convenience, not the claw invocation.)

## When to engage — you own the whole lifecycle now

There is no separate discover or ops skill anymore; the composer owns the entire path.

- **Build / design a new strategy, or a lay thesis to turn into one** → here.
- **"What can I build? Is X possible?"** → here, but FETCH THE CATALOG FIRST (below).
- **"Which strategy should I run? / I want a <theme> strategy"** → here — route the theme to an
  archetype + the catalog and COMPOSE it (see Routing a theme, below). No pre-built picker.
- **Operate a strategy you built here — "what's it doing?", "close it", "update it"** → here,
  via `status` / `close` (see Operate, below).
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

## Routing a theme → an archetype (there is no pre-built picker)

When the ask is "which strategy should I run" or "I want a `<theme>` strategy", don't reach for a
catalog of ready-made strategies — there isn't one; you COMPOSE. Route the theme to one of the
four archetypes, then run the interview:
- directional trend on names the user names → `trend_momentum`; a range-break / fresh-highs hawk →
  `breakout`; a macro long/short pair ("long AI, short memecoins") → `thesis_fund`; "find them for
  me" off the live board → `classifier` (or `breakout` with `discover: true`).
Fetch the catalog (above) to ground what the theme can actually key on, then compose from there.

## Elicitation → the answers file (this is your real work)

**Verify buildability BEFORE the question burn.** Map the thesis to catalog nodes + an archetype first
(`composer catalog`/`describe`); if the mapping needs a hatch (pure_fn / surgical edit / mcp_read), know WHICH
one before you ask the user six questions — don't elicit a full spec you then can't build.

The composer needs the **7 decisions that are the type-signature of `scan()`**, captured in
an answers YAML file. This is a contract, not a script — draw it out one question at a time,
in plain language, mining the opening ask for anything already stated:

1. **Archetype** (4) — `trend_momentum` (fixed universe → indicators → hard-gates + weighted
   scorer) · `breakout` (a directional range-break as the hard gate; fixed basket OR discovered
   universe) · `thesis_fund` (fixed long/short legs) · `classifier` (a pure_fn direction on a
   discovered set). Unknown → off-map skeleton.
2. **Universe** — fixed `whitelist` (trend_momentum / breakout) · `long_basket`/`short_basket`
   legs (thesis_fund) · `discover: true` + filters (classifier / breakout derive it from the live
   board). `trend_momentum` is fixed-universe only — a `discover` answer is rejected with a
   teaching error naming breakout/classifier, never a silent empty universe.
3. **Intervals** — candle intervals, e.g. `["1h","4h"]`.
4. **Cardinality** — `max_positions: 1` (single best) or `>1` (rank a pool, cap to slots).
5. **Memory** — `ttl_seconds` signal-dedup window. Three TTL-ish knobs, never conflate: `ttl_seconds` = signal
   DEDUP window · `interval_seconds` = scan CADENCE · `valid_for_seconds` = per-signal envelope TTL.
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

**Gate selection now flows INTO generation — not a post-generate hand-edit.** When the edge names
conditions that must ALL hold ("only when volume confirms AND smart money leans in AND it just
broke out"), capture them in the answers so `new` GENERATES the right scorer — don't generate a
default and reconcile it by hand-editing the graph. Two optional answer fields do this:
- **`scoring:`** — re-select the hard-vs-weighted split: `required_gates` (feature ids that must
  ALL pass), `weights` (feature id → weighted-sum weight), `min_score`. Omit a field to keep the
  archetype default; set it to override.
- **`indicators:`** — ADD registry indicator nodes (`id`, `node`, `with`) so they become
  addressable by id from `scoring`. For a NUMERIC condition ("fresh within 5 bars", "don't chase a
  >15% runaway"), add a `threshold_gate` (`op` lt|lte|gt|gte, `value`) and CHAIN it off the raw
  feature with `source:` — a raw number has no correct truthiness, so this turns it into a real
  `1.0`/`0.0` gate.
A `scoring` name matching no feature node, or an `indicators` node the registry lacks, is a LOUD
generation error — never a silent drop. Hand-editing the graph is the escape hatch, not the norm.

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
2. **Compute is never gated; only the seams are.** The hard limits are the graph's seams: data IN
   (source nodes are the only place I/O happens — a data feed with no source node is a real wall;
   report it, don't fake it in a scanner), the signal envelope OUT, and execution (actions/DSL —
   the runtime's job). Everything between the seams — gates, scoring, classification, the user's
   EDGE — is yours to build. Prefer an existing registry ingredient when one fits (don't reinvent
   `volume_surge_ratio`; the catalog is tested, reusable, and legible), and most gate/indicator
   intent now flows straight from the answers (`scoring` + `indicators` + `threshold_gate`, above).
   But a **`kind: pure_fn` node is a first-class path, not a last resort** — bespoke logic the
   vocabulary doesn't have is exactly what it exists for: typed `inputs:`/`outputs:`, exactly one
   top-level `def`, `math`/`statistics` only (no I/O, no ctx/MCP), inline `tests:`. Hand-editing
   the graph is likewise fine when surgical: stay inside the node-graph shape, and `describe` every
   node you wire (`openclaw senpi composer describe <node>` — never guess ports from the catalog
   one-liner). **For reads: reach for a curated source node FIRST** (`catalog`/`describe` before
   anything). When none exposes the read you need, the **`mcp_read` escape hatch** calls an
   arbitrary MCP tool (must be snapshot-listed and read-only — mutations are refused) and emits a
   `raw` payload you MUST normalize in a `pure_fn` (a `raw` wire feeds nothing else). It is a LAST
   RESORT: say in the `edge:` WHAT you're fetching and WHY the curated path can't express it
   (`check` annotates the use anyway, and flags when a first-class node already wraps the tool —
   prefer it). **NEVER invent a capability** — if a tool or data feed doesn't exist, do not
   fabricate it or fake it in a scanner. A gap report means a missing SOURCE capability that no
   hatch reaches — surface it to the Senpi team as an add request, and come back to the user with
   the nearest thing that IS possible from the catalog; only when there is no path at all, tell
   them plainly what cannot be done and why.
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
5. **Wallet — `composer fund` creates + funds it (never improvise a budget):** first AGREE the
   budget with the user explicitly (never invent one; ≥ $100), then
   `openclaw senpi composer fund <staged-dir> --budget N`. It preflights your visible balance,
   creates the strategy wallet (MCP `strategy_create_custom_strategy`, DSL-protected runtime still
   comes from `install`), waits for it to reach ACTIVE, and is SAFE to re-run while funding settles
   (re-run IS the poll). If it reports a shortfall or a wait: funds moved from Hyperliquid to EVM can
   LAG ~a minute before the backend sees them — just wait and re-run; do NOT go bridging in pieces.
   Never call raw `strategy_create*` to stand up the wallet by hand.
6. **Install — the ONE sanctioned box-side path:**
   `openclaw senpi composer install <staged-dir>` — omit `--wallet` and it auto-binds the wallet
   `composer fund` just funded (or pass `--wallet 0x…` explicitly to override; a 0x… 40-hex address
   from `strategy_list`; pass a graph file plus `-o <staging-dir>` if you don't have the staged dir
   path). It copies the unit to an immutable, content-addressed dir under senpi-state,
   binds the wallet into the COPY (staged source stays pristine), and invokes
   `openclaw senpi runtime create -p` **itself**. This verb owns the entire box-side step. Re-install
   with the same content + wallet is a safe no-op (`ALREADY_INSTALLED`). On failure it prints a
   `CMP2xx` teaching error naming the exact next command — **READ it and DO what it says; do not
   improvise around the verb.**
7. **Verify:** `openclaw senpi runtime list` shows the installed runtime (id `<strategy>-<hash8>`).
   ONE running runtime per wallet. (To UPDATE or tear down, see Operate, below.)
8. **Resume anytime:** `openclaw senpi composer status <graph>` — the file IS the state; it reports
   your lifecycle position and names the next verb. Add `--state-dir` to EXTEND the chain onto the
   box: `staged → landed → registered → running → live`, distinguishing which hash is
   installed/running vs a merely-landed prior version. No journal to reconcile.

### Never improvise authoring — keyed to a real dev-test failure
- **The composer artifact chain (graph → check → deploy → install) is the ONLY authoring path.** Never
  hand-write `scan.py`, scanner modules, or `runtime.yaml` outside the staged tree — even when the composer
  seems unable to express the thesis (doing so loses vendoring, the check oracle, the install path, and the
  teaching errors). The sanctioned hatches below — `pure_fn`, surgical graph edits, `mcp_read` — are IN-chain:
  graph-level, still pass `check`, allowed.
- **Climb the hatch ladder before concluding the vocabulary lacks something:** (1) re-check the registry —
  `composer catalog` + `composer describe <node|kind|port_type|archetype>`; a capability's absence from your
  memory or from ONE CLI surface is not absence — `describe pure_fn` is the authoring contract for bespoke
  compute; (2) `kind: pure_fn` / surgical graph edits / `mcp_read` for unwrapped reads (all above); (3) only if
  genuinely inexpressible at a hard seam: tell the user plainly what IS buildable, offer the nearest
  approximation with the difference stated honestly, and record a gap report for maintainers. Never a dead-end
  refusal, never a bypass.
- **A tool error like "unknown node" for something the catalog's prose names → CHECK THE OTHER SURFACE**
  (`describe`/`catalog`), not proof the capability is fiction.

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

## Operate — status, teardown, update (this skill owns it now)

The composer absorbs what `senpi-strategy-ops` used to do — there is no ops handoff.

- **Status:** `openclaw senpi composer status <graph> --state-dir <state-dir>` walks the lifecycle
  chain `staged → landed → registered → running → live` from artifacts + one read-only `runtime
  list`. Liveness is HONEST: `runtime list` proves only the process is up, not that `scan()` has
  ticked — "running" ≠ "operating". Confirm real ticking with `openclaw senpi state -r <id> --json`.
- **Teardown:** `openclaw senpi composer close <target>` is the ONLY sanctioned teardown. It STOPS
  the runtime first, CONFIRMS it is gone, and ONLY THEN closes the strategy (flattens all positions,
  returns funds). It is idempotent and submit-only — `strategy_close` is async, so re-run it to POLL
  until `closed`. `<target>` = a strategy name / canonical hash dir / runtime id / `0x…` wallet.
  **NEVER call raw MCP `strategy_close` while a runtime is live** — it strands the runtime trading a
  wallet the server is tearing down (a hard invariant `close` enforces: if the runtime won't stop, it
  WITHHOLDS the close and fails loud rather than strand).
- **Update:** re-author → re-check → re-deploy (new content ⇒ new hash) → `composer close` the OLD
  strategy → `install` the new one. Don't hand-delete the runtime and leave the strategy open.

## Gap reports = the honest refusal

If the answers or an edit reference a data source / capability the registry lacks, `new` (or
`check`) writes nothing and prints a structured **gap report**: what was asked, what kind, why
it isn't buildable, and the nearest registry alternatives. Relay that to the user as-is — it
is the correct outcome for something not yet buildable, not a bug to code around. Tell them
what IS possible instead (from the catalog), and that adding the capability is a
registry-side change, not something to fake in a scanner. Same for ambiguity: if a brief
lacks something you can't responsibly guess (which assets, long/short, how much risk), ask —
don't invent a plausible-sounding specific.
