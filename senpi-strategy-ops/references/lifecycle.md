# Lifecycle internals — what `deploy.py` and `close.py` actually do

Both scripts live in `senpi-strategy-ops/scripts/`, share `_pkg.py` (package model) + `_cli.py`
(openclaw CLI + tolerant JSON digging) + `_mcp.py` (vendored stdlib HTTP MCP client, reads
`SENPI_AUTH_TOKEN` / `SENPI_MCP_URL`). They are stdlib-only (plus PyYAML). No daemons, no `push_signal`.

## Why there is no scanner daemon anymore

Runtime 2.x **supervises** the scanner. `openclaw senpi runtime create -p <runtime.yaml>` hot-loads the
runtime, which spawns the Python scaffold child and calls `scan(inputs, ctx)` every `interval_seconds`,
time-boxed by `timeout_seconds`, restarting a crashed child itself. The old model (a separate
`nohup python3 … &` producer daemon that pushed signals over HTTP) is gone. Deploy = create a runtime;
nothing else to keep alive.

## `deploy.py <id> --budget N [--decision-model M] [--ref <branch>] [--no-fetch] [--dry-run] [--json]`

**Fetch (step 0).** The agent host has the skills installed but not the strategy packages, so if
`strategies/<id>/` isn't on local disk, `deploy.py` downloads it from the remote (`_fetch.py`: GitHub
tree listing + raw file fetch from `SENPI_SKILLS_REPO`@`SENPI_SKILLS_REF`, default
`Senpi-ai/senpi-skills`@`strategy-v2`; `--ref` overrides, `--no-fetch` disables). The rendered
runtime.yaml is written **beside** the source so its relative `path: ./scanners` still resolves.

**Pre-check.** Deploy always creates fresh wallets, so before creating anything it looks up every leg's
runtime (`<id>-<instance>`) in `runtime list`; if **any** is already live it **refuses** ("already
deployed — run close.py first") and creates **no** wallets. Redeploy = `close` then `deploy`.

Per instance, in order:

1. **Wallet — always create new.** MCP `strategy_create_custom_strategy(initialBudget = max(100,
   budget × funding_share), positions=[], skillName=<id>, skillVersion=<version>)`, then poll
   `strategy_list` by `strategyId` until status **ACTIVE** → read `strategyWalletAddress`. (Async; the
   submit uses a raised HTTP timeout, completion comes from the poll.) There is **no `--wallet` reuse**;
   `--budget` is required for a real run.
2. **Render.** Substitute `${wallet_env}` (+ the decision-model env iff the runtime has a
   `decision_mode: llm` action) into the leg's `runtime.yaml` → `<pkg>/.build/<instance>.runtime.yaml`.
   Asserts **zero unresolved `${...}`** before continuing. No telegram is injected (deprecated).
3. **Create.** `openclaw senpi runtime create -p <rendered> --runtime-id <id>-<instance>`. Pinning
   `--runtime-id` keeps the runtime id equal to the runtime.yaml `name` (else it derives from the build
   filename), so verify/close lookups match.
4. **Cross-verify.** Poll `runtime list --json` (present + running) → `state -r <id> --json` until the
   `external_scanner` shows a completed tick (a positive run count) or the deadline
   (`interval_seconds` + buffer) elapses. `live` only when a tick is confirmed; `registered` if the
   runtime is up but no tick yet (not live — run Monitor); `failed` if create failed or the runtime never
   ran.

There is **no `--reinstall`** (a fresh deploy always wants fresh wallets, so it can't reuse a live
runtime's wallet). Redeploy in place = `close` then `deploy`. `--dry-run` validates + plans (renders in
memory) with **no** side effects: no wallet creation, no create.

## `close.py <id> [--instance name] [--timeout S] [--dry-run] [--json]`

Discovery is ledger-free and **strategy-driven**: MCP `strategy_list` filtered by `skillName == <id>`
gives the package's strategies, and **`strategyId` + wallet come straight from each strategy record** —
NOT via the runtime. This is the key fix: close must not depend on a live runtime to resolve the id, or
it can't clean up **orphaned** strategies (e.g. wallets a failed deploy created before `runtime create`).
The runtime is used **only to stop** the strategy, found by wallet (`find_runtime_by_wallet`), if one is
live. `--instance` scoping needs the live runtime to know which strategy is that leg (the strategy record
has no leg label); if it's gone, omit `--instance` to close the whole strategy.

Per strategy, in order:

1. **Stop the runtime if one is live** — match it by wallet, `openclaw senpi runtime delete --id <name>
   --address <wallet>`, confirm gone. Orphans (no runtime) skip straight to step 2.
2. **Close the strategy** — submit MCP `strategy_close(strategyId)`. `strategy_close` flattens **all**
   positions **and** closes the strategy (returns funds); there is **no** separate close-positions step.
3. **Confirm (async!).** `strategy_close` returns before positions are actually flat on-chain. The submit
   uses a raised HTTP timeout, then the script **polls `strategy_list` by `strategyId` until status
   `CLOSED`** (or the strategy drops out of the list) under `--timeout` (default 300s). Reports `closed`
   only when confirmed; `closing` (positions still flattening) — not a hang, not a false success — if the
   deadline passes.

Close **always** closes the strategy (it never just stops the runtime). `--instance` scopes which leg(s)
to close.

## Runtime CLI surface used (from `senpi-trading-runtime/references/runtime-cli.md`)

| Command | Used for |
|---|---|
| `openclaw senpi runtime create -p <yaml> --runtime-id <id>` | deploy step 3 |
| `openclaw senpi runtime list [--json]` | idempotency check, verify, reverse lookup, close discovery |
| `openclaw senpi runtime delete --id <id> --address <wallet>` | reinstall, close step 1 |
| `openclaw senpi status -r <id> [--json]` / `state -r <id> [--json]` | verify + Monitor (scanner ticked) |
| `openclaw senpi dsl positions\|inspect\|closes` · `action list\|history\|decisions` | troubleshooting |

## MCP tools used (via `_mcp.py`)

`strategy_create_custom_strategy` (create wallet) · `strategy_list` (poll ACTIVE/CLOSED, reverse lookup) ·
`strategy_close` (flatten + close) · optionally `strategy_get` / `strategy_get_clearinghouse_state` to
cross-check zero open positions. Note: `timeout=` on a call is the **HTTP request** timeout, not the
async on-chain completion time — lifecycle ops submit then poll.
