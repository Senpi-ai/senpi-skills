# Senpi — Open-Source AI Trading for Hyperliquid

**An AI that runs your Hyperliquid strategy 24/7 — reads the whole market, finds the edge, sizes the trade, and protects the position while you sleep.**

This repository is the **open-source layer** of the Senpi Hyperliquid AI Harness: the [skills](#the-skills) that give a Senpi agent its trading capabilities, and the [strategy templates](#the-strategy-templates) it can deploy. MIT-licensed, readable, forkable.

**Deploy an agent:** [senpi.ai](https://senpi.ai) · **Arena:** [senpi.ai/arena](https://senpi.ai/arena) · **Exchange:** [Hyperliquid](https://hyperliquid.xyz)

---

## The Senpi Hyperliquid AI Harness

Senpi 2.0 isn't a chatbot with a trading API bolted on. It's a **harness** — a disciplined stack that wraps a market-tuned AI model in deterministic execution and risk machinery, so an autonomous agent can trade real capital without hallucinating a position or forgetting a stop.

```
                    ┌─────────────────────────────────────────────┐
   You (chat) ────▶ │  Senpi Samurai  — the model                 │   tuned for Hyperliquid
                    │  not a generalist in a trading costume      │
                    └───────────────────────┬─────────────────────┘
                                            │
                    ┌───────────────────────▼─────────────────────┐
                    │  OpenClaw host + agent workspace            │   AGENTS.md: skills-first routing,
                    │  (memory, guardrails, heartbeats)           │   guardrails, name-free
                    └───────────────────────┬─────────────────────┘
                                            │  match intent → skill
                    ┌───────────────────────▼─────────────────────┐
                    │  SKILLS  (this repo, open source)           │   12 skills: analyze, discover,
                    │  hidden-engine pattern: script → JSON → talk│   author, deploy, review …
                    └───────────────────────┬─────────────────────┘
                                            │  call tools
                    ┌───────────────────────▼─────────────────────┐
                    │  Senpi MCP surface  (62 tools)               │   market · discovery · leaderboard
                    │  market / strategy / execution / DSL / …    │   strategy · execution · ratchet-stop
                    └───────────────────────┬─────────────────────┘
                                            │
                    ┌───────────────────────▼─────────────────────┐
                    │  @senpi-ai/runtime  — the supervisor plugin │   runs scan(inputs, ctx) on interval,
                    │  scan() · sizing · risk gates · two-phase   │   owns execution + the DSL exits
                    │  DSL exits · telemetry event log            │
                    └───────────────────────┬─────────────────────┘
                                            │
                                     ┌──────▼──────┐
                                     │ Hyperliquid │   perps: ~230 crypto + ~95 equities/
                                     └─────────────┘   metals/indices/pre-IPO, 24/7
```

**What's open source (this repo):** the **skills** and the **strategy templates** — the parts you'd want to read, audit, fork, or contribute to.
**What's the platform:** the Samurai model, the OpenClaw host integration, the MCP backend, and the `@senpi-ai/runtime` supervisor (installed as a managed plugin).

The two talk through a clean contract: **skills call MCP tools; strategies export `scan(inputs, ctx)`; the runtime owns everything downstream.** Nothing in this repo places an order directly — it goes through the supervised runtime, which is where sizing, risk, and exits live.

---

## The skills

A Senpi agent's capabilities are **skills** — each one packages the right multi-step workflow for a class of request, so the model reaches for a proven path instead of hand-assembling raw tool calls (a known source of double-counted collateral, misread sub-wallets, and "your position is unprotected" false alarms).

Every analytical skill follows the **hidden-engine pattern**: a vendored, stdlib-only `mcp_client.py` + a deterministic Python engine that emits structured JSON + a `SKILL.md` that narrates the result under hard guardrails (no fabricated forward numbers, honest data sourcing, process-over-outcome). The engine gathers and computes; the model judges and explains.

| Skill | Ver | Role |
|---|---|---|
| **Analyze** | | |
| [`senpi-portfolio`](senpi-portfolio/) | 1.7.1 | All-wallet portfolio, positions, DSL protection, per-strategy mandate reads |
| [`senpi-market-pulse`](senpi-market-pulse/) | 1.1.1 | Daily cross-asset market read (crypto, equities, commodities, macro, funding regime) |
| [`senpi-smart-money`](senpi-smart-money/) | 1.1.1 | Where the most-profitable wallets are positioned vs. the crowd |
| [`senpi-trader-research`](senpi-trader-research/) | 1.0.2 | Rank + vet Hyperliquid traders before copying them |
| [`senpi-improve-trades`](senpi-improve-trades/) | 1.1.1 | Retrospective review + health checks off the **telemetry event log**: exit quality, missed signals, leaks, crashes, "if I'd held" counterfactual |
| [`senpi-account-status`](senpi-account-status/) | 1.1.1 | Points, loyalty tier, fees, Arena standing, referrals |
| **Run a strategy** | | |
| [`senpi-strategy-discover`](senpi-strategy-discover/) | 2.3.0 | Conversational picker — rank the catalog against your worldview |
| [`senpi-strategy-author`](senpi-strategy-author/) | 2.4.2 | Build/edit a DSL-protected strategy package, one decision at a time |
| [`senpi-strategy-ops`](senpi-strategy-ops/) | 2.2.1 | Deploy / monitor / close a named strategy (`deploy.py`, `close.py`) |
| [`senpi-trading-runtime`](senpi-trading-runtime/) | 3.2.1 | The runtime contract reference: `scan(inputs, ctx)`, `runtime.yaml`, DSL |
| **Move money / positioning** | | |
| [`senpi-deposit-withdraw-transfer`](senpi-deposit-withdraw-transfer/) | 1.1.0 | The money-movement rails (funds in via embedded wallet or in-app USDC purchase; out via the app) |
| [`senpi-why`](senpi-why/) | 1.0.3 | "Why Senpi / vs. other tools" — the positioning answer |

Skills **compose**: `improve-trades` pulls in `market-pulse` + `smart-money` + `portfolio`; `discover` hands a chosen package to `ops`; `author` hands a built package to `ops`. The agent routes by **intent**, not keywords, and never re-implements one skill inside another.

---

## The strategy templates

A **strategy** is a deployable **package** under [`strategies/`](strategies/) — a market thesis compiled into scanner logic + risk config + exits, that runs on its own funded wallet. There are **80+** in the catalog today, forward-tested across **$10M+** in notional trade value and battle-tested in the public [Agents Arena](https://senpi.ai/arena), where Senpi agents have traded **$30M+ in notional volume**.

### Package anatomy

```
strategies/spider/
├── strategy.yaml            ← manifest: id, version, catalog{} (discovery metadata), instances[]
├── swing/                   ← one instance = one wallet (multi-wallet funds have several)
│   ├── runtime.yaml         ← the executable spec: strategy, scanners, actions, exit, risk
│   └── scanners/
│       ├── scan.py          ← exports scan(inputs, ctx) → list of signals
│       └── scoring.py       ← pure, unit-testable thesis math
└── scalp/  …                ← a second instance (different cadence, different wallet)
```

- **`strategy.yaml`** — source of truth for deploy + attribution. Its `instances[]` array is what makes a package expand into 1–N deployed strategies, one wallet each, split by `funding_share`. **21 of them are multi-wallet** (e.g. long+short funds, core+ballast, hedge+escalation).
- **`runtime.yaml`** — the runtime's self-contained spec. The runtime **spawns and supervises** `scan()`, calling it every `interval_seconds` and owning everything after: signal validation, conviction-weighted sizing (`margin_pct`), execution (`FEE_OPTIMIZED_LIMIT`), slot accounting, `risk.guard_rails`, and the DSL exits. **No separate scanner daemon.**
- **`strategies/catalog.json`** — the generated registry index (never hand-edit; run [`senpi-trading-runtime/scripts/gen_catalog.py`](senpi-trading-runtime/scripts/)). `senpi-strategy-discover` ranks it.

### Every strategy exits through the DSL

There are **no manual close actions**. Exits are 100% owned by the runtime's **two-phase DSL** (Dynamic Stop-Loss), configured in each `runtime.yaml`'s `exit:` block:

- **Phase 1 — survive.** A hard stop (`max_loss_pct`) cuts losers fast from entry. This protects the position the moment it opens.
- **Phase 2 — lock.** As a winner runs, a ratcheting ladder of `tiers[]` (`trigger_pct` → `lock_hw_pct`) trails the stop upward, banking a growing share of the high-water mark while keeping the tail alive.

That asymmetry — lose small, let winners run — is the engine behind every strategy template.

### And a risk engine wraps the whole strategy

The DSL protects each *position*; a portfolio-level **risk engine** governs the whole *strategy* — deterministic guards the model can't prompt its way around, enforced every tick:

- **Circuit breakers** — a daily-loss halt and an intraday drawdown breaker stop trading on a bad day.
- **Turnover brakes** — max-entries-per-day plus consecutive-loss and per-asset cooldowns throttle overtrading, because fees are the quiet killer of every bot.
- **Hard gates** — margin, notional, and leverage limits reject any signal that would breach them, each logged with a reason code (`no_slots`, `no_margin`, `risk_gate_*`, `asset_banned`).
- **Conviction-weighted sizing** — position size scales off the live account (`margin_pct`) and the signal's own score, not a fixed lot.

The scanner proposes; the runtime's risk engine disposes.

### The strategy templates, by archetype

The 80+ templates span the full cross-asset spectrum (majors, alts, universe crypto, XYZ equities, commodities, indices, pre-IPO) at 1–10× leverage, 74 advanced / 13 starter, mostly long/short. The range is deliberate — directional and market-neutral, single-asset and whole-universe, momentum and mean-reversion, copy-trading and macro, everyday starters and crisis insurance:

| Archetype | # | What it does | Examples |
|---|---|---|---|
| Trend-following | 22 | Ride durable multi-timeframe trends | Spider, Elephant, Python, Lynx |
| Single-market specialist | 15 | Master one asset deeply | Kodiak (SOL), Coyote (BTC/ETH), Falcon (pre-IPO) |
| Breakout / momentum | 16 | Buy the break, gated on trend + smart money | Hawk, Badger, Hare (crypto session scalp), Kite (SMC/ICT) |
| Structural / neutral | 9 | Non-directional (DCA, market-neutral, thematic) | Tortoise (DCA), Cougar |
| Contrarian / fade | 8 | Fade crowding once it exhausts | Camel (funding), Owl, Pangolin |
| Copy-trading | 9 | Mirror proven traders | Jackal, Whalehunter, Shadow (multi-trader fresh-entry mirror) |
| Macro thesis | 4 | Read the regime, set a posture, rotate by attrition | Chimp (daily), Gorilla (weekly), Rotator (3h conviction rebalance) |
| Event-driven | 1 | IPO / new-listing convexity | Magpie |
| Risk parity | 2 | Equal-risk / diversified capital preservation across uncorrelated classes | Ox, Mandate (no-leverage crypto + RWA) |
| Tail-risk | 1 | Standing insurance + crisis convexity | Rhino |

Browse the live set with `senpi-strategy-discover` rather than any hand-maintained list — the catalog is the source of truth.

---

## The runtime contract (`@senpi-ai/runtime`)

The supervisor that turns a package into a live, risk-managed strategy. A strategy author only writes `scan()` + config; the runtime owns the rest.

- **`scan(inputs, ctx)`** — your scanner, called every `interval_seconds`. `inputs` are the config from `runtime.yaml`; `ctx` gives you `ctx.senpi_mcp.call_tool(...)` (read market/discovery/leaderboard data) and `ctx.state` (a bounded, persistent per-scanner store for dedup/history). You return signals (`{score, direction, ...}`); you never place an order.
- **Two-phase DSL exit engine** — a pure tick function evaluates hard-timeout → dead-weight → weak-peak → phase-1 breach → phase-2 tier advance on every price update, and emits typed close reasons (`tier_breach`, `max_retrace`, `trailing_floor`, `weak_peak`, `hard_timeout`, …).
- **Risk guard rails** — daily-loss halt, drawdown circuit breaker, consecutive-loss + per-asset cooldowns, max-entries-per-day turnover cap. Blocked signals are logged with a reason code (`no_slots`, `no_margin`, `risk_gate_*`, `asset_banned`).
- **Telemetry event log** — every decision an agent makes is recorded to a per-strategy on-disk event stream (`position.opened`, `dsl.created/tier_advanced/closed`, `signal.outcome`, `order.filled/failed`, `runtime.paused`, …), readable via `openclaw senpi events / explain / audit`. This is the observability layer: it's what `senpi-improve-trades` mines for exit quality, leaks, blocked signals, and health checks (crashes, missed runs, protection gaps) — so an agent can review and improve its own work, and you can see exactly what it did and why.
- **Skills auto-upgrade** — a manifest-driven coordinator keeps installed skills current (semver-gated by a `maxMajor` ceiling), so agents pick up improvements without manual re-installs.

---

## Getting started

The fastest path is to **[deploy an agent directly on senpi.ai](https://senpi.ai)** — it stands up a full agent host (the runtime plugin + Senpi MCP) for you, no infra to run.

To run a strategy by hand on your own [OpenClaw](https://openclaw.ai) host:

```bash
# 1. Install the runtime plugin + configure Senpi MCP (SENPI_AUTH_TOKEN)
openclaw plugins install @senpi-ai/runtime

# 2. Pick a strategy (or ask the agent: "what should I trade?")
#    → senpi-strategy-discover ranks strategies/catalog.json against your goals

# 3. Deploy — creates a funded wallet per instance, deploys, verifies the scanner ticked
python3 senpi-strategy-ops/scripts/deploy.py <id> --budget <usd>

# 4. Monitor
openclaw senpi status            # liveness; strategy is live once its scanner has a recent tick

# 5. Close — flattens positions, returns funds
python3 senpi-strategy-ops/scripts/close.py <id>
```

To **build a new strategy**, start with [`senpi-strategy-author`](senpi-strategy-author/) and the [`senpi-trading-runtime`](senpi-trading-runtime/) contract.

### Requirements
- An [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+) with the `@senpi-ai/runtime` plugin
- A funded Hyperliquid wallet per strategy instance (no shared capital)
- A [Senpi](https://senpi.ai) MCP access token (`SENPI_AUTH_TOKEN`)

---

## Repo layout

```
senpi-skills/
├── senpi-portfolio/  senpi-market-pulse/  senpi-smart-money/      ← analyze
│   senpi-trader-research/  senpi-improve-trades/  senpi-account-status/
├── senpi-strategy-discover/  senpi-strategy-author/                ← run a strategy
│   senpi-strategy-ops/  senpi-trading-runtime/
├── senpi-deposit-withdraw-transfer/  senpi-why/                    ← money / positioning
│
├── strategies/                    ← 80+ strategy packages + the registry
│   ├── catalog.json               ← GENERATED index
│   └── <id>/ …                    ← strategy.yaml + <instance>/{runtime.yaml, scanners/}
│
└── CLAUDE.md                      ← repo conventions for AI editors
```

## License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://www.coinbase.com/ventures).

> Trading perpetual futures carries substantial risk of loss. Senpi is software, not financial advice; strategies can and do lose money. Nothing here is a promise of returns.
