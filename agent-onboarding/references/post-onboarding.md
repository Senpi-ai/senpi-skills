# Post-Onboarding Reference

## About Senpi

Senpi is a trading platform on Hyperliquid -- a high-performance perpetual futures DEX.

**What agents can do:**
- Discover profitable traders (Hyperfeed + Discovery)
- Mirror top traders or run custom strategies with full position control
- Trade 200+ crypto perps, plus equities, metals, and indices via XYZ DEX

**Core loop:** spot what's profiting -> validate with data -> trade or copy.

**After the MCP server is active**, call `read_senpi_guide(uri="senpi://guides/senpi-overview")` for the full platform reference (wallets, strategies, tool categories, fees, workflows, gotchas).

---

## Available Skills

Pre-built trading strategies to install on top of the core MCP tools. Each skill is a SKILL.md file loaded into the agent.

| Skill | Description |
|-------|-------------|
| **DSL / DSL Tight** | Trailing stop loss with ROE-based tier triggers (tight variant is more aggressive) |
| **Opportunity Scanner** | Scores 500+ perps 0-400 on smart money + technicals |
| **Emerging Movers** | Tracks smart money concentration every 60s |
| **Whale Index** | Auto-mirrors top Discovery traders by PnL, win rate, consistency |
| **Autonomous Trading** | Orchestrates multiple skills with budget, target, and deadline |
| **WOLF Strategy** | Autonomous 2-3 slot concentrated position manager |
| **HOWL** | Nightly self-improvement loop analyzing trade history |

**How to install a skill:**
- Senpi Telegram app: Send the SKILL.md file to your agent with "Here are some new superpowers"
- Other agents: Download from `github.com/Senpi-ai/senpi-skills/<skill-name>/SKILL.md`

Most skills require a funded wallet ($500-$1k+) and a high-capability model (Claude Opus or equivalent).

---

## Suggested First Actions

After restarting the agent and funding the wallet, suggest:
- "Check your portfolio" (uses `account_get_portfolio` tool)
- "Discover top traders" (uses `discovery_get_top_traders` tool)
- "View market data" (uses `market_get_prices` tool)
