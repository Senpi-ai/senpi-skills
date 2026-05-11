# Senpi Skills — Open-Source AI Trading Skills for Hyperliquid

Every file in this repo is a self-contained, plug-and-play **skill** for an autonomous AI trading agent that operates on [Hyperliquid](https://hyperliquid.xyz) via the [Senpi](https://senpi.ai) platform.

The repo is two things stacked on top of each other:

1. **Capabilities** — the runtime, the exit engine, the helpers package, the onboarding flow. Reusable infrastructure that every trading strategy plugs into.
2. **Trading Strategy Skills** — individual scanner + producer + runtime configs that embody a specific market thesis. Each skill is a directory you can pull, deploy, and run on its own funded wallet.

Skills are versioned and MIT-licensed. Anyone can fork a skill, modify it, or build a new one from scratch using the capabilities below.

**Platform:** [senpi.ai](https://senpi.ai) · **Live fleet tracker:** [strategies.senpi.ai](https://strategies.senpi.ai) · **Arena competition:** [senpi.ai/arena](https://senpi.ai/arena)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       SENPI PLATFORM                          │
│  Hyperfeed data layer · Top-trader scoring · 48 MCP tools     │
└────────────────────────────────┬─────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────┐
│                     CAPABILITIES (this repo)                  │
│                                                                │
│   senpi-trading-runtime ─────  Plugin runtime (v1.0 / v2.0)    │
│       │                        Position tracker, scanner       │
│       │                        ingest, LLM decision gate,      │
│       │                        risk guard-rails                │
│       │                                                         │
│   dsl-dynamic-stop-loss ─────  DSL exit engine                 │
│                                Phase 1 (max-loss + retrace) +  │
│                                Phase 2 (ratcheting trailing)   │
│                                                                 │
│   senpi_runtime_helpers ─────  In-process SenpiClient (v2 only)│
│       (helper branch)          producer_daemon, fcntl lock,    │
│                                fee-aware order placement       │
│                                                                 │
│   fee-optimizer       ───────  When to ALO vs MARKET           │
│   shared              ───────  Hyperfeed scoring primitives    │
│   opportunity-scanner ───────  4-stage funnel: 500 perps → top │
│   emerging-movers     ───────  SM market-rank acceleration     │
│   whale-index         ───────  Top-trader notional aggregator  │
│                                                                 │
│   senpi-entrypoint, senpi-onboard, senpi-getting-started-guide │
│                                Onboarding + setup flows        │
└────────────────────────────────┬─────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────┐
│              TRADING STRATEGY SKILLS (this repo)              │
│                                                                │
│   ~40 self-contained skills, one per directory                 │
│   Each: producer/scanner script + runtime.yaml + SKILL.md      │
│                                                                 │
│   Bucketed below by trading thesis, not asset class.           │
└────────────────────────────────┬─────────────────────────────┘
                                 │
                  ┌──────────────┴──────────────┐
                  │      Strategy wallet(s)      │
                  │ Isolated capital, on-chain   │
                  │ Each skill = its own wallet  │
                  └──────────────────────────────┘
```

---

# Capabilities

The infrastructure every trading skill plugs into. None of these are strategies — they're the substrate.

## `senpi-trading-runtime/` — Plugin Runtime

The OpenClaw plugin that owns the trading loop. Replaces the legacy Python cron + state file system.

Two major versions exist; a skill targets one of them via its `runtime.yaml`:

- **Runtime 1.0** — Python DSL cron, fcntl-locked producer scripts, openclaw subprocess for MCP calls. Most skills in this repo run on 1.0.
- **Runtime 2.0** — In-process producer daemon, direct HTTPS to MCP, declarative `risk.guard_rails`, native FEE_OPTIMIZED_LIMIT entries + exits, trade-chain DB telemetry. New skills target 2.0 by default.

Runtime version is determined by which plugin is loaded on the operator's host, not by which features the YAML declares.

## `dsl-dynamic-stop-loss/` — DSL Exit Engine

Two-phase exit logic with no Python state files:

- **Phase 1** — max-loss + consecutive-breach + retrace. Cuts losing trades early.
- **Phase 2** — ratcheting trailing stop with tiered locks (e.g. +10%/35%, +20%/55%, +35%/70% of high-water margin ROE). Lets winners run while locking incremental gains.
- **Optional time cuts** — hard_timeout, weak_peak_cut, dead_weight_cut. Single-asset agents typically disable time cuts to avoid the v1 DSL Phase 2 hard-timeout misfire.

Used by every active trading skill in the repo.

## `_helpers/senpi_runtime_helpers/` — In-process Client (Runtime 2.0 only)

> Currently lives on the `helper-mcp-envelope-aligned` branch; pulling URLs in skill READMEs reflect that. Will land on main with the runtime 2.0 stable release.

A small Python package every Runtime 2.0 skill imports:

- `SenpiClient` — direct HTTPS to MCP (no `mcporter` / `openclaw` subprocess shell-out) and direct POST to runtime `/signals`.
- `producer_daemon(fn, interval_seconds, name, tick_timeout)` — long-lived loop with built-in fcntl reentrancy guard, structured tick telemetry, signal-handled graceful shutdown.
- `log_event` / `cache` / `parallel` — shared logging schema, simple TTL cache, parallel MCP fan-out.

## `fee-optimizer/` — Order-type Decision Skill

When to use FEE_OPTIMIZED_LIMIT (ALO maker) vs MARKET orders on Hyperliquid. Standard params for entry / exit / take-profit. Loaded as a side skill at deploy time; most strategy skills already encode their choice in `runtime.yaml`.

## `shared/` — Hyperfeed Scoring Primitives

`hyperfeed_scoring.py` — reusable scoring components used by multiple strategy skills (rank-velocity, contribution-velocity, drawdown rejection). Pure stdlib.

## `opportunity-scanner/` — Universe-narrowing Funnel

Four-stage funnel that screens all 500+ Hyperliquid perps down to a top-N opportunity list. Scores 0–400 across Smart Money, market structure, technical setup, and fundamentals. Strategy skills can consume the output as one of multiple inputs.

## `emerging-movers/` — SM Rank Acceleration

Lightweight scanner that tracks Smart Money market-concentration rank changes across all Hyperliquid assets. Flags assets accelerating up the ranks before consensus solidifies.

## `whale-index/` — Top-Trader Notional Aggregator

Per-asset rollup of top-trader notional positioning. Useful as a confluence input for strategies that already have a primary signal.

## Onboarding & setup

- `senpi-entrypoint/` — Onboard an AI agent end-to-end (account, API key, MCP config, first skill install).
- `senpi-getting-started-guide/` — Guides a user through their first trade (mirror or custom strategy).
- `senpi-onboard/` — Account + API-key + MCP-server setup only.

## `autonomous-trading/` — Budget/Target/Deadline Orchestrator

Gives an agent a budget, a target, and a deadline; orchestrates DSL + Opportunity Scanner + Emerging Movers into a full lifecycle. Higher-level than any individual strategy skill.

---

# Trading Strategy Skills

Each strategy is a directory at the repo root. The bucketing below is by **how the strategy decides what to trade**, not by which asset it ends up on. A skill belongs to one bucket only.

Each row links to the skill's own README and notes the runtime version it targets.

## Single-asset alpha hunters (Kodiak family)

Patient, single-asset specialists. One ticker per skill, deep wall of confluence required before entry, DSL Phase 2 set to ride winners.

| Skill | Asset | Runtime | One-liner |
|---|---|---|---|
| [kodiak](kodiak/) | SOL | 2.0 | SOL alpha hunter — base technical score + trend strength gates |
| [grizzly](grizzly/) | BTC | 2.0 | BTC alpha hunter — Kodiak template, BTC-specific tuning |
| [polar](polar/) | ETH | 2.0 | ETH alpha hunter — hybrid hyperfeed + structural veto |
| [wolverine](wolverine/) | HYPE | 2.0 | HYPE alpha hunter — Kodiak template ported to native HYPE |

## XYZ-market specialists

Trade Hyperliquid's HIP-3 `xyz:*` perps — equities, commodities, indices, metals. 24/7 markets, different spread / funding profile than crypto.

| Skill | Universe | Runtime | One-liner |
|---|---|---|---|
| [bald-eagle](bald-eagle/) | XYZ macro | 1.0 | Wide DSL timings tuned for macro-asset rhythm |
| [kestrel](kestrel/) | XYZ macro | 2.0 | Macro breakout rider on commodities/indices/equities |
| [dire](dire/) | xyz:BRENTOIL | 1.0 | BRENTOIL specialist — news-driven oil momentum |

## Multi-signal confluence

Combine multiple independent signals (SM concentration, trend, funding, structure) and only enter when several agree.

| Skill | Runtime | One-liner |
|---|---|---|
| [cheetah](cheetah/) | 2.0 | Multi-signal confluence sniper — strict gate, lower frequency, higher quality |
| [condor](condor/) | 1.0 | "One amazing trade per day" — high-conviction momentum |
| [sentinel](sentinel/) | 1.0 | Quality-trader convergence scanner |
| [hawk](hawk/) | 1.0 | Multi-asset momentum bot |

## Smart-Money signal followers

Watch the top-trader cohort and either mirror or stalk their positions with our own DSL + risk overlay.

| Skill | Runtime | One-liner |
|---|---|---|
| [jackal](jackal/) | 2.0 | Smart Stalker — LLM-gated mirror of top-trader entries |
| [spider](spider/) | 2.0 | Patient anchor — single long-side position, 7+ day hold |
| [vulture](vulture/) | 2.0 | Long-tail momentum rider — pre-arms Phase 2 tier-2 trailing |

## Contrarian / faders

Bet against crowded positioning. Funding extremes, exhaustion, late-cycle SM crowding.

| Skill | Runtime | One-liner |
|---|---|---|
| [pangolin](pangolin/) | 2.0 | Funding rate fader — strikes against extreme funding |
| [owl](owl/) | 1.0 | Pure contrarian — crowding-unwind plays |
| [Grizzly-Horribilis](Grizzly-Horribilis/) | 1.0 | BTC contrarian sniper |
| [bison](bison/) | 1.0 | Conviction holder — wide bands, ratchet trailing |
| [lemon](lemon/) | 1.0 | Degen fader — counter-trade CHOPPY traders at peaks |
| [dog](dog/) | 1.0 | Multi-asset SM-exhaustion fader |

## Striker / rank-jump

Enter on rank acceleration or trend ignition. High frequency, tight DSL, fast exits.

| Skill | Runtime | One-liner |
|---|---|---|
| [roach](roach/) | 2.0 | Striker-only — Stalker disabled, position discipline |
| roach-b (variant) | 2.0 | Striker-only variant B — A/B partner to Roach |
| [jaguar](jaguar/) | 1.0 | Hot-streak striker — rank-jump scanner |
| [raptor](raptor/) | 1.0 | Hot streak follower |
| [orca](orca/) | 1.0 | Gen-2 striker with FIRST_JUMP detection |
| [cobra](cobra/) | 1.0 | Arena sprint predator — single-asset, concentrated margin |

## Macro / regime-aware

Cross-asset, regime detection, range-bound liquidity capture. Don't require a single primary signal.

| Skill | Runtime | One-liner |
|---|---|---|
| [mantis](mantis/) | 1.0 | Cross-asset catchup hunter — BTC lead → correlated alts |
| [mamba](mamba/) | 1.0 | Range-bound + regime protection |
| [viper](viper/) | 1.0 | Range-bound liquidity sniper |
| [komodo](komodo/) | 1.0 | Momentum event consensus |

## Velocity / pattern detection

Detect emerging acceleration before consensus solidifies.

| Skill | Runtime | One-liner |
|---|---|---|
| [phoenix](phoenix/) | 1.0 | Contribution velocity scanner — SM profit accel vs price |
| [hydra](hydra/) | 1.0 | Squeeze detector |
| [vixen](vixen/) | 1.0 | Multi-asset trend scanner |
| [shark](shark/) | 1.0 | Position tracker + liquidation cascade scanner |
| [rhino](rhino/) | 1.0 | Momentum pyramider |
| [barracuda](barracuda/) | 1.0 | Funding decay collector |

## Specialized missions

Unique theses that don't fit the buckets above.

| Skill | Runtime | One-liner |
|---|---|---|
| [turbine](turbine/) | 2.0 | Volume-rotation engine — builder-fee farming on maker-only rotation across two strategy wallets |
| [otter](otter/) | 2.0 | Open Interest velocity hunter — 1h OI delta with price confirmation |
| [python](python/) | 1.0 | Patient multi-asset scanner — multi-day hold |
| [scorpion](scorpion/) | 2.0 | Multi-market active trader — both crypto AND XYZ commodities |

---

# Repo layout

```
senpi-skills/
├── README.md                       ← this file
├── CLAUDE.md                       ← repo conventions for Claude agents
├── DSL-MIGRATION-PLAYBOOK.md       ← Runtime 1 → 2 migration notes
├── GUIDE.md                        ← general dev guide
├── catalog.json                    ← skill registry
│
├── senpi-trading-runtime/          ╮
├── dsl-dynamic-stop-loss/          │
├── _helpers/senpi_runtime_helpers/ │ Capabilities (see top of this README)
│   (on helper-mcp-envelope-aligned)│
├── fee-optimizer/                  │
├── shared/                         │
├── opportunity-scanner/            │
├── emerging-movers/                │
├── whale-index/                    │
├── autonomous-trading/             │
├── senpi-entrypoint/               │
├── senpi-getting-started-guide/    │
└── senpi-onboard/                  ╯
│
├── kodiak/  grizzly/  polar/  wolverine/      ╮
├── cheetah/ condor/   sentinel/ hawk/         │
├── jackal/  spider/   vulture/                │
├── pangolin/ owl/  bison/  lemon/  dog/       │
├── Grizzly-Horribilis/                        │
├── roach/   jaguar/  raptor/  orca/  cobra/   │ Trading Strategy Skills
├── mantis/  mamba/   viper/   komodo/         │
├── phoenix/ hydra/   vixen/   shark/  rhino/  │
├── barracuda/                                 │
├── turbine/ otter/   python/   scorpion/      │
├── bald-eagle/  kestrel/  dire/               ╯
│
└── (legacy strategy proposals: feral-fox-v3-strategy.md, ghost-fox-*,
    tiger-strategy/, wolf-strategy/, wolf-howl/ — kept for reference)
```

Each strategy directory contains:

```
<skill>/
├── README.md           ← what it does, parameters, install
├── SKILL.md            ← agent instructions (frontmatter + thesis)
├── runtime.yaml        ← runtime config (scanners, actions, risk, DSL)
├── config/             ← operator-overridable defaults
├── scripts/            ← producer / scanner Python
├── state/              ← state files (wallet-isolated subdirs)
└── references/         ← supporting docs
```

---

# Getting started

1. Deploy an [OpenClaw](https://openclaw.ai) agent and configure Senpi MCP access.
2. Pick a strategy skill from the buckets above. Read its `README.md`.
3. Install the Runtime 1.0 or 2.0 plugin per the skill's requirement. Runtime 2.0 skills additionally need the `senpi_runtime_helpers` package pulled from the `helper-mcp-envelope-aligned` branch.
4. Pull the skill's scripts + `runtime.yaml` from main into your host workspace.
5. Set the required env vars (`<SKILL>_WALLET`, `SENPI_AUTH_TOKEN`, and optionally a `<SKILL>_DECISION_MODEL` for LLM-gated actions).
6. Start the producer daemon (Runtime 2.0) or the openclaw cron (Runtime 1.0) per the skill's README.

## Requirements

- An [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+)
- A funded Hyperliquid wallet per strategy (each skill is its own wallet — no shared capital)
- [Senpi](https://senpi.ai) MCP access token

## Contributing

Each skill is self-contained. To build a new one:

1. Start from a runtime-2 skill (`kodiak/`, `cheetah/`, or `roach/`) as a template.
2. Replace the producer's signal-generation logic with your thesis.
3. Tune `runtime.yaml` — universe, score thresholds, DSL config, risk guard-rails.
4. Document in `SKILL.md` (frontmatter) and `README.md` (operator-facing).
5. Submit a PR.

Bucketing in this README is by thesis, not asset. New skills should add themselves to whichever bucket fits, or open a new one if the thesis is genuinely novel.

# License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://coinbase.com/ventures).
