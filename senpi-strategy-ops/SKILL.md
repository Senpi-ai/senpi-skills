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
  down via close.py, never a raw strategy_close (that strands the runtime). A
  strategy is a PACKAGE (strategy.yaml + one runtime.yaml per instance + scanners/)
  the runtime supervises in-process — no scanner daemon. deploy.py runs three
  resumable steps (create→runtime→verify); close.py tears down (stop runtime +
  strategy_close → flattens positions, returns funds). The id (spider, polar,
  kodiak) is the package folder. NOT for choosing WHICH strategy
  (senpi-strategy-discover) or building/editing one (senpi-strategy-author).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.9.0"
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
python3 senpi-strategy-ops/scripts/deploy.py validate <id>                 # 0. preflight — deploy-ready? (no side effects)
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget <usd>   # 1. create wallets & fund them
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>                  # 2. register the runtime(s)
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                  # 3. GATE — confirm LIVE (runtime+scanner+DSL+budget)
python3 senpi-strategy-ops/scripts/status.py                               # what am I running? (+ health)
python3 senpi-strategy-ops/scripts/close.py          <id>                  # teardown one strategy
python3 senpi-strategy-ops/scripts/close.py          --all                 # teardown EVERY open strategy
```
**Always tear down through `close.py`** (one `<id>` or `--all`) — it deletes the runtime *and* closes the
strategy. A raw `strategy_close` MCP call closes the strategy but **leaves the runtime registered**, which
collides on the next deploy. "close all strategies / return funds to main" → `close.py --all`.
Pass the **strategy `id`** for a CATALOG strategy (what `senpi-strategy-discover` hands over, e.g.
`spider`) — it's fetched from the remote if not on disk. For a **locally-authored package, pass its
DIRECTORY path** (absolute is safest, e.g. `/data/workspace/strategies/<id>`): a bare id only resolves
locally relative to your CWD, so from anywhere else it becomes a remote catalog fetch — never what you
want for a package you just wrote. An on-disk package is authoritative; an invalid one surfaces its
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

**Step 0.5 — preflight (recommended).** `deploy.py validate <id>` reports every structural + render
issue in **one pass**, with **no side effects**, before you fund anything. The deployer **accepts the
flat single-instance layout** agents naturally scaffold — one `runtime.yaml` + `scanners/` at the
package root — by synthesizing the canonical `main` instance, so there's **no need to restructure into
`main/`**. Any remaining fix is named prescriptively (e.g. `set runtime name: <id>-main`). A package
that exists **on disk is authoritative** — an invalid local package surfaces its real error and is
never silently replaced by a stale remote fetch.

**Step 1 — creating wallets & funding them** (`create`; one fresh wallet per instance; budget splits by
`funding_share`, **min $100 each** — confirm with the user first). `create` runs the same full preflight
first, so it refuses **before funding** if the package isn't deploy-ready:
```
python3 scripts/deploy.py create spider --budget 200
```
Per instance it calls `strategy_create_custom_strategy(skillName=<id>, skillVersion=<version>,
strategyName=<id>-<instance>)` — **every wallet is named for its role in the strategy** (e.g. a
WhaleHunter deploy with two sub-wallets creates `whalehunter-long` and `whalehunter-short`), **never
left as a bare `0x…` address**, so the user can tell a strategy's wallets apart in the app, balances,
and notifications. Naming is best-effort: if the backend rejects a name (conflict/format), that wallet
is still created (unnamed) rather than failing the deploy. It records the `strategyId`, and polls
`strategy_list` to **ACTIVE** — **bounded** (~150s). If it prints
**`creating`** (wallets still funding), just **re-run the same `create` command** — it resumes and
**never re-creates** a wallet. It prints **`wallets-ready`** when done. **`create` never reuses an existing
wallet — every deploy gets a FRESH one.** If an existing `<id>` strategy is found: a **runtime-less** one
(funded but never got a runtime — the reuse trap an agent keeps landing back on) is **closed to recover its
funds**, then a new wallet is created (prints **`closing-existing`**; re-run `create` once it's closed and
funds are back); a **live, running** one is left untouched — `create` **refuses** so it can't silently
flatten a real book (`close.py <id>` first to redeploy). The **`--budget` is a hard target**: create funds
exactly what you ask (split by `funding_share`, $100/wallet floor); if your live balance can't cover it,
create **HALTS with `underfunded`** and the exact shortfall — it will **NEVER silently fund less** (the
"$1,000 → $100" failure). Fund/free USDC or confirm a smaller amount, then re-run. **Never hand-edit
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
is the runtime running?), the deployed **runtime.yaml** (does it declare the external scanner + a DSL
preset?), and **MCP `strategy_list`** (funded?). It does **not** gate on `senpi status`/`senpi state` — that
JSON is **flaky-empty/throws for a minute+ after start** (seen live: `verify` got nothing while a manual
`status -r`/`state -r` seconds apart returned healthy). Those reads are used only to **downgrade** a scanner
to `broken` on *positive* evidence (disabled / erroring / runtime-reported unhealthy). When they're
unreadable, the scanner reads **`supervised`** = live: a running runtime **spawns and supervises** the
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
> `create` reports **`closing-existing`**, it's closing a runtime-less `<id>` wallet to recover funds so it
> can deploy fresh — re-run `create` once it's closed. If it **refuses** "already deployed AND running", a
> live `<id>` strategy exists — `close.py <id>` first to redeploy on a fresh wallet. If **`runtime`** lost its
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
all). **Redeploy** = `close` then `create`/`runtime`/`verify`.

## Invariants

- The wallet-creation MCP call carries attribution **`skillName`/`skillVersion` = the package
  `strategy.yaml` `id`/`version`** (not this skill's). `deploy.py` does this automatically.
- The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.

## Install — include the MCP helper

The scripts in `scripts/` import a vendored MCP helper, `scripts/mcp_client.py`, at runtime.
**Install the whole `scripts/` directory** — omitting `mcp_client.py` fails with
`No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
