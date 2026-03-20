[README (8).md](https://github.com/user-attachments/files/26147922/README.8.md)
# Senpi Skills — Autonomous AI Trading Agents for Hyperliquid

30+ AI trading skills. Open source. Real money. Live tracked.

Senpi Skills is the open-source repository for autonomous trading strategies on [Hyperliquid](https://hyperliquid.xyz) via [Senpi](https://senpi.ai). Each skill is a self-contained trading agent that scans markets, enters and exits positions, manages trailing stops, and protects capital — autonomously, 24/7, with no human in the loop.

**Live tracker:** [strategies.senpi.ai](https://strategies.senpi.ai)

---

## What We Learned From 30 Agents and $26K

Eight days. Thirty agents. $5.5M in volume. 6,764 trades. The findings that shaped every skill in this repo:

**The model is a commodity.** Polar made 29 trades at +28.1%. Ghost Fox made 1,078 trades at -58.5%. Same LLM, same exchange, same week. 86-point performance spread. The edge is the skill — the scanner, the scoring, the risk management — not the model.

**Fewer trades wins.** Consistent inverse correlation between trade frequency and performance across all 30 agents. Seven agents sat at 0% with zero trades during chop. They were the smartest ones in the room.

**Agents will self-modify into worse performance.** Every agent that adjusted its own config during a losing streak made things worse. Zero exceptions. The fix: hardcode critical parameters in the scanner code itself, not in instructions the agent can override.

**The weak peak bleed.** Fox v1.0 produced 17 Stalker trades at score 6-7 with a 17.6% win rate. Trades would bump +0.5%, stall, and DSL would cut them for $3-$10 each. Death by a thousand cuts. Fixed in v1.2+ with raised thresholds and tighter Phase 1 timing.

---

## Active Skills

### The A/B/C/D Experiment (Stalker/Striker Scanner Variants)

Four agents running the same base scanner, same capital, same market. Only the Stalker filter differs.

| Skill | Version | Experiment Variable | Description |
|---|---|---|---|
| 🐋 [ORCA](./orca) | v1.2 | Control | Dual-mode SM scanner. Stalker minScore 7, climb 8+, streak gate. Fox's lessons applied. |
| 🦊 [FOX](./fox) | v2.0 | Breadth | Stalker requires 3+ distinct scoring reasons. Tests confirmation breadth. |
| 🦗 [MANTIS](./mantis) | v3.0 | Signal quality | Contribution acceleration threshold 3x higher, weak +1 tier eliminated. Tests SM velocity quality. |
| 🪳 [ROACH](./roach) | v1.0 | Stalker elimination | Striker only. Stalker disabled entirely. Tests whether Stalker is pure drag. |

### Gen-2 Intelligence

| Skill | Version | Description |
|---|---|---|
| 🐆 [JAGUAR](./jaguar) | v1.0 | Three-mode scanner (Stalker + Striker + Hunter) with gen-2 signal intelligence. TCS/TRP quality tags, momentum events, contribution velocity. Pyramiding on Phase 2 winners. Max 7 positions. |
| 🐉 [HYDRA](./hydra) | v1.0 | Six-source squeeze scanner. Funding divergence, liquidation cascades, OI surges, momentum exhaustion, SM consensus, trend alignment. Independent monitor watchdog. Self-learning tier disablement. |
| 🦅 [RAPTOR](./raptor) | v1.0 | Tier 2 momentum events filtered by TCS/TRP quality tags + SM leaderboard confluence. 3-5 trades/day. |
| 🔥 [PHOENIX](./phoenix) | v1.0.1 | Contribution velocity scanner. SM profit velocity diverging from price. One API call, zero state. |
| 🛡️ [SENTINEL](./sentinel) | v1.0 | Inverted pipeline: finds rising assets → verifies quality traders via momentum event tags. Most selective scanner in fleet. |

### Momentum Pyramider

| Skill | Version | Description |
|---|---|---|
| 🦏 [RHINO](./rhino) | v2.0 | Enter small (30%), add at +10% ROE (40%), add at +20% ROE (30%). Thesis re-validated before every add. Hardened after March 19 death loop. 🟡 Ready — awaiting deployment. |

### Single-Asset Hunters

| Skill | Version | Asset | Description |
|---|---|---|---|
| 🐻 [GRIZZLY](./grizzly) | v2.1.1 | BTC | Three-mode lifecycle: HUNT → RIDE → STALK → RELOAD. 10x cap. |
| 🐻‍❄️ [POLAR](./polar) | v1.0 | ETH | #1 performer at +28.1%. The proof that single-asset + lifecycle works. |
| 🐻 [KODIAK](./kodiak) | v1.1.1 | SOL | Tighter DSL floors for SOL's volatility. 10x cap. |
| 🦡 [WOLVERINE](./wolverine) | v1.1 | HYPE | 5-10x leverage. BTC divergence is bonus-only. |
| 🐆 CHEETAH | v1.0 | HYPE | Different scanner than Wolverine. Zero trades in chop = correct. |
| 🦅 [CONDOR](./condor) | v1.0.1 | Multi | BTC/ETH/SOL/HYPE — picks strongest thesis. Conviction-scaled margin. |

### Specialized Scanners

| Skill | Version | Description |
|---|---|---|
| 🦬 [BISON](./bison) | v1.2.1 | Conviction trend holder. Requires 4H/1H agreement. Zero trades in chop = correct. |
| 🐟 [BARRACUDA](./barracuda) | v1.0.1 | Funding decay collector. 6-gate model. Building local funding history (230 assets, 11K+ snapshots). |
| 🦉 [OWL](./owl) | v5.2 | Contrarian crowding-unwind. Five-factor model. |
| 🦅 [HAWK](./hawk-trading-bot) | v1.2 | Single best signal picker. 20x leverage (too high — monitoring). |
| 🦅 [BALD EAGLE](./bald-eagle) | v1.0 | XYZ equities only (tokenized stocks: S&P500, NVDA, GOLD, SILVER). Lower trader thresholds for thin markets. |
| 🦎 [KOMODO](./komodo) | v1.0 | Momentum event consensus. Uses real-time leaderboard_get_momentum_events. |

### Shared Infrastructure

| Plugin | Purpose |
|---|---|
| [DSL Dynamic Stop Loss](./dsl-dynamic-stop-loss) | Trailing stop engine. [High Water Mode spec](./dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md). |
| [Fee Optimizer](./fee-optimizer) | ALO vs MARKET decision, fee computations. |
| [Emerging Movers](./emerging-movers) | Leaderboard scanner shared by FOX, WOLF, and VIXEN. |
| [Opportunity Scanner](./opportunity-scanner) | Deep 4-stage funnel scanner. |
| [Senpi Onboard](./senpi-onboard) | Agent onboarding and account setup. |
| [Getting Started Guide](./senpi-getting-started-guide) | Interactive first-trade tutorial. |

---

## Paused Skills

These skills are not actively trading. Code is preserved for reference and potential reactivation. Each was paused based on live performance data.

| Skill | Last Version | Reason Paused | Replaced By |
|---|---|---|---|
| 🦊 [VIXEN](./vixen) | v1.0 | -47.3% ROI. Scanner logic absorbed into Orca. | Orca v1.2 |
| 👻 [Ghost Fox](./ghost-fox-strategy) | v2.0 | -58.5% ROI. 1,078 trades. Fee drag. | Orca v1.2 |
| 🦊 [Feral Fox](./feral-fox-v3-strategy.md) | v3.0 | -31.5% ROI. | Orca v1.2 |
| 🐺 [Dire Wolf](./wolf-strategy) | v1.0 | -42.3% ROI. | Orca v1.2 |
| 🐺 [WOLF](./wolf-strategy) | v1.0 | Base scanner superseded. | Orca v1.2 |
| 🐍 [COBRA](./cobra) | v2.0 | -53.0% ROI. | Paused |
| 🐍 [MAMBA](./mamba) | v1.0 | -36.8% ROI. | Paused |
| 🐍 [ANACONDA](./anaconda) | v1.0 | -21.9% ROI. | Paused |
| 🐍 [VIPER](./viper) | v1.0 | -19.9% ROI. | Paused |
| 🐺 [JACKAL](./jackal) | v2.0 | -32.8% ROI. | Paused |
| 🦂 [SCORPION](./scorpion) | v2.0 | -21.1% ROI. Stale position data. | Komodo v1.0 |
| 🐅 [TIGER](./tiger-strategy) | v1.0 | -58.0% ROI. Over-trading. | Paused |
| 🦈 [SHARK](./shark) | v1.0 | -4.3% ROI. | Paused |
| 🐊 [CROC](./croc) | v1.0 | -42.9% ROI. Fee drag. | Paused |
| 🐊 [GATOR](./gator) | v1.0 | Funding arb underperformed. | Paused |
| 🦅 EAGLE | v1.0 | Correlation breaks not profitable. | Paused |
| 🐆 PANTHER | v1.0 | BB squeeze scalping not profitable. | Paused |

---

## The Proven Pattern

Across all 30+ skills, one pattern dominates: **fewer trades + higher conviction + wider stops = better performance.**

| Agent | Trades | Return | The Lesson |
|---|---|---|---|
| 🐻‍❄️ Polar | 29 | **+28.1%** | Single asset, lifecycle patience, wide stops |
| 🦗 Mantis | 460 | **+5.6%** | SM scanner, moderate frequency |
| 🦊 Fox | 554 | **+3.7%** | Selective entries, let winners run |
| 👻 Ghost Fox | 1,078 | **-58.5%** | Over-trading kills |
| 🐅 Tiger | 726 | **-58.0%** | More scanners ≠ better results |

Every winning skill upgrade has tightened entry filters, not loosened stop losses.

---

## DSL v1.1.1 Pattern (Mandatory)

Every active skill uses this DSL state pattern. If any field is wrong, the agent is running with broken safety.

```json
{
  "highWaterPrice": null,
  "highWaterRoe": null,
  "phase1": {
    "consecutiveBreachesRequired": 3,
    "phase1MaxMinutes": 30,
    "deadWeightCutMin": 10,
    "absoluteFloorRoe": -20
  }
}
```

Critical field names — get these wrong and positions bleed silently:
- `phase1MaxMinutes` (NOT hardTimeoutMinutes)
- `deadWeightCutMin` (NOT deadWeightCutMinutes)
- `absoluteFloorRoe` (NOT absoluteFloor — no static price values)
- `highWaterPrice: null` (NOT 0)
- `consecutiveBreachesRequired: 3` (NOT 1)

---

## Architecture

```
Plugins ──→ Skills ──→ Agents
(shared)     (scanner)   (deployed instance)
```

**Plugins** are shared infrastructure — trailing stops, risk management, fee optimization. Maintained once, every skill benefits.

**Skills** are the trading logic — the scanner that embodies a thesis about how to make money. Each skill is a self-contained directory with a scanner, config helper, configuration, and SKILL.md that the agent reads as its operating instructions.

**Agents** are deployed instances of skills running with real capital on the Senpi Predators arena. Multiple agents can run the same skill with different parameters.

---

## Quick Start

1. Deploy [OpenClaw](https://openclaw.ai) with [Senpi](https://senpi.ai) MCP configured
2. Install a skill: `npx skills add Senpi-ai/senpi-skills/<skill-name>`
3. The agent reads SKILL.md, runs bootstrap, creates crons, and starts trading
4. Monitor via Telegram alerts and [strategies.senpi.ai](https://strategies.senpi.ai)

**Recommended first skill:** [ORCA v1.2](./orca) — dual-mode scanner with Fox's live trading lessons applied. The most battle-tested scanner in the zoo.

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token
- Python 3.8+ (no external dependencies — all skills use stdlib only)

## Contributing

Each skill is self-contained in its own directory. See any skill's SKILL.md for the full agent instructions. All active skills use [DSL High Water Mode](./dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md) as the target trailing stop configuration.

**When adding a new skill**, add an entry to [`catalog.json`](./catalog.json).

## License

MIT — Built by [Senpi](https://senpi.ai).
