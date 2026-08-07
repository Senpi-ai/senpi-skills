# Lifecycle internals — what `deploy.py` and `close.py` actually do

Both scripts live in `senpi-strategy-ops/scripts/`, share `_pkg.py` (package model) + `_cli.py`
(openclaw CLI + tolerant JSON digging) + `mcp_client.py` (vendored stdlib HTTP MCP client, reads
`SENPI_AUTH_TOKEN` / `SENPI_MCP_URL`). They are stdlib-only (plus PyYAML). No daemons, no `push_signal`.

## Why there is no scanner daemon anymore

Runtime 2.x **supervises** the scanner. `openclaw senpi runtime create -p <runtime.yaml>` hot-loads the
runtime, which spawns the Python scaffold child and calls `scan(inputs, ctx)` every `interval_seconds`,
time-boxed by `timeout_seconds`, restarting a crashed child itself. The old model (a separate
`nohup python3 … &` producer daemon that pushed signals over HTTP) is gone. Deploy = create a runtime;
nothing else to keep alive.

## Deploy — the runtime's `openclaw senpi deploy` verb

Deploy is **one verb running as a detached job**, not a blocking call and not a chain of scripted steps:
wallet funding (CREATE → FUND → ACTIVE, incl. bridging) and the first scan tick (up to an instance's
`interval_seconds`) are each multi-minute waits that would blow the ~180s tool/session timeout. The verb
returns in ~1s with a `deployId`; the agent polls `openclaw senpi deploy status` until the job is terminal.

Per instance the job runs five steps, each recorded with its own outcome:

1. **reconcile** — read live backend strategies and match by `strategyName` (`<id>` for a single-instance
   package, `<id>-<instance>` for a multi, sanitized). Exactly one live match → **adopt** its wallet
   (create is skipped). More than one → refuse **`[E_STATE_AMBIGUOUS_WALLETS]`**: one may be a funded live
   strategy, so it points at read-only triage and never at close/recreate. Zero → this instance needs a wallet.
2. **preflight** — `account_get_portfolio` (forced fresh) → the accessible-USDC waterfall (HL perps + HL
   spot USDC + EVM USDC; never `total_withdrawable`) → the funding plan (split by `funding_share`, floored
   at **$10/wallet** — the platform floor `min_budget.py` owns — minus a per-wallet fee buffer). A shortfall
   **HALTS** with `[E_FUNDS_SHORT]` or `[E_FUNDS_BELOW_FLOOR]` and **no create call is made**. The budget is
   a hard target — it is never silently scaled down. An unreadable balance yields "unknown" and the deploy
   proceeds: the backend is the funding authority and would fail loudly.
   Once that hard floor passes, the step computes the package's **calculated minimum** locally (the verb's
   port of `min_budget.py`, parity-tested against it) and **warns, never refuses**, when a wallet comes up
   short: `[W_BUDGET_BELOW_STRATEGY_MIN]`, or `[W_BUDGET_UNRESOLVED]` when a sleeve's sizing could not be
   read and the figure is only a lower bound (that note carries the shortfall too, and the same escape).
   The floor is the platform's rule; the calculated minimum is a design estimate and the user sizes their
   own budget. Both land on the final report (`minBudget`, `minWalletCount`, `belowMin`, `minBudgetNote`,
   `minBudgetUnresolved`) so they survive to a `live` render, not just the running narration; `belowMin`
   is set under either code.
   **The shortfall claim is PER WALLET, against what each one is actually allocated** — not the whole
   budget against the whole-package minimum. Those two differ the moment anything is adopted:
   `planFunding` splits the budget among the instances still NEEDING a wallet, while `minBudget` is
   computed across every instance. Comparing the totals once announced a shortfall on a re-run that was
   handing a single $13.50 sleeve the entire $20 — money that was not short, on a deploy that was not
   even touching the other sleeve. `minBudget`/`minWalletCount` therefore ride the report as
   **context** ("deploying the whole package fresh needs $30 across 2 wallets"), never as the claim.
   The escape is **close-then-redeploy** — a re-run at a larger `--budget` would only adopt what is
   already there — and it is **scoped to the underfunded sleeves** (`close.py <id> --instance <name>`),
   because `close.py <id>` would tear down adopted, live, funded sleeves this warn is not about.
   **No emitted command may need a precondition the report lacks**, so the scoped form appears only
   when those sleeves have a live RUNTIME: `--instance` resolves a sleeve through its runtime and
   hard-exits without one, and its error text then tells the reader to omit `--instance` and close
   the whole package — the exact widening the scoping exists to prevent. The other states get what
   they can actually support: a funded wallet with no runtime (deadline-abandoned after create, or a
   failed rollback) gets a read-only `status.py <id>` triage pointer and NO teardown command; a
   deploy that created nothing gets no escape at all; and where `[E_ROLLBACK_INCOMPLETE]` is present
   it owns the cleanup, so the budget warn defers to it by name instead of emitting a second,
   differently-scoped close. The redeploy figure is sized for every wallet the re-run will fund —
   the closed sleeves plus any that never got a wallet — not just the ones being closed.
   An instance that declares **no `funding_share`** is sized against `1/n` — the split a FRESH deploy of
   the package would apply — rather than `min_budget.py`'s whole-book `1.0`, which understates the total
   by the wallet count. (The Python is safe with `1.0` because it only runs at catalog-generation time, on
   packages `_pkg.validate` has already forced to declare shares summing to 1; the verb is a direct path
   with no such gate.) A **quoted** share (`"0.4"`) parses as a number here, matching `min_budget._f` so
   the card's `min_budget` and the verb's agree — note this does NOT make the whole card agree: `_pkg.py`
   reads the field raw (a quoted share raises in `_pkg.validate`) and `gen_catalog.derive_funding_split`
   drops non-numeric values, so a quoted share is a packaging bug `deploy.py validate` catches loudly.
3. **create** — one `strategy_create_custom_strategy(initialBudget, positions=[], strategyName,
   skillName=<id>, skillVersion=<version>)` per needing instance, then poll `strategy_list` to **ACTIVE**
   (bounded by `--max-wait`, default 150s — `deploy.py` forwards the same flag and defaults it identically;
   an explicit value also becomes `deploy.py`'s own poll budget, in either direction). A name rejection retries **once** without `strategyName` —
   naming is best-effort legibility and must never block a deploy. Deadline hit → `pending` (re-run resumes).
4. **install** — render the instance's `runtime.yaml` (substitute `${wallet_env}` + the decision-model env
   iff a `decision_mode: llm` action) and install it with the instance directory attached, so `path:
   ./scanners` resolves against the YAML's own directory. An existing runtime already on this wallet is an
   idempotent skip; one bound to a **different/old** wallet is **deleted and recreated** on the fresh wallet,
   never updated in place.
5. **observe** — poll the runtime's scanner rows for one **fresh** tick within `--tick-wait` (default 120s).
   What counts as a tick depends on the scanner: an interval (built-in) scanner proves it ran with
   `lastRunStatus` of `ok` **or `heartbeat`** — `ok` literally means "found a signal", so a quiet scanner
   records `heartbeat` and that is still a tick; an **external** (supervised push) scanner proves it with a
   fresh `lastAliveAt`, because intake short-circuits an empty POST before any run telemetry and a
   watch-only scanner therefore never records a run status at all. **Fresh** means at or after this
   install: scanner telemetry is name-keyed and survives a re-mint, so a stale `ok` from a previous
   incarnation never certifies this one. Seen → `ok` with the row quoted verbatim. Not seen →
   **`unobserved`**, which is truthful, not success — and when the registry says the first tick is still
   ahead of the window, the report names *when* it is due. A row that **errored since this install** →
   `failed` immediately, quoting `lastError` (polling the rest of the budget adds nothing). Every scanner
   disabled → `unobserved` with that said out loud, not a silent wait. `--tick-wait 0` skips the check
   entirely and reports `installed-unobserved`; it **can never report `live`**.

   A package whose scanner entries carry an **`enabled` key** is refused up front, before anything is
   created — whatever the value. The engine never reads a scanner-level `enabled` (registration comes
   from the strategy, not the scanner entry), so an `enabled: false` scanner would register and tick
   anyway: the package would trade while its author believes it is switched off. The refusal names
   every offending line — instance, scanner, file, value — so one edit pass fixes the package. A
   scanner runs because the package declares it: to stop one, remove the scanner entry.

**No local deploy state.** There is no `.deploy-state.json` any more — the backend strategies and the
runtime registry ARE the record, which is what killed the whole `E_STATE_*` lost-state class. The job does
keep an append-only JSONL journal under the runtime's state dir (`deploys/<deployId>.jsonl`), but it is
**narration only**: nothing ever reads it to decide an action. It is read for exactly three things —
rendering `deploy status` history, the boot scan that stamps a journal left unterminated by a restart as
`interrupted`, and one narrow carve-out: the **strategyId this box journaled at create time**, used only
as a LOOKUP KEY when reconcile's name match finds nothing (a create whose custom name the backend rejected
lands under a backend-assigned name that the name match can never find again). Every such id is re-read
from the live backend before reconcile may act on it — the journal supplies the key, the backend supplies
the decision. Nothing auto-resumes; only a fresh deploy does, and it reconciles.

**Single-flight, and self-freeing.** One deploy job per agent. A second start refuses
`[E_DEPLOY_IN_PROGRESS]`: concurrent deploys read one shared funding waterfall and two preflights could
both pass while jointly overdrawing. **There is no cancel** — undeploying is closing the strategy
(`close.py`), not stopping the job. Instead every MCP call the job makes is deadline-bounded — the job stops
*waiting* on an overrunning call, which is what makes the step boundary reachable; the request may
still be in flight server-side, so an overrun is reported as an unknown outcome, never as a failure —
and the job carries a wall-clock deadline: past it the run is abandoned at its **next step boundary** (an in-flight
money-moving call always completes and is journaled) and, after a grace longer than any single call's
deadline (so the abandoned job is no longer *waiting* on a money-moving call when the slot comes back —
a call it stopped waiting on may still land server-side, which the next deploy reconciles), the slot is
freed even if the run is still wedged inside an await. An abandoned deploy reports `failed` with the
resume command.

**Rollback is exactly one case.** A wallet **this job created and funded** whose *install* then failed is
closed and its funds returned (`strategy_close` returns them to the owner wallet on its own). Never an
adopted wallet — it predates this deploy; never on an observe failure — the runtime is installed and may
open a position at any moment; never on a refusal — nothing was created. If that close cannot run (the
wallet holds open positions) or fails, the report says **`[E_ROLLBACK_INCOMPLETE]`** and names the wallet,
the amount and the command to reclaim it: a stranded funded wallet is never silent. That command is an MCP
**`strategy_close` on that wallet address** — a funded wallet with no runtime cannot be reached by
`close.py --instance`. The package-wide `close.py <id>` is offered **only** when nothing else in the
package is live; otherwise the caveat names the live sleeves it would take down and says not to reach for
it. Run the command the report prints, never a wider one. The crash case does not
unwind — the boot scan never moves money — so an `interrupted` status names both exits (re-run to adopt,
or close to reclaim) along with the amount, read fresh from the backend.

## `deploy.py {validate|create|runtime|verify} <id> | status [<id>] …` — the funded path

`deploy.py` keeps its CLI contract but no longer deploys anything itself. It owns the **front half** —
package resolution (a path, or a bare catalog id fetched from the remote) and the side-effect-free
preflight — then starts the verb, polls `deploy status`, and relays the report **verbatim**. Its three
action subcommands (`create` / `runtime` / `verify`) all drive the same idempotent verb; they remain
distinct so existing docs and transcripts stay valid. `status` is the exception: it reads the agent's
**last deploy job** — one record, not package-addressed — so it resolves no package and fetches nothing.
An id is optional there and acts as an assertion: if the job ran a different package, `status` refuses
(naming both, and pointing at `status.py <id>` for what that package is actually doing) rather than
printing the other package's report and exit code under the id you asked about.

**The live-universe ticker gate lives HERE, and only here.** `universe_preflight` (`validate_universe.py`
against the live HL instrument list) runs in the three action subcommands, immediately before the verb is
started — not in `validate` (which exits after the structural + render pass) and not in the runtime,
which has no live-instrument check of its own. So `openclaw senpi deploy -p <dir> --budget <usd>` will
fund and install a package whose hardcoded tickers are dead (the `xyz:NASDAQ` shape: registers, ticks,
never trades). Fund through the wrapper; use the bare verb for read-only `deploy status` and for resuming
a package the wrapper already gated.

**Exit codes** (identical to the verb's): `0` live · `2` refused · `3` failed · `4`
installed-unobserved · `5` interrupted · `6` pending (a wallet still funding, or the job still running) ·
`1` internal/transport error — also the fallback for a status the wrapper does not recognise, so a new
status can never read as success, and for the wrapper's own "could not answer" cases: a start it could
not follow, and a `status` it could not answer as asked — an id that does not match the recorded job, or
a `--ref` it has no use for (no deploy state is reported in either, and a re-run refuses identically). Branch on the code; use `--json` for anything richer.

**Package fetch.** Any subcommand fetches `strategies/<id>/` from the remote if it isn't on disk
(`_fetch.py`: GitHub tree + raw from `SENPI_SKILLS_REPO`@`SENPI_SKILLS_REF`, default
`Senpi-ai/senpi-skills`@`main`; `--ref` overrides). Fetches land in the **durable strategies root** —
`SENPI_STRATEGIES_DIR` if set, else `<OPENCLAW_WORKSPACE_DIR>/strategies`, else `/data/workspace/strategies`
on agent hosts (with a loud stderr warning on the last-resort CWD-relative dev fallback) — never inside a
managed skill dir: a package written there is destroyed on the next skill update.

## `close.py [<id>] [--all] [--instance name] [--dry-run] [--json]`

Like deploy, close **does not block** on the async flatten — it stops + triggers, returns `closing`, and
hands polling to the agent (re-run). Discovery is ledger-free and **strategy-driven**: MCP `strategy_list`
filtered by `skillName == <id>` (resolved from `strategyMetadata.skillName`) gives the package's OPEN
strategies; **`strategyId` + wallet come straight from each strategy record** — NOT via the runtime, so
close also cleans up **orphaned** strategies (wallets a failed deploy created before `runtime create`). The
runtime is used **only to stop** the strategy, found by wallet (`find_runtime_by_wallet`).
**`--all`** closes **every** OPEN strategy across all packages (for "close all strategies / return funds")
and deletes their runtimes. `--instance` needs the live runtime to identify an instance; if it's gone, omit it.

Per strategy:

1. **Stop the runtime if one is live** — by wallet, `runtime delete`, confirm gone. Orphans skip to 2.
2. **Trigger `strategy_close(strategyId)`** — flattens **all** positions + closes the strategy (funds
   returned). Submit **only**, no wait. Only submitted while status is `ACTIVE`, so a re-run won't
   re-submit.
3. **Return `closing` immediately.** The agent polls by **re-running `close.py <id>`** — idempotent
   (runtime gone → skip; status closing/closed → skip re-submit) — which reports `closed` once the
   strategy leaves the active set (or drops out of `strategy_list`).

Close **always** closes the strategy (it never just stops the runtime). `--instance` scopes which instance(s)
to close.

## `status.py [<id>] [--fast] [--json]` — "what am I running?"

The single source of truth for the running fleet. Reads **live** `strategy_list` ∪ `openclaw runtime
list` (never the ephemeral deploy state), matches strategies to runtimes **by wallet**, and for each
running instance calls **`openclaw senpi status -r <id>`** to upgrade process-level "running" to the runtime's
own verdict + active-position count. Classes:

- **healthy / degraded / unhealthy / unknown** — ACTIVE strategy + live runtime, per the runtime's `status`
  health, which is fail-closed: `unknown` = scanner not yet proven by a tick (verify, don't assume);
  the deploy report's `overall: live` is the deploy-time liveness gate — re-read it read-only with
  `openclaw senpi deploy status`, never by re-running `deploy.py verify` (that starts the deploy verb
  and can fund/install); degraded/unknown print a triage hint.
- **runtime-stopped** — ACTIVE + runtime exists but not running.
- **no-runtime** — autonomous *package* strategy (`skillName`, no `traderAddress`) with **no runtime** →
  the only no-runtime anomaly (funded but not running, likely an interrupted deploy); printed with the fix
  (`deploy.py runtime <id>` to start, or `close.py <id>` to recover funds).
- **copy** — copy-trading strategy (follows a `traderAddress`) → run by Senpi's copy engine, no runtime.
- **manual** — manual / app-managed strategy → you manage it in the app, no runtime.

A strategy off the runtime is **not broken** — copy/manual are managed elsewhere by design; `status.py`
prints *how* each is managed (an info line, not a warning). Only `no-runtime` (autonomous, missing runtime)
is flagged.

`--fast` skips the per-runtime `status` call (one per running instance) and reports plain `running`. It also
lists **orphan runtimes** (a runtime with no OPEN strategy → safe to `runtime delete`). Grouped by
package; `<id>` filters. Answer "what am I running / list my strategies / is my fleet healthy" with this,
not by hand-composing `strategy_list`.

## Runtime CLI surface used (from `senpi-trading-runtime/references/runtime-cli.md`)

| Command | Used for |
|---|---|
| `openclaw senpi runtime create -p <yaml> --runtime-id <id>` | deploy step 3 |
| `openclaw senpi runtime list [--json]` | idempotency check, verify, reverse lookup, close discovery |
| `openclaw senpi runtime delete --id <id> --address <wallet>` | reinstall, close step 1 |
| `openclaw senpi status -r <id> [--json]` / `state -r <id> [--json]` | verify + Monitor (scanner ticked) |
| `openclaw senpi dsl positions\|inspect\|closes` · `action list\|history\|decisions` | troubleshooting |

## MCP tools used (via `mcp_client.py`)

`strategy_create_custom_strategy` (create wallet) · `strategy_list` (poll ACTIVE/CLOSED, reverse lookup) ·
`strategy_close` (flatten + close) · optionally `strategy_get` / `strategy_get_clearinghouse_state` to
cross-check zero open positions. Note: `timeout=` on a call is the **HTTP request** timeout, not the
async on-chain completion time — lifecycle ops submit then poll.
