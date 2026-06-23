---
name: senpi-strategy-ops
description: >-
  Deploy / monitor / close a NAMED Senpi trading strategy (a.k.a. a "predator").
  Use when the user names a strategy to run, e.g. "install spider", "deploy the
  polar strategy", "set up kodiak", "run the spider strategy", "is my strategy
  live?", "stop/close/uninstall polar". A strategy is a PACKAGE (strategy.yaml +
  one runtime.yaml per instance + scanners/); the runtime SUPERVISES each
  scanner's scan(inputs, ctx) in-process — there is NO scanner daemon to launch.
  Two one-shot lifecycle commands own everything: deploy.py (always creates fresh
  wallets, runs runtime create, cross-verifies) and close.py (stops the runtime,
  then strategy_close, which flattens positions and returns funds). The strategy
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
`push_signal`.** Ops owns the deployed lifecycle as **two one-shot commands**:

```
python3 senpi-strategy-ops/scripts/deploy.py <id> --budget <usd> [--decision-model <m>] [--dry-run]
python3 senpi-strategy-ops/scripts/close.py  <id> [--instance <name>] [--dry-run]
```

Both scripts call MCP directly (vendored stdlib client `scripts/_mcp.py`, reads `SENPI_AUTH_TOKEN`) and
drive `openclaw senpi runtime …` — so each is a true single command. Mechanics:
[`references/lifecycle.md`](references/lifecycle.md). Manifest schema:
[`references/strategy-yaml-schema.md`](references/strategy-yaml-schema.md).

## Deploy — create → run → verify (one command)

**Step 0 — resolve which strategy.** The user's word ("spider", "the polar predator") is a strategy
**`id`** = the package directory name (`spider/` = `id: spider`). Match it to an `id` in the registry:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/strategy-v2/catalog.json
```
(or `ls` the packages on the host). No match → hand off to **senpi-strategy-discover** to pick one.

**Step 1 — deploy.** Deploy **always creates one fresh wallet per instance** (budget split by
`funding_share`, **min $100 each**) via MCP — confirm the budget with the user first:
```
python3 scripts/deploy.py spider --budget 200
```
The script, per instance: `strategy_create_custom_strategy(initialBudget=<share>, positions=[],
skillName=<id>, skillVersion=<version>)` → polls `strategy_list` by `strategyId` until **ACTIVE** →
renders the leg's `runtime.yaml` with its wallet → `openclaw senpi runtime create` → **cross-verifies**
the `external_scanner` actually ticked. There is **no wallet-reuse path**: if the strategy is already
deployed, deploy **refuses** ("already deployed — close first") and creates no wallets; redeploy =
**close then deploy**. `--decision-model <bare-model>` is required **only** if a `runtime.yaml` has a
`decision_mode: llm` action (rule-mode strategies like spider need none).

**Report** from the structured output, not raw logs:
```jsonc
{ "strategy":"spider","version":"6.0.0","status":"live",
  "attribution":{ "skillName":"spider","skillVersion":"6.0.0" },
  "instances":[ { "instance":"swing","runtime_id":"spider-swing","wallet":"0x…","status":"live" },
                { "instance":"scalp","runtime_id":"spider-scalp","wallet":"0x…","status":"live" } ] }
```
Per-instance status: `live` (runtime up **and** a scanner tick confirmed) · `registered` (runtime up,
no tick confirmed before timeout — **not live yet**, run Monitor) · `failed`. **`registered` ≠ ticking.**
Preview first with `--dry-run` (validates + plans; no wallet creation, no side effects).

### Worked example — "install spider"
```
user: "deploy spider with $200"
1. resolve → id = spider (package spider/, two instances: swing 60% / scalp 40%)
2. confirm budget ($200 → swing ~$120, scalp $100 min) → deploy:
   python3 scripts/deploy.py spider --budget 200
   → creates 2 wallets (attribution spider/6.0.0), runtime create spider-swing + spider-scalp, verifies
3. report → status "live" once both external scanners have ticked
```

### Host prerequisites
`openclaw` + the `@senpi-ai/runtime` plugin running; `SENPI_AUTH_TOKEN` exported; PyYAML available; the
strategy package on disk (repo/registry checkout). Smoke with `--dry-run` first.

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

## Close — stop → close (one command)

```
python3 scripts/close.py spider
```
Per leg: **stop the runtime** (`runtime delete`, confirm gone so nothing re-opens) → **`strategy_close`**,
which flattens **all** positions and closes the strategy (funds returned). `strategy_close` is **async** —
the script submits then **polls `strategy_list` until `CLOSED`** under a bounded deadline (`--timeout`),
reporting `closed` only when confirmed and `closing` (positions still flattening) rather than hanging if
the deadline passes. Close **always** closes the strategy. `--instance <name>` scopes which leg(s) to
close. **Redeploy** = `close` then `deploy` (deploy always provisions fresh wallets — there is no
in-place reinstall).

## Invariants

- The wallet-creation MCP call carries attribution **`skillName`/`skillVersion` = the package
  `strategy.yaml` `id`/`version`** (not this skill's). `deploy.py` does this automatically.
- The runtime package is **`@senpi-ai/runtime`** — never `@senpi/runtime`.
