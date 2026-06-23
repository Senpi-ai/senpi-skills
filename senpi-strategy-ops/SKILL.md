---
name: senpi-strategy-ops
description: >-
  Deploy / monitor / close a NAMED Senpi trading strategy (a.k.a. a "predator").
  Use when the user names a strategy to run, e.g. "install spider", "deploy the
  polar strategy", "set up kodiak", "run the spider strategy", "is my strategy
  live?", "stop/close/uninstall polar". A strategy is a PACKAGE (strategy.yaml +
  one runtime.yaml per instance + scanners/); the runtime SUPERVISES each
  scanner's scan(inputs, ctx) in-process — there is NO scanner daemon to launch.
  deploy.py runs in three resumable steps (create wallets → runtime create →
  verify ticking) and close.py tears down (stop runtime + strategy_close, which
  flattens positions and returns funds). The strategy
  id (spider, polar, kodiak) is the package folder; match the user's word to a
  registry/catalog id. NOT for choosing WHICH strategy (senpi-strategy-discover)
  or building/editing one (senpi-strategy-author).
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0.0"
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
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget <usd>   # 1. create + fund wallet(s)
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>                  # 2. render + runtime create
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                  # 3. confirm scanners tick
python3 senpi-strategy-ops/scripts/close.py          <id>                  # teardown
```
Pass the **strategy `id`** (what `senpi-strategy-discover` hands over, e.g. `spider`); the package is
fetched from the remote if not on disk. The scripts call MCP directly (`scripts/_mcp.py`, reads
`SENPI_AUTH_TOKEN`) + drive `openclaw senpi runtime …`. Mechanics + state machine:
[`references/lifecycle.md`](references/lifecycle.md). Manifest: [`references/strategy-yaml-schema.md`](references/strategy-yaml-schema.md).

## Deploy — three resumable steps

**Step 0 — resolve which strategy.** The user's word ("spider") is a strategy **`id`**. To confirm it
exists, check the registry; no match → hand to **senpi-strategy-discover**:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/strategy-v2/strategies/catalog.json
```

**Step 1 — `create`** (one fresh wallet per instance; budget splits by `funding_share`, **min $100 each**
— confirm with the user first):
```
python3 scripts/deploy.py create spider --budget 200
```
Per instance it calls `strategy_create_custom_strategy(skillName=<id>, skillVersion=<version>)`, records
the `strategyId`, and polls `strategy_list` to **ACTIVE** — **bounded** (~150s). If it prints
**`creating`** (wallets still funding), just **re-run the same `create` command** — it resumes from the
state file and **never re-creates** a wallet. It prints **`wallets-ready`** when done.

**Step 2 — `runtime`** (fast): `python3 scripts/deploy.py runtime spider` renders each leg's runtime.yaml
with its wallet and runs `openclaw senpi runtime create`. Idempotent (skips an existing runtime). Prints
`registered`. `--decision-model` only for a `decision_mode: llm` action (rule-mode strategies need none).

**Step 3 — `verify`** (resumable): `python3 scripts/deploy.py verify spider` polls until each
`external_scanner` has ticked (bounded). Prints `live`; if a slow leg hasn't ticked within its
`interval_seconds`, it prints `registered` — **re-run `verify`** to keep checking. `deploy.py status
<id>` shows current state any time.

> **Do NOT improvise.** A package strategy is a **runtime-supervised scanner** — deploy it **only** via
> these steps. Never substitute a raw `strategy_create_custom_strategy` MCP call to "deploy" it: that
> makes an **empty** custom-position strategy, not the running scanner. Funding is **automatic**
> (Hyperliquid perps → HL spot → EVM bridge). If `create` reports insufficient USDC / `available: 0`, the
> wallet genuinely lacks accessible funds (often locked in other strategies) — have the user fund/free
> USDC, then **re-run `create`**. Do not switch tools. If `create` **refuses** with "existing strategies
> not in deploy state", a prior run was interrupted — `close.py <id>` the strays first, then `create`.

**Report** from the structured output, not raw logs:
```jsonc
{ "strategy":"spider","version":"6.0.0","status":"live",
  "attribution":{ "skillName":"spider","skillVersion":"6.0.0" },
  "instances":[ { "instance":"swing","runtime_id":"spider-swing","wallet":"0x…","status":"live" },
                { "instance":"scalp","runtime_id":"spider-scalp","wallet":"0x…","status":"live" } ] }
```
Overall status across the steps: `create` → `creating` (re-run) | `wallets-ready`; `runtime` →
`registered`; `verify` → `live` (scanner ticked) | `registered` (re-run verify). Per-instance status
flows `pending → creating → active → registered → live`. **`registered` ≠ ticking.** `create`/`runtime`
take `--dry-run` (plan only; no side effects).

### Worked example — "install spider"
```
user: "deploy spider with $200"
1. resolve → id = spider (two instances: swing 60% / scalp 40%; $200 → swing ~$120, scalp $100 min)
2. create → python3 scripts/deploy.py create spider --budget 200
            → wallets-ready  (if "creating", re-run the same command until wallets-ready)
3. runtime → python3 scripts/deploy.py runtime spider          → registered (spider-swing + spider-scalp)
4. verify  → python3 scripts/deploy.py verify spider           → live  (re-run if a slow leg hasn't ticked)
```

### Host prerequisites
`openclaw` + the `@senpi-ai/runtime` plugin running; `SENPI_AUTH_TOKEN` exported (the same token the
MCP session uses); PyYAML available (`python3 -m pip install pyyaml` if missing). The package itself does
**not** need to be pre-placed — it is fetched. Smoke `create`/`runtime` with `--dry-run` first.

## Monitor — is it actually live?

Do **not** trust "runtime: running" alone. A strategy is **live** only when its runtime is running AND
each instance's `external_scanner` has a recent successful tick. Verify with the runtime CLI:
- `openclaw senpi status -r <runtime_id> --json` / `openclaw senpi state -r <runtime_id> --json`
- field-level liveness decision tree → [`references/liveness-verification.md`](references/liveness-verification.md)
- DSL / action / position troubleshooting → `openclaw senpi dsl|action …` (see lifecycle.md) and the
  engine mental model in `senpi-trading-runtime/references/runtime-concepts.md`

`runtime_id` = each leg's `runtime.yaml` top-level `name` (`spider-swing`, `spider-scalp`); they all
carry `group: <id>`, so you can rediscover a deployed strategy's runtimes ledger-free via
`openclaw senpi runtime list` matching `group == <id>`.

## Close — stop → trigger → (agent polls)

```
python3 scripts/close.py spider          # stop runtime(s) + trigger strategy_close, return immediately
python3 scripts/close.py spider          # re-run = poll; reports `closed` once flattened
```
Per strategy: **stop the runtime** (if live) → **trigger `strategy_close`** (flattens **all** positions
+ closes the strategy, funds returned). `strategy_close` is **async**, so the script **does not wait** —
it returns `closing` and hands polling to you: **re-run `close.py spider`** until it reports `closed`.
Re-runs are idempotent (runtime already gone → skip; already closing/closed → no re-submit). Strategies
are discovered from `strategy_list` (`skillName==<id>`), so close also cleans up **orphaned** wallets
that have no runtime. `--instance <name>` scopes a leg (needs its live runtime to map; else omit to close
all). **Redeploy** = `close` then `create`/`runtime`/`verify`.

## Invariants

- The wallet-creation MCP call carries attribution **`skillName`/`skillVersion` = the package
  `strategy.yaml` `id`/`version`** (not this skill's). `deploy.py` does this automatically.
- The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.
