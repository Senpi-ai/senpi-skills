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
  version: "2.2.0"
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
python3 senpi-strategy-ops/scripts/deploy.py create  <id> --budget <usd>   # 1. create wallets & fund them
python3 senpi-strategy-ops/scripts/deploy.py runtime <id>                  # 2. set up autonomous trading (DONE after this)
python3 senpi-strategy-ops/scripts/deploy.py verify  <id>                  # optional: confirm a scan fired (only if asked)
python3 senpi-strategy-ops/scripts/status.py                               # what am I running? (+ health)
python3 senpi-strategy-ops/scripts/close.py          <id>                  # teardown one strategy
python3 senpi-strategy-ops/scripts/close.py          --all                 # teardown EVERY open strategy
```
**Always tear down through `close.py`** (one `<id>` or `--all`) — it deletes the runtime *and* closes the
strategy. A raw `strategy_close` MCP call closes the strategy but **leaves the runtime registered**, which
collides on the next deploy. "close all strategies / return funds to main" → `close.py --all`.
Pass the **strategy `id`** (what `senpi-strategy-discover` hands over, e.g. `spider`); the package is
fetched from the remote if not on disk. The scripts call MCP directly (`scripts/mcp_client.py`, reads
`SENPI_AUTH_TOKEN`) + drive `openclaw senpi runtime …`. Mechanics + state machine:
[`references/lifecycle.md`](references/lifecycle.md). Manifest: [`references/strategy-yaml-schema.md`](references/strategy-yaml-schema.md).

## Deploy — two steps (then it's autonomously trading; `verify` is optional)

**Step 0 — resolve which strategy.** The user's word ("spider") is a strategy **`id`**. To confirm it
exists, check the registry; no match → hand to **senpi-strategy-discover**:
```
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/strategy-v2/strategies/catalog.json
```

**Step 1 — creating wallets & funding them** (`create`; one fresh wallet per instance; budget splits by
`funding_share`, **min $100 each** — confirm with the user first):
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
**never re-creates** a wallet. It prints **`wallets-ready`** when done. `create` is **self-healing**: it
reconciles recorded wallets against the backend (drops any CLOSED/FAILED and recreates) and **sizes each
wallet to your live balance minus a fee buffer**. So **never hand-edit `.deploy-state.json` and never
lower `--budget` to dodge a rounding/funding error** — just re-run `create`.

**Step 2 — setting up the autonomous trading strategy** (`runtime`, fast): `python3 scripts/deploy.py
runtime spider` renders each instance's runtime.yaml with its wallet and runs `openclaw senpi runtime create`.
**Self-healing**: if a runtime already exists on the right ACTIVE wallet it's skipped; if it's stale
(different/CLOSED wallet, e.g. orphaned by an earlier close) it's deleted and recreated. Prints
`registered`. `--decision-model` only for a `decision_mode: llm` action (rule-mode strategies need none).

**Once Step 2 prints `registered`, deployment is DONE — the strategy is live and trading autonomously.**
It scans on its own schedule and opens positions when *its* signals fire (spider swing ~300s, scalp ~60s
cadence). Tell the user it's set up and running; **do NOT sleep/poll waiting for the first scan tick** —
that's normal strategy behavior, not part of deploy.

**Optional — `verify`** (only if the user asks "is it actually scanning / live yet?"): `python3
scripts/deploy.py verify spider` checks each `external_scanner` once. The first `scan()` only fires on its
`interval_seconds`, so right after `runtime` it reports `registered` (not ticked yet) — expected, not a
failure; re-run after the interval to see `live`. `deploy.py status <id>` shows current state any time.
Do not run `verify` (and never `sleep` then verify) as a default step.

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

## Remember it — the last step of every deploy (and close)

A deploy isn't finished until you've **recorded it to memory**, so *any future chat* knows this strategy
exists and what it's *for* without re-deriving it (a fresh chat that can't recall the mandate ends up
grading an all-weather core or a crisis hedge on a momentum benchmark and calling it "dead weight"). On a
successful deploy, save one small, **stable** record per strategy:

- **`strategy_wallet → label`** — e.g. `0x4c68…9f91 → ox` — so you always know your strategies by name.
- **Mandate + expected behavior** — what it's *for* and how it's *meant* to run, from its `strategy.yaml`
  `catalog` (`belief_plain` / `thesis` / `archetype`). e.g. *"ox — risk-parity all-weather core:
  diversified, low-turnover, uncorrelated to rotations; flat-ish in calm is normal."*
- **DSL preset (intent)** — the `exit:` preset from `runtime.yaml`, so you know it's protected and roughly how.
- Why the user chose it, and the date.

On **close**, update the record the same way — drop the closed strategy.

**Do NOT memorize live state** — PnL, positions, DSL floor/tier, or "is it running." Those are queryable
and they drift; read them live (senpi-portfolio / `status.py` / the DSL check) when asked. Memory holds
the **names + mandates**; the live tools hold the **truth**.

**Reconcile before you assert.** A strategy can be closed from the app, a heartbeat, or another chat, or
redeployed to a new wallet — so before you state what's running, reconcile your remembered set against
`status.py` / `strategy_list`. Memory is the narrative; `status.py` is authoritative for what's live. The
full config (exact scanner / scoring / DSL tiers) is **not** memory — look it up in the package
(`runtime.yaml` / `scan.py`, keyed by `skillName`+`version`) only when the user wants the exact mechanics.

### Worked example — "install spider"
```
user: "deploy spider with $200"
1. resolve → id = spider (two instances: swing 60% / scalp 40%; $200 → swing ~$120, scalp $100 min)
2. create → python3 scripts/deploy.py create spider --budget 200
            → wallets-ready  (if "creating", re-run the same command until wallets-ready)
3. runtime → python3 scripts/deploy.py runtime spider          → registered (spider-swing + spider-scalp)
4. verify  → python3 scripts/deploy.py verify spider           → live  (re-run if a slow instance hasn't ticked)
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
of **orphan runtimes**. `--fast` skips the per-runtime health call; `--json` for machine output. **Tell the
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
