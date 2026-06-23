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

# Senpi MCP — Tool Reference

Every skill and strategy ultimately reads/acts through the **Senpi MCP** server. Each tool's full schema
(params, types, response shape) lives in the MCP server — load it via your MCP client, or call
`list_senpi_guides` / `read_senpi_guide`. Load `senpi://guides/senpi-overview` first if you're new.

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
| `estimate_custom_strategy_positions_opening` | Preview what a custom strategy would open at current prices |

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
| `execution_estimate_position_opening` | Copy-trading preview — what mirroring a specific trade would open, with skip categorization |
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
| `list_senpi_guides` | Enumerate all Senpi reference guides |
| `read_senpi_guide` | Fetch the full text of one guide by `senpi://` URI |

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
