---
name: senpi-strategy-ops
description: >-
  Deploy / monitor / close a NAMED Senpi trading strategy.
  Use when the user names a strategy to run — "install spider", "deploy polar",
  "set up kodiak", "run the spider strategy", "is my strategy live?", "what am I
  running", "list my strategies" (→ status.py),
  "are my positions protected? / do they have a stop-loss (DSL)?",
  "stop/close/uninstall polar" — and for teardown like "close all strategies",
  "return funds to main", "tear everything down" (→ close.py --all). ALWAYS tear
  down via close.py, never a raw strategy_close (that strands the runtime). Also
  the skill that APPLIES an edit to an already-live strategy: the edit itself is
  authored in senpi-strategy-author (the only skill that knows the scanner / yaml /
  DSL schema — "make my live strategy more aggressive", change leverage/sizing/DSL
  starts THERE); ops then applies it with deploy.py upgrade — no in-place reload
  yet, so it closes the arm and redeploys on a FRESH wallet (consent-gated, per
  arm). ops deploys / closes / applies; it does NOT author or edit strategy files.
  A strategy is a PACKAGE (strategy.yaml + one
  runtime.yaml per instance + scanners/) the runtime supervises in-process — no
  scanner daemon. deploy.py runs three resumable steps (create→runtime→verify),
  plus upgrade (edit a live strategy); close.py tears down (stop runtime +
  strategy_close → flattens positions, returns funds). The id (spider, polar,
  kodiak) is the package folder. NOT for choosing WHICH strategy
  (senpi-strategy-discover) or authoring / editing the strategy files themselves (senpi-strategy-author).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.16.2"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Strategy Ops — deploy, monitor, close

A strategy is a **package**: `strategy.yaml` (the deploy manifest) + one `runtime.yaml` per instance +
`<instance>/scanners/`. The runtime **spawns and supervises** each `scanners/scan.py`, calling
`scan(inputs, ctx)` every `interval_seconds` and owning everything downstream — signal validation,
sizing/execution, the two-phase DSL exit, risk guard-rails. **There is no separate scanner daemon, no
`push_signal`.** Ops owns the deployed lifecycle. Deploy is **three short, resumable steps** (each fits a
tool call — wallet funding and the first scan tick are slow, so they must not block one long call):

```
openclaw senpi validate <package-dir>                                     # 0a. does it RUN? (no wallet, no funding)
python3 senpi-strategy-ops/scripts/deploy.py validate <id>                 # 0b. preflight — deploy-ready? (no side effects)
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget <usd>   # 1. create wallets & fund them
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>                  # 2. register the runtime(s)
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                  # 3. GATE — confirm LIVE (runtime+scanner+DSL+budget)
python3 senpi-strategy-ops/scripts/deploy.py upgrade <id> --instance <arm> --budget <usd>  # apply an EDIT to a live strategy (close→redeploy fresh; resumable; consent-gated)
python3 senpi-strategy-ops/scripts/status.py                               # what am I running? (+ health)
python3 senpi-strategy-ops/scripts/close.py          <id>                  # teardown one strategy
python3 senpi-strategy-ops/scripts/close.py          --all                 # teardown EVERY open strategy
```
**Always tear down through `close.py`** (one `<id>` or `--all`) — it deletes the runtime *and* closes the
strategy. A raw `strategy_close` MCP call closes the strategy but **leaves the runtime registered**, which
collides on the next deploy. "close all strategies / return funds to main" → `close.py --all`.
Pass the **strategy `id`** for a CATALOG strategy (what `senpi-strategy-discover` hands over, e.g.
`spider`) — it's fetched from the remote if not on disk, always into the **durable strategies root**
(`SENPI_STRATEGIES_DIR` if set, else the agent workspace `strategies/` dir — normally
`/data/workspace/strategies`), never a CWD-relative path — a package written inside a managed skill
dir is destroyed on the next skill update. A bare id resolves from the durable root first (that copy
holds the deploy state and is authoritative), then CWD-relative `strategies/<id>` as a legacy
fallback, so re-running a step works from any directory. For a **locally-authored package, pass its
DIRECTORY path** (absolute is safest, e.g. `/data/workspace/strategies/<id>`): author into the
durable root too, never into a skill directory. An on-disk package is authoritative; an invalid one surfaces its
real errors and is never silently replaced by a remote fetch. The scripts call MCP directly
(`scripts/mcp_client.py`, reads
`SENPI_AUTH_TOKEN`) + drive `openclaw senpi runtime …`. Mechanics + state machine:
[`references/lifecycle.md`](references/lifecycle.md). Manifest: [`references/strategy-yaml-schema.md`](references/strategy-yaml-schema.md).

## Deploy — three steps: create → runtime → verify (NOT live until `verify` passes)

> **The loop is a gate, not a suggestion.** A strategy is LIVE only when `verify` returns `live` — every
> instance **runtime-running + scanner-active + DSL-wired + funded to what was requested**. If any step is
> incomplete, the strategy is **not live**; say so and fix the flagged component. Never report "live" off
> `registered` alone.

**Step 0 — resolve which strategy.** The user's word ("spider") is a strategy **`id`**. To confirm it
exists, check the registry; no match → hand to **senpi-strategy-discover**:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/strategies/catalog.json
```

**Step 0.5 — preflight. Two questions, two commands.**

`openclaw senpi validate <package-dir>` answers **does it run** — loads every scanner file, runs one
real tick against live read-only data, counts what it read, and checks each emitted signal against
the runtime's own wire schema. No wallet, no funding. `PASS` records a proof of runnability beside
the recipe; **`UNPROVEN` (exit 2) is not a pass** — the tick ran and established nothing, usually a
gate in `scan()` that should consult `ctx.dry_run`.

`deploy.py validate <id>` answers **is the package well formed** — every structural + render
issue in **one pass**, with **no side effects**, before you fund anything.

**Today nothing downstream re-checks runnability.** `deploy.py create` funds a wallet on the
strength of the package alone — it verifies structure, not that the scanner reads anything. So if
`senpi validate` was skipped, or returned `UNPROVEN` and was waved through, `create` will fund it
and `verify` will report `live`: a running strategy, real money, trading nothing. Run
`senpi validate` yourself, and do not deploy a package that has not returned `PASS`.

*(Coming: the deploy verb becomes a hard gate — it verifies the proof `senpi validate` writes and
refuses to fund without one. Validating now costs nothing then, because a package that already
passed carries its proof.)*

The deployer **accepts the flat single-instance layout** agents naturally scaffold — one `runtime.yaml` + `scanners/` at the
package root — by synthesizing the canonical `main` instance, so there's **no need to restructure into
`main/`**. Any remaining fix is named prescriptively (e.g. `set runtime name: <id>-main`). A package
that exists **on disk is authoritative** — an invalid local package surfaces its real error and is
never silently replaced by a stale remote fetch.

**Step 1 — creating wallets & funding them** (`create`; one fresh wallet per instance; budget splits by
`funding_share`, **min $10 each** — confirm with the user first). `create` runs the same full preflight
first, so it refuses **before funding** if the package isn't deploy-ready:
```
python3 scripts/deploy.py create spider --budget 200
```
Per instance it calls `strategy_create_custom_strategy(skillName=<id>, skillVersion=<version>,
strategyName=<id>-<instance>)` — **every wallet is named for its role in the strategy** (e.g. a
WhaleHunter deploy with two sub-wallets creates `whalehunter-long` and `whalehunter-short`), **never
left as a bare `0x…` address**, so the user can tell a strategy's wallets apart in the app, balances,
and notifications. Naming is best-effort: if the backend rejects a name (conflict/format), that wallet
is still created (unnamed) rather than failing the deploy. It records the `strategyId`, then polls
`strategy_list` to **ACTIVE before submitting the next instance** — **one wallet funds at a time**,
all **bounded** by one shared `--max-wait` (~150s). Two funding jobs on one embedded wallet race, and
the loser reads a balance the winner already claimed, funds **$0.00** and parks in `PENDING_FUNDING`.
If it prints **`creating`** (a wallet still funding), just **re-run the same `create` command** — it
resumes, **adopting the wallets it already created** and never re-creating or closing them. It prints
**`wallets-ready`** when done. If an existing `<id>` strategy is found **that this deploy did not
create**: a **runtime-less** one (funded but never got a runtime — the reuse trap an agent keeps landing
back on) is **closed to recover its funds**, then a new wallet is created (prints **`closing-existing`**;
re-run `create` once it's closed and funds are back); a **live, running** one is left untouched —
`create` **refuses** so it can't silently
flatten a real book (to apply an edit to it, use **`deploy.py upgrade`** — see *Upgrade*; `close.py <id>`
only to tear it down). The **`--budget` is a hard target**: create funds
exactly what you ask (split by `funding_share`, $10/wallet floor); if your live balance can't cover it,
create **HALTS with `underfunded`** and the exact shortfall — it will **NEVER silently fund less** (the
"$1,000 → $10" failure). Fund/free USDC or confirm a smaller amount, then re-run. **Never hand-edit
`.deploy-state.json` and never lower `--budget` to dodge a funding error** — just re-run `create`. When the
user names a budget per strategy ("$1k on X, $2k on Y"), deploy each with its own `--budget` and **confirm
the split before funding**.

**Step 2 — setting up the autonomous trading strategy** (`runtime`, fast): `python3 scripts/deploy.py
runtime spider` renders each instance's runtime.yaml **fresh from its `${WALLET_ENV}` template with the
wallet Step 1 created** and runs `openclaw senpi runtime create`. **Never reuses an old wallet:** the
runtime is always (re)built from scratch on the fresh wallet — if a same-name runtime already exists on a
**different/old** wallet it is **deleted and recreated**, never `runtime update`d in place; only an exact
same-wallet match is an idempotent skip. **Self-heals a lost deploy state:** if Step 1 succeeded but its
`.deploy-state.json` was lost (a sub-agent died before persisting), `runtime` **re-resolves the fresh
wallet from the live ACTIVE `<id>` strategy** instead of dead-ending — so you never hand-register a runtime
onto an old wallet. It **won't guess** — it refuses **split by cause**: **`[E_STATE_NO_WALLETS]`** (backend
has **zero** ACTIVE `<id>` wallets — nothing exists, so re-run `deploy.py create <id> --budget <usd>`, then
`runtime`) vs **`[E_STATE_AMBIGUOUS_WALLETS]`** (**>1** candidate ACTIVE wallets, one may be a funded **live**
strategy — triage read-only with `python3 status.py <id>`, resolve WITH THE USER which wallet is live, then
re-run `runtime`; **never `close.py`/recreate to "start clean"**). Prints `registered`.
`--decision-model` only for a `decision_mode: llm` action (rule-mode strategies need none).

**Once Step 2 prints `registered`, the runtime is wired — but the strategy is NOT confirmed live yet.**
Run **Step 3 `verify`**. **Do NOT tell the user the strategy is live until `verify` returns `live`.**

**Step 3 — `verify`** (the required gate): `python3 scripts/deploy.py verify spider` returns **`live`** only
when every instance is runtime-running + scanner-active + **DSL-wired** (`exit.dsl_preset` present and the
monitor enabled) + **funded to what was requested**; otherwise **`not-live`** (exit 2) with the failing
component named (e.g. `scanner=broken`, `dsl=config-missing`, `budget=underfunded`). Fix it and re-run.

**How it judges liveness — reliable backbone, flaky reads only downgrade.** The gate rests on signals that
are *reliably* readable right after deploy: **`openclaw senpi runtime list`** (authoritative inventory —
is the runtime running? a `running — NO ENTRY SCANNERS` status there means the entry scanners never
wired: NOT live), the deployed **runtime.yaml** (does it declare the external
scanner + a DSL preset?), and **MCP `strategy_list`** (funded?). It does **not** gate on `senpi status`/`senpi state` — that
JSON is **flaky-empty/throws for a minute+ after start** (seen live: `verify` got nothing while a manual
`status -r`/`state -r` seconds apart returned healthy). Those reads are used only to **downgrade** a scanner
to `broken` on *positive* evidence (disabled / erroring / runtime-reported unhealthy). A runtime-reported
**`unknown`** is NOT positive evidence — it is the fail-closed "not yet proven by a tick" verdict,
equivalent to unreadable/unmeasured. When they're unreadable, the scanner reads **`supervised`** = live: a running runtime **spawns and supervises** the
declared scanner (restarting it on crash), so runtime-running + scanner-declared ⇒ it's being driven, and
the DSL protects positions regardless. Never reads on-disk state files. It's a **single fast check** — a
scheduled/supervised scanner passes, so it does **not** wait for the first scan tick and you must **never
`sleep` then verify**. Still: **do not tell the user the strategy is live unless `verify` returned `live`.**
`deploy.py status <id>` shows deploy state any time.

> **Do NOT improvise.** A package strategy is a **runtime-supervised scanner** — deploy it **only** via
> these steps. Never substitute a raw `strategy_create_custom_strategy` MCP call to "deploy" it: that
> makes an **empty** custom-position strategy, not the running scanner. Funding is **automatic**
> (Hyperliquid perps → HL spot → EVM bridge). If `create` reports **`underfunded`** (or insufficient USDC /
> `available: 0`), the balance can't cover the requested budget (often locked in other strategies) — **do
> what the note's code says**: **`[E_FUNDS_SHORT]`** = fund/free USDC OR confirm a lower amount with the
> user (the note gives the exact `--budget ≤ X` ceiling it can fund), then **re-run `create`**;
> **`[E_FUNDS_BELOW_FLOOR]`** = no budget is valid, so help the user **deposit** and re-run — **never**
> suggest a lower budget below the floor. Do not switch tools. If
> `create` reports **`closing-existing`**, it's closing a runtime-less `<id>` wallet **this deploy never
> created** to recover funds so it can deploy fresh — re-run `create` once it's closed. A wallet this
> deploy DID create is adopted on a re-run, never closed. If it **refuses** "already deployed AND running", a
> live `<id>` strategy exists — to apply an edit use **`deploy.py upgrade <id> [--instance <arm>] --budget
> <usd>`** (closes + redeploys fresh, consent-gated; see *Upgrade*), not a bare `close`+`create`. If **`runtime`** lost its
> deploy state and can't safely resolve the fresh wallet, it refuses **split by cause** — and in **both**
> cases you **never hand-register a runtime onto an old wallet** (no manual `runtime create`/`update` with a
> wallet from a leftover yaml). **`[E_STATE_NO_WALLETS]`**: the backend has **zero** ACTIVE `<id>` wallets, so
> nothing exists and nothing is at risk — just re-run `deploy.py create <id> --budget <usd>`, then `runtime`.
> **`[E_STATE_AMBIGUOUS_WALLETS]`**: there are **>1** candidate ACTIVE wallets and one may be a funded **live**
> strategy — do **read-only** triage first (`python3 status.py <id>` maps each wallet to its runtime/strategy),
> then resolve WITH THE USER which wallet is live before re-running `deploy.py runtime <id>`. **Never
> `close.py`/recreate to "start clean"** here — that can tear down a funded live strategy.

**Report** from the structured output, not raw logs (then always close with the **How it runs** block below):
```jsonc
{ "strategy":"spider","version":"6.0.0","status":"live",
  "attribution":{ "skillName":"spider","skillVersion":"6.0.0" },
  "instances":[ { "instance":"swing","runtime_id":"spider-swing","wallet":"0x…","status":"live" },
                { "instance":"scalp","runtime_id":"spider-scalp","wallet":"0x…","status":"live" } ] }
```
Overall status across the steps: `create` → `creating` (re-run) | `closing-existing` (re-run once closed) |
`wallets-ready` | **`underfunded`** (balance < requested — `[E_FUNDS_SHORT]`: fund more / lower the ask;
`[E_FUNDS_BELOW_FLOOR]`: deposit only, never lower); `runtime` →
`registered`; `verify` → **`live`** | **`not-live`** (a component confirmed broken — fix it, re-run).
Per-instance
status flows `pending → creating → active → registered → live`. **`registered` ≠ live — `verify` is the
gate.** `create`/`runtime` take `--dry-run` (plan only; no side effects).

### Final step — tell the user HOW each strategy runs (REQUIRED on every deploy)

The user just funded a strategy; the last thing they see must explain **how the thing they funded actually behaves** — not just "it's live." After the `live` report, ALWAYS close the confirmation with a compact **"How it runs"** block per deployed strategy (per instance when their configs differ). Read every value from the **deployed `<instance>/runtime.yaml`** and the package **`strategy.yaml` catalog** — never invent a number. Three things, plain language, no raw YAML:

- **Cadence — how often it acts.** From the `external_scanner`'s `interval_seconds`. If `inputs` carry a slower *decision* clock (`recalibrationHours`, `thesisRefreshHours`, `regimeRefreshHours`), lead with THAT and note the wake interval. Translate to human: `interval_seconds: 300` → "scans every 5 minutes"; `interval_seconds: 21600` + `recalibrationHours: 168` → "re-reads the whole market **weekly**, waking every 6h to act on that read."
- **Scoring — what it grades and the entry bar.** One or two sentences: the catalog `belief_plain`/`thesis` (what signal it scores) + the runtime `inputs` gate (`minScore` and the conviction bands, `leverageTiers`/`marginPctTiers`). e.g. "ranks the book by relative strength + smart-money lean; opens a name only above its score threshold, sizing bigger at higher conviction (leverage steps up base→apex)."
- **Protection — the DSL exit ladder.** From `exit.dsl_preset`: the hard stop (`phase1.max_loss_pct`), the profit-lock ladder (`phase2.tiers`: first `trigger_pct` → top `lock_hw_pct`), and any time cut (`weak_peak_cut`/`hard_timeout`). State whether it has a manual close action or is **DSL-only** (no `CLOSE_POSITION` action → "no manual exits — the stop does all the selling"). e.g. "hard stop at −18% from entry; as a winner runs, a trailing floor ratchets up, locking profit from +8% to +80%; a stalled position is cut at 48h."

Keep it to ~3 short lines per strategy. Multi-instance packages whose legs differ (e.g. a long book vs a short book, core vs ballast) get one block each **or** a shared block that names the per-side difference. This is what turns "it's live" into "here's exactly how it trades" — required even when the user didn't ask.

### Worked example — "install spider"
```
user: "deploy spider with $300"
1. resolve → id = spider (two instances: swing 60% / scalp 40%; $300 → swing $180, scalp $120)
2. create → python3 scripts/deploy.py create spider --budget 300
            → wallets-ready  (if "creating", re-run until wallets-ready; if "underfunded", follow the note's
                             code — [E_FUNDS_SHORT] fund more / lower to its --budget ≤ X; [E_FUNDS_BELOW_FLOOR] deposit only)
3. runtime → python3 scripts/deploy.py runtime spider          → registered (spider-swing + spider-scalp)
4. verify  → python3 scripts/deploy.py verify spider           → live  (runtime+scanner+DSL+budget all green)
5. confirm → "🕷️ Spider is live (swing + scalp)." + the required How it runs block, e.g.:
   • Cadence — scans every 5 min (swing) / 5 min (scalp).
   • Scoring — grades tech/AI names on 4h/1h trend + smart-money consensus; opens above its score bar,
     sizing bigger at higher conviction.
   • Protection — hard stop −22% from entry; a trailing floor ratchets up locking profit from +15% to
     +80%; DSL-only, no manual exits.
```

### Host prerequisites
`openclaw` + the `@senpi-ai/runtime` plugin running; `SENPI_AUTH_TOKEN` exported (the same token the
MCP session uses); **Python 3 only — no PyYAML/pip needed** (the scripts use PyYAML if present, else a
vendored stdlib YAML loader). The package itself is fetched, not pre-placed. Smoke `create`/`runtime`
with `--dry-run` first.

## Monitor — what am I running? / is it actually live?

**"What strategies am I running?" / "list my strategies" / "is my fleet healthy?"** →
`python3 scripts/status.py` (add `<id>` to filter). It's the single source of truth: it reads live
`strategy_list` ∪ `runtime list` (NOT the ephemeral deploy state), and for each running instance calls
`openclaw senpi status -r <id>` to upgrade process-level "running" to the runtime's **own health verdict**
(**healthy / degraded / unhealthy**) plus **active-position count**. A strategy with **no runtime is not
"broken"** — it's just not autonomous, and `status.py` says how it's managed: **copy** (follows a
`traderAddress`, run by Senpi's copy engine) or **manual** (you manage it in the app). The *only*
no-runtime anomaly is an **autonomous package strategy** (skillName, no trader) missing its runtime →
flagged **no-runtime** with the fix (likely an interrupted deploy). Also **runtime-stopped**, plus a list
of **orphan runtimes**. On a host **without openclaw** the registry isn't visible, so package strategies
report **runtime-unknown** — not a diagnosis; check from the runtime host, and never read it as an
interrupted deploy. `--fast` skips the per-runtime health call; `--json` for machine output. **Tell the
user the management mode for off-runtime strategies — do not call them idle.** Don't hand-compose
`strategy_list` — use `status.py`.

**"Are my open positions protected? / do they have a stop-loss?"** → the DSL coverage verdict
(PROTECTED / UNPROTECTED / STOP-NOT-ON-VENUE). Key trap: an unprotected position shows up as an
**absence** in `senpi dsl positions`, so you must reconcile open positions against the tracked set — full
procedure in [`senpi-trading-runtime/references/dsl-protection-check.md`](../senpi-trading-runtime/references/dsl-protection-check.md).

Do **not** trust "runtime: running" alone. A strategy is **live** only when its runtime is running AND
each instance's `external_scanner` has a recent successful tick (`status.py` reports `running`; confirm a
tick with `deploy.py verify <id>`). Verify with the runtime CLI:
- `openclaw senpi status -r <runtime_id> --json` / `openclaw senpi state -r <runtime_id> --json`
- field-level liveness decision tree → [`references/liveness-verification.md`](references/liveness-verification.md)
- DSL / action / position troubleshooting → `openclaw senpi dsl|action …` (see lifecycle.md) and the
  engine mental model in `senpi-trading-runtime/references/runtime-concepts.md`

`runtime_id` = each instance's `runtime.yaml` top-level `name` (`spider-swing`, `spider-scalp`); they all
carry `group: <id>`, so you can rediscover a deployed strategy's runtimes ledger-free via
`openclaw senpi runtime list` matching `group == <id>`.

## Close — stop → trigger → (agent polls)

```
python3 scripts/close.py spider          # stop runtime(s) + trigger strategy_close, return immediately
python3 scripts/close.py spider          # re-run = poll; reports `closed` once flattened
python3 scripts/close.py --all           # close EVERY open strategy (all packages) + delete runtimes
```
Per strategy: **stop the runtime** (if live) → **trigger `strategy_close`** (flattens **all** positions
+ closes the strategy, funds returned). `strategy_close` is **async**, so the script **does not wait** —
it returns `closing` and hands polling to you: **re-run `close.py spider`** until it reports `closed`.
Re-runs are idempotent (runtime already gone → skip; already closing/closed → no re-submit). Strategies
are discovered from `strategy_list` (`skillName==<id>`), so close also cleans up **orphaned** wallets
that have no runtime. `--instance <name>` scopes an instance (needs its live runtime to map; else omit to close
all). **Redeploy / upgrade a deployed strategy — see the next section, never a bare `close`+`create`.**

## Upgrade — apply an edited scan.py / scoring.py / runtime.yaml to a LIVE strategy

> **STOPGAP.** The close→redeploy-on-a-fresh-wallet mechanics below exist only until the runtime engine
> ships in-place scanner reload (`update`/`refresh`). When it lands, this whole section and the `upgrade`
> verb collapse to a thin call to it — the command surface stays, the wallet-swap semantics go away. Treat
> the wallet-swap as scheduled for deletion, not durable doctrine.

The most common change request: the user asks to re-score / re-scan / re-tune a strategy (e.g. "make my
live strategy more aggressive"). **The edit itself — changing `scoring.py` / `scan.py` / `runtime.yaml`,
leverage, sizing, DSL — is authored in `senpi-strategy-author`** (the only skill that knows the scanner /
yaml / DSL schema). This skill's job is to **APPLY** that edited package to the live strategy. **There is no
in-place scanner reload yet** (a runtime-team `refresh` is coming), so applying = **close the arm, then
recreate it on a FRESH wallet with the edited files** — done **per arm**, so a multi-instance strategy's
other sleeves keep running. **`deploy.py upgrade` does the whole apply** — the ONLY correct way. Do not
hand-sequence it, and never hand-render a runtime.yaml (the `./scanners` "NO ENTRY SCANNERS" trap the verb
exists to prevent).

**Do this:**
1. **Confirm the edited package is on disk** in the durable root (`/data/workspace/strategies/<id>/[<instance>/]…`)
   — authored via `senpi-strategy-author`, not hand-guessed here. **Prove the edit still RUNS before you
   close anything**: `openclaw senpi validate <package-dir>` must return `PASS` — an edit is exactly when a
   scanner breaks, and `upgrade` flattens a live book. (`upgrade` also re-runs the structural `validate`
   preflight itself, so a bad `./scanners` path is caught BEFORE anything closes — but that checks shape,
   not runnability.)
2. **Run `upgrade` and re-run it to advance** — one guided step per call (close → fund fresh wallet →
   register runtime → verify), exactly like `create` resumes. `--instance` is required for a multi-arm
   package (upgrades one sleeve, siblings untouched); the budget funds the fresh wallet:
   ```
   python3 scripts/deploy.py upgrade <id> --instance <arm> --budget <usd>
   ```
   - **Flatten consent is built in.** If the arm holds an open position, the first call HALTS with
     `needs-consent` — it will NOT silently market-exit a live book. Surface the flatten to the user
     (positions close, funds return to main, redeploy on a NEW wallet), get an explicit yes, then re-run
     with `--yes`.
   - Each re-run reports where it is: `closing` (async flatten in flight — re-run) → `closed` → then the
     normal `wallets-ready` → `registered` → `live`. A transient `underfunded` right after close just
     means the old funds are still returning — wait and re-run. **Exit codes:** `0` = done (`live`), `2` =
     refused / action-required (`needs-consent`, `blocked`, `failed`), **`3` = resumable, re-run**
     (`closing`/`closed`). Don't treat `closed` as done — the old arm is gone and nothing is deployed yet;
     keep re-running until `live`.
   - **Budget continuity:** the fresh wallet is funded with `--budget` from the main wallet. If the old arm
     held **more** than `--budget`, the surplus does NOT follow it across — it returns to main and stays
     there. To carry the whole balance over, set `--budget` to the old arm's size (or higher).
3. **Tell the user what changed:** the wallet is NEW (old → new); funds moved main → new (up to `--budget`
   — any surplus stays in main); a custom ratchet/stop ladder on the old positions does NOT carry over
   (they were closed) — re-apply it if wanted.

**NEVER, during an upgrade:**
- hand-render a `runtime.yaml` or run raw `openclaw senpi runtime create` on a hand-built file — the
  `./scanners` trap. `upgrade` renders it INSIDE the instance dir for you.
- raw `strategy_create_custom_strategy` — that's a naked wallet with no runtime.
- `create` on top of a live strategy to "just re-fund it" — `create` refuses a running strategy; `upgrade`
  is the path that closes-then-redeploys.
- claim "upgraded / live" before `upgrade` reports `live`.

*(Under the hood `upgrade` drives the same tested `close → create → runtime → verify` path on a fresh
wallet. In-place `refresh` — reload the scanner with no close, no flatten, same wallet — is coming from the
runtime team; the verb swaps to it when it lands, same command surface.)*

## Invariants

- The wallet-creation MCP call carries attribution **`skillName`/`skillVersion` = the package
  `strategy.yaml` `id`/`version`** (not this skill's). `deploy.py` does this automatically.
- The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.

## Install — include the MCP helper

The scripts in `scripts/` import a vendored MCP helper, `scripts/mcp_client.py`, at runtime.
**Install the whole `scripts/` directory** — omitting `mcp_client.py` fails with
`No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
