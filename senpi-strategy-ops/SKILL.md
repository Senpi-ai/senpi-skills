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
  the runtime supervises in-process — no scanner daemon. `deploy.py create <id>
  --budget <usd>` takes a package live end to end (it gates the package, then runs
  the runtime's detached deploy job; watch with `senpi deploy status`);
  close.py tears down (stop runtime + strategy_close → flattens positions,
  returns funds). The id (spider, polar, kodiak) is the package folder. NOT for choosing WHICH strategy
  (senpi-strategy-discover) or building/editing one (senpi-strategy-author).
license: Apache-2.0
metadata:
  author: Senpi
  version: "3.0.0"
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
`push_signal`.** Ops owns the deployed lifecycle. Deploy is **one verb**: the runtime runs the whole
path — funds preflight → wallet create+fund → runtime install → one observed scanner tick — as a
**detached job** you then watch. It returns in ~1s; you poll until it is terminal.

```
python3 senpi-strategy-ops/scripts/deploy.py validate <id>              # 0. preflight — deploy-ready? (no side effects)
python3 senpi-strategy-ops/scripts/deploy.py create <id> --budget <usd> # 1. THE FUNDED PATH: validates, then starts the deploy
openclaw senpi deploy status                                            # 2. poll until terminal; read the verified report
python3 senpi-strategy-ops/scripts/status.py                            # what am I running? (+ health)
python3 senpi-strategy-ops/scripts/close.py          <id>               # teardown one strategy
python3 senpi-strategy-ops/scripts/close.py          --all              # teardown EVERY open strategy
```
**Fund through `deploy.py create|runtime|verify <id>`, not through the bare verb.** All three resolve the
package (a bare catalog `id` is fetched from the remote), run the structural preflight, and only then
start the runtime's `senpi deploy` job, poll it, and relay its report **verbatim**. The wrapper's value
is resolution, that structural pass, and the verbatim relay — **not** a gate the verb lacks:
`openclaw senpi deploy` itself now refuses a dead universe **pre-money**
(`[E_UNIVERSE_NOT_LIVE]` — every hardcoded instrument must be live on Hyperliquid, checked before
anything is created and fail-closed when the live list cannot be read). Use the bare verb for the
**read-only** `openclaw senpi deploy status`, and whenever package resolution is not needed.
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

## Deploy — one lifecycle: start the verb, poll `status` until terminal

> **`status` is the gate, not a suggestion.** A strategy is LIVE only when the deploy report's
> `overall` is **`live`** — every instance created + installed + a **verified scanner tick observed**.
> `installed-unobserved` means the tick was NOT seen inside the wait window: that is not failure and
> not success — say exactly that, and check back with `openclaw senpi scanner -r <runtimeId>`. Never
> report "live" off a started job, a running phase, or an install alone.

**Step 0 — resolve which strategy.** The user's word ("spider") is a strategy **`id`**. To confirm it
exists, check the registry; no match → hand to **senpi-strategy-discover**:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/strategies/catalog.json
```

**Step 0.5 — preflight (recommended).** `deploy.py validate <id>` reports every structural + render
issue in **one pass**, with **no side effects**, before you fund anything — and it resolves a bare
catalog id to a package directory on disk (fetching it if needed). It also **reports** the live universe
(dead hardcoded instruments are listed as validate errors; an unreachable instrument list is a loud note,
never a silent pass) — the deploy verb enforces that same gate before money moves, so `validate` is the
cheap early read of it, not the thing holding it. That one read is a **network call**
(`market_list_instruments`): `validate` needs `SENPI_AUTH_TOKEN` and can take a few seconds — up to
~25s on a slow or token-less host, which ends in the loud note, never in a failed validate. The deployer **accepts the flat single-instance layout** agents naturally scaffold — one
`runtime.yaml` + `scanners/` at the package root — by synthesizing the canonical `main` instance, so
there's **no need to restructure into `main/`**. Any remaining fix is named prescriptively (e.g. `set
runtime name: <id>-main`). A package that exists **on disk is authoritative** — an invalid local package
surfaces its real error and is never silently replaced by a stale remote fetch.

**Step 1 — start the deploy.** Budget splits across instances by `funding_share`, **min $10 each** (the
platform wallet floor) — **confirm the amount with the user first**. Two tiers, and only the first one
stops anything: below the $10/wallet floor the deploy **refuses**; and when the split leaves any wallet
with less than **its own** sizing needs, it **deploys and warns** (`[W_BUDGET_BELOW_STRATEGY_MIN]` — that
sleeve runs degraded; see the budget warnings below). The second check is per wallet against what it is
actually allocated, not the budget against the package total — those differ whenever a sleeve is adopted.
```
python3 senpi-strategy-ops/scripts/deploy.py create spider --budget 300
```
It validates locally, starts the job — which itself refuses pre-money on a dead universe — and then
polls `deploy status` for you, printing the verb's report verbatim.
Flags: `--decision-model <model>` (required only for a `decision_mode: llm` action),
`--tick-wait <s>` (how long the job waits to observe a tick; default 120, `0` skips),
`--max-wait <s>` (how long the JOB waits for a wallet to reach ACTIVE — default 150, and the wrapper
forwards the same number; pass it and it also becomes how long `deploy.py` polls, in either direction),
`--json`.

`deploy.py` polls for ~150s by default and then **returns**, staying inside the ~180s tool timeout — a
longer foreground wait would just get the call killed, losing the report and the exit code while the
job runs on. A job still running at that point is **exit `6` / pending**, printed with the snapshot and
`openclaw senpi deploy status` to watch it: that is a normal outcome on a slow funding leg or a long
scanner interval, **not** a failure and not a reason to re-run `create`.

Inside the job, per instance: **reconcile** (match live strategies by name — an existing live wallet is
adopted, never duplicated) → **preflight** (accessible-balance check) → **create** (one
`strategy_create_custom_strategy` call carrying `initialBudget`, `strategyName=<id>[-<instance>]` and the
**`skillName`/`skillVersion` attribution** from `strategy.yaml`; the backend funds from its own waterfall)
→ **install** (renders the instance's runtime.yaml onto the fresh wallet and registers it) → **observe**
(polls for one **fresh** tick — `ok` *or* `heartbeat` for interval scanners, a fresh `lastAliveAt` for
external ones; "fresh" means at or after this install, so stale telemetry from a previous incarnation
never certifies this one. See `references/lifecycle.md`). **Every wallet is named for its role** (a
WhaleHunter deploy makes `whalehunter-long` / `whalehunter-short`), never a bare `0x…`; naming is
best-effort — a rejected name creates the wallet unnamed rather than failing the deploy. **The verb never
reuses an old wallet:** a same-id runtime bound to a different wallet is deleted and recreated on the
fresh one, never updated in place.

**Only one deploy runs at a time.** Starting a second refuses **`[E_DEPLOY_IN_PROGRESS]`** naming the
running job — concurrent deploys share one funding waterfall and could jointly overdraw. Watch the
running one. **There is no `cancel` verb:** undeploying a strategy is closing it (`close.py <id>`),
not stopping the job. Every MCP call the job makes is timeout-bounded and the job itself has a
wall-clock deadline (the per-call bound stops the job *waiting*; the request may still be in flight
server-side, so an overrun reports the outcome as unknown, never as "it failed"), so a wedged deploy
abandons itself at the next step boundary and frees the slot
— you never have to wait for a gateway restart.

**Step 2 — poll `openclaw senpi deploy status` until it is terminal.** While running it prints the phase
as an in-progress fact. Once terminal it prints the full per-instance report with the evidence quoted
from the surfaces that prove it (`totalFunded`, the scanner row's `lastRunStatus`/`runCount`).

**Exit codes** (both `openclaw senpi deploy status` and `deploy.py`): `0` live · `2` refused · `3`
failed · `4` installed-unobserved · `5` interrupted · `6` pending (a wallet still funding, or the job
still running) · `1` internal/transport error — which is also what an unrecognised status returns, so
a new status can never read as success. `1` covers two more wrapper-side "could not answer" cases: a
start it could not follow (read `openclaw senpi deploy status` — the job may be running), and a
`deploy.py status` that could not be answered as asked — an id that is not the recorded job's package,
a `--ref` (which `status` never uses, since it fetches nothing), or a read that came back with **no
snapshot at all** (the verb's `[NOT_FOUND]` when no deploy has ever run on this agent, relayed in its
own words). Nothing about any deploy is
reported in those, and re-running them refuses identically. A `status` that DOES read a snapshot
reports that job's own code — `refused`/`failed`/still-running jobs report `2`/`3`/`6`, never "no job".
Branch on the code; read `--json` for anything richer.

Terminal `overall` values:

| `overall` | Meaning | What to do |
|---|---|---|
| `live` | every instance installed **and** a scanner tick observed | report live + the **How it runs** block. A `warn:` line (`[W_BUDGET_*]`) may ride a `live` report — relay it; it did **not** stop the deploy (see the budget warnings below) |
| `installed-unobserved` | installed, no tick seen inside `--tick-wait` (or `--tick-wait 0` skipped the check) | say exactly that; check `openclaw senpi scanner -r <runtimeId>` in a few minutes. External scanners legitimately tick on long intervals. **`--tick-wait 0` can never report `live`** — nothing was verified |
| `pending` | a wallet was still funding when the poll budget ran out | re-run the same deploy command — it resumes and adopts the wallet |
| `refused` | a gate said no (`[E_FUNDS_*]`, `[E_UNIVERSE_NOT_LIVE]`, `[E_STATE_AMBIGUOUS_WALLETS]`, `[INVALID_REQUEST]`) | **do what the refusal's code says** (below); nothing was created past it |
| `failed` | a step genuinely failed (backend rejection, install error, scanner erroring) | read the quoted cause, fix it, re-run |

**A gateway restart** while a job was running renders it **`interrupted`** on the next `status`: you get
the journal history **and** a fresh read of what actually exists, plus the resume command. Nothing
resumes on its own — only a fresh deploy does, and it reconciles.

**Re-running is always safe.** There is **no local deploy-state file**: the backend strategies and the
runtime registry are the record, so re-running the same deploy command reconciles and adopts whatever
already exists instead of duplicating it. A wallet that is still **initializing** is **waited for**, not
adopted half-built and not duplicated — but only the statuses that resolve on their own are waited on
(see the PAUSED refusal below).

> **Behaviour change from the old three-step flow.** `create` used to refuse when an `<id>` strategy
> was already deployed and running, and to close a runtime-less one to force a fresh wallet. The verb
> **adopts** instead — no close, no second wallet. Two consequences worth saying out loud to the user:
> deploy **never adds funds to a wallet that already exists** (ask for $500 against a wallet holding
> $100 and the report says the $500 was NOT added — top up separately, or `close.py` and redeploy),
> and "re-run to get a fresh wallet" is no longer a thing (`close.py <id>` first).

**Packages with no exit block are refused before anything is funded.** If an instance declares no
`exit.dsl_preset` / `exit.engine: dsl`, the verb refuses: every position it opened would run with no
stop loss and no trailing floor. Fix the runtime.yaml and re-check with `deploy.py validate <id>`.

> **Do NOT improvise.** A package strategy is a **runtime-supervised scanner** — deploy it **only** via
> `deploy.py create|runtime|verify`. Never substitute a raw `strategy_create_custom_strategy` MCP call to "deploy" it: that makes
> an **empty** custom-position strategy, not the running scanner, and it carries no attribution. Never
> reach for `openclaw senpi runtime create` — it is internal and skips the funds preflight, the
> attribution and the verified tick. Funding is **automatic** (Hyperliquid perps → HL spot → EVM bridge).
> Follow the refusal's code, exactly:
> - **`[E_FUNDS_SHORT]`** — the balance covers the $10/wallet floor but not the requested budget. Fund/free
>   USDC **or** confirm a lower amount with the user (the refusal names the exact `--budget <X>` it can
>   fund), then re-run. **Never lower the budget without asking.**
> - **`[E_FUNDS_BELOW_FLOOR]`** — **no budget is valid.** Help the user deposit
>   (`senpi-deposit-withdraw-transfer`), then re-run. **Never** suggest a lower budget here.
> - **`[E_STATE_AMBIGUOUS_WALLETS]`** — >1 live wallet matches an instance and one may be a funded **live**
>   strategy. Triage **read-only** (`python3 status.py <id>` maps each wallet to its runtime/strategy),
>   resolve WITH THE USER which wallet is live, then re-run. **Never `close.py`/recreate to "start clean"** —
>   that can tear down a funded live strategy.
> - **`[E_DEPLOY_IN_PROGRESS]`** — another deploy is running. Watch it (`deploy status`). There is nothing
>   to cancel; a wedged job times out and frees the slot on its own.
> - **`[E_ROLLBACK_INCOMPLETE]`** — a wallet this deploy created and funded had its install fail, and the
>   automatic close did not complete. **The wallet is live, funded and unwatched.** The refusal names the
>   wallet, the amount and the command to reclaim it — **follow that command, do not substitute one**.
>   It is a direct MCP `strategy_close` on that address, because a wallet with no runtime cannot be
>   reached by `close.py --instance`; the refusal offers the package-wide `close.py <id>` only when
>   nothing else in the package is live, and otherwise names the live sleeves that command would take
>   down with it. Tell the user either way. Never leave this one unreported.
> - **A live `<id>` strategy that is PAUSED (or mid-teardown)** — the verb refuses immediately with the
>   real status quoted; it does **not** wait, because a paused strategy never becomes ACTIVE on its own.
>   Resume it and re-run, or `close.py <id>` first if you meant to start over. **Never fund a second
>   wallet beside it.**
> - **`[E_SCANNER_PATH_UNRESOLVED]`** — an install could not resolve a relative scanner path. The verb always
>   passes the instance directory, so this means someone used `runtime create` by hand — use the verb.
> - **`[E_UNIVERSE_NOT_LIVE]`** — the package hardcodes instrument(s) that are not live on Hyperliquid.
>   **Nothing was created.** The one refusal names every dead instrument, both forms it checked
>   (`T` and `xyz:T`), and the exact file + key path each appears in. Fix each named instrument in the
>   package (the **senpi-strategy-author** edit path), re-check read-only with
>   `python3 senpi-strategy-ops/scripts/validate_universe.py <dir>`, then re-run. A dead name never
>   errors at runtime — the scan skips it and the strategy silently trades nothing — so **never
>   "deploy anyway"**. If the step instead reports that the live instrument list **could not be read**,
>   nothing is claimed dead and nothing was created: retry once the MCP server is reachable.

**The `W_` prefix means WARNING — the deploy went through.** Every code above stops something; a `W_`
code never does. The two budget codes below are the `W_` ones. They ride a
`live` report as `minBudget` / `minWalletCount` / `belowMin` / `minBudgetNote` / `minBudgetUnresolved`
(printed as `calculated minimum:` and `warn:` lines by `deploy status`). **Never report a deploy as
failed, and never close a wallet, because of one:**
- **`[W_BUDGET_BELOW_STRATEGY_MIN]`** — one or more wallets **this deploy funded** got less than their
  own sizing needs (the warn names each one and both numbers: `scalp $12.00 (needs $13.50)`). The
  strategy **deployed and is running**, just **degraded** — fewer slots than the author designed, each
  position a larger share of its wallet. Tell the user plainly, and offer the authored size as a
  *choice*: a smaller book is legitimate, not a mistake to undo.
  **Follow the warn's own escape verbatim — do not improvise one, and do not widen it.** It is
  close-then-redeploy (deploy never adds funds to an existing wallet, so a re-run only adopts it) and
  it is **scoped**: `close.py <id> --instance <name>` when only some sleeves are short. Never widen
  that to `close.py <id>` — the other sleeves may be adopted, live and funded, and this warn is not
  about them. Some reports deliberately carry **no** command: a funded wallet with no runtime cannot
  be closed by instance at all, so the warn points at read-only `status.py <id>` triage instead —
  that is the correct answer there, not a gap for you to fill with a package-wide close. A
  `funding_share: 0` sleeve gets no command either: NO budget can lift it above the $10 floor, so
  closing and re-deploying would only reproduce the identical warn — the warn names the real fix (a
  share in strategy.yaml); relay that, never close the sleeve over it. And where
  `[E_ROLLBACK_INCOMPLETE]` is on the report, it owns the cleanup; do that first.
  `minBudget` in the report is **context** ("the whole package fresh needs $30 across 2 wallets"), not
  the thing that was violated — a partially-adopted deploy splits the budget among fewer wallets.
- **`[W_BUDGET_UNRESOLVED]`** — one or more sleeves publish risk weights rather than slot sizes, so the
  minimum could not be computed and the printed figure is a **lower bound**. The deploy ran. Say the
  number **may be understated** and size conservatively; do not quote it as verified. If a wallet is
  ALSO short the warn names it and `belowMin` is set — the two codes are **not mutually exclusive**, so
  never read "no `[W_BUDGET_BELOW_STRATEGY_MIN]`" as "the budget was enough". Read `belowMin`. That case
  carries the same scoped escape; follow it verbatim too.

**Report** from the structured output, not raw logs (then always close with the **How it runs** block below):
```jsonc
{ "strategy":"spider","version":"6.0.0","status":"live",
  "attribution":{ "skillName":"spider","skillVersion":"6.0.0" },
  "instances":[ { "instance":"swing","runtime_id":"spider-swing","wallet":"0x…","status":"live" },
                { "instance":"scalp","runtime_id":"spider-scalp","wallet":"0x…","status":"live" } ] }
```
Quote the report's numbers verbatim — `funded` is the backend's `totalFunded`, and the tick line is the
scanner row's own fields. **Never re-derive a number in prose.**

### The funded path — `deploy.py create|runtime|verify`

`deploy.py` no longer deploys anything itself. Each of its three action subcommands resolves the package
(a bare catalog id is fetched), runs the structural preflight, then starts the **same** verb — which
holds the live-universe gate itself, pre-money — polls it, and prints its report verbatim. All three
keep the same flags (`--budget`, `--decision-model`,
`--max-wait`, `--tick-wait`, `--json`, `--dry-run`) and the verb's exit codes — **`2` refused / `3`
failed** (full map above). **The old `== 2` habit no longer catches a failure**: the pre-verb script
exited 2 on failure, this one exits 3, so anything branching on `== 2` alone silently treats every
failed deploy as a success. Branch on the whole map — and note the behaviour change below.

> **`verify` now runs a deploy — it can move money.** It used to be a read-only check. It drives the same
> idempotent verb, so on a package whose wallets already exist it just reconciles and observes — but given
> a `--budget` and a missing wallet it **will create and fund one**, and on a funded wallet with no runtime
> it **installs and starts trading**. **Reserve it for an intentional resume/reconcile**, never for
> "is it still ticking?" — monitoring runs on the read-only surfaces (`status.py`, `openclaw senpi deploy
> status`, `senpi scanner`, `senpi status`, `senpi state`; see **Monitor** below). `deploy.py status
> [<id>]` shows the last deploy job and starts nothing — there is **one job record per agent**, so an id
> you pass is checked against it and a mismatch **refuses** (exit `1`, no deploy state reported, re-running
> refuses again) instead of printing another package's verdict under your package's name.

### Host prerequisites
`openclaw` + the `@senpi-ai/runtime` plugin running; `SENPI_AUTH_TOKEN` exported (the same token the
MCP session uses); **Python 3 only — no PyYAML/pip needed** (the scripts use PyYAML if present, else a
vendored stdlib YAML loader). The package itself is fetched, not pre-placed. Smoke with
`deploy.py validate <id>` first.

### Final step — tell the user HOW each strategy runs (REQUIRED on every deploy)

The user just funded a strategy; the last thing they see must explain **how the thing they funded actually behaves** — not just "it's live." After the `live` report, ALWAYS close the confirmation with a compact **"How it runs"** block per deployed strategy (per instance when their configs differ). Read every value from the **deployed `<instance>/runtime.yaml`** and the package **`strategy.yaml` catalog** — never invent a number. Three things, plain language, no raw YAML:

- **Cadence — how often it acts.** From the `external_scanner`'s `interval_seconds`. If `inputs` carry a slower *decision* clock (`recalibrationHours`, `thesisRefreshHours`, `regimeRefreshHours`), lead with THAT and note the wake interval. Translate to human: `interval_seconds: 300` → "scans every 5 minutes"; `interval_seconds: 21600` + `recalibrationHours: 168` → "re-reads the whole market **weekly**, waking every 6h to act on that read."
- **Scoring — what it grades and the entry bar.** One or two sentences: the catalog `belief_plain`/`thesis` (what signal it scores) + the runtime `inputs` gate (`minScore` and the conviction bands, `leverageTiers`/`marginPctTiers`). e.g. "ranks the book by relative strength + smart-money lean; opens a name only above its score threshold, sizing bigger at higher conviction (leverage steps up base→apex)."
- **Protection — the DSL exit ladder.** From `exit.dsl_preset`: the hard stop (`phase1.max_loss_pct`), the profit-lock ladder (`phase2.tiers`: first `trigger_pct` → top `lock_hw_pct`), and any time cut (`weak_peak_cut`/`hard_timeout`). State whether it has a manual close action or is **DSL-only** (no `CLOSE_POSITION` action → "no manual exits — the stop does all the selling"). e.g. "hard stop at −18% from entry; as a winner runs, a trailing floor ratchets up, locking profit from +8% to +80%; a stalled position is cut at 48h."

Keep it to ~3 short lines per strategy. Multi-instance packages whose legs differ (e.g. a long book vs a short book, core vs ballast) get one block each **or** a shared block that names the per-side difference. This is what turns "it's live" into "here's exactly how it trades" — required even when the user didn't ask.

### Worked example — "install spider"
```
user: "deploy spider with $300"
1. resolve  → id = spider (two instances: swing 60% / scalp 40%; $300 → swing $180, scalp $120)
              confirm the split with the user BEFORE funding
2. preflight→ python3 scripts/deploy.py validate spider        → deploy-ready (2 instances)
3. start    → python3 scripts/deploy.py create spider --budget 300
              (starts the job, which refuses pre-money on a dead universe: dpl-a1b2c3d4 — phase: reconcile)
4. watch    → it polls for you; or openclaw senpi deploy status  (repeat until it is terminal)
              running (phase: create) → running (phase: install) → done — live
              (if it ends `installed-unobserved`, say the tick was not observed yet and check
               `openclaw senpi scanner -r spider-swing` in a few minutes — do NOT call it live)
5. confirm  → "🕷️ Spider is live (swing + scalp)." + the required How it runs block, e.g.:
   • Cadence — scans every 5 min (swing) / 5 min (scalp).
   • Scoring — grades tech/AI names on 4h/1h trend + smart-money consensus; opens above its score bar,
     sizing bigger at higher conviction.
   • Protection — hard stop −22% from entry; a trailing floor ratchets up locking profit from +15% to
     +80%; DSL-only, no manual exits.
```

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
each instance's `external_scanner` has a recent successful tick (`status.py` reports `running`). Confirm
the tick on a **read-only** surface — never with `deploy.py verify`, which now runs the deploy verb and
can create, fund and install (see the funded path above):
- `python3 scripts/status.py <id>` — the fleet view + each runtime's own health verdict
- `openclaw senpi deploy status` — the last deploy job's report (starts nothing)
- `openclaw senpi scanner -r <runtime_id>` — the scanner rows (`lastRunStatus`, `runCount`), the tick itself
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
