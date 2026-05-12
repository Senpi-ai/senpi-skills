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
┌──────────────────────────────────────────────────────────────────────────┐
│                            SENPI PLATFORM                                 │
│   Hyperfeed data layer  ·  Top-trader scoring  ·  Hyperliquid execution   │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────┐
│                        SENPI MCP — 68 tools, 13 categories                │
│                                                                            │
│   Discovery (5)            Hyperfeed (6)        Strategy lifecycle (12)   │
│   Strategy state (4)       Position (4)         Execution (4)             │
│   Market data (6)          Ratchet Stop (6)     Arena (5)                 │
│   Audit (3)                Account (2)          Treasury (2)              │
│   User & rewards (7)       Documentation (2)                              │
│                                                                            │
│   Full table further down ↓                                                │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────┐
│                        CAPABILITIES (this repo)                           │
│                                                                            │
│   senpi-trading-runtime  ─── @senpi/runtime plugin (>= v1.1.0)            │
│                              Consumes: Strategy state, Position,           │
│                              Execution, Audit MCP categories               │
│                                                                            │
│   dsl-dynamic-stop-loss  ─── DSL exit engine                              │
│                              Phase 1 (max-loss + retrace) +                │
│                              Phase 2 (ratcheting trailing)                 │
│                              Consumes: Strategy state, Position MCP        │
│                                                                            │
│   senpi_runtime_helpers  ─── In-process SenpiClient + producer_daemon     │
│                              (helpers-native skills only)                  │
│                              Wraps: ALL MCP categories via SenpiClient     │
│                              CLI: senpi-helpers list / health / restart    │
│                                                                            │
│   fee-optimizer          ─── When to ALO vs MARKET                         │
│   shared                 ─── Hyperfeed scoring primitives                  │
│   opportunity-scanner    ─── 4-stage funnel: 500 perps → top               │
│   emerging-movers        ─── SM market-rank acceleration                   │
│   whale-index            ─── Top-trader notional aggregator                │
│                              Consume: Discovery, Hyperfeed, Market data    │
│                                                                            │
│   senpi-entrypoint, senpi-onboard, senpi-getting-started-guide             │
│                              Onboarding + setup flows                      │
│                              Consume: User & rewards, Account, Docs        │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────┐
│                  TRADING STRATEGY SKILLS (this repo)                      │
│                                                                            │
│   ~40 self-contained skills, one per directory                             │
│   Each: producer/scanner script + runtime.yaml + SKILL.md                  │
│                                                                            │
│   Bucketed below by trading thesis, not asset class.                       │
│   Skills consume MCP via the runtime + helpers; never call MCP directly.   │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                   ┌───────────────┴───────────────┐
                   │      Strategy wallet(s)        │
                   │ Isolated capital, on-chain     │
                   │ Each skill = its own wallet    │
                   └────────────────────────────────┘
```

The data flow is **upward-from-Hyperliquid, gated-downward-through-MCP**: market state and on-chain positions are read via MCP, capabilities turn those reads into actions (signals, decisions, exits), and the runtime pushes back through MCP to execute on Hyperliquid. Strategy skills produce signals; they do **not** call MCP directly — all MCP traffic goes through the runtime or the helpers package.

---

# Capabilities

The infrastructure every trading skill plugs into. None of these are strategies — they're the substrate.

## At a glance

Every capability is a thin layer over a specific slice of the Senpi MCP surface. The table below maps each capability to the MCP categories it depends on and the most-used tools within those categories.

| Capability | MCP categories used | Key tools touched |
|---|---|---|
| `senpi-trading-runtime` | Strategy state · Position · Execution · Audit · Ratchet Stop | `strategy_get_clearinghouse_state`, `create_position`, `edit_position`, `close_position`, `cancel_order`, `execution_get_open_position_details`, `audit_query`, `ratchet_stop_*` |
| `dsl-dynamic-stop-loss` | Strategy state · Position · Ratchet Stop | `strategy_get_clearinghouse_state`, `ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_events`, `close_position` |
| `senpi_runtime_helpers` | ALL — the in-process client wraps every MCP tool | `mcp_call(tool, **params)` — generic dispatch over the full 68-tool surface |
| `fee-optimizer` | Market data · Position | `market_get_asset_data`, `create_position` (FEE_OPTIMIZED_LIMIT params) |
| `shared` (`hyperfeed_scoring`) | Hyperfeed · Discovery | `leaderboard_get_top`, `leaderboard_get_trader`, `discovery_get_top_traders` |
| `opportunity-scanner` | Hyperfeed · Discovery · Market data | `leaderboard_get_markets`, `discovery_get_top_traders`, `market_get_asset_data`, `market_get_funding_regime` |
| `emerging-movers` | Hyperfeed | `leaderboard_get_markets`, `leaderboard_get_momentum_events` |
| `whale-index` | Hyperfeed · Discovery | `leaderboard_get_top`, `leaderboard_get_trader_positions`, `discovery_get_trader_state` |
| `autonomous-trading` | Strategy lifecycle · Position · Account | `strategy_create_custom_strategy`, `strategy_top_up`, `account_get_portfolio`, `create_position`, `close_position` |
| `senpi-entrypoint`, `senpi-onboard`, `senpi-getting-started-guide` | User & rewards · Account · Strategy lifecycle · Documentation | `user_get_me`, `account_get_portfolio`, `strategy_create`, `list_senpi_guides`, `read_senpi_guide` |

Detail on each capability follows. The full tool surface is enumerated in the **Senpi MCP — Tool Reference** section below.

## `senpi-trading-runtime/` — Plugin Runtime

The OpenClaw plugin (`@senpi/runtime`, currently published as `1.1.0`) that owns the trading loop. Auto-upgrades on operator hosts via standard OpenClaw plugin install.

> The current generation of the runtime is a major rewrite from the legacy Python DSL cron model — in-process producer daemon, direct HTTPS to MCP, declarative `risk.guard_rails`, native FEE_OPTIMIZED_LIMIT entries + exits, trade-chain DB telemetry, GET `/state` for daemon liveness.

Two skill architecture patterns exist; a skill targets one via its `runtime.yaml`:

- **Legacy producer** — Python producer/scanner runs via openclaw cron, calls MCP through `mcporter` subprocess, emits signals via `openclaw senpi external-scanner ingest` CLI. Older skills (Bison, Jaguar, Owl, Dog, Lemon, Python, Dire, Condor, Raptor, Orca, Mantis, plus Hawk and the killed Cobra/Phoenix) still run this way.
- **Helpers-native** — In-process producer daemon using `senpi_runtime_helpers.SenpiClient` (direct HTTPS to MCP, direct POST to runtime `/signals`, long-lived `producer_daemon` loop, scanner_lock reentrancy guard). Operated via the `senpi-helpers` CLI (list / health / stats / stop / restart). 14 skills are on this pattern: Cheetah, Kodiak, Polar, Wolverine, Grizzly, Scorpion, Vulture, Roach, Roach-B, Pangolin, Jackal, Otter, Spider, Kestrel, Turbine.

Both patterns run on the same plugin version. The difference is purely how the producer talks to the plugin.

## `dsl-dynamic-stop-loss/` — DSL Exit Engine

Two-phase exit logic with no Python state files:

- **Phase 1** — max-loss + consecutive-breach + retrace. Cuts losing trades early.
- **Phase 2** — ratcheting trailing stop with tiered locks (e.g. +10%/35%, +20%/55%, +35%/70% of high-water margin ROE). Lets winners run while locking incremental gains.
- **Optional time cuts** — hard_timeout, weak_peak_cut, dead_weight_cut. Single-asset agents typically disable time cuts to avoid the v1 DSL Phase 2 hard-timeout misfire.

Used by every active trading skill in the repo.

## `_helpers/senpi_runtime_helpers/` — In-process Client (helpers-native skills)

A small Python package every helpers-native skill imports. Ships on `main` alongside the strategy skills; one shared install per host at `${OPENCLAW_WORKSPACE}/skills/_helpers/`.

- `SenpiClient` — direct HTTPS to MCP (no `mcporter` / `openclaw` subprocess shell-out) and direct POST to runtime `/signals`.
- `producer_daemon(fn, interval_seconds, name, tick_timeout)` — long-lived loop with built-in `scanner_lock` reentrancy guard, structured tick telemetry, self-describing state files (`pid.json` / `boot.json` / `heartbeat.json`), signal-handled graceful shutdown.
- `tick_cache` / `parallel` — coalescing TTL cache and bounded parallel MCP fan-out for producers that touch many assets per tick.
- `log_event` / `cli` — structured logging schema, plus the `senpi-helpers` CLI operators use for daemon lifecycle.

### `senpi-helpers` CLI

Operator-facing wrapper at `_helpers/senpi-helpers`. Bypasses the openclaw gateway for daemon ops — reads/writes the daemon's own state files directly.

```
senpi-helpers list                 — all daemons on host
senpi-helpers health <name>        — pid + heartbeat status
senpi-helpers stats <name>         — log-parsed counters (mcp_calls, signals_posted, ticks, errors)
senpi-helpers stop <name>          — SIGTERM + poll + SIGKILL escalation
senpi-helpers restart <name>       — re-exec from saved boot.json
```

See [`_helpers/senpi_runtime_helpers/references/cli-reference.md`](_helpers/senpi_runtime_helpers/references/cli-reference.md) for the full reference.

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

# Senpi MCP — Tool Reference

Every capability and strategy skill in this repo ultimately calls into the **Senpi MCP** server. The MCP exposes ~65 tools that handle everything from trader discovery to position lifecycle to Arena standings.

Each tool's full schema (params, types, response shape) is in the MCP server itself — load it via your MCP client, or call `list_senpi_guides` / `read_senpi_guide` for the curated reference docs.

### Discovery — find traders and strategies worth copying

| Tool | Purpose |
|---|---|
| `discovery_get_top_traders` | Rank top traders across TAS / TCS / TRP composite scores; filter by win rate, ROI, trade volume |
| `discovery_get_top_strategies` | Top-performing strategies (mirror + custom) by ROE / PnL / volume |
| `discovery_get_trader_state` | Open positions, current PnL, recent activity for a specific trader |
| `discovery_get_trader_history` | Closed-position history with realized PnL, leverage, fees per trade |
| `discovery_get_open_position_realized_pnl` | Realized PnL on currently-open positions (closed legs of partial exits) |

### Hyperfeed — top-trader leaderboard data layer

| Tool | Purpose |
|---|---|
| `leaderboard_get_top` | Top-N traders ranked by delta PnL over a rolling window |
| `leaderboard_get_trader` | Single trader's PnL breakdown, position metrics, freshness tier |
| `leaderboard_get_trader_positions` | Position-level delta PnL for one trader |
| `leaderboard_get_markets` | Per-asset market concentration — which assets the top cohort is piling into |
| `leaderboard_get_momentum_events` | Tier-classified momentum events (entries, scaling, exits) with behavioral tags |
| `leaderboard_get_status` | System health + window mechanics for the Hyperfeed data layer |

### Strategy lifecycle — create, top-up, pause, close

| Tool | Purpose |
|---|---|
| `strategy_list` | Filterable list of user's strategies (by status, type, trader, ID) |
| `strategy_get` | Detailed config + performance for one strategy by ID |
| `strategy_create` | Create a **mirror** strategy that copies a trader (async lifecycle: subscribe → fund → init → active) |
| `strategy_create_custom_strategy` | Create a **custom** strategy with operator-defined positions / budget |
| `strategy_update` | Edit slippage, SL/TP, mirror multiplier on an active strategy |
| `strategy_top_up` | Add capital to a running strategy (multi-chain funding supported) |
| `strategy_pause` | Pause a strategy (note: pause is one-way — no resume) |
| `strategy_close` | Irreversible closure — closes positions, withdraws to source wallet |
| `strategy_close_positions` | Close specific position(s) without closing the whole strategy |
| `strategy_withdraw_funds` | Withdraw idle USDC from a strategy wallet |
| `strategy_bridge_funds_from_hyperliquid_to_evm` | Bridge USDC from HL back to an EVM chain |
| `estimate_custom_strategy_positions_opening` | Preview what a custom strategy would actually open at current prices (OG + MANUAL modes) |

### Strategy state — wallet inspection (read-only)

| Tool | Purpose |
|---|---|
| `strategy_get_clearinghouse_state` | Full HL perp account state (positions, margin, withdrawable) across main + xyz subaccounts |
| `strategy_get_open_orders` | Resting limit / stop-limit orders by wallet |
| `strategy_get_asset_trading_limits` | Per-asset max position size, available margin, mark price |
| `strategy_get_pnl_and_account_value_history` | Time-series of PnL + account value (for charts) |

### Position lifecycle — direct order placement

| Tool | Purpose |
|---|---|
| `create_position` | Open a position on a strategy wallet (MARKET / LIMIT, optional SL/TP attached) |
| `edit_position` | Resize an existing position by target amount (handles flips) |
| `close_position` | Full close — cleans up SL/TP attachments automatically |
| `cancel_order` | Cancel a resting order (idempotent; returns wasAlreadyCancelled if filled/cancelled) |

### Execution — pre-trade estimation + post-trade inspection

| Tool | Purpose |
|---|---|
| `execution_estimate_position_opening` | Copy-trading preview — what would mirroring a specific trade open, with skip categorization |
| `execution_get_open_position_details` | Detailed live position state (entry, mark, funding accrued, unrealized PnL) |
| `execution_get_closed_position_details` | Historical closed-trade fields with net-PnL breakdown |
| `execution_get_order_status` | Status of a specific order ID (filled / resting / canceled) |

### Market data

| Tool | Purpose |
|---|---|
| `market_list_instruments` | All Hyperliquid perp instruments (main + xyz HIP-3 dex) |
| `market_get_asset_data` | Per-asset candles + order book + funding context — primary scanner input |
| `market_get_prices` | Batch price snapshot across multiple assets |
| `market_get_funding_history` | Historical funding rates per asset |
| `market_get_funding_regime` | Classified funding regime (LONG_CROWDED / SHORT_CROWDED / NEUTRAL) |
| `market_get_cross_asset_flows` | Detects BTC-led moves and which alts haven't caught up yet |

### DSL / Ratchet Stop — position protection engine

| Tool | Purpose |
|---|---|
| `ratchet_stop_add` | Attach a ratchet stop to a position with tiered ROE thresholds |
| `ratchet_stop_edit` | Update tier thresholds / lock fractions on an existing ratchet config |
| `ratchet_stop_delete` | Remove the ratchet config (does not close the position) |
| `ratchet_stop_get` | Read current ratchet config + state for one position |
| `ratchet_stop_list` | Ratchet configs across all strategies (or one) |
| `ratchet_stop_events` | Event log — tier triggers, locks taken, exit fires |

### Arena — weekly + monthly competition

| Tool | Purpose |
|---|---|
| `arena_leaderboard` | Rankings (weekly or monthly) by ROE %, with qualification flags |
| `arena_pool` | Current prize pool size for the active period |
| `arena_prizes` | Historical prize payouts |
| `arena_roe_chart` | ROE time-series for an Arena participant |
| `arena_week_prizes` | Prize distribution detail for a specific week |

### Audit — full action history with reasoning

| Tool | Purpose |
|---|---|
| `audit_get_recent_actions` | Most recent agent actions (create / update / close / etc.) |
| `audit_get_strategy_history` | Mutation timeline for one strategy with AI reasoning per action |
| `audit_query` | Advanced query — filter by user / tool / type / time / duration |

### Account & portfolio

| Tool | Purpose |
|---|---|
| `account_get_portfolio` | Total user balances by category (idle, in-strategy, in-position) |
| `account_get_historical_info` | PnL and balance time-series with configurable buckets |

### Treasury & transfers

| Tool | Purpose |
|---|---|
| `send_usdc` | Send USDC to a recipient (multi-chain routing) |
| `transfer_spot_to_perps` | Move USDC from Hyperliquid Spot wallet to Perps wallet |

### User identity & rewards

| Tool | Purpose |
|---|---|
| `user_get_me` | Authenticated user profile (ID, embedded wallet, referral code) |
| `user_get_senpi_points` | Points balance, season info, loyalty tier multiplier |
| `user_get_senpi_points_leaderboard` | Global points leaderboard |
| `user_get_referral_rewards` | Accumulated referral rewards balance (25% of builder fees from referees) |
| `user_claim_referral_rewards` | Claim accumulated rewards to USDC |
| `get_loyalty_tiers` | Loyalty tier definitions + fee discounts |
| `get_share_your_wins` | Recently closed winning positions worth sharing |

### Documentation

| Tool | Purpose |
|---|---|
| `list_senpi_guides` | Enumerate all Senpi reference guides (load first if unsure which guide is relevant) |
| `read_senpi_guide` | Fetch the full text of one guide by `senpi://` URI |

> Guides cover parameter semantics, calculation methodology, workflow patterns, and gotchas for the most-used tools — especially `discovery_get_top_traders`, `leaderboard_get_*`, `strategy_create*`, `create_position`, `audit_*`, and the Arena rules. Load `senpi://guides/senpi-overview` first if you're new to the platform.

---

# Trading Strategy Skills

Each strategy is a directory at the repo root. The bucketing below is by **how the strategy decides what to trade**, not by which asset it ends up on. A skill belongs to one bucket only.

Each row links to the skill's own README and notes the runtime version it targets.

## Single-asset alpha hunters (Kodiak family)

Patient, single-asset specialists. One ticker per skill, deep wall of confluence required before entry, DSL Phase 2 set to ride winners.

| Skill | Asset | Pattern | One-liner |
|---|---|---|---|
| [kodiak](kodiak/) | SOL | helpers-native | SOL alpha hunter — base technical score + trend strength gates |
| [grizzly](grizzly/) | BTC | helpers-native | BTC alpha hunter — Kodiak template, BTC-specific tuning |
| [polar](polar/) | ETH | helpers-native | ETH alpha hunter — hybrid hyperfeed + structural veto |
| [wolverine](wolverine/) | HYPE | helpers-native | HYPE alpha hunter — Kodiak template ported to native HYPE |

## XYZ-market specialists

Trade Hyperliquid's HIP-3 `xyz:*` perps — equities, commodities, indices, metals. 24/7 markets, different spread / funding profile than crypto.

| Skill | Universe | Pattern | One-liner |
|---|---|---|---|
| [bald-eagle](bald-eagle/) | XYZ macro | legacy | Wide DSL timings tuned for macro-asset rhythm |
| [kestrel](kestrel/) | XYZ macro | helpers-native | Macro breakout rider on commodities/indices/equities |
| [dire](dire/) | xyz:BRENTOIL | legacy | BRENTOIL specialist — news-driven oil momentum |

## Multi-signal confluence

Combine multiple independent signals (SM concentration, trend, funding, structure) and only enter when several agree.

| Skill | Pattern | One-liner |
|---|---|---|
| [cheetah](cheetah/) | helpers-native | Multi-signal confluence sniper — strict gate, lower frequency, higher quality |
| [condor](condor/) | legacy | "One amazing trade per day" — high-conviction momentum |
| [sentinel](sentinel/) | legacy | Quality-trader convergence scanner |
| [hawk](hawk/) | legacy | Multi-asset momentum bot |

## Smart-Money signal followers

Watch the top-trader cohort and either mirror or stalk their positions with our own DSL + risk overlay.

| Skill | Pattern | One-liner |
|---|---|---|
| [jackal](jackal/) | helpers-native | Smart Stalker — LLM-gated mirror of top-trader entries |
| [spider](spider/) | helpers-native | Patient anchor — single long-side position, 7+ day hold |
| [vulture](vulture/) | helpers-native | Long-tail momentum rider — pre-arms Phase 2 tier-2 trailing |

## Contrarian / faders

Bet against crowded positioning. Funding extremes, exhaustion, late-cycle SM crowding.

| Skill | Pattern | One-liner |
|---|---|---|
| [pangolin](pangolin/) | helpers-native | Funding rate fader — strikes against extreme funding |
| [owl](owl/) | legacy | Pure contrarian — crowding-unwind plays |
| [Grizzly-Horribilis](Grizzly-Horribilis/) | legacy | BTC contrarian sniper |
| [bison](bison/) | legacy | Conviction holder — wide bands, ratchet trailing |
| [lemon](lemon/) | legacy | Degen fader — counter-trade CHOPPY traders at peaks |
| [dog](dog/) | legacy | Multi-asset SM-exhaustion fader |

## Striker / rank-jump

Enter on rank acceleration or trend ignition. High frequency, tight DSL, fast exits.

| Skill | Pattern | One-liner |
|---|---|---|
| [roach](roach/) | helpers-native | Striker-only — Stalker disabled, position discipline |
| roach-b (variant) | helpers-native | Striker-only variant B — A/B partner to Roach |
| [jaguar](jaguar/) | legacy | Hot-streak striker — rank-jump scanner |
| [raptor](raptor/) | legacy | Hot streak follower |
| [orca](orca/) | legacy | Gen-2 striker with FIRST_JUMP detection |
| [cobra](cobra/) | legacy | Arena sprint predator — single-asset, concentrated margin |

## Macro / regime-aware

Cross-asset, regime detection, range-bound liquidity capture. Don't require a single primary signal.

| Skill | Pattern | One-liner |
|---|---|---|
| [mantis](mantis/) | legacy | Cross-asset catchup hunter — BTC lead → correlated alts |
| [mamba](mamba/) | legacy | Range-bound + regime protection |
| [viper](viper/) | legacy | Range-bound liquidity sniper |
| [komodo](komodo/) | legacy | Momentum event consensus |

## Velocity / pattern detection

Detect emerging acceleration before consensus solidifies.

| Skill | Pattern | One-liner |
|---|---|---|
| [phoenix](phoenix/) | legacy | Contribution velocity scanner — SM profit accel vs price |
| [hydra](hydra/) | legacy | Squeeze detector |
| [vixen](vixen/) | legacy | Multi-asset trend scanner |
| [shark](shark/) | legacy | Position tracker + liquidation cascade scanner |
| [rhino](rhino/) | legacy | Momentum pyramider |
| [barracuda](barracuda/) | legacy | Funding decay collector |

## Specialized missions

Unique theses that don't fit the buckets above.

| Skill | Pattern | One-liner |
|---|---|---|
| [turbine](turbine/) | helpers-native | Volume-rotation engine — builder-fee farming on maker-only rotation across two strategy wallets |
| [otter](otter/) | helpers-native | Open Interest velocity hunter — 1h OI delta with price confirmation |
| [python](python/) | legacy | Patient multi-asset scanner — multi-day hold |
| [scorpion](scorpion/) | helpers-native | Multi-market active trader — both crypto AND XYZ commodities |

---

# Repo layout

```
senpi-skills/
├── README.md                       ← this file
├── CLAUDE.md                       ← repo conventions for Claude agents
├── DSL-MIGRATION-PLAYBOOK.md       ← Legacy → helpers-native migration notes
├── GUIDE.md                        ← general dev guide
├── catalog.json                    ← skill registry
│
├── senpi-trading-runtime/          ╮
├── dsl-dynamic-stop-loss/          │
├── _helpers/senpi_runtime_helpers/ │ Capabilities (see top of this README)
│   
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
3. Install the `@senpi/runtime` plugin (>= 1.1.0) per standard `openclaw plugin install`. Helpers-native skills additionally need the `_helpers/senpi_runtime_helpers/` package pulled from `main` into `${OPENCLAW_WORKSPACE}/skills/_helpers/`.
4. Pull the skill's scripts + `runtime.yaml` from main into your host workspace.
5. Set the required env vars (`<SKILL>_WALLET`, `SENPI_AUTH_TOKEN`, and optionally a `<SKILL>_DECISION_MODEL` for LLM-gated actions).
6. Start the producer per the skill's README — helpers-native skills launch a long-lived `producer_daemon` (manage via `senpi-helpers` CLI); legacy skills run via openclaw cron.

## Requirements

- An [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+)
- A funded Hyperliquid wallet per strategy (each skill is its own wallet — no shared capital)
- [Senpi](https://senpi.ai) MCP access token

## Contributing

Each skill is self-contained. To build a new one:

1. Start from a helpers-native skill (`kodiak/`, `cheetah/`, or `roach/`) as a template.
2. Replace the producer's signal-generation logic with your thesis.
3. Tune `runtime.yaml` — universe, score thresholds, DSL config, risk guard-rails.
4. Document in `SKILL.md` (frontmatter) and `README.md` (operator-facing).
5. Submit a PR.

Bucketing in this README is by thesis, not asset. New skills should add themselves to whichever bucket fits, or open a new one if the thesis is genuinely novel.

# License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://coinbase.com/ventures).
