# Senpi skills: open-source AI agent skills and strategy templates for Hyperliquid

**senpi-skills is Senpi's open-source library of AI agent skills and 140+ strategy templates for trading Hyperliquid perps across crypto, stocks, commodities and indices.** Every Senpi agent runs these skills and updates from this repo, and the strategy pages on [senpi.ai/strategies](https://senpi.ai/strategies) are built from its catalog.

It's for pro traders who code, and for their AI agents: read exactly what a strategy does before you run it, fork it, or build your own on the same contract. MIT-licensed.

**In use:** 252 strategy runtimes ran on Senpi agents in the 7 days to 23 Nov 2026, and 2,122 machines cloned this repo 25,104 times in the 14 days to 22 Nov (GitHub's count; most are Senpi agent boxes updating their skills). Runtimes are counted from Senpi's production telemetry, with Senpi team and test accounts left out.

**Try it:** deploy a template from [senpi.ai/strategies](https://senpi.ai/strategies) · browse all of them in [STRATEGIES.md](STRATEGIES.md) · ask Claude, ChatGPT, Codex or Cursor about any of them with the [Senpi plugin](https://github.com/Senpi-ai/senpi-plugin)

---

## How do I build a Hyperliquid trading bot in Python?

A Senpi strategy is a small Python module that exports `scan(inputs, ctx)` and returns signals. The Senpi runtime calls it on a schedule and does the rest: sizing, order execution, risk limits and exits. Your code never places an order.

1. Start from the template in [`strategies/`](strategies/) closest to your idea (each folder is a complete package), or describe the strategy in plain English to your Senpi agent at [senpi.ai](https://senpi.ai) and it writes the package with [`senpi-strategy-author`](senpi-strategy-author/).
2. Put the thesis in `scanners/scoring.py` (pure functions you can unit-test) and the market reads in `scanners/scan.py`.
3. Set the exits and risk limits in `runtime.yaml`. Every position gets a stop from the moment it opens.

The contract is in [`senpi-trading-runtime`](senpi-trading-runtime/). Guide: [How to code a trading strategy on Hyperliquid](https://senpi.ai/resources/learn/how-to-code-a-trading-strategy-on-hyperliquid).

## How do I use Claude or ChatGPT to trade on Hyperliquid?

Add Senpi to Claude, ChatGPT, Grok or Hermes, and your own Senpi agent places, changes and closes Hyperliquid trades for you, with your approval: see [Senpi for Claude](https://senpi.ai/senpi-for-claude). In Claude Code, Codex and Cursor, the [Senpi plugin](https://github.com/Senpi-ai/senpi-plugin) adds Senpi's research tools: the market, smart money, top traders, your own portfolio and this repo's strategy catalog.

## How can I automate a trading strategy on Hyperliquid without coding?

Describe it to your Senpi agent in plain English ("go long SOL when it breaks its 7-day high and the top traders are net long; cut it at 8%") and it builds, checks and deploys the strategy. Or pick a ready-made template on [senpi.ai/strategies](https://senpi.ai/strategies). Every one is open source here, so you can read its rules first. More: [How to automate a crypto trading strategy without coding](https://senpi.ai/resources/learn/automate-a-crypto-strategy-without-code).

## How do I follow smart money or copy trade on Hyperliquid?

[senpi.ai/traders](https://senpi.ai/traders) shows, free and with no account, where Hyperliquid's proven traders and biggest whales are positioned. Copy-trading templates such as [Jackal](strategies/jackal/), [Squid](strategies/squid/) and [Whalehunter](strategies/whalehunter/) mirror proven traders with your own size, and many other templates gate their entries on what the top traders hold. Inside your agent, [`senpi-smart-money`](senpi-smart-money/) and [`senpi-trader-research`](senpi-trader-research/) read the same data.

## Is it safe? What can a strategy do with my money?

- **Its own wallet and budget.** Each strategy trades from its own strategy wallet, funded with the budget you choose. No strategy shares capital with another.
- **A stop on every position.** The runtime's two-phase DSL (dynamic stop-loss) owns every exit: a hard stop from the moment a position opens, then a ratcheting ladder that locks in more of a winner as it runs.
- **Limits the model can't talk its way around.** Each strategy sets risk limits that the runtime enforces on every tick: a daily-loss halt, a drawdown breaker, maximum entries per day, cooldowns after losses, and margin, notional and leverage caps.
- **Nothing hidden.** Every rule a template trades by is in its folder here.

Senpi is non-custodial. How custody, signing and withdrawals work: [Is Senpi safe?](https://senpi.ai/resources/learn/is-senpi-safe)

---

## The skills

A Senpi agent's abilities are **skills**. Each one packages a tested multi-step workflow for one kind of request, so the model follows a proven path instead of assembling raw tool calls. Analytical skills pair a deterministic Python engine, which gathers and computes and emits JSON, with a `SKILL.md` that explains the result under strict rules: no invented numbers, honest sources, process over outcome.

| Skill | Ver | Role |
|---|---|---|
| **Analyze** | | |
| [`senpi-portfolio`](senpi-portfolio/) | 1.40.0 | Every wallet in one list by value (Senpi strategy wallets and the wallets you added), with positions, stop protection, each strategy's mandate, and managed and read-only subtotals |
| [`senpi-market-pulse`](senpi-market-pulse/) | 1.11.0 | The day's cross-asset read: crypto, stocks, commodities, macro and the funding regime; the positions check includes your saved wallets, read-only |
| [`senpi-smart-money`](senpi-smart-money/) | 1.8.0 | Where the most profitable wallets are positioned against the crowd, and what they just did; the alignment check includes your saved wallets, read-only |
| [`senpi-signals`](senpi-signals/) | 2.12.0 | Non-obvious market developments (funding extremes, the proven cohort against the 4h crowd, momentum events, cross-asset laggards), ranked through a trade lens and a news lens, from the same Senpi Signals the app and the browser extension show |
| [`senpi-trader-research`](senpi-trader-research/) | 1.9.0 | Ranks and vets Hyperliquid traders before you copy them: copyability, minimum budget, the live book; your saved wallets go to the Quant Desk, never the copy shortlist |
| [`senpi-improve-trades`](senpi-improve-trades/) | 1.25.4 | Reviews your trading from the telemetry event log: P&L net of fees, exit quality, give-back from the peak, missed signals, leaks and crashes; saved wallets are reviewed read-only as manual trades, in one book with your Senpi strategies |
| [`quant-desk`](quant-desk/) | 1.51.0 | **Quant Desk**: "run quant desk" on any Hyperliquid address reads 90 days of fills, fees and funding, scores six 0-100 dimensions and an overall quant score, prices each leak, audits protection, and compares the wallet with the proven and hot cohorts. With no address it finds candidates by account size; `--book` reads a Senpi user's whole book. Public data, read-only |
| [`senpi-account-status`](senpi-account-status/) | 1.11.4 | Points and rank, the Senpi trading fee (none on a paid plan or with credits), referrals, and the AI credits meter with the live credit balance (chat and tool turns spend plan credits, never trading money); the plan catalog, what running out of credits stops (the AI, simulations and the strategies on the agent; exchange stop-losses and mirror strategies stay) and what a lapsed plan pauses |
| **Run a strategy** | | |
| [`senpi-strategy-discover`](senpi-strategy-discover/) | 2.59.0 | Picks strategies for you: ranks the catalog against your view of the market and quotes each pick's exits and position size from its card; your holdings are context (saved wallets read-only, never a budget) |
| [`senpi-strategy-author`](senpi-strategy-author/) | 3.23.0 | Builds or edits a strategy package with DSL exits, one decision at a time |
| [`senpi-strategy-ops`](senpi-strategy-ops/) | 3.42.1 | Deploys, monitors and closes a named strategy (`deploy.py`, `close.py`) |
| [`senpi-trade`](senpi-trade/) | 1.13.0 | Direct trades, and copy trading of a specific trader, one decision at a time |
| [`senpi-trading-runtime`](senpi-trading-runtime/) | 4.1.18 | The runtime contract: `scan(inputs, ctx)`, `runtime.yaml` and the DSL |
| **Move money / positioning** | | |
| [`senpi-deposit-withdraw-transfer`](senpi-deposit-withdraw-transfer/) | 1.6.4 | Moving money: funds in through the funding card (a deposit address or an in-card USDC purchase), out through the app; top-ups checked against perps first, withdrawals at the exact available amount, fees stated before money moves |
| [`senpi-why`](senpi-why/) | 1.7.1 | What makes Senpi different: your AI quant reads the market, reviews your trading and runs your strategy 24/7, wherever you trade |

Skills compose: `improve-trades` pulls in `market-pulse`, `smart-money` and `portfolio`; `discover` hands a chosen package to `ops`; `author` hands a built package to `ops`. The agent routes by intent, and no skill re-implements another.

---

## The strategy templates

A **strategy** is a deployable package under [`strategies/`](strategies/): a market thesis written as scanner logic, risk settings and exits, running on its own funded wallet. [STRATEGIES.md](STRATEGIES.md) lists every template by style, with what it does, its markets, direction and risk.

```
strategies/spider/
├── strategy.yaml            <- manifest: id, version, catalog (discovery metadata), instances
├── swing/                   <- one instance = one wallet (multi-wallet funds have several)
│   ├── runtime.yaml         <- the executable spec: strategy, scanners, actions, exit, risk
│   └── scanners/
│       ├── scan.py          <- exports scan(inputs, ctx) and returns signals
│       └── scoring.py       <- pure, unit-testable thesis math
└── scalp/ ...               <- a second instance (different cadence, different wallet)
```

- **`strategy.yaml`** is the source of truth for deploying and attribution. Its `instances` list makes one package expand into one or more strategies, one wallet each, split by `funding_share`.
- **`runtime.yaml`** is the runtime's self-contained spec. The runtime calls `scan()` every `interval_seconds` and owns everything after it: signal checks, conviction-weighted sizing (`margin_pct`), execution, slot accounting, `risk.guard_rails` and the DSL exits.
- **`strategies/catalog.json`** is the generated index (never hand-edit it; run [`senpi-trading-runtime/scripts/gen_catalog.py`](senpi-trading-runtime/scripts/), then `scripts/gen_strategies_md.py`). `senpi-strategy-discover` ranks it.

### Every strategy exits through the DSL

There are no manual close actions. The runtime's two-phase DSL (dynamic stop-loss), set in each `runtime.yaml`'s `exit:` block, owns every exit:

- **Phase 1, survive.** A hard stop (`max_loss_pct`) cuts losers fast, from the moment the position opens.
- **Phase 2, lock.** As a winner runs, a ratcheting ladder of `tiers` (`trigger_pct` to `lock_hw_pct`) raises the stop, banking a growing share of the high-water mark while the trade stays open.

Lose small, let winners run: that asymmetry is behind every template.

### A risk engine wraps the whole strategy

The DSL protects each position. A strategy-level risk engine applies deterministic guards on every tick:

- **Circuit breakers:** a daily-loss halt and an intraday drawdown breaker stop trading on a bad day.
- **Turnover brakes:** maximum entries per day, consecutive-loss and per-asset cooldowns, because fees are the quiet killer of every bot.
- **Hard gates:** margin, notional and leverage limits reject any signal that would breach them, each logged with a reason code (`no_slots`, `no_margin`, `risk_gate_*`, `asset_banned`).
- **Conviction-weighted sizing:** position size scales with the live account (`margin_pct`) and the signal's own score, not a fixed lot.

The scanner proposes; the runtime's risk engine disposes.

---

## How it fits together

```
   You (chat) ──▶  Senpi Samurai: the model, tuned for Hyperliquid
                        │
                        ▼
                   OpenClaw host and agent workspace (memory, guardrails, heartbeats)
                        │  matches intent to a skill
                        ▼
                   SKILLS (this repo, open source): script → JSON → explanation
                        │  calls tools
                        ▼
                   Senpi MCP tools: market, discovery, leaderboard, strategy, execution, stops
                        │
                        ▼
                   @senpi-ai/runtime: runs scan(inputs, ctx), sizing, risk gates, DSL exits, event log
                        │
                        ▼
                   Hyperliquid perps: crypto, stocks, indices, commodities, pre-IPO, 24/7
```

**Open source here:** the skills and the strategy templates, the parts you'd want to read, audit, fork or contribute to. **The platform:** the Samurai model, the OpenClaw host, the MCP backend and the `@senpi-ai/runtime` supervisor, installed as a managed plugin.

The contract between them: skills call MCP tools; strategies export `scan(inputs, ctx)`; the runtime owns everything downstream. Nothing in this repo places an order directly. Orders go through the supervised runtime, which is where sizing, risk and exits live.

## The runtime contract (`@senpi-ai/runtime`)

The supervisor turns a package into a live, risk-managed strategy. A strategy author writes only `scan()` and config; the runtime owns the rest.

The `@senpi-ai/runtime` package itself is proprietary. The skills and strategy templates in this repo are MIT.

- **`scan(inputs, ctx)`** is your scanner, called every `interval_seconds`. `inputs` is the config from `runtime.yaml`; `ctx` gives you `ctx.senpi_mcp.call_tool(...)` for market, discovery and leaderboard data, and `ctx.state`, a small persistent store per scanner. You return signals (`{score, direction, ...}`); you never place an order.
- **Two-phase DSL exit engine:** a pure tick function checks hard timeout, dead weight, weak peak, the phase 1 breach and the phase 2 tier advance on every price update, and records a typed close reason (`tier_breach`, `max_retrace`, `trailing_floor`, `weak_peak`, `hard_timeout` and others).
- **Risk guard rails:** daily-loss halt, drawdown circuit breaker, consecutive-loss and per-asset cooldowns, and a maximum-entries-per-day cap. Blocked signals are logged with a reason code.
- **Telemetry event log:** every decision is written to an on-disk event stream per strategy (`position.opened`, `dsl.created`, `dsl.tier_advanced`, `dsl.closed`, `signal.outcome`, `order.filled`, `order.failed`, `runtime.paused` and more), readable with `openclaw senpi events`, `explain` and `audit`. It's what `senpi-improve-trades` reads for exit quality, leaks, blocked signals and health checks, so an agent can review its own work and you can see exactly what it did and why.
- **Skills stay current:** a manifest-driven updater keeps installed skills up to date (semver-gated by a `maxMajor` ceiling), so agents get improvements without reinstalling.

---

## Run it yourself (advanced)

The fastest path is to **[launch an agent on senpi.ai](https://senpi.ai)**. It sets up the agent host, the runtime plugin and the Senpi tools for you, with no infrastructure to run.

To run a strategy by hand on your own [OpenClaw](https://openclaw.ai) host:

```bash
# 1. Install the runtime plugin and set up Senpi MCP access (SENPI_AUTH_TOKEN)
openclaw plugins install @senpi-ai/runtime

# 2. Pick a strategy (or ask the agent: "what should I trade?")
#    senpi-strategy-discover ranks strategies/catalog.json against your goals

# 3. Deploy: creates a funded wallet per instance, deploys, checks the scanner ticked
python3 ~/.openclaw/skills/senpi-strategy-ops/scripts/deploy.py <id> --budget <usd>

# 4. Monitor
openclaw senpi status            # a strategy is live once its scanner has a recent tick

# 5. Close: flattens positions and returns funds
python3 ~/.openclaw/skills/senpi-strategy-ops/scripts/close.py <id>
```

To build a new strategy, start with [`senpi-strategy-author`](senpi-strategy-author/) and the [`senpi-trading-runtime`](senpi-trading-runtime/) contract.

**Requirements:** an [OpenClaw](https://openclaw.ai) agent host (Linux, Python 3.8+) with the `@senpi-ai/runtime` plugin; a funded Hyperliquid wallet per strategy instance (no shared capital); a [Senpi](https://senpi.ai) MCP access token (`SENPI_AUTH_TOKEN`).

---

## Repo layout

```
senpi-skills/
├── senpi-portfolio/  senpi-market-pulse/  senpi-smart-money/      <- analyze
│   senpi-trader-research/  senpi-improve-trades/  senpi-account-status/  senpi-signals/  quant-desk/
├── senpi-strategy-discover/  senpi-strategy-author/                <- run a strategy
│   senpi-strategy-ops/  senpi-trade/  senpi-trading-runtime/
├── senpi-deposit-withdraw-transfer/  senpi-why/                    <- money / positioning
│
├── strategies/                    <- the strategy packages and the registry
│   ├── catalog.json               <- GENERATED index
│   └── <id>/ ...                  <- strategy.yaml + <instance>/{runtime.yaml, scanners/}
├── STRATEGIES.md                  <- GENERATED list of every template (scripts/gen_strategies_md.py)
│
└── CLAUDE.md                      <- repo conventions for AI editors
```

**Branches:** `main` is what every Senpi agent installs, and changes reach it through reviewed pull requests. Senpi's dev environment installs from `dev`.

## Contributing

Pull requests are welcome: a new template, a fix, a better test. Read [CLAUDE.md](CLAUDE.md) for the conventions; CI runs every package's tests. If a template here helped your trading, star the repo so other traders can find it.

## License

MIT. Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://www.coinbase.com/ventures).

> Trading perpetual futures carries substantial risk of loss. Senpi is software, not financial advice; strategies can and do lose money. Nothing here is a promise of returns.
