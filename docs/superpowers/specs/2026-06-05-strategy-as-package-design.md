# Design: Strategies as Deployable Packages + Single-Command Install

> **Status:** design (brainstormed via superpowers `brainstorming`). Design-only — no migrations
> executed. Boundary is full-stack: skills repo + installer + `@senpi-ai/runtime` may all change.
>
> **Author/reviewer loop:** this doc is the spec. After approval it transitions to an implementation
> plan (`writing-plans`).

---

## 1. Context — why this change

Today every trading strategy (owl, bison, spider, …~54) is modeled as an **agent skill**: a directory
with `SKILL.md` + `references/skill-attribution.md` + `runtime.yaml`(s) + `config/*.json` +
`scripts/*-producer.py`. That is a category error and it is the root of the problems:

- A **skill** is *knowledge an agent reads* (static, stateless, loaded into context).
- A **strategy** is a *deployable, stateful, autonomous service*: a scanner daemon + a runtime
  config + one-or-more wallets with real capital + a lifecycle.

Dressing a deployable service as a skill leaves install undefined: `install_skill` only copies the
directory, then a human/agent hand-executes a multi-step prose flow (validate wallet → write yaml →
`runtime create` → `nohup producer.py &` → grep logs). Topology (spider needs 2 wallets / 2 runtimes
/ 2 daemons) is prose-only; tunables drift across producer/config/yaml; versions drift across
frontmatter/attribution/catalog.

### The correct model (decided)

- **A strategy = `scanner` (script) + `runtime.yaml`(s).** The `runtime.yaml` is the *deterministic
  system* (scanners, actions incl. an optional `llm` decision engine, DSL exits, risk gates). The
  scanner sits *outside* the runtime and pushes signals in. "Deterministic" = the system is a fixed,
  declarative, auditable spec — not that there is no LLM.
- **A strategy is NOT a skill.** Skills are for the *agent's capability* — how to find, suggest,
  author, edit, improve, and install strategies.
- **The AI's job is authoring/editing** strategies in conversation; the produced artifact runs
  deterministically.
- **Install is one command.** The AI gathers two inputs — `budget` and `wallet` (an address or
  `"new"`) — and runs a single command. Everything else is deterministic *inside* the command.

---

## 2. What a strategy is — the package

A strategy is a **package** (folder/bundle) with three kinds of file:

```
owl/                            spider/
  scanner.py                      scanner.py
  runtime.yaml                    runtime-swing.yaml
  strategy.yaml   ◄── deploy      runtime-scalp.yaml
                      declaration  strategy.yaml   ◄── deploy declaration
```

- **`scanner.py`** — the signal producer (the logic/IP). Authored against the constrained
  `senpi_runtime_helpers` SDK. Emits signals only; never executes, never hardcodes a wallet address.
- **`runtime.yaml`** (one per instance) — the deterministic runtime spec consumed by
  `@senpi-ai/runtime`. Unchanged in shape from today (scanners, actions, DSL, risk).
- **`strategy.yaml`** — a small **deploy declaration**. The only new file. **It is data, not a
  skill.** It is the single cross-instance, install-facing source of truth.

Terminology: a strategy *definition* deploys into N **instances**. `main` for single-instance
strategies; `swing`/`scalp` for spider. (Generic — not spider's "leg".)

### 2.1 `strategy.yaml` schema

```yaml
schema_version: 1

id: spider                          # strategy identity (== package dir name)
version: "5.1.1"                    # THE one version (attribution + catalog derive from here)

catalog:                            # what discover/suggest needs
  name: "Spider — Two-Persona Style Hunter"
  emoji: "🕷️"
  tagline: "AI/Tech multi-day momentum book + macro/majors fast mean-reversion book."
  group: multi-asset-whitelist      # archetype slug
  risk_level: moderate
  min_budget: 100

requires:
  runtime: ">=1.1.0"                # @senpi-ai/runtime semver range (NOT the yaml schema major)

defaults:                           # env VAR NAMES only — never values
  decision_model_env: SPIDER_DECISION_MODEL
  telegram_chat_id_env: TELEGRAM_CHAT_ID
  auth_token_env: SENPI_AUTH_TOKEN

instances:                          # one per deployable unit. owl => length 1, spider => length 2.
  - name: swing
    runtime: runtime-swing.yaml     # this instance's runtime spec
    scanner:
      entrypoint: scanner.py
      name: spider_swing_signals    # MUST match external_scanner.name in runtime-swing.yaml
      signal_type: SPIDER_SWING_MOMENTUM
    wallet_env: SPIDER_SWING_WALLET # env var the runtime render + scanner daemon both bind to
    env:                            # instance-selecting env injected into the daemon
      SPIDER_LEG: swing
    tick_seconds: 300
    funding_share: 0.60             # share of budget when wallet="new"
    params:                         # SINGLE SOURCE of scanner tunables (no more drift)
      minScore: 5
      marginPct: 0.28
      maxLeverage: 10
      slots: 3
      # asset sets, rsi thresholds, etc.

  - name: scalp
    runtime: runtime-scalp.yaml
    scanner:
      entrypoint: scanner.py        # SAME script — multiplexed by env
      name: spider_scalp_signals
      signal_type: SPIDER_SCALP_REVERSION
    wallet_env: SPIDER_SCALP_WALLET
    env:
      SPIDER_LEG: scalp
    tick_seconds: 60
    funding_share: 0.40
    params:
      minScore: 4
      marginPct: 0.15
      maxLeverage: 5
      slots: 4
```

Single-instance strategy (owl) — same schema, `instances` length 1:

```yaml
schema_version: 1
id: owl
version: "8.0.1"
catalog:
  name: "Owl — Pure Contrarian Crowding-Unwind Hunter"
  emoji: "🦉"
  tagline: "Fades crowded perps once exhaustion confirms the unwind."
  group: contrarian-unwind
  risk_level: aggressive
  min_budget: 100
requires:
  runtime: ">=1.1.0"
defaults:
  decision_model_env: OWL_DECISION_MODEL
  telegram_chat_id_env: TELEGRAM_CHAT_ID
  auth_token_env: SENPI_AUTH_TOKEN
instances:
  - name: main
    runtime: runtime.yaml
    scanner:
      entrypoint: scanner.py
      name: owl_signals
      signal_type: OWL_CONTRARIAN_FADE
    wallet_env: WALLET_ADDRESS
    env: {}
    tick_seconds: 900
    funding_share: 1.0
    params:
      minScore: 12
      marginPct: 0.25
      maxLeverage: 10
      slots: 2
```

### 2.2 Why a separate `strategy.yaml` (not folded into `runtime.yaml`)

There is *one `runtime.yaml` per instance*. Anything that spans instances — that spider *has* two
instances, the funding split, the shared id/version/attribution/catalog — has no single `runtime.yaml`
to live in. `strategy.yaml` is that one cross-instance, install-facing file; `runtime.yaml` stays a
pure runtime spec. For a single-instance strategy it's tiny.

### 2.3 Signal routing — how the scanner knows where to publish

The scanner needs three things to publish; only the first is strategy-identity:

1. **The strategy wallet address** — the routing key. One runtime per wallet, so the address
   identifies which strategy/runtime a signal belongs to. The scanner calls
   `push_signal(address=<wallet>, …)`.
2. **The scanner name** — must equal `external_scanner.name` in that instance's `runtime.yaml`
   (e.g. `spider_swing_signals`). Binds the signal to the right scanner slot/schema.
3. **The signal endpoint** — host/port to POST to (default `127.0.0.1:8787`). Host-level, not
   strategy-specific.

There is **no separate "runtime ID"** — the wallet address *is* the identity the runtime is keyed on.

**Consequence (critical for determinism):** the scanner must NOT hardcode the address — it is unknown
until install time (especially `wallet="new"`). The address flows:

```
install_strategy → create/use wallet ─┬─► rendered into runtime.yaml  (${WALLET_ENV})
                                        └─► injected into scanner daemon env (the wallet_env)
scanner reads address from env → push_signal(address=…, scanner="…_signals")
                                → local runtime (keyed on that wallet) receives + validates
```

This is why each instance declares `wallet_env` + `scanner.name`: install wires the freshly
created/given address into *both* the runtime render and the daemon env, and the scanner name binds
the slot. The scanner stays portable, which is what makes `"new"` and re-install work.

---

## 3. The single-command install (deterministic core)

### 3.1 The AI-facing contract

The AI gathers `(budget, wallet)` and calls **one** tool:

```
install_strategy(id, budget, wallet [, decision_model] [, telegram_chat_id] [, reinstall])
```

`wallet` is either an existing strategy-wallet address or the literal `"new"`. Everything else is
optional with sane host defaults. (Under the hood this is a CLI: `senpi strategy install …`.)

### 3.2 What the command does (no AI orchestration inside)

```
1. Resolve the package by id (from the strategy registry / repo, or a workspace path for
   AI-authored one-offs). Parse strategy.yaml. Validate requires.runtime against installed engine.
2. FOR EACH instance in strategy.yaml.instances:
   a. WALLET (idempotent, keyed by (id, instance) in a host install ledger):
        - wallet=="new": create a strategy wallet funded with  budget * funding_share,
          attribution = { strategy_id: id, strategy_version: version }.
        - wallet==address (single-instance): use it.
        - re-install: reuse the ledger-recorded wallet; never double-provision.
   b. RUNTIME: render instance.runtime, substituting the wallet_env, decision_model_env,
      telegram_chat_id_env; `runtime create`. (idempotent per one-runtime-per-wallet.)
   c. DAEMON: launch instance.scanner.entrypoint with declared env:
        <wallet_env>=<addr>  <auth_token_env>=<tok>  <decision_model_env>=<model>  <instance.env...>
      (e.g. SPIDER_LEG=swing). Lock keyed by (wallet, instance).
   d. VERIFY: liveness-gate — runtime running AND the instance's external_scanner has ticked.
      Record per-instance phase (wallet_ready → runtime_created → daemon_launched → verified).
3. Return a structured report (below). Partial failure resumes the failed instance from its last
   good phase on re-run; healthy instances untouched.
```

### 3.3 Structured return (so the AI reports deterministically)

```jsonc
{
  "strategy": "spider", "version": "5.1.1", "status": "live",   // live | degraded | failed
  "instances": [
    { "name": "swing", "wallet": "0xSWING…", "budget": 600, "runtime_id": "spider-swing",
      "daemon": "spider-swing-…", "phase": "verified", "status": "live",
      "scanner_run_count": 3, "last_tick_at": "…Z" },
    { "name": "scalp", "wallet": "0xSCALP…", "budget": 400, "runtime_id": "spider-scalp",
      "daemon": "spider-scalp-…", "phase": "verified", "status": "live",
      "scanner_run_count": 7, "last_tick_at": "…Z" }
  ],
  "attribution": { "strategy_id": "spider", "strategy_version": "5.1.1" }
}
```

---

## 4. Install command — worked examples

### Single-instance (owl), create a fresh wallet

```
install_strategy(id="owl", budget=500, wallet="new")
```
→ creates 1 wallet ($500) · 1 runtime · 1 scanner daemon → returns `status: live`, 1 instance.

### Single-instance (owl), use an existing wallet

```
install_strategy(id="owl", budget=500, wallet="0xABC…")
```

### Multi-instance (spider), create fresh wallets — same two inputs

```
install_strategy(id="spider", budget=1000, wallet="new")
```
→ creates SWING wallet ($600) + SCALP wallet ($400) · 2 runtimes · 2 daemons
  (`SPIDER_LEG=swing|scalp`) → returns `status: live`, 2 instances.

### The full AI-side flow (the only steps the AI ever does)

```
AI: "How much do you want to fund it with?"     → user: "$1000"
AI: "New strategy wallet, or an existing one?"   → user: "new"
AI: install_strategy(id="spider", budget=1000, wallet="new")
AI: "Spider is live — 2 instances (swing $600, scalp $400), both scanners ticking."
```

### CLI form (what the tool wraps)

```
senpi strategy install owl    --budget 500  --wallet new
senpi strategy install spider --budget 1000 --wallet new
```

---

## 5. The skill side — agent lifecycle capability

Strategies are not skills; the **lifecycle capability** is. Recommendation: **one** skill — evolve the
existing `senpi-trading-runtime` (it already owns the SDK + runtime know-how) into the strategy
lifecycle skill, teaching the agent to:

- **find / suggest** — read the registry index (catalog) and recommend by archetype/budget/regime.
- **author** — emit a new package (`scanner.py` from the SDK + `runtime.yaml` + `strategy.yaml`),
  validated against the `strategy.yaml` schema.
- **edit / improve** — modify a package in place (tune `params`, adjust DSL, rewrite scanner logic).
- **install / operate** — call `install_strategy`; report from the structured result; manage
  start/stop/restart via the daemon CLI.

Split into several skills later only if it grows unwieldy (YAGNI). `senpi-entrypoint` keeps routing
intent (operational vs strategic vs ambiguous) and hands strategic flows to this skill.

---

## 6. Authoring & extensibility — AI as the deterministic author

The point of the package format is that **AI emits into a constrained substrate**, so its output is
deterministic to validate and install:

- **Seeds/templates:** owl/bison/spider become reference packages the AI clones and adapts.
- **Constrained scanner:** authored against `senpi_runtime_helpers` (SenpiClient, `producer_daemon`,
  `push_signal`) — no hand-rolled MCP/daemon/loop. Reads tunables from `strategy.yaml` `params`,
  reads the wallet from env.
- **Validatable declaration:** `strategy.yaml` + `runtime.yaml` have schemas; a validator asserts
  cross-file consistency (below). AI output that passes the validator installs deterministically.
- **Where packages live:** the shared registry (repo `strategies/` + generated index) for the curated
  fleet; the user's workspace for AI-authored one-offs (`install_strategy` accepts a path). Editing an
  existing strategy = modify the package → re-run `install_strategy` (idempotent).

### 6.1 Validator (consistency the install relies on)

- `id` == package dir name; `version` is the single source for attribution + catalog.
- each instance's `scanner.name` exists as an `external_scanner` in its `runtime`; `wallet_env`
  appears as `${…}` in that `runtime`.
- `runtime.yaml` top-level `version:` major == 1 (plugin schema), distinct from strategy `version`.
- `params` is the only tunable source — scanner reads it; no second copy (`_DEFAULTS`) in the scanner.
- no `@senpi/runtime` (without `-ai`) anywhere (preserves CLAUDE.md rule).

---

## 7. Attribution & versioning

`strategy_create*` carries `strategy_id` + `strategy_version` (from `strategy.yaml`) instead of
`skill_name`/`skill_version`. The CLAUDE.md attribution section is updated to the new keys. One
version (`strategy.yaml.version`) feeds the catalog entry and the attribution — no more 3-way drift.

---

## 8. Migration (described, not executed)

- **No flag day — back-compat shim:** `install_strategy` uses the package path when `strategy.yaml`
  exists; else the installer falls back to the legacy prose flow. Un-migrated skills keep working.
- **Per strategy:** add `strategy.yaml` (lift tunables from scanner/config/SKILL.md, reconciling to
  the value the *scanner actually uses*); repoint the scanner to read `params`; delete
  `config/*.json`, `references/skill-attribution.md` (now derived), and the SKILL.md skill dressing;
  keep thesis prose as the package README. Run the validator (0 errors). Smoke `install_strategy`.
- **Order:** owl → bison (single-instance) → spider (multi-instance, proves `instances[]` length-2 +
  env injection + auto-create/split wallets).
- **Catalog/registry:** generate the registry index from all `strategy.yaml`s; keep the existing
  GitHub-raw fetch URL/contract for `senpi-entrypoint`.

---

## 9. Open questions / risks

- **params vs logic boundary** — `params` holds declarative data the scanner reads; algorithm shape
  (spider `build_universe`, owl exhaustion state machine) stays in Python. Validator guards against
  pushing control flow into `strategy.yaml`.
- **decision_model / auth token** — env-only, never committed/echoed; installer validates the model
  is a bare name (no provider prefix).
- **ledger loss (host reimage)** — reconstruct `(id, instance)→wallet` from `strategy_list`
  attribution before creating; always confirm before creating a funded wallet.
- **funding_share is provisioning-only** — splits the initial budget across instance wallets; the
  runtime has no combined pool, so it is not an ongoing rebalancer.

---

## 10. Verification (for when implementation begins)

1. `install_strategy(owl, 500, "new")` → `status: live`, 1 instance, scanner `run_count>0`; re-run
   does NOT create a second wallet (ledger idempotency).
2. `install_strategy(spider, 1000, "new")` → `status: live`, 2 instances, 2 distinct wallets
   ($600/$400), both daemons ticking with `SPIDER_LEG=swing|scalp`.
3. Validator flags an intentional `scanner.name` ↔ `external_scanner` mismatch and an intentional
   version drift.
4. Grep the diff for `@senpi/` without `-ai` → no matches.
