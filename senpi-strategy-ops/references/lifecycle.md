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

## `deploy.py {create|runtime|verify|status} <id> …`

Deploy is **three short, resumable steps**, not one blocking call — because wallet funding (CREATE →
FUND → ACTIVE, incl. bridging) and the first scan tick (up to a leg's `interval_seconds`) are each
multi-minute waits that blow the ~180s tool/session timeout. Each step is **bounded** (`--max-wait`,
default 150s) and the agent **re-runs a step until it reports done**.

**Package fetch.** Any step fetches `strategies/<id>/` from the remote if it isn't on disk (`_fetch.py`:
GitHub tree + raw from `SENPI_SKILLS_REPO`@`SENPI_SKILLS_REF`, default `Senpi-ai/senpi-skills`@`strategy-v2`;
`--ref` overrides).

**State file** `<pkg>/.deploy-state.json` — `{instances: {name: {strategyId, wallet, status}}}`, status
flowing `pending → creating → active → registered → live`. Every sub-action persists, so a kill mid-step
just means re-run that step.

1. **`create <id> --budget N`** — per instance, `strategy_create_custom_strategy(skillName=<id>,
   skillVersion=<version>, initialBudget=max(100, N×funding_share))`; the `strategyId` is recorded to the
   state file **immediately** (before any polling) so a re-run **resumes instead of re-creating**. Then
   poll `strategy_list` by `strategyId` to **ACTIVE** → record wallet. Bounded: not all ACTIVE within
   `--max-wait` → exit **`creating`** (re-run to resume); all ACTIVE → **`wallets-ready`**.
   **Anti-duplicate guard:** before creating, it lists `strategy_list` for `skillName==<id>`; if it finds
   strategies **not** in the state file (an interrupted prior run), it **refuses** and says to `close.py
   <id>` first — never blindly funds duplicates.
2. **`runtime <id>`** — per instance: render the leg's `runtime.yaml` (substitute `${wallet_env}` + the
   decision-model env iff a `decision_mode: llm` action) **beside the source** (so `path: ./scanners`
   resolves) → `openclaw senpi runtime create -p <rendered> --runtime-id <id>-<instance>`. Idempotent:
   skips a runtime that already exists. Requires wallets `active` (run `create` first). → `registered`.
3. **`verify <id>`** — bounded poll of `openclaw senpi state -r <id>-<instance>` until each
   `external_scanner` shows a completed tick. All ticked → **`live`**; deadline hit → **`registered`**
   (re-run `verify`). `status <id>` prints the state file any time.

There is **no `--reinstall`** and no wallet-reuse — redeploy = `close` then `create`/`runtime`/`verify`.

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
