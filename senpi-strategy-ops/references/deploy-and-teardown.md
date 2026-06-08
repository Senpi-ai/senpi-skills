# Deploy & teardown — the executable procedure

`install_strategy` / `uninstall_strategy` ship as **`senpi-helpers install` / `senpi-helpers uninstall`**
(in the `senpi-trading-runtime` SDK). **This doc explains what those commands do under the hood** — each
step is read straight from the package's `strategy.yaml`, so it is reproducible — and serves as the
manual fallback. Preview the exact per-instance plan (commands + env + budget split) without side
effects via `senpi-helpers install <pkg> --budget <usd> --dry-run` (or the standalone
`senpi-strategy-ops/scripts/deploy_strategy.py <pkg> --budget <usd>`).

## `strategy.yaml` → deploy mapping

Everything the deploy needs is declared in `strategy.yaml`:

| `strategy.yaml` field | Used for |
|---|---|
| `id`, `version` | attribution on the agent's wallet-creation MCP call (`skillName` / `skillVersion`) |
| `instances[]` | one wallet + one runtime + one daemon **per entry** (owl → 1, spider → 2) |
| `instances[].funding_share` | split of `budget` for this instance's wallet (`budget × share`) |
| `instances[].wallet_env` | env var the runtime render AND the daemon bind to |
| `instances[].runtime` | the `runtime.yaml` to create for this instance |
| `instances[].scanner.{entrypoint,name,signal_type}` | the daemon script + the `external_scanner` it feeds |
| `instances[].env` | instance-selecting env injected into the daemon (e.g. `SPIDER_LEG=swing`) |
| `defaults.{decision_model_env,telegram_chat_id_env,auth_token_env}` | env var **names** to populate at launch |

## Install — per instance

For **each** entry in `instances[]`:

**1. Wallet** — `senpi-helpers install` does **not** create wallets; you supply ready address(es). Per
instance, get the wallet one of two ways:
- **New wallet** (agent MCP — async + funded): `strategy_create_custom_strategy(initialBudget = budget ×
  funding_share, positions=[], skillName = <id>, skillVersion = <version>)` — **camelCase
  `skillName`/`skillVersion`**, **`initialBudget` min $100 *per wallet*** (the `funding_share` split must
  keep every instance ≥ $100). **Confirm with the user first.** Then **poll `strategy_list` by
  `strategyId` until status `ACTIVE`** and read **`strategyWalletAddress`** (creation runs `CREATE_WALLET
  → FUND_WALLET → INITIALIZE_POSITIONS → ACTIVE`, incl. EVM→Hyperliquid bridging + a $1 fee — don't
  deploy until ACTIVE).
- **Existing wallet**: use a strategy wallet the user already holds (with consent) — find via
  `strategy_list`, or the user provides the address.

Then pass the ready address(es): `senpi-helpers install <pkg> --wallet <name>=0x..` (repeat per
instance). **Idempotency** is from **live runtime state** (`is_runtime_registered`), NOT the ledger — an
instance whose wallet already has a runtime is reported `already_installed` and skipped unless
`--reinstall`.

**2. Runtime.** The `runtime.yaml` binds `${wallet_env}`, `${decision_model_env}`,
`${telegram_chat_id_env}`. `senpi-helpers install` pre-renders these and creates the runtime; the manual
equivalent **exports them first** so the engine resolves `${…}` at create time:
```
<wallet_env>=<addr>  <decision_model_env>=<bare-model>  <telegram_chat_id_env>=<id> \
  openclaw senpi runtime create --path <pkg>/<instance.runtime>
```
> **`decision_model` must be a BARE model name** (e.g. `claude-sonnet-4-20250514`) — **no provider
> prefix**. A prefixed value is the classic 500 "unknown model" failure; the runtime YAMLs warn about
> this inline. One runtime per wallet.

**3. Daemon.** Launch the scanner with the declared env:
```
<wallet_env>=<addr>  <auth_token_env>=<token>  <decision_model_env>=<bare-model>  <instance.env...> \
  nohup python3 -u <pkg>/scripts/<scanner.entrypoint> > /tmp/<id>-<instance>-scanner.log 2>&1 & disown
```
(e.g. `SPIDER_LEG=swing` for spider's swing instance). The scanner reads its wallet from `wallet_env`
and its tunables from `strategy.yaml` via `load_params()`.

**4. Verify**. `senpi-helpers install` performs a registration check after launch. Then run the
liveness-gate yourself — "running" ≠ "operating", see `liveness-verification.md`:
```
openclaw senpi runtime list        # this instance's runtime is 'running'
openclaw senpi state --json        # field-level walk
senpi-helpers health <daemon>      # exit 0 = healthy
```
The instance is **live** only when the runtime is running AND its `external_scanner` has a recent
successful tick (`runCount > 0`).

## Runtime-engine CLI (the half `senpi-helpers` doesn't cover)

`senpi-helpers` manages the **daemon** (the scanner process). The **runtime engine** is managed by the
`openclaw senpi` CLI:

| Command | Purpose |
|---|---|
| `openclaw senpi runtime create --path <yaml>` | create a runtime from a rendered `runtime.yaml` |
| `openclaw senpi runtime list` | list runtimes + status (running/stopped) |
| `openclaw senpi runtime delete --id <id>` | delete a runtime (teardown) |
| `openclaw senpi status` | plugin/runtime health summary |
| `openclaw senpi state --json` | field-level state (scanners, actions, DSL ticks) |

(See `senpi-helpers-cli.md` for the daemon side: `list`/`health`/`stats`/`start`/`stop`/`restart`.)

## Teardown — per instance (reverse order)

`senpi-helpers uninstall <package-dir> [--instance <name>]` does this from the **package + live state**
(ledger-free), in order:

1. **Stop the daemon** — find it among running daemons (`senpi-helpers list`) by the instance's
   `scanner.name`, then `senpi-helpers stop <daemon>`.
2. **Verify flat / intended** — confirm open positions are handled per the user's intent (close now,
   or let the runtime/DSL wind down). **Never silently abandon open risk.**
3. **Delete the runtime** — `openclaw senpi runtime delete --id <runtime_id>`, where `<runtime_id>` is
   the instance's `runtime.yaml` top-level `name:`.
4. Repeat for every instance; report per-instance teardown status.

**`--reinstall`** is the safe "redeploy in place": stop the old daemon → delete + recreate the runtime →
relaunch the daemon, **same wallet**.
