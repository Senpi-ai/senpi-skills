# Senpi Skills — Open-Source AI Trading for Hyperliquid

Open-source **skills** and **strategy packages** for autonomous AI trading agents that operate on
[Hyperliquid](https://hyperliquid.xyz) via the [Senpi](https://senpi.ai) platform.

The repo is two things:

1. **Core skills** — the agent's lifecycle capabilities: discover a strategy, build one, deploy/run it,
   and the runtime-engine contract they all share. Reusable, MIT-licensed.
2. **Strategy packages** — deployable trading strategies, each a directory under `strategies/`. A
   package embodies a market thesis and runs on its own funded wallet. Browse the catalog and pick one
   with `senpi-strategy-discover`; deploy it with `senpi-strategy-ops`.

> 🛠 **Building a strategy?** Start with **`senpi-strategy-author`** (how to build/edit a package) and
> **`senpi-trading-runtime`** (the runtime contract your `scan()` runs under).

**Platform:** [senpi.ai](https://senpi.ai) · **Live fleet tracker:** [strategies.senpi.ai](https://strategies.senpi.ai) · **Arena:** [senpi.ai/arena](https://senpi.ai/arena)

---

## Core skills

Four skills cover the full strategy lifecycle. They consume the Senpi MCP surface; strategy code never
calls MCP directly — it reads through the runtime.

| Skill | Role | What it owns |
|---|---|---|
| [`senpi-strategy-discover`](senpi-strategy-discover/) | **Find / recommend** | A conversational, analyst-style picker. Ranks the `strategies/catalog.json` registry against the user's goals ("what should I trade?"). |
| [`senpi-strategy-author`](senpi-strategy-author/) | **Build / edit** | Create a new strategy package from scratch or clone/tune an existing one — `scan()` logic, `runtime.yaml`, DSL exits, risk gates. |
| [`senpi-strategy-ops`](senpi-strategy-ops/) | **Deploy / monitor / close** | The lifecycle commands: `deploy.py <id>` (create wallets → deploy → verify) and `close.py <id>` (stop → close). |
| [`senpi-trading-runtime`](senpi-trading-runtime/) | **Runtime contract** | The infra bundle: how `@senpi-ai/runtime` behaves — it supervises each package's `scan(inputs, ctx)`, validates/sizes/executes signals, and runs the two-phase DSL exits. The other three skills reference it. |

The runtime package is **`@senpi-ai/runtime`** — what operators install on their Hyperliquid hosts.

---

## Strategy packages

A strategy is a **package**, not a skill — adopting it provisions a funded strategy wallet that trades
its thesis. Packages live under `strategies/<id>/`:

```
strategies/
├── catalog.json              ← the registry (GENERATED — never hand-edit)
└── spider/                   ← a strategy package; <id> == dir name
    ├── strategy.yaml         ← deploy manifest: id, version, catalog, instances[]
    ├── swing/                ← one instance (multi-instance strategies have several, each its own wallet)
    │   ├── runtime.yaml      ← self-contained runtime spec (scanners, actions, exit, risk)
    │   └── scanners/
    │       ├── scan.py       ← exports scan(inputs, ctx) -> list[dict] of signals
    │       └── scoring.py    ← pure, unit-testable thesis math
    └── scalp/  …
```

- **`strategy.yaml`** is the single source of truth for deploy + attribution (`id`, `version`,
  `catalog`, `instances[]`). It bundles one or more `runtime.yaml` instances into one deployable unit.
- **`runtime.yaml`** is the runtime's own self-contained spec. The runtime **spawns and supervises**
  `scan()`, calling it every `interval_seconds` and owning everything downstream — signal validation,
  sizing/execution (`FEE_OPTIMIZED_LIMIT`), slot accounting, `risk.guard_rails`, and the two-phase DSL
  trailing-stop exits. **There is no separate scanner daemon.**
- **`strategies/catalog.json`** is the registry index, generated from every `strategies/*/strategy.yaml`
  via `senpi-trading-runtime/scripts/gen_catalog.py`. `senpi-strategy-discover` reads it.

### Lifecycle

```
discover ──▶ author ──▶ ops
  pick      (build/tune    deploy.py <id> --budget <usd>   → creates a fresh wallet per instance,
 from        a package)    deploys, cross-verifies each scanner ticked
catalog                    close.py <id>                   → stops runtimes, closes strategies
```

Deploy always provisions fresh wallets (one per instance, split by `funding_share`, ≥$100 each, with
MCP attribution `skillName`/`skillVersion` = the package `id`/`version`); close always flattens positions
and closes the strategy. Redeploy = `close` then `deploy`.

The full fleet (~50 strategies) is the `strategies/catalog.json` registry — **browse it with
`senpi-strategy-discover` rather than a hand-maintained list here.**

---

# Repo layout

```
senpi-skills/
├── README.md                   ← this file
├── CLAUDE.md                   ← repo conventions for AI editors
│
├── senpi-strategy-discover/    ← find / recommend a strategy (reads the catalog)
├── senpi-strategy-author/      ← build / edit a strategy package
├── senpi-strategy-ops/         ← deploy / monitor / close   (deploy.py, close.py)
├── senpi-trading-runtime/      ← the @senpi-ai/runtime contract + gen_catalog.py
│
├── strategies/                 ← strategy packages + the registry
│   ├── catalog.json            ← GENERATED registry index
│   └── <id>/                   ← one package per strategy (e.g. spider/)
│       ├── strategy.yaml
│       └── <instance>/{runtime.yaml, scanners/}
│
└── docs/                       ← design docs
```

---

# Getting started

1. Onboard an [OpenClaw](https://openclaw.ai) agent and configure Senpi MCP access (`SENPI_AUTH_TOKEN`).
   Install the runtime plugin: `openclaw plugins install @senpi-ai/runtime`.
2. **Pick a strategy** — use `senpi-strategy-discover` (or read `strategies/catalog.json`) to choose one.
3. **Deploy it** — `python3 senpi-strategy-ops/scripts/deploy.py <id> --budget <usd>`. This creates a
   funded wallet per instance, deploys each `runtime.yaml`, and verifies the scanner is ticking.
4. **Monitor** — `openclaw senpi status` / `state`; the strategy is live once its `external_scanner`
   has a recent successful tick.
5. **Close** — `python3 senpi-strategy-ops/scripts/close.py <id>` flattens positions and closes the
   strategy, returning funds.

To **build a new strategy**, start from `senpi-strategy-author` and the `senpi-trading-runtime` contract.

## Requirements

- An [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+) with the `@senpi-ai/runtime` plugin
- A funded Hyperliquid wallet per strategy instance (no shared capital)
- A [Senpi](https://senpi.ai) MCP access token (`SENPI_AUTH_TOKEN`)

# License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://coinbase.com/ventures).
