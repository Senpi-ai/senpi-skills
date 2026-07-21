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
  profit-lock) — AND for operating ANY running strategy, built here or not:
  "what's my strategy doing?", "is it healthy?", "why did(n't) it trade?",
  "close/stop it", "update it". Composer strategies appear as bare CUSTOM strategies on
  backend reads with NO obvious markers — NEVER conclude "no runtime exists" from a
  backend MCP read alone. ALSO use the moment a user asks WHAT IS
  POSSIBLE — "what can I build?", "is X possible?", "can Senpi detect/trade Y?" — to
  fetch the node catalog (the world) and answer from it instead of guessing. The
  composer now owns the whole path: author → check → deploy → fund → install →
  update → status → close. This is the ONE strategy-lifecycle skill on this box:
  discovery, authoring, ops, and teardown all live here — no other skill handles them.
license: Apache-2.0
metadata:
  author: Senpi
  version: "0.3.12"
  status: experimental
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Composer — idea → verified strategy, driven by the composer

You do NOT hand-write scanners, YAML schemas, or DSL presets. The **composer** owns all
construction: you elicit the user's intent into `composer new` inputs, `composer new`
generates a complete, valid graph, and `composer check` issues the verdict. Your only
irreplaceable jobs are **the interview**, **passing the user's edge verbatim**, and
**reading the composer's verdicts back to the user honestly**. Everything about what nodes
exist and how they wire is fetched from the composer at point of use — never recalled.

## Invocation

**Primary form — the registered `senpi_strategy` tool:** one string param `args` = the CLI argv after
`openclaw senpi` (e.g. `args: "composer status hype-btc-rv"`); it renders exactly what the CLI renders and
is the always-present front door. The exec CLI is the equivalent and still works identically:
```
openclaw senpi composer <verb> ...
```
Verbs: `catalog`, `describe <node>`, `new`, `check <graph>`, `deploy <graph>`,
`simulate <target> --from 30d`, `fund <target> --budget N`, `install <target> [--wallet 0x…]`,
`update <target>`, `status [<target>|--group X]` (bare = cross-strategy portfolio), `close <target|--group X>`.

**Preview before funding:** `composer simulate <target> --from 30d` replays the compiled scanners
over historical candles and prints the signal timeline ("would have entered/exited here"). Offer it
before `fund` on any new or edited strategy. Relay its LOW-FIDELITY banner verbatim — signals only:
no fills/fees/PnL, no DSL exit protection, non-candle sources frozen. Never present simulate output
as expected returns.

**Canonical layout — `graph.yaml` is THE strategy record.** Every verb defaults to
`<state>/composer/<strategy>/` (graph.yaml · wallet.json · staged/ · releases/<hash8>/); lifecycle
verbs accept the bare STRATEGY NAME. Never scatter graphs into tmp or `-v2` copies — edit the one
graph.yaml and re-run the chain; each installed release permanently embeds the graph that produced
it. `check` auto-upgrades a one-version-old `spec` block in place (relays its notice); anything
older is a regenerate, not an edit.

**Always invoke verbs with `--json`** — you need the machine-readable verdict (verdict/stages/errors, the
`DEFAULTED:` block, the GREEN `SUMMARY`); the human render can be buried under box noise.

## When to engage — you own the whole lifecycle now

There is no separate discover or ops skill anymore; the composer owns the entire path.

- **Build / design a new strategy, or a lay thesis to turn into one** → here.
- **"What can I build? Is X possible?"** → here, but FETCH THE CATALOG FIRST (below).
- **"Which strategy should I run? / I want a <theme> strategy"** → here — route the theme to an
  archetype + the catalog and COMPOSE it (see Routing a theme, below). No pre-built picker.
- **Operate ANY running strategy, built here or not — "what's it doing?", "is it healthy?",
  "why did(n't) it trade?", "close it", "update it"** → here, via `status` / `close` (see Operate,
  below). Composer strategies read back as bare CUSTOM on the backend — never infer "no runtime" from an MCP read.
- **Anything with a supervised exit is composed here**, never stood up with a raw MCP
  `strategy_create*` call (that path carries no supervised exit and registers no named
  strategy — a confirmed silent failure).
- **Wallet-only MANUAL trading (no graph, no runtime — the user places positions themselves)** →
  `composer fund <name> --manual --budget N` creates a MANUAL-kind strategy; direct MCP position
  writes are SANCTIONED on it, status/portfolio show its `kind`, and `composer close` does the reduced teardown.
- **Situational awareness FIRST** — before proposing ANY new build or teardown, run `composer
  status` / `openclaw senpi runtime list` to learn what already exists. Never propose a fresh build
  while a related strategy is running; never propose teardown before diagnosis.

Ambiguous? Ask one disambiguating question before acting.

## Capability questions — fetch the world, don't guess

The moment the user asks what is possible, run `openclaw senpi composer catalog` (~1.2k
tokens: every node name + one-liner + params, grouped source / filter / scorer / ranker /
emitter / stateful). Answer strictly from it — it is the closed world of DATA and PIPELINE;
COMPUTE is not a shelf, it is yours to write (`pure_fn` — see the catalog's compute block;
`describe indicators` lists the curated formulas when one fits exactly). If they ask about a capability the catalog lacks, say so plainly (see Gap reports);
do not imply it is buildable. This is the earliest, cheapest way to set honest expectations.

## Composition ladder — one thesis can be N strategies
When a thesis needs BOTH directions on one asset ("cut the loser, let the winner run"), independent
risk budgets per leg, or per-leg exit styles → never call it impossible: propose N strategies (each
its OWN wallet) operated as one logical position. Be explicit with the user: each wallet funds
separately (N× minimums), and cross-strategy PnL is viewed at the PORTFOLIO level. Link the legs
with `new --group <name>`: `status --group` / `close --group` then operate the set, and telemetry
aggregates per group. The group is a box-local LABEL (not visible in the web UI) with NO
coordinated behavior — no pooled funding, no rebalancing; group close is sequential, and the
warning it prints about remaining legs holding naked exposure is real — relay it.

## Routing a theme → an archetype (there is no pre-built picker)

When the ask is "which strategy should I run" or "I want a `<theme>` strategy", don't reach for a
catalog of ready-made strategies — there isn't one; you COMPOSE. Route the theme to one of the
archetypes, then run the interview:
- directional trend on names the user names → `trend_momentum`; a range-break / fresh-highs hawk →
  `breakout`; a macro long/short pair ("long AI, short memecoins") → `thesis_fund`; "find them for
  me" off the live board → `classifier` (or `breakout` with `discover: true`).
- a benchmark-relative / "X always beats Y" thesis (exit when the pair inverts) → `relative_value`
  (pairwise, exactly 2 assets), NOT `trend_momentum` — the exit is cross-asset, unobservable to the DSL,
  so it composes a thesis-driven exit pair (routing test below; `describe relative_value` for the shape).
- rotation / relative strength across a basket / "long the strongest, short the weakest" → `relative_value`
  cross_sectional member — rank a universe of ≥3 (or a discovered board) and rotate; exactly 2 = pairwise.
- fade the crowd / overextension / mean-reversion ("everyone's long, fade it"; "it ran too far") →
  `contrarian_fade` (members fade_crowd / fade_price inferred from the answers).
- "copy good traders / follow whales / mirror the leaderboard" → `copy_trading` (addresses given →
  named_traders; "find the best" → leaderboard_follow; `describe copy_trading`). BEFORE creating
  anything, PRESENT BOTH paths and let the USER choose: (a) **backend raw mirror** (MCP
  `strategy_create` with `traderAddress`) — mirrors one trader's every trade exactly, no DSL
  protection, no filters, inherits their current book; (b) **composer `copy_trading` archetype** —
  conviction aggregation across a cohort, hysteresis exits, DSL trailing-stop protection, custom
  filters/risk. RULE: a copy ask with ANY condition or filter attached (consistency windows,
  don't-enter-at-top, asset filters) is a strategy specification → composer, never a raw mirror.
Fetch the catalog (above) to ground what the theme can actually key on, then compose from there.

## Elicitation → `composer new` inputs (this is your real work)

**Verify buildability BEFORE the question burn.** Map the thesis to catalog nodes + an archetype first
(`composer catalog`/`describe`); if the mapping needs a hatch (pure_fn / surgical edit / mcp_read), know WHICH
one before you ask the user six questions — don't elicit a full spec you then can't build.

The composer needs the **7 decisions that are the type-signature of `scan()`**, fed to
`composer new` as flags or a THROWAWAY scratch answers file. This is a contract, not a script —
draw it out one question at a time, in plain language, mining the opening ask for anything already
stated:

1. **Archetype** (7) — `trend_momentum` (fixed universe → indicators → hard-gates + weighted
   scorer) · `breakout` (a directional range-break as the hard gate; fixed basket OR discovered
   universe) · `thesis_fund` (fixed long/short legs) · `classifier` (a pure_fn direction on a
   discovered set) · `relative_value` (pairwise = exactly 2, OR cross_sectional = rank a ≥3 /
   discovered universe and rotate strongest/weakest; a signed net-score entry PLUS a thesis-driven
   exit the DSL can't observe — see Exit composition below) · `contrarian_fade` (fade a
   crowded/overextended move; members fade_crowd / fade_price) · `copy_trading` (follow traders: fixed
   addresses or the leaderboard; universe DERIVED from the cohort's holdings, no whitelist).
   Unknown → off-map skeleton.
2. **Universe** — fixed `whitelist` (trend_momentum / breakout) · `long_basket`/`short_basket`
   legs (thesis_fund) · `discover: true` + filters (classifier / breakout derive it from the live
   board). `trend_momentum` is fixed-universe only — a `discover` answer is rejected with a
   teaching error naming breakout/classifier, never a silent empty universe.
3. **Intervals** — candle intervals, e.g. `["1h","4h"]`.
4. **Cardinality** — `max_positions: 1` (single best) or `>1` (rank a pool, cap to slots).
5. **Memory** — `dedup_seconds` signal-dedup window. Three TTL-ish knobs, never conflate: `dedup_seconds` = signal
   DEDUP window · `interval_seconds` = scan CADENCE · `valid_for_seconds` = per-signal envelope TTL.
   Trade FREQUENCY comes from `interval_seconds` + how often the edge conditions fire; NEVER shrink
   `dedup_seconds` to "trade more" — it only re-admits duplicate signals.
6. **Risk + protection** — `margin_pct` (PERCENT of withdrawable, (0,100]) + `leverage`, and
   the **exit protection preset**: `protected_standard` (balanced, the DEFAULT) ·
   `protected_tight` (cut failures fast) · `protected_wide` (let a runner breathe). PROTECTION
   IS ON BY DEFAULT — every emitted strategy is a complete runtime with a DSL exit (two-phase
   trailing stop + time-cuts). Only pass `unmanaged: true` if the user KNOWINGLY wants no exit
   (naked positions that can run to liquidation) — `check` is RED (CMP120) otherwise, and the
   opt-out prints a loud warning. Optional guard rails: `drawdown_halt_pct`,
   `max_consecutive_losses`, `cooldown_seconds`, `max_entries_per_day`. When `exit_signals` is
   present (a thesis-driven exit — see Exit composition) the unset default flips to `protected_wide`.
7. **Edge** — the thesis in the user's own plain words. **Carried VERBATIM; nothing parses
   it.** This is the one thing only you can supply — never sanitize or interpret it away.

### Exit composition — the thesis-driven exit pair (route with the guard FIRST)
**Routing guard (verbatim test):** exit expressible as this position's own price/PnL/time → DSL preset,
NO exit scanner. The compiled entry/exit pair is ONLY for exits the DSL cannot observe (cross-asset /
thesis-driven). Default stays single scanner + DSL.

**Three exit mechanisms, never conflated:** **on-order SL/TP** (static, exchange-side) vs **DSL** (our
dynamic engine — Hyperliquid has NO native dynamic stop) vs **signal exits** (thesis-driven closes via
a second, exit scanner). A signal exit NEVER replaces the DSL preset — they are complementary (CMP123).

When — and ONLY when — the thesis has a signal-driven exit, capture **`exit_signals`** (cadence + the
two hysteresis thresholds + cooldown; defaults `{interval_seconds: 300, enter_threshold: 3.0,
exit_threshold: 2.0, reentry_cooldown_seconds: 1800}` are strong — ask ONLY what the thesis implies).
And ASK the protection question: protected_wide recommended (the DSL is the safety net behind your
thesis exit; opting tighter may front-run it, opting out is at your own risk — CMP123 blocks
unmanaged). `describe close_signal_envelope` / `describe open_positions` carry the shape.

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

Pass these to `composer new` as flags, or write a THROWAWAY scratch answers file (`--answers`)
and DELETE it after `new` — it is TRANSIENT interview input, NOT a record. The graph's
`metadata.spec` is the sole record; nothing keeps an answers file (there is no answers slot in the
canonical layout). To CHANGE a machine-generated strategy later you edit `metadata.spec` in the
graph and regenerate (see the edit doctrine) — never "keep the answers file and re-run". Minimal
scratch shape (every field has a sane default, so a short file is enough):
```yaml
archetype: thesis_fund
strategy: my_strat
universe: { long_basket: ["BTC","ETH"], short_basket: ["SOL"] }
intervals: ["4h"]
max_positions: 2
dedup_seconds: 240
exit_preset: protected_standard   # protection-by-default; omit to bind the default, or set tight/wide
risk: { margin_pct: 5.0, leverage: 3.0 }
edge: >
  The user's thesis, verbatim and specific.
requires: []        # name a capability that may not exist yet -> gap report, nothing built
```

## Generate → check → deploy → install

1. **Generate the anchor:**
   `openclaw senpi composer new my_strat --archetype thesis_fund --answers <scratch> -o <dir>`
   → a COMPLETE, VALID graph with mechanically-bindable values filled and bespoke slots
   marked `# TODO(edit)` (working defaults, so it validates unedited). `--answers <scratch>` (or
   flags) is TRANSIENT input — delete the scratch file after; the graph's `metadata.spec` now holds
   everything. `new` refuses to clobber a hand-edited graph without `--force`.
   **Relay the `DEFAULTED:` block `new` prints** — it names every interview-relevant field you did NOT
   set explicitly (the value that got baked). Surface all of them to the user; the protection question
   (`exit_preset`) is ALWAYS asked explicitly, never accepted silently by default.
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
   **A GREEN check emits a `SUMMARY` block** (universe, indicators, pure_fn params, emitter sizing,
   exit preset, thesis-exit tuning, risk). Before fund/install, relay it VERBATIM as the pre-fund
   sign-off — it is the strategy's actual spec; never re-describe the strategy from memory or arithmetic.
4. **Deploy — stage the PRISTINE, self-contained unit:**
   `openclaw senpi composer deploy <graph> -o <dir>/dist` — hard-gated on a GREEN check; stages a
   standalone strategy PACKAGE TREE (`strategy.yaml` + `main/runtime.yaml` with the DSL exit +
   `main/scanners/scan.py` + `main/scanners/exit_scan.py` when the unit composes a thesis exit (one
   staged tree, one install) + `main/scanners/strategy_primitives/` — a vendored primitives snapshot,
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
- **One graph = one entry + one exit scanner (the machine-emitted pair).** A second INDEPENDENT entry
  thesis is another strategy/wallet (the composition ladder above) — never hand-add scanners to a
  compiled unit.
- **The composer artifact chain (graph → check → deploy → install) is the ONLY authoring path.** Never
  hand-write `scan.py`, scanner modules, or `runtime.yaml` outside the staged tree — even when the composer
  seems unable to express the thesis (doing so loses vendoring, the check oracle, the install path, and the
  teaching errors). The sanctioned hatches below — `pure_fn`, surgical graph edits, `mcp_read` — are IN-chain:
  graph-level, still pass `check`, allowed.
- **Edit doctrine — the graph's `metadata.spec` is the SOLE record (no answers file is kept).** For a
  MACHINE-GENERATED graph (it carries a `composer new` marker), the edit loop is: edit `metadata.spec`
  IN the graph, then `composer new <strategy> --from-spec` — it rebuilds the answers from that spec and
  REGENERATES the graph in place (params AND structure), no answers file involved. The MOMENT you
  hand-edit the graph's NODES/fn bodies, that graph IS the spec permanently — `--from-spec` REFUSES it
  (CMP257, no clean spec→graph mapping) and NEVER `composer new --force` over it (that discards the
  edits). To re-baseline a hand-edited graph, generate to a DIFFERENT path and merge your edits across.
- **Never rewrite the whole graph file.** When a surgical graph edit fails mechanically (edit-tool
  friction), retry with SMALLER edits — a wholesale rewrite once produced an invalid graph and
  burned a session.
- **Exit protection lives IN the graph** — `new` writes the resolved ladder to
  `metadata.spec.protection` (seeded from the preset). Tune tiers/floors THERE, then
  `check → deploy → update`; NEVER edit the emitted `runtime.yaml` (install verifies content hashes
  and will refuse). A graph one `spec` version behind is AUTO-UPGRADED in place by `check` (it
  relays the notice) — no manual step. A graph more than one version behind is a regenerate, not an
  edit: `check` refuses with CMP249.
- **Climb the hatch ladder before concluding the vocabulary lacks something:** (1) re-check the registry —
  `composer catalog` + `composer describe <node|kind|port_type|archetype>`; a capability's absence from your
  memory or from ONE CLI surface is not absence — `describe pure_fn` is the authoring contract for bespoke
  compute; (2) `kind: pure_fn` / surgical graph edits / `mcp_read` for unwrapped reads (all above); (3) only if
  genuinely inexpressible at a hard seam: tell the user plainly what IS buildable, offer the nearest
  approximation with the difference stated honestly, and record a gap report for maintainers. Never a dead-end
  refusal, never a bypass.
- **A tool error like "unknown node" for something the catalog's prose names → CHECK THE OTHER SURFACE**
  (`describe`/`catalog`), not proof the capability is fiction.
- **Tests encode expectations — update them, never stub them to pass.** A pure_fn signature change
  gives EXISTING fixtures realistic values for the new input; new behavior gets NEW cases. Weakening
  or stubbing a test to pass is a fabricated GREEN, not a fix.
- **One semantic unit per node/pure_fn.** A gate is not a scorer — don't bury new compute inside an
  existing node; give every pure_fn a descriptive title/one-liner.
- **Adding to a held position is NOT supported** (never existed in the runtime). The nearest workaround
  is close-then-reopen larger — fees/slippage twice, a moment flat, DSL floors reset,
  `per_asset_cooldown_seconds` delays the reopen — usually not what the user wants. Say so plainly;
  never improvise a pyramid.

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

The composer owns the operational surface end to end — there is no separate ops skill and no handoff.

- **Status is the FRONT DOOR:** `openclaw senpi composer status <strategy> --state-dir <state-dir>`
  answers any question about a specific strategy — the lifecycle chain `staged → landed → registered
  → running → live` PLUS, when the runtime is live, each scanner's cadence/last-check/quiet-checks/
  errors, every open position's protection in plain ROE, and the risk gates. RELAY IT VERBATIM; do
  not paraphrase or infer. A quiet scanner reads "no signal emitted yet" — there is NO invented
  reason. If the live section reads `UNAVAILABLE [CMP259]`, the gateway is unreachable or the
  runtime is not running — report that; never guess protection state.
- **Portfolio / all-my-strategies questions → bare `composer status`** (no target) = the single
  cross-strategy PORTFOLIO view: managed/manual/unmanaged rows (`--json` carries `kind` per row),
  protection quoted from the NAMED box engine, stranded/unmanaged runtimes listed WITH their recovery
  text, any missing data rendered `unavailable (reason)`. Relay it VERBATIM.
- **2-RUNG LADDER:** run `composer status` FIRST. Only when it flags trouble it cannot explain do
  you drop to the raw plumbing — `openclaw senpi state|scanner|dsl positions|dsl inspect --json` —
  and those outputs are ALSO relayed verbatim, never re-derived (a re-derivation flipped a PnL sign
  on real money once). Protection semantics are the engine's, as status renders them: breaches are
  phase-1-only and CONSECUTIVE (any tick back above the floor resets the count); in phase 2 the
  venue stop tracks the tier floor; tiers display 1-based.
- **Lifecycle claims are verbatim too** — NEVER assert runtime/deploy/protection status ("it's
  live", "it's protected", "it deployed") without quoting the surface that proves it (`composer
  status`, `runtime list`, `dsl inspect`). No status claim from memory.
- **Name the surface you quote** — reporting protection/position state, say WHERE it came from ("box
  engine via `composer status`"). If a divergence warning appears (two surfaces disagree), relay BOTH
  sides verbatim — never silently pick one.
- **Reads vs writes — where a change lands.** Reads are legitimate ANYWHERE (`composer status`, raw
  plumbing, backend MCP). A WRITE scoped to a strategy passes through the RECORD: edit
  `spec.protection` → `composer check` → `composer update`. A position-level MCP edit on a
  composer-MANAGED wallet is a FLAGGED STOPGAP — it moves the live position only, the record keeps old
  values, and today it RESETS backend high-water state; it is fully SANCTIONED only on MANUAL-kind
  wallets. If a result carries the composer-managed annotation, RELAY it and offer the record path; if
  it carries the teardown note, route to `composer close`.
- **Teardown:** `openclaw senpi composer close <target>` is the ONLY sanctioned teardown. It STOPS
  the runtime first, CONFIRMS it is gone, and ONLY THEN closes the strategy (flattens all positions,
  returns funds). It is idempotent and submit-only — `strategy_close` is async, so re-run it to POLL
  until `closed`. `<target>` = a strategy name / canonical hash dir / runtime id / `0x…` wallet.
  **NEVER call raw MCP `strategy_close` while a runtime is live** — it strands the runtime trading a
  wallet the server is tearing down (a hard invariant `close` enforces: if the runtime won't stop, it
  WITHHOLDS the close and fails loud rather than strand). `strategy_close` is the server-side FULL
  teardown (flattens ALL positions, returns funds) — it is NEVER part of an edit; teardown goes through
  `composer close` only.
- **Update an INSTALLED strategy — never tears down the strategy, wallet, or funds.** Editing a live
  strategy does NOT flatten positions or return funds: re-author → re-check → re-deploy, then
  `composer update <staged-dir>` — it swaps in the new runtime version in place and the wallet auto-binds
  (no `fund`, no `close`). Reserve the close-then-install path for a full teardown, not an edit.
  **Batch related edits into ONE check → deploy → update cycle** — every `update` is a live runtime
  swap (a brief unprotected window), not a per-tweak ritual.

## Gap reports = the honest refusal

If the answers or an edit reference a data source / capability the registry lacks, `new` (or
`check`) writes nothing and prints a structured **gap report**: what was asked, what kind, why
it isn't buildable, and the nearest registry alternatives. Relay that to the user as-is — it
is the correct outcome for something not yet buildable, not a bug to code around. Tell them
what IS possible instead (from the catalog), and that adding the capability is a
registry-side change, not something to fake in a scanner.

**Capital orchestration is MANUAL today — say so plainly, never quietly drop it.** Rebalancing
triggers, winner-allocation, "split capital into 3 at $N" are NOT automatable: state that honestly
and offer the manual alternative (the user re-funds / closes strategies via the composer verbs).

Same for ambiguity: if a brief lacks something you can't responsibly guess (which assets,
long/short, how much risk), ask — don't invent a plausible-sounding specific.
