# Senpi Skills — Open-Source AI Trading Skills for Hyperliquid

> ## 🛠 Building a strategy? Read one doc: [`senpi-trading-runtime/references/strategy-creation.md`](senpi-trading-runtime/references/strategy-creation.md)
> It's self-contained — the full path, an inline producer skeleton, a complete `runtime.yaml`, DSL presets, an archetype→example map, and the gotchas, in a single fetch. **Start there; you should not need to browse the repo or fetch other files to build a working strategy.**

Every file in this repo is a self-contained, plug-and-play **skill** for an autonomous AI trading agent that operates on [Hyperliquid](https://hyperliquid.xyz) via the [Senpi](https://senpi.ai) platform.

The repo is two things stacked on top of each other:

1. **Capabilities** — the runtime (which bundles the Python Producer SDK), the exit engine, the onboarding flow. Reusable infrastructure that every trading strategy plugs into.
2. **Trading Strategy Skills** — individual scanner + producer + runtime configs that embody a specific market thesis. Each skill is a directory you can pull, deploy, and run on its own funded wallet.

Skills are versioned and MIT-licensed. Anyone can fork a skill, modify it, or build a new one from scratch using the capabilities below.

**Platform:** [senpi.ai](https://senpi.ai) · **Arena competition:** [senpi.ai/arena](https://senpi.ai/arena)

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
│   senpi-trading-runtime  ─── Plugin runtime + Python Producer SDK                 │
│                              Consumes: Strategy state, Position,           │
│                              Execution, Audit MCP categories               │
│                                                                            │
│   dsl-dynamic-stop-loss  ─── DSL exit engine                              │
│                              Phase 1 (max-loss + retrace) +                │
│                              Phase 2 (ratcheting trailing)                 │
│                              Consumes: Strategy state, Position MCP        │
│                                                                            │
│                              (Python Producer SDK — senpi_runtime_helpers │
│                              — ships inside this skill: SenpiClient,       │
│                              producer_daemon, fcntl lock)                  │
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
│   26 production skills + 10 onboarding tier = 36 total, one per directory │
│   Each: producer script + runtime.yaml + SKILL.md + config.json            │
│                                                                            │
│   Bucketed below into 11 producer archetypes (see                          │
│   senpi-trading-runtime/references/producer-patterns.md).                  │
│   Skills consume MCP via the runtime + helpers; never call MCP directly.   │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                   ┌───────────────┴───────────────┐
                   │      Strategy wallet(s)        │
                   │ Isolated capital, on-chain     │
                   │ Each skill = its own wallet    │
                   └────────────────────────────────┘
```

The data flow is **upward-from-Hyperliquid, gated-downward-through-MCP**: market state and on-chain positions are read via MCP, capabilities turn those reads into actions (signals, decisions, exits), and the runtime pushes back through MCP to execute on Hyperliquid. Strategy skills produce signals; they do **not** call MCP directly — all MCP traffic goes through the runtime or its bundled Python SDK.

---

# Capabilities

The infrastructure every trading skill plugs into. None of these are strategies — they're the substrate.

## At a glance

Every capability is a thin layer over a specific slice of the Senpi MCP surface. The table below maps each capability to the MCP categories it depends on and the most-used tools within those categories.

| Capability | MCP categories used | Key tools touched |
|---|---|---|
| `senpi-trading-runtime` | Strategy state · Position · Execution · Audit · Ratchet Stop | `strategy_get_clearinghouse_state`, `create_position`, `edit_position`, `close_position`, `cancel_order`, `execution_get_open_position_details`, `audit_query`, `ratchet_stop_*` |
| `dsl-dynamic-stop-loss` | Strategy state · Position · Ratchet Stop | `strategy_get_clearinghouse_state`, `ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_events`, `close_position` |
| `senpi_runtime_helpers` (ships with `senpi-trading-runtime`) | ALL — the in-process client wraps every MCP tool | `mcp_call(tool, **params)` — generic dispatch over the full 68-tool surface |
| `fee-optimizer` | Market data · Position | `market_get_asset_data`, `create_position` (FEE_OPTIMIZED_LIMIT params) |
| `shared` (`hyperfeed_scoring`) | Hyperfeed · Discovery | `leaderboard_get_top`, `leaderboard_get_trader`, `discovery_get_top_traders` |
| `opportunity-scanner` | Hyperfeed · Discovery · Market data | `leaderboard_get_markets`, `discovery_get_top_traders`, `market_get_asset_data`, `market_get_funding_regime` |
| `emerging-movers` | Hyperfeed | `leaderboard_get_markets`, `leaderboard_get_momentum_events` |
| `whale-index` | Hyperfeed · Discovery | `leaderboard_get_top`, `leaderboard_get_trader_positions`, `discovery_get_trader_state` |
| `autonomous-trading` | Strategy lifecycle · Position · Account | `strategy_create_custom_strategy`, `strategy_top_up`, `account_get_portfolio`, `create_position`, `close_position` |
| `senpi-entrypoint`, `senpi-onboard`, `senpi-getting-started-guide` | User & rewards · Account · Strategy lifecycle · Documentation | `user_get_me`, `account_get_portfolio`, `strategy_create`, `list_senpi_guides`, `read_senpi_guide` |

Detail on each capability follows. The full tool surface is enumerated in the **Senpi MCP — Tool Reference** section below.

## `senpi-trading-runtime/` — Plugin Runtime

The OpenClaw plugin that owns the trading loop. Replaces the legacy Python cron + state file system.

Current release: **`@senpi/runtime` 1.1.0** (live on prod since 2026-05-12). Every live skill in this repo runs on 1.1.0. Features:

- In-process producer daemon (long-lived Python loop, no per-tick subprocess spawn)
- Direct HTTPS to MCP and direct POST to runtime `/signals` (no `mcporter` / `openclaw` subprocess shell-out)
- Declarative `risk.guard_rails` — daily caps, drawdown halt, consecutive-loss halt, per-asset cooldowns
- Native FEE_OPTIMIZED_LIMIT on entries AND exits (~0.02–0.03% maker-fill savings per close vs MARKET)
- Trade-chain DB telemetry — LIFECYCLE / DECISION_EXECUTED / ACTION_RESULT / DSL_CREATED / DSL_CLOSED per trade
- `GET /state` daemon liveness probe + `{success, data, error}` envelope on `/signals` + `/audit`

Two producer patterns are supported, both running on runtime 1.1.0:

- **Helpers-native** — producer imports `senpi_runtime_helpers` (SDK bundled with the runtime skill). Default for new skills.
- **Direct-MCP** — producer calls MCP directly via the wrapper client. Used by a handful of older or specialized skills (e.g. Turbine).

Runtime version is determined by which plugin is loaded on the operator's host, not by which features the YAML declares.

## `dsl-dynamic-stop-loss/` — DSL Exit Engine

Two-phase exit logic with no Python state files:

- **Phase 1** — max-loss + consecutive-breach + retrace. Cuts losing trades early.
- **Phase 2** — ratcheting trailing stop with tiered locks (e.g. +10%/35%, +20%/55%, +35%/70% of high-water margin ROE). Lets winners run while locking incremental gains.
- **Optional time cuts** — hard_timeout, weak_peak_cut, dead_weight_cut. Single-asset agents typically disable time cuts to avoid the v1 DSL Phase 2 hard-timeout misfire.

Used by every active trading skill in the repo.

## `senpi_runtime_helpers` — Python Producer SDK (ships with `senpi-trading-runtime`)

The Python SDK every helpers-native producer imports. Ships inside the `senpi-trading-runtime` skill (`senpi-trading-runtime/senpi_runtime_helpers/`) — installing the runtime skill installs the SDK.

- `SenpiClient` — direct HTTPS to MCP (no `mcporter` / `openclaw` subprocess shell-out) and direct POST to runtime `/signals`.
- `producer_daemon(fn, interval_seconds, name, tick_timeout)` — long-lived loop with built-in fcntl reentrancy guard, structured tick telemetry, signal-handled graceful shutdown.
- `log_event` / `cache` / `parallel` — shared logging schema, simple TTL cache, parallel MCP fan-out.
- `senpi-helpers` operator CLI — list / health / stats / stop / restart for producer daemons.

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

The fleet — Senpi's flagship **AI Hedge Funds** plus a deep bench of single-strategy archetypes. Every skill targets runtime **1.1.0**. Archetype bucketing matches [`senpi-trading-runtime/references/producer-patterns.md`](senpi-trading-runtime/references/producer-patterns.md), the canonical archetype catalog.

Each row links to the skill's directory.

## 🏦 AI Hedge Funds — flagship multi-strategy

Senpi's flagship line. Each fund runs **two complementary strategy books on two wallets** under one producer — a real multi-strategy fund, not a single scanner — with its own built-in risk controls. Pick the style that fits your market view.

| Fund | Style | Books | Description |
|---|---|---|---|
| [spider](spider/) | AI/Tech | AI/Tech momentum (long) + macro/majors mean-reversion (both ways) | Buys the strongest AI & tech names — including brand-new listings the day they appear — and rides winners for days; a faster book trades majors + oil both ways for quick profits. |
| [octopus](octopus/) | Market-Neutral | Long leaders + short laggards | Longs the strongest and shorts the weakest of the liquid crypto cross-section at once — profits from the **gap** (dispersion), ~beta-neutral, in any market. |
| [camel](camel/) | Carry | +funding shorts + −funding longs | Gets **paid to hold** — systematically collects the funding the crowd pays, on positions where the crowd is exhausting. Steady income, both directions. |
| [caracal](caracal/) | Volatility | Crypto breakouts + XYZ catalyst breakouts | Waits for a market to coil, then rides the breakout **either way** — across crypto and stocks/oil/gold, 24/7. Episodic: most ticks empty by design. |
| [elephant](elephant/) | Global Macro | Macro trend + macro fade | Trades the cross-asset macro complex — equity indices, metals, energy, FX + BTC — riding macro trends and fading the overreactions. Both directions, 24/7. |
| [wolf](wolf/) | Event-Driven | Risk-on rotation + risk-off rotation | Trades the **turn**. A shared cross-asset regime detector (equities + oil + gold + BTC + the dollar) decides which book works — longing beaten-down beta in a confirmed risk-on regime, or longing defensives + shorting risk in a confirmed risk-off regime. Capital rotates to whichever regime is in force. |
| [rhino](rhino/) | Tail-Risk | Always-on hedge + stress-gated escalation | Carries cheap **convexity** — bleeds a little in calm, pays big in shocks. A small always-on hedge in crisis beneficiaries (gold/oil/dollar/yen) plus a dormant book that fires hard when a stress detector confirms a shock: long the spiking crisis assets, short the cratering risk assets. |
| [ox](ox/) | Risk-Parity | Vol-balanced core + defensive ballast | The **all-weather core**. Sizes every sleeve by **inverse volatility** so no asset class dominates risk (true risk parity) — a LONG basket across crypto, indices, metals, energy, and FX, plus a defensive ballast (gold/dollar/yen) that scales up when the tape turns risk-off. Always invested, low leverage, low turnover. |
| [cougar](cougar/) | U.S. Equity L/S | Long leaders + short laggards (tokenized stocks) | Trades the booming **tokenized U.S. equity market** (trade.xyz: NVDA, TSLA, AAPL, …) cross-sectionally — longs the relative-strength leaders, shorts the laggards, ~market-neutral. Harvests equity **dispersion**: stock-selection alpha, not market direction. |
| [magpie](magpie/) | IPO / New-Listing Event | Pre-IPO accumulation + graduation momentum | Trades the **new-listing event arc** (the SpaceX $1.4B-day-1 pattern). Auto-discovers pre-IPO perpetuals by their funding signature and rides the ramp; then detects the IPOP→equity **conversion** and rides the explosive first-days price discovery. Event alpha. |
| [lion](lion/) | Two-Speed-Market L/S | Long AI + HYPE/SOL, short SP500 + laggard alts | Bets on a **K-shaped divergence**: longs the structural winners (the **AI complex** + crypto's winners **HYPE/SOL**) and shorts the laggards (the broad **U.S. market via SP500** + a gated basket of laggard alts). Trend-confirmed, conviction-sized (HYPE big, SOL small). Harvests the **dispersion between the two speeds** — cross-asset (equities + crypto), net exposure an operator dial. |
| [cub](cub/) | Lion + Pre-IPO | ~90% Lion engine + ~10% pre-IPO ramp | A **variation of Lion** that allocates **~90%** to the Lion two-speed AI long/short engine and **~10%** to a **pre-IPO ramp satellite** — auto-discovers pre-IPO perpetuals (IPOPs) by funding signature (Lemur method) and longs the ones ramping into their listing (the SpaceX/Cerebras pattern), catching the next AI winner before it converts. 90/10 is an operator funding split. Three books. |

Some funds' books also map to a single-strategy archetype below (Spider §4, Camel §7, Octopus §13); the volatility, global-macro, event-driven, tail-risk, risk-parity, equity-long/short, IPO-event, two-speed-market, and Lion-plus-pre-IPO funds (Caracal, Elephant, **Wolf**, **Rhino**, **Ox**, **Cougar**, **Magpie**, **Lion**, **Cub**) are multi-book funds documented in [`producer-patterns.md`](senpi-trading-runtime/references/producer-patterns.md) §17–§26.

## 🎯 Thesis Funds — bet your view

Pick *what you believe will happen*; the fund trades the long/short basket that expresses it. **One engine** ([thesis-fund](thesis-fund/)) drives all of these — each is a `THESIS` preset (a variant of the same skill). It only **presses** a position when the market is *confirming* the thesis, and de-risks via the DSL when it isn't — disciplined conviction, not a hope trade. One wallet per thesis.

| Thesis (preset) | The bet | Expression |
|---|---|---|
| 🐻 Risk-Off | Bet against the Trump economy | long gold/metals · short US indices + BTC |
| 🐂 U.S. Recovery | Risk-on rebound | long US indices + BTC · short gold |
| 🛢️ War Escalation | Iran/US/Israel quagmire deepens | long oil + gold · short equities + BTC |
| 🕊️ War Recovery | De-escalation | short oil + gold · long equities + BTC |
| ⚡ HYPE vs. Market | HYPE keeps outrunning the majors | long HYPE · short BTC/ETH/SOL |
| 🥇 Gold over Bitcoin | Real gold beats digital gold | long gold · short BTC |
| 🟠 Bitcoin over Gold | Digital gold wins | long BTC · short gold |

Add a new bet by editing [`thesis-fund/config/thesis-presets.json`](thesis-fund/config/thesis-presets.json) — no code change.

## 🎯 Onboarding tier — new to Senpi? Start here.

Thirteen v1.0 strategies designed for first-time operators. Most share the same scaffold: **helpers-native producer + Smart-Money direction gate via `leaderboard_get_markets` + DSL Phase 1 floor + Phase 2 ratchet ladder + race-window dedup**, with simple scoring and runtime-owned exits. A few deliberately break that mold — **Sheep** is long-only triple-EMA-stack (no shorts), and **Tortoise** has no scoring at all (DCA cadence is the signal).

Pick by what you want to trade. Each is its own self-contained skill directory at the repo root.

### 🟢 Crypto Trend Followers — pick your coin (single-asset, SM-confirmed)

| Skill | Asset | Description |
|---|---|---|
| [beaver](beaver/) | BTC | **Default first strategy.** 4h trend + Smart-Money direction gate. Wide Bison-pattern DSL (T0 lock 0 → T5 lock 85). |
| [heron](heron/) | ETH | Same shape as Beaver, ETH. |
| [hummingbird](hummingbird/) | HYPE | Same shape, HYPE. |
| [sheep](sheep/) | BTC · ETH · SOL · HYPE | **Long-only triple-EMA-stack.** Fires LONG only when 15m + 1h + 4h EMAs are all stacked bullishly. Never shorts — for users who want trend exposure without learning what shorts are. |

### 🔵 Diversified Crypto Basket

| Skill | Assets | Description |
|---|---|---|
| [hedgehog](hedgehog/) | BTC + ETH + SOL | Equal-weight basket, each asset directional independently. BTC long + ETH short is allowed. Up to 3 simultaneous positions, per-position DSL. |

### 🟤 Accumulation (no prediction, no scoring)

| Skill | Assets | Description |
|---|---|---|
| [tortoise](tortoise/) | BTC + ETH + SOL | **DCA scheduler.** Buys a fixed % of budget on a strict 24h cadence. No price prediction, no scoring — most-overdue past interval wins. LONG only. The most accessible trade in crypto: zero prediction skill required. |
| [koala](koala/) | Single asset (default BTC) | **Set-and-forget HODL.** Fires one LONG signal per lifetime and holds with the widest DSL in any Senpi agent (max_loss 30%, retrace 25, 90d hard_timeout). No scoring, no decisions after deploy. The simplest possible Senpi agent — for users whose entire trading thesis is "I want to own BTC and have a safety net." |

### 🟣 Multi-week Arena Conviction Mirror

| Skill | Source | Description |
|---|---|---|
| [albatross](albatross/) | Arena leaders | Mirrors trades from Arena leaders selected by composite ROE conviction (`0.3 × monthly + 0.7 × weekly_mean − 0.5 × weekly_stdev`). Rewards persistence, not lucky-week luck. **Requires user-scope auth token.** |

### 🟡 Technical Patterns

| Skill | Signal | Description |
|---|---|---|
| [hawk](hawk/) | 7d high/low breakout | Buy 4h breakouts above 7d high; short breakdowns below 7d low. **Tight DSL** (8% max_loss, lock at +5%) — failed breakouts get cut fast. |
| [salamander](salamander/) | Pullback in trend | Buy 3-7% pullbacks in 4h uptrends; short rallies in downtrends. **Asymmetric DSL** — wider Phase 1, tight Phase 2. |

### 🟠 XYZ Equities (Hyperliquid HIP-3 / trade.xyz, 23/5 trading)

Senpi's distinctive moat — equity, commodity, and pre-IPO perps that retail can't trade anywhere else.

| Skill | Universe | Description |
|---|---|---|
| [lemur](lemur/) | Pre-IPO Perpetuals (IPOPs) | **Auto-discovers IPOPs** via the trade.xyz funding signature (\|funding\| ≤ 1e-7 AND max_leverage ≤ 5). Today: xyz:SPCX. Auto-expands when ANTHROPIC / OPENAI / STRIPE list. |
| [bobcat](bobcat/) | Big tech | NVDA / TSLA / AAPL / META / MSFT / GOOGL / AMZN / AMD / MU / INTC / TSM / ORCL. 4h trend + SM. 48h hard timeout for the weekend pricing gap. |
| [raccoon](raccoon/) | All XYZ (excl. IPOPs) | **Weekend-only.** Fri 22:00 UTC → Mon 00:00 UTC. Captures the Mon-open reconciliation snap-back when trade.xyz external pricing resumes after the 50h internal-oracle window. |
| [iguana](iguana/) | xyz:SP500 · xyz:XYZ100 | **Broad indices only — no stock-picking.** Trades whichever has the stronger 4-day move past 1.5%. The closest thing to an index fund, but 24/7 on Hyperliquid. |

---

# The 16 production archetypes

Below are the production-tier strategies sorted by their producer-pattern archetype. See [`senpi-trading-runtime/references/producer-patterns.md`](senpi-trading-runtime/references/producer-patterns.md) for the canonical pattern catalog. The onboarding tier above maps into these same archetypes (Beaver/Heron/Hummingbird are single-asset; Hedgehog/Hawk/Salamander/Bobcat are multi-asset whitelist; Albatross is trader-follower; Lemur/Raccoon are XYZ specialist).

## 1. Universe trend-follower

Top-N universe scan, multi-TF + SM consensus, conviction-tiered leverage. Catches coordinated risk-on / risk-off moves across crypto majors.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [condor](condor/) | v3.0 | Top-50 HL liquid | Scans top-50 liquid assets every 180s. SM consensus + multi-TF alignment → conviction tiers. |
| [cheetah](cheetah/) | v5.2 | Top-100 SM | Multi-signal confluence (SM + velocity + dual-price + volume + quality-trader). 15-point integer score. |
| [python](python/) | v1.0 | Multi-tier universe | Mixed-signature scan with multi-day-hold thesis. Funding, volume, RSI extremes, move-exhaustion penalty. |
| [scorpion](scorpion/) | v3.0 | Universe + funding | Universe scan with funding-regime backstop and post-close per-asset cooldown. |

## 2. Single-asset alpha hunter (Kodiak family)

One asset, six-gate entry validation, tight scoring, conviction-tiered leverage.

| Skill | Version | Asset | Description |
|---|---|---|---|
| [kodiak](kodiak/) | v5.1 | SOL | The original template. Six-gate framework, SOL-tuned thresholds. |
| [grizzly](grizzly/) | v5.3 | BTC | BTC-tuned thresholds — calmer regime, tighter sizing. |
| [polar](polar/) | v3.0 | ETH | ETH-tuned thresholds, deep confluence required. |
| [wolverine](wolverine/) | v3.0 | HYPE | HYPE-tuned thresholds for its high-vol native profile. |
| [koala](koala/) | v1.0 | Operator-chosen (default BTC) | **Onboarding tier — state-trigger variant.** No scoring, no `market_get_asset_data` call. Fires ONE LONG signal per lifetime and holds with the widest DSL in any Senpi agent (max_loss 30%, retrace 25, 90d hard_timeout). For users whose entire trading thesis is "I want to own BTC and have a safety net." |

## 3. Single-asset XYZ specialist

Kodiak architecture applied to a single non-crypto XYZ asset.

| Skill | Version | Asset | Description |
|---|---|---|---|
| [dire](dire/) | v1.0 | xyz:BRENTOIL | BRENTOIL specialist. Wider phase-1 loss tolerances, commodity-specific drawdown guardrails. |
| [falcon](falcon/) | v1.0 | xyz: conversion events | **Event-detection layer.** Trades the IPOP→equity conversion itself: classifies every xyz instrument IPOP vs STANDARD by funding signature, caches it, and fires when one flips (funding jumps ~100x, throttle off → free price discovery). Rides post-conversion momentum. Wide DSL, 7d hard timeout. |

## 4. Multi-asset whitelist

Strict whitelist of 3–6 majors, best-of-N selection per tick.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [bison](bison/) | v1.0 | BTC · ETH · SOL | Iterates BTC / ETH / SOL per tick, fires the best-scoring above MIN_SCORE. Tick 300s. |
| [badger](badger/) | v1.0 | BTC · ETH · SOL · HYPE | Takes a breakout only when **rising open interest** confirms it (new money, not a fakeout). OI-state cache. Wide "let winners run" DSL. |
| [tortoise](tortoise/) | v1.0 | BTC · ETH · SOL | **Onboarding — DCA scheduler.** Buys a fixed % of budget on a strict 24h cadence. No price prediction, no scoring. Most-overdue past interval wins. LONG only. Wide DSL + 30d hard_timeout for compounding. |
| [sheep](sheep/) | v1.0 | BTC · ETH · SOL · HYPE | **Onboarding — long-only triple-stack.** Fires LONG only when 15m + 1h + 4h EMAs are all stacked bullishly. Never shorts. Balanced DSL + `weak_peak_cut` 6h/3%. |
| [iguana](iguana/) | v1.0 | xyz:SP500 · xyz:XYZ100 | **Onboarding — XYZ index basket.** The simplest XYZ exposure — just the broad indices. Picks whichever has the stronger 4-day move past 1.5% and trades its direction. Balanced DSL + 48h hard_timeout. |
| [sailfish](sailfish/) | v1.0 | BTC · ETH · SOL · HYPE | **Relative-strength rotator.** Ranks the universe by ~2.7d RS each tick; longs the leader iff leader RS ≥ 1% AND beats runner-up by ≥ 1.5pp (no whipsaw). Rotation via DSL exit + re-entry. Balanced DSL + 96h hard_timeout. |
| [stag](stag/) | v1.0 | BTC · ETH · SOL · HYPE (often single-asset) | **Parabolic-run hunter.** Entry-side pair for the new `parabolic_runner` DSL preset (widest in the catalog: max_loss 25%, retrace 18, 2 breaches required, 14d outer bound). Strict 5-gate filter (200-SMA + 7d ≥25% + vol surge + acceleration + SM ≥60% LONG). LONG only. Operator-driven — most ticks return empty by design. Reference: HYPE 2026-05 (+60% in 16 days). |
## 5. Trader-follower / hot-streak

Top-trader pool + conviction-gated coat-tail entries.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [raptor](raptor/) | v3.0 | Multi (top traders) | 24h-cached trader pool. Gates on reputation, position size, SM alignment, per-trader entry discipline. |
| [jackal](jackal/) | v1.0 | Multi (top traders) | Active trader pool + new-entry detector. Enriches with TA + funding regime. |
| [remora](remora/) | v1.0 | Operator-picked whale set | **Hand-picked mirror.** You name the whales; Remora mirrors each one's largest-notional position, with a consensus boost when ≥2 agree + an ELITE-tier bonus. Wide DSL, 120h staleness cap. |

## 6. Striker / rank-jump

Detect rank acceleration on the SM leaderboard. First-jump events, high-conviction-per-trade.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [jaguar](jaguar/) | v3.2 | Multi (rank-jumpers) | Detects 10+ rank jumps in one tick from mid-ranks (#25+). $3M+ notional, 50+ trader-count gates. |
| [roach](roach/) | v1.0 | Multi (Strikers) | Striker-only emitter. FIRST_JUMP / IMMEDIATE_MOVER with volume floor. |
| [roach](roach/) (roach-b instance) | v1.0 | Multi (Strikers) | Second wallet instance of the Roach producer. |
| [orca](orca/) | v1.0 | Multi (Strikers) | Gen-1 vanilla Striker — FIRST_JUMP + volume + base scoring. |
| [meerkat](meerkat/) | v1.0 | Multi (momentum-event feed) | **Event-feed variant.** Reads `leaderboard_get_momentum_events` directly and snipes the freshest (≤30min), highest-tier (3 ≥10% · 2 ≥5%) momentum events. Wide DSL + short 36h hard timeout. Tick 120s. |

## 7. Funding-regime fade

Persistent funding extremity → fade the crowd at exhaustion.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [pangolin](pangolin/) | v1.4 | Multi (OI > $3M) | Funding extremity + persistence + SM positioning + cooldowns. Quiet-hours gating (00–04 UTC). |
| [dog](dog/) | v2.0 | 4-coin whitelist | Funding fade on 4-coin watchlist with regime hard-gate. |
| [vulture](vulture/) | v2.3 | HYPE | HYPE funding-regime contrarian. Funding-history + held-position enrichment. |
## 8. Contrarian crowding-unwind hunter

Wait for crowd to overcommit AND exhaustion signals; enter opposite.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [owl](owl/) | v6.1 | Multi (OI > $3M) | Crowding persistence (1+ hour) + multi-signal exhaustion. Tick 900s. 6h per-asset cooldown. |
| [lemon](lemon/) | v1.1 | Crypto majors + XYZ | Degen Fader — counter-trades CHOPPY/DEGEN consensus. MACRO_TREND_GATE blocks fades during strong BTC trends. |
| [egret](egret/) | v1.0 | BTC · ETH · SOL · HYPE | SM-divergence fader — fades extreme Smart-Money crowding (≥70%) that price won't confirm. Tight DSL + maker-only entry + time-cuts on. |

## 9. Cross-asset lag detector

BTC leads alts on macro moves; capture the catch-up.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [mantis](mantis/) | v5.0 | Multi (BTC-led laggards) | BTC moves >2% in 4h → identifies laggard alts with follow-rate ≥0.8. Tick 60s. Often silent on quiet BTC days. |
| [osprey](osprey/) | v1.0 | BTC → xyz: equity proxies | **Cross-VENUE variant.** When BTC moves, crypto-correlated XYZ equities (COIN/MSTR/miners) lag on the other venue. Self-computes the catch-up gap (`leader move × beta − proxy move`) from candles. Wide DSL, 96h hard timeout. |

## 10. Multi-asset XYZ contrarian fader

Multiple XYZ macro assets, contrarian direction flip on SM over-concentration.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [bald-eagle](bald-eagle/) | v1.0 | 6 XYZ macro | CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100. Spread filter + 10-min stale-cancel auto-purge. |
| [kestrel](kestrel/) | v1.1 | 13 XYZ macro | 13-asset XYZ macro universe with funding alignment. Broader variant of the XYZ contrarian thesis. |

## 11. Volume engine / market-making (specialized)

Not directional. Two-wallet pair recycling builder fees against a volume target.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [turbine](turbine/) | v3.2 | Two-wallet pair | High-frequency cancel + create cycle. Volume wallet + runner wallet. Daily top-ups; net bleed = mission cost rate. |

## 12. Microstructure / order-flow

Trade the order book + open-interest dynamics directly — forced flow and resting-depth skew as the edge.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [piranha](piranha/) | v1.0 | BTC · ETH · SOL · HYPE | Rides forced flow — OI unwinding fast + a violent move + a thin book ⇒ liquidation cascade. OI-velocity self-compute fallback. Wide DSL + 24h hard timeout. |
| [marlin](marlin/) | v1.0 | BTC · ETH · SOL · HYPE | Order-book-imbalance momentum — bid/ask resting-depth skew as the entry-TIMING edge on a momentum thesis (not a scalper). Wide DSL + 24h hard timeout. |

## 13. Relative-value / pairs

Trade the spread between two correlated assets, not a single asset's direction.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [chameleon](chameleon/) | v1.0 | ETH/BTC · SOL/ETH · SOL/BTC | Ratio mean-reversion — trades the high-beta leg when a pair's ratio z-score extends past ~2σ and starts reverting. Single-position directional bet (not a two-leg spread). Mean-reversion DSL (tight ladder, 48h). |
## 14. Meta-strategy follower / copy-the-copiers

Follow not individual traders but the top-performing **strategies** — and trade their performance-weighted consensus.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [cuckoo](cuckoo/) | v1.0 | Top-strategy consensus | Auto-discovers the top-N strategies by performance and trades what ≥2 of them agree on most, weighted by each one's ROI (capped so one outlier can't dominate). Wide DSL + 96h staleness cap. **User-scope auth.** |

## 15. Self-tuning / adaptive-threshold agent

The first archetype where the agent **modifies its own behavior based on its own trade history.** The agent runs a normal scoring producer but on a scheduled cron pulls its own closed-trade telemetry (via `audit_query`), buckets trades by entry score, and auto-raises `MIN_SCORE` when bottom buckets bleed. Productizes the [Vulture v4.1 manual cull](https://github.com/Senpi-ai/senpi-skills/pull/337) as a first-class pattern.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [lynx](lynx/) | v1.0 | BTC · ETH · SOL · HYPE | Multi-asset momentum scorer with a 6h audit cron. Every audit: pull own closed trades, bucket by entry score, raise `MIN_SCORE` if any bucket at-or-above the floor has ≥8 samples averaging worse than -1% ROE. Caps at `maxMinScore: 7`. Logs every adjustment with bleeding-bucket evidence. **First fleet agent that modifies its own behavior based on its own track record.** |

## 16. Regime classifier / meta-router

Watches macro conditions and **classifies the market** into TREND_UP / TREND_DOWN / CHOP. Publishes the classification in every tick output — including ticks where no trade is taken. The "meta-router" framing is aspirational: future runtime work can let other agents subscribe to the regime channel as a gating input.

| Skill | Version | Asset / Universe | Description |
|---|---|---|---|
| [coyote](coyote/) | v1.0 | BTC (positional) + universe (dispersion) | 3-regime classifier (TREND_UP / TREND_DOWN / CHOP) with vol-confirmation on the down side (crash = drop + vol spike, not slow grind). LONG BTC in TREND_UP, SHORT BTC in TREND_DOWN, no trade in CHOP. Regime + all 3 input metrics published on every tick. Balanced DSL. |

> The **volatility** (Caracal), **global-macro** (Elephant), **event-driven / regime-rotation** (Wolf), and **tail-risk / crisis-alpha** (Rhino) archetypes live in the 🏦 AI Hedge Funds section at the top — each is a two-book fund rather than a single-strategy agent. Their per-edge architecture is documented in [`producer-patterns.md`](senpi-trading-runtime/references/producer-patterns.md) §17–§21 (Wolf §20, Rhino §21 add a shared cross-asset "brain" — a regime/stress read computed once per tick that gates which book fires).

For full archetype theses, distinguishing MCP signatures, and code snippets, see [`senpi-trading-runtime/references/producer-patterns.md`](senpi-trading-runtime/references/producer-patterns.md).

> **Live performance:** [senpi.ai/arena](https://senpi.ai/arena) (weekly ROE). One agent — Sentinel — runs an in-house producer not published to this repo; it appears on the Arena but has no source link here.

---

# Repo layout

```
senpi-skills/
├── README.md                       ← this file
├── CLAUDE.md                       ← repo conventions for Claude agents
├── catalog.json                    ← skill registry
│
├── senpi-trading-runtime/          ╮
│   └── senpi_runtime_helpers/      │ ← Python Producer SDK bundled with runtime
├── dsl-dynamic-stop-loss/          │
├── fee-optimizer/                  │ Capabilities (see top of this README)
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
├── condor/  cheetah/  python/  scorpion/      │
├── jackal/  spider/   raptor/                 │
├── pangolin/ dog/  vulture/                   │ 26 Trading Strategy Skills
├── owl/  lemon/                               │ (live fleet)
├── roach/   jaguar/  orca/                    │
├── mantis/                                    │
├── bison/   dire/  bald-eagle/  kestrel/      │
└── turbine/                                   ╯
```

Roach-B is a second wallet instance of the `roach/` producer (no separate directory). Otter (`otter/`) is in the repo but currently paused; see git history for prior versions.

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
3. Install the senpi-trading-runtime plugin per the skill's requirement. Producer-based skills additionally need the `senpi-trading-runtime` skill installed (`npx skills add … --skill senpi-trading-runtime -g -y`) — it ships the Python Producer SDK (`senpi_runtime_helpers`) the producer imports.
4. Pull the skill's scripts + `runtime.yaml` from main into your host workspace.
5. Set the required env vars (`<SKILL>_WALLET`, `SENPI_AUTH_TOKEN`, and optionally a `<SKILL>_DECISION_MODEL` for LLM-gated actions).
6. Start the producer daemon per the skill's README.

## Requirements

- An [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+)
- A funded Hyperliquid wallet per strategy (each skill is its own wallet — no shared capital)
- [Senpi](https://senpi.ai) MCP access token

## Contributing

Each skill is self-contained. To build a new one:

1. Start from a producer-based skill (`kodiak/`, `cheetah/`, or `roach/`) as a template.
2. Replace the producer's signal-generation logic with your thesis.
3. Tune `runtime.yaml` — universe, score thresholds, DSL config, risk guard-rails.
4. Document in `SKILL.md` (frontmatter) and `README.md` (operator-facing).
5. Submit a PR.

Bucketing in this README is by thesis, not asset. New skills should add themselves to whichever bucket fits, or open a new one if the thesis is genuinely novel.

# License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://coinbase.com/ventures).
