[README.md](https://github.com/user-attachments/files/26472209/README.md)
# Senpi Skills — Autonomous AI Trading Agents for Hyperliquid

52 AI trading agents. Open source. Real money. Live onchain.

Each skill is a self-contained autonomous trading agent that scans the [Hyperfeed](https://senpi.ai) — Senpi's proprietary real-time data layer tracking the top 1,000 traders on [Hyperliquid](https://hyperliquid.xyz) — and enters, manages, and exits positions 24/7 with no human in the loop.

**Live fleet tracker:** [strategies.senpi.ai](https://strategies.senpi.ai)
**Arena competition:** [senpi.ai/arena](https://senpi.ai/arena)
**Platform:** [senpi.ai](https://senpi.ai)

---

## The Thesis

**Fewer trades + higher conviction + wider stops = better performance.**

This was proven across 30+ live agents with real money. The fleet's top performers are single-asset lifecycle hunters that wait for extreme Smart Money (SM) consensus before entering. The worst performers are high-frequency multi-asset scanners that churn fees.

**The model is a commodity. The data layer is the edge. The runtime is the moat.**

---

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │          SENPI PLATFORM              │
                    │                                      │
                    │   48 MCP Tools  ·  Hyperfeed Data    │
                    │   Top 1K Traders · Real-time Signals │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │         PLUGIN RUNTIME               │
                    │                                      │
                    │  position_tracker (10s) + DSL (30s)  │
                    │  Tracks positions onchain             │
                    │  Evaluates exits via DSL engine       │
                    │  Eliminates state file bugs           │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
     ┌────────▼────────┐ ┌────────▼────────┐ ┌─────────▼───────┐
     │  SKILL (Scanner) │ │  SKILL (Scanner) │ │  SKILL (Scanner) │
     │  cobra-scanner   │ │  grizzly-scanner │ │  scorpion-scanner│
     │  Entry logic only │ │  Entry logic only │ │  Entry logic only│
     └────────┬─────────┘ └────────┬─────────┘ └────────┬────────┘
              │                    │                     │
     ┌────────▼────────┐ ┌────────▼────────┐ ┌─────────▼───────┐
     │   VAULT ($1K)   │ │   VAULT ($1K)   │ │   VAULT ($1K)   │
     │  Funded wallet   │ │  Funded wallet   │ │  Funded wallet   │
     │  Live on Hyperl. │ │  Live on Hyperl. │ │  Live on Hyperl. │
     └─────────────────┘ └─────────────────┘ └─────────────────┘
```

**Skills** contain the trading logic — a scanner that embodies a thesis about how to make money. Each skill is a self-contained directory with a scanner script, runtime.yaml, SKILL.md (agent instructions), and README.

**The Plugin Runtime** (runtime.yaml) manages position tracking and exits. The `position_tracker` polls onchain state every 10 seconds. The DSL engine evaluates exit conditions every 30 seconds. This eliminates an entire class of bugs from the Python DSL cron system (missing state files, wallet field injection, silent cron deaths).

**Vaults** are funded wallets on Hyperliquid. Each agent gets its own vault with isolated capital.

---

## The Plugin Runtime

Every active agent runs on the plugin runtime defined in `runtime.yaml`. This replaced the legacy Python DSL cron system.

```yaml
name: example-tracker
version: 1.0.0
description: >
  Agent description here.

strategy:
  wallet: "${WALLET_ADDRESS}"
  budget: 1000
  slots: 1                    # Max concurrent positions
  margin_per_slot: 400        # $ per position
  enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s              # Polls onchain every 10 seconds

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 30         # Evaluates DSL every 30 seconds
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 180
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 60
      min_value: 3.0
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 45
    phase1:
      enabled: true
      max_loss_pct: 15.0
      retrace_threshold: 8
      consecutive_breaches_required: 3
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 5,  lock_hw_pct: 25 }
        - { trigger_pct: 10, lock_hw_pct: 45 }
        - { trigger_pct: 15, lock_hw_pct: 60 }
        - { trigger_pct: 20, lock_hw_pct: 75 }

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

Key rules:
- `version` is always `1.0.0` in runtime.yaml (skill version lives in SKILL.md metadata)
- `execution` block is NOT supported in YAML — FEE_OPTIMIZED_LIMIT is specified in the scanner's entry output and passed to `create_position` via MCP
- Scanners handle entries only. DSL handles all exits. Scanners must NEVER exit positions.

---

## DSL Dynamic Stop Loss

The DSL engine is the shared exit mechanism for every agent. It runs as a plugin, evaluating onchain position state every 30 seconds.

### How It Works

**Phase 1 (Loss Protection):** Monitors unrealized ROE against a dynamic floor. If ROE retraces beyond the threshold from the high water mark for N consecutive checks, the position is closed.

**Phase 2 (Profit Locking):** As ROE climbs past configurable tier triggers, the floor ratchets up. A position that hits +10% ROE locks in a percentage of that gain as a trailing stop.

**Timeouts:** Hard timeout closes positions that haven't moved. Weak peak cut exits positions stuck at low gains. Dead weight cut exits positions sitting at breakeven.

### DSL State (Generated by Scanner)

The scanner outputs complete DSL state when entering a position:

```json
{
  "coin": "BTC",
  "direction": "SHORT",
  "leverage": 10,
  "leverageType": "CROSS",
  "absoluteFloorRoe": null,
  "highWaterRoe": null,
  "highWaterPrice": null,
  "currentTier": 0,
  "consecutiveBreaches": 0,
  "consecutiveBreachesRequired": 3,
  "phase1MaxMinutes": 45,
  "deadWeightCutMin": 45,
  "phase1": {
    "maxLossPct": 15.0,
    "retraceThreshold": 8,
    "enabled": true
  },
  "phase2": {
    "enabled": true,
    "tiers": [
      { "triggerPct": 5,  "lockHwPct": 25 },
      { "triggerPct": 10, "lockHwPct": 45 },
      { "triggerPct": 15, "lockHwPct": 60 }
    ]
  },
  "hardTimeout": { "enabled": true, "intervalInMinutes": 180 },
  "weakPeakCut": { "enabled": true, "intervalInMinutes": 60, "minValue": 3.0 },
  "deadWeightCut": { "enabled": true, "intervalInMinutes": 45 }
}
```

Critical fields — get these wrong and positions run unprotected:
- `highWaterPrice: null` (NOT 0 — DSL initializes this dynamically)
- `absoluteFloorRoe: null` (NOT a static price — DSL calculates dynamically)
- `consecutiveBreachesRequired: 3` (prevents single-tick noise from closing)
- `phase1MaxMinutes` (NOT `hardTimeoutMinutes`)
- `deadWeightCutMin` (NOT `deadWeightCutMinutes`)

### Fee-Optimized Execution

All agents use `FEE_OPTIMIZED_LIMIT` orders specified in the scanner's entry JSON output (not in runtime.yaml):

```json
{
  "orderType": "FEE_OPTIMIZED_LIMIT",
  "ensureExecutionAsTaker": true,
  "executionTimeoutSeconds": 30
}
```

This places a maker order first (~0.02% fee), falls back to taker (~0.05% fee) if not filled in 30 seconds.

---

## Skill Categories

### Single-Asset Lifecycle Hunters
Patient agents that track one asset through HUNT→RIDE→STALK→RELOAD phases. Wait for extreme SM conviction before entering. Widest DSL settings.

| Skill | Asset | Description |
|---|---|---|
| 🐻‍❄️ [Polar](./polar) | ETH | Three-mode lifecycle. The patience benchmark. |
| 🐻 [Grizzly v3.0](./grizzly) | BTC | BTC lifecycle hunter with hardened scoring. |
| 🐻 [Grizzly Horribilis](./grizzly) | BTC | Aggressive BTC variant. Higher leverage on conviction. |
| 🐻 [Kodiak](./kodiak) | SOL | SOL lifecycle hunter. |
| 🦡 [Wolverine v2.0](./wolverine) | HYPE | HYPE hunter. Entry-only scanner, 7x leverage. |
| 🐆 [Cheetah](./cheetah) | HYPE | HYPE SM scanner with daily trade cap. |

### Multi-Asset SM Scanners
Scan across BTC/ETH/SOL/HYPE for the highest SM conviction at any moment. Conviction-scaled margin allocation.

| Skill | Description |
|---|---|
| 🦅 [Condor v2.0](./condor) | Multi-asset conviction-scaled margin (25/35/45%). |
| 🐋 [Orca v2.0](./orca) | Canonical scanner template. Stalker + Striker dual-mode. |
| 🔥 [Phoenix v2.0](./phoenix) | Contribution velocity scanner. SM profit velocity diverging from price. |
| 🐆 [Jaguar v2.0](./jaguar) | Striker-only multi-asset. No Stalker, no pyramiding. |

### Arena-Optimized Agents
Built specifically to win weekly Arena competitions. Higher conviction, concentrated margin.

| Skill | Description |
|---|---|
| 🐍 [Cobra v1.1](./cobra) | Arena Sprint Predator. Trades the #1 SM dominant asset with $400 margin, 10x leverage. |
| 🦂 [Scorpion v2.0](./scorpion-v2) | Altcoin Swarm Hunter. Detects coordinated altcoin risk-off events (5+ alts at SM >2%), trades the best target. |

### Intelligence Agents
Use advanced Hyperfeed signals — momentum events, trader quality tags (TCS/TRP), contribution velocity, inverted pipelines.

| Skill | Description |
|---|---|
| 🦅 [Raptor v2.0](./raptor) | Tier 2 momentum events + TCS/TRP quality tags. |
| 🛡️ [Sentinel](./sentinel) | Inverted pipeline: rising assets → verify quality traders. Most selective scanner. |
| 🍋 [Lemon](./lemon) | Degen Fader. Counter-trades CHOPPY/DEGEN traders at 10x+ leverage bleeding -10%+ ROE. |
| 🦅 [Bald Eagle v2.0](./bald-eagle) | XYZ Alpha Hunter. All 54 XYZ assets (commodities, indices, equities). Spread gate >0.1%. |

### Specialized

| Skill | Description |
|---|---|
| 🦬 [Bison v1.2](./bison) | Conviction trend holder. Requires 4H/1H agreement. |
| 🐟 [Barracuda](./barracuda) | Funding decay collector. Building local funding history (230 assets, 11K+ snapshots). |
| 🦉 [Owl](./owl) | Contrarian crowding-unwind. |
| 🦈 [Mako](./mako-strategy) | Volume generation engine. Single Python process, no LLM in execution path. |

---

## Lessons from the Fleet

### 1. Scanners Enter. DSL Exits. Never Both.

When scanners re-evaluate open positions and close them on "thesis invalidation," they chop winners before DSL can trail them. The one trade you let run is worth more than all other winners combined.

**All v2.0+ agents output NO_REPLY when a position is active.** DSL is the only exit mechanism.

### 2. Fees Are the Silent Killer

Two agents can have nearly identical profit but wildly different fee loads. The difference: FEE_OPTIMIZED_LIMIT orders and wider DSL that doesn't churn in and out.

**All new agents must use FEE_OPTIMIZED_LIMIT and target a profit-to-fee ratio above 5:1.**

### 3. XYZ Equities Banned at Parse Level

XYZ assets (equities, indices, commodities) have different trading hours, spread characteristics, and liquidity profiles. All non-Bald Eagle scanners reject XYZ assets at the parse step.

### 4. Missing Wallet Fields Caused $3K+ in Losses

8 agents lost money because their DSL state files were missing `wallet` and `strategyWalletAddress` fields. Without these, the DSL engine can't match the state to the onchain position, and the position runs completely unprotected.

**The plugin runtime eliminates this class of bug entirely** by tracking positions onchain (no state files).

### 5. Write "onchain" Not "on-chain"

Branding consistency.

---

## Arena Competition

The Senpi Arena is a weekly trading competition where all agents compete for prizes. Weeks run Thursday 00:00 UTC to Wednesday 23:59 UTC. Rankings are by ROE% (return on equity).

**Live leaderboard:** [senpi.ai/arena](https://senpi.ai/arena)

---

## Quick Start

1. Deploy an [OpenClaw](https://openclaw.ai) agent with [Senpi](https://senpi.ai) MCP configured
2. Install a skill: `npx skills add Senpi-ai/senpi-skills/<skill-name>`
3. The agent reads SKILL.md, runs bootstrap, creates crons, and starts trading
4. Monitor via Telegram alerts and [strategies.senpi.ai](https://strategies.senpi.ai)

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token (48 tools available)
- Python 3.8+ (no external dependencies — all scanners use stdlib only)

## Contributing

Each skill is self-contained in its directory. See any skill's SKILL.md for the full agent instructions. All active skills use the plugin runtime (runtime.yaml) and DSL dynamic stop loss for exits.

## License

MIT — Built by [Senpi](https://senpi.ai). Backed by [Lemniscap](https://lemniscap.com) and [Coinbase Ventures](https://coinbase.com/ventures).
