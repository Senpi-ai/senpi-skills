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

## `deploy.py {create|runtime|verify|status} <id> …`

Deploy is **three short, resumable steps**, not one blocking call — because wallet funding (CREATE →
FUND → ACTIVE, incl. bridging) and the first scan tick (up to an instance's `interval_seconds`) are each
multi-minute waits that blow the ~180s tool/session timeout. Each step is **bounded** (`--max-wait`,
default 150s) and the agent **re-runs a step until it reports done**.

**Package fetch.** Any step fetches `strategies/<id>/` from the remote if it isn't on disk (`_fetch.py`:
GitHub tree + raw from `SENPI_SKILLS_REPO`@`SENPI_SKILLS_REF`, default `Senpi-ai/senpi-skills`@`main`;
`--ref` overrides). Fetches land in the **durable strategies root** — `SENPI_STRATEGIES_DIR` if set,
else `/data/workspace/strategies` on agent hosts — never a CWD-relative path: a package written inside
a managed skill dir is destroyed on the next skill update.

**State file** `<pkg>/.deploy-state.json` — `{instances: {name: {strategyId, wallet, status}}}`, status
flowing `pending → creating → active → registered → live`. Every sub-action persists, so a kill mid-step
just means re-run that step.

1. **`create <id> --budget N`** —
   - **Reconcile first:** for each recorded `strategyId`, re-fetch its backend status; if **not `ACTIVE`**
     (CLOSED / FAILED / gone) the entry is **discarded** so it gets recreated. This is the durable fix for
     stale `.deploy-state.json` (reusing a CLOSED wallet, or getting stuck on a FAILED instance) — **no manual
     state editing**.
   - **Anti-duplicate guard:** if `strategy_list` shows **OPEN** `skillName==<id>` strategies not in the
     state file (an interrupted run), refuse and point at `close.py <id>` (closed/failed history is ignored).
   - **Fund to live balance:** sizes the to-create wallets from `account_get_portfolio`
     (`total_in_hyperliquid`) minus a per-wallet fee buffer, split by `funding_share` and capped to
     available — so sequential funding + creation fees can't leave an instance $1 short. **Never lower `--budget`
     to dodge rounding; just re-run.**
   - Per instance: `strategy_create_custom_strategy(skillName=<id>, skillVersion=<version>, initialBudget=…,
     strategyName=<id>-<instance>)` — names the wallet for its role (e.g. `whalehunter-short`), never a bare
     address; best-effort (falls back to unnamed if the name is rejected). Record `strategyId` **immediately**,
     poll `strategy_list` to **ACTIVE** (bounded by `--max-wait`).
     Not all ACTIVE → **`creating`** (re-run to resume); all ACTIVE → **`wallets-ready`**.
2. **`runtime <id>`** — per instance: render the instance's `runtime.yaml` (substitute `${wallet_env}` + the
   decision-model env iff a `decision_mode: llm` action) **beside the source** (so `path: ./scanners`
   resolves) → `openclaw senpi runtime create … --runtime-id <id>-<instance>`. **Self-healing:** an existing
   runtime on the right ACTIVE wallet is skipped; a stale one (different/CLOSED wallet — e.g. orphaned by an
   earlier close) is **deleted and recreated** (fixes the "already exists" / "wallet CLOSED" collisions).
   Requires wallets `active`. → `registered`. **After this, deployment is DONE** — the strategy is live and
   trading autonomously; it scans on its own `interval_seconds` and opens positions when its signals fire.
   Do **not** wait/poll for the first tick as part of deploy.
3. **`verify <id>` — OPTIONAL** (only when the user asks "is it actually scanning yet?"). **Fast single
   check** (`--max-wait 0` default) of `openclaw senpi state -r <id>-<instance>` for a completed tick. It
   does **not** block: a scanner's first `scan()` only fires on its `interval_seconds`. Right after
   `runtime` it reports `registered` (not ticked yet) — expected; re-run after the interval to see `live`.
   `--max-wait S` opts into a bounded poll. `status <id>` prints the state file any time. Never `sleep`
   then `verify` as a default step.

**Ephemeral state.** `.deploy-state.json` exists only to resume an in-progress deploy. `verify` **deletes
it once all instances are `live`** (a partial `registered` keeps it). So a completed deploy leaves no
state → the next deploy (e.g. after a close) starts clean and can't reuse stale wallets. `verify`/`status`
work without it (runtime ids derive from the manifest). `create`'s reconcile is the safety net for partial
state. There is **no `--reinstall`** and no wallet-reuse — redeploy = `close` then `create`/`runtime`/`verify`.

## `close.py [<id>] [--all] [--instance name] [--dry-run] [--json]`

Like deploy, close **does not block** on the async flatten — it stops + triggers, returns `closing`, and
hands polling to the agent (re-run). Discovery is ledger-free and **strategy-driven**: MCP `strategy_list`
filtered by `skillName == <id>` (resolved from `strategyMetadata.skillName`) gives the package's OPEN
strategies; **`strategyId` + wallet come straight from each strategy record** — NOT via the runtime, so
close also cleans up **orphaned** strategies (wallets a failed deploy created before `runtime create`). The
runtime is used **only to stop** the strategy, found by wallet (`find_runtime_by_wallet`).
**`--all`** closes **every** OPEN strategy across all packages (for "close all strategies / return funds")
and deletes their runtimes. `--instance` needs the live runtime to identify an instance; if it's gone, omit it.
After a real package close, the package's `.deploy-state.json` is deleted (state is ephemeral).

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

- **healthy / degraded / unhealthy** — ACTIVE strategy + live runtime, per the runtime's `status` health
  (`healthy` ≠ a confirmed scanner tick — use `deploy.py verify <id>` for that; degraded prints a triage hint).
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
