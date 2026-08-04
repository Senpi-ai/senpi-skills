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
   at $100/wallet, minus a per-wallet fee buffer). A shortfall **HALTS** with `[E_FUNDS_SHORT]` or
   `[E_FUNDS_BELOW_FLOOR]` and **no create call is made**. The budget is a hard target — it is never
   silently scaled down. An unreadable balance yields "unknown" and the deploy proceeds: the backend is the
   funding authority and would fail loudly.
3. **create** — one `strategy_create_custom_strategy(initialBudget, positions=[], strategyName,
   skillName=<id>, skillVersion=<version>)` per needing instance, then poll `strategy_list` to **ACTIVE**
   (bounded by `--max-wait`, default 150s). A name rejection retries **once** without `strategyName` —
   naming is best-effort legibility and must never block a deploy. Deadline hit → `pending` (re-run resumes).
4. **install** — render the instance's `runtime.yaml` (substitute `${wallet_env}` + the decision-model env
   iff a `decision_mode: llm` action) and install it with the instance directory attached, so `path:
   ./scanners` resolves against the YAML's own directory. An existing runtime already on this wallet is an
   idempotent skip; one bound to a **different/old** wallet is **deleted and recreated** on the fresh wallet,
   never updated in place.
5. **observe** — poll the runtime's scanner rows for one `lastRunStatus: "ok"` within `--tick-wait`
   (default 120s, `0` skips). Seen → `ok` with the row quoted verbatim. Not seen → **`unobserved`**, which
   is truthful, not success: external scanners legitimately tick on long intervals. A row that only ever
   errors → `failed`, quoting `lastError`.

**No local deploy state.** There is no `.deploy-state.json` any more — the backend strategies and the
runtime registry ARE the record, which is what killed the whole `E_STATE_*` lost-state class. The job does
keep an append-only JSONL journal under the runtime's state dir (`deploys/<deployId>.jsonl`), but it is
**narration only**: nothing ever reads it to decide an action. It is read for exactly two things —
rendering `deploy status` history, and the boot scan that stamps a journal left unterminated by a restart
as `interrupted`. Nothing auto-resumes; only a fresh deploy does, and it reconciles.

**Single-flight.** One deploy job per agent. A second start refuses `[E_DEPLOY_IN_PROGRESS]`: concurrent
deploys read one shared funding waterfall and two preflights could both pass while jointly overdrawing.
`openclaw senpi deploy cancel` sets a flag honored at **step boundaries only** — an in-flight money-moving
call always completes and is journaled, and nothing is rolled back.

## `deploy.py {validate|create|runtime|verify|status} <id> …` — the compatibility wrapper

`deploy.py` keeps its CLI contract but no longer deploys anything itself. It owns the **front half** —
package resolution (a path, or a bare catalog id fetched from the remote) and the side-effect-free
preflight — then starts the verb, polls `deploy status`, and relays the report **verbatim**. Its three
action subcommands (`create` / `runtime` / `verify`) all drive the same idempotent verb; they remain
distinct so existing docs and transcripts stay valid. Exit code 2 on a `refused`/`failed` report.

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
  `deploy.py verify <id>` remains the deploy-time liveness gate; degraded/unknown print a triage hint.
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
