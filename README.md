# Senpi Skills — The Home of Hyperliquid Agents

16 AI trading skills. 11 trading strategies. 26 scanners. All open source. All tracked live.

Senpi Skills is the open source repository for autonomous trading strategies on [Hyperliquid](https://hyperliquid.xyz) via [Senpi](https://senpi.ai). Each skill is a self-contained trading agent that scans markets 24/7, enters and exits positions, manages trailing stops, and protects capital — autonomously.

**Live tracker:** [senpi.ai/predators](https://senpi.ai/predators) — every skill running with real money, full transparency.

## Skills (16 unique trading agents)

### Momentum & Leaderboard
| Skill | Description | Scanner Interval |
|---|---|---|
| 🦊 [FOX](./fox) | Explosive breakout sniper. Catches First Jumps on the leaderboard before the crowd. **Best performer at +18% ROI.** | 3 min |
| 🐺 [WOLF](./wolf-strategy) | Pack hunter. Leaderboard momentum, enters early on what smart money is buying. | 3 min |
| 🦅 [HAWK](./hawk-trading-bot) | Scans BTC, ETH, SOL, HYPE every 30 seconds. Picks the single strongest signal. | 30 sec |

### Single-Asset Hunters
| Skill | Description | Scanner Interval |
|---|---|---|
| 🐻 [GRIZZLY](./grizzly) | BTC only. Every signal source. 15-20x leverage. Maximum conviction. | 3 min |
| 🐆 CHEETAH | HYPE only. 8-12x leverage. Fastest predator for the fastest asset. | 3 min |

### Multi-Signal Convergence
| Skill | Description | Scanner Interval |
|---|---|---|
| 🐅 [TIGER](./tiger-strategy) | 5 parallel scanners, 230 assets, ROAR meta-optimizer that learns from results. | 5 min |
| 🐍 [COBRA](./cobra) | Triple convergence. Only strikes when price, volume, and new money all agree. | 3 min |
| 🦬 [BISON](./bison) | Conviction holder. Top 10 assets, 4h trend thesis, holds hours to days. | 5 min |

### Range & Technical
| Skill | Description | Scanner Interval |
|---|---|---|
| 🐍 [VIPER](./viper) | Range-bound mean reversion at support/resistance. Works when nothing is trending. **+6% ROI.** | 5 min |
| 🐆 PANTHER | BB squeeze breakout scalper. Fastest cuts in the zoo. | 2 min |
| 🦅 EAGLE | Correlation breaks + macro events. BTC/ETH vs alts divergence. | 3 min |

### Alternative Edge
| Skill | Description | Scanner Interval |
|---|---|---|
| 🦉 [OWL](./owl) | Pure contrarian. Enters against extreme crowding when exhaustion signals fire. | 15 min |
| 🦈 [SHARK](./shark) | SM consensus + liquidation cascade front-running. | 5 min |
| 🐊 [CROC](./croc) | Funding rate arbitrage. Collects payments while waiting for the snap. | 15 min |
| 🦂 [SCORPION](./scorpion) | Mirrors whale wallets. Exits the instant they do. | 5 min |
| 🦗 MANTIS | High-conviction whale mirror. 4+ whale consensus, 30-min aging, quality scoring. | 5 min |

## Trading Strategies (11 config overrides)

Trading strategies run on a parent skill's scanner with different entry filters, DSL settings, and risk parameters. Same code, different personality.

### On FOX
| Strategy | What Changes |
|---|---|
| 🦊 [FERAL FOX](./feral-fox-strategy%201.2.md) | Score 7+, 3 reasons, regime enforced, structural invalidation, no time exits |
| 👻 [GHOST FOX](./ghost-fox-strategy%20(1).md) | Feral Fox entries + DSL High Water Mode infinite trailing at 85% of peak |
| 🐱 LYNX | Patient high-bar momentum. Score 10+, wide stops, no time exits. The proven pattern. |
| 🐺 JACKAL | Choppy market variant. Score 12+, fast Phase 1 kills, 5-min dead weight. A/B test against LYNX. |

### On WOLF
| Strategy | What Changes |
|---|---|
| 🐺 [DIRE WOLF](./wolf-strategy) | Replaces WOLF config entirely. FIRST_JUMP only, zero rotation, maker fees, DSL High Water. |
| 🐺 WOLF NIGHTSHIFT | After-hours Asian session momentum. |

### On TIGER
| Strategy | What Changes |
|---|---|
| 🦁 LION | Patient multi-scanner. Stricter confluence, scanner weighting, no time exits, drawdown auto-resume. |
| 🐅 TIGER SNIPER | Compression + correlation scanners only. Maximum confluence bar. |

### On VIPER
| Strategy | What Changes |
|---|---|
| 🐍 [MAMBA](./mamba-strategy%20(1).md) | Viper entries + DSL High Water. Catches the range bounce AND the breakout that escapes. |

### On CROC
| Strategy | What Changes |
|---|---|
| 🐊 GATOR | Patient funding arb. No time exits, structural thesis exits only (funding flip = dead), funding income tracking. |

### On OWL
| Strategy | What Changes |
|---|---|
| 🦉 OWL AGGRESSIVE | Lower crowding threshold, faster entry. |

## Shared Infrastructure

These are plugins used by all skills automatically. Users don't need to install them separately.

| Plugin | Purpose |
|---|---|
| [DSL Dynamic Stop Loss](./dsl-dynamic-stop-loss) | Trailing stop engine. Supports fixed ROE tiers and [High Water Mode](./dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md) (percentage-of-peak locks). |
| [ALO Guide](./alo-guide-for-agents.md) | Maker order execution for fee-optimized entries. |
| [Senpi Onboard](./senpi-onboard) | Agent onboarding and account setup. |
| [Getting Started Guide](./senpi-getting-started-guide) | Interactive first-trade tutorial. |
| [Emerging Movers](./emerging-movers) | Leaderboard scanner shared by FOX and WOLF. |
| [Opportunity Scanner](./opportunity-scanner) | Deep 4-stage funnel scanner for FOX. |
| [Whale Index](./whale-index) | Legacy whale mirror (replaced by SCORPION/MANTIS). |
| [Wolf Howl](./wolf-howl) | WOLF's nightly self-improvement loop. |

## DSL High Water Mode

The trailing stop configuration originally designed for and proven on FOX. Instead of locking fixed ROE amounts, High Water Mode locks a percentage of the peak. The stop trails at 85% of the highest ROE the trade has ever reached, with no ceiling.

**Full spec:** [dsl-high-water-spec 1.0.md](./dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md)

**Adoption guide (all skills):** [dsl-high-water-adoption-guide.md](./dsl-dynamic-stop-loss/dsl-high-water-adoption-guide.md)

Skills using High Water Mode: BISON, GRIZZLY, CHEETAH, GHOST FOX, DIRE WOLF, MAMBA, LYNX, JACKAL, MANTIS, OWL, GATOR, LION. FOX targets High Water as default. COBRA, HAWK, SCORPION, VIPER, SHARK have adoption configs ready.

## Architecture

```
Plugins ──→ Skills ──→ Trading Strategies
(shared)     (scanner)   (config override)
```

**Plugins** are shared infrastructure — trailing stops, risk management, fee optimization. Maintained once, every skill benefits.

**Skills** are the trading logic — the scanner that embodies a thesis about how to make money. FOX's First Jump detector. VIPER's range analysis. OWL's crowding exhaustion. Each skill is a thin layer on top of shared plugins.

**Trading Strategies** are saved configurations — the specific numbers that tune how aggressively a skill behaves. LYNX is FOX's scanner with score 10+ filters. LION is TIGER's scanners with no time exits. The skill is the predator. The strategy is how you teach it to hunt.

## Quick Start

1. Deploy [OpenClaw](https://openclaw.ai) with [Senpi](https://senpi.ai) MCP configured
2. Install a skill: `npx skills add Senpi-ai/senpi-skills/<skill-name>`
3. The agent reads SKILL.md, runs bootstrap, creates crons, and starts trading
4. Monitor via Telegram alerts and [senpi.ai/predators](https://senpi.ai/predators)

**Recommended first skill:** FOX — proven +18% ROI, includes copy trading + autonomous mode.

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token
- Python 3.8+ (no external dependencies — all skills use stdlib only)

## Contributing

Each skill is self-contained in its own directory. Trading strategies are config override files (JSON + markdown spec). See any skill's SKILL.md for the full agent instructions.

All skills use [DSL High Water Mode](./dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md) as the target trailing stop configuration. See the [adoption guide](./dsl-dynamic-stop-loss/dsl-high-water-adoption-guide.md) for per-skill tier tables.

## License

Apache-2.0 — Built by [Senpi](https://senpi.ai). Attribution required for derivative works.
