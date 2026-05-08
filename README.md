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
     │   WALLET ($1K)   │ │   WALLET ($1K)   │ │   WALLET ($1K)   │
     │  Funded wallet   │ │  Funded wallet   │ │  Funded wallet   │
     │  Live on Hyperl. │ │  Live on Hyperl. │ │  Live on Hyperl. │
     └─────────────────┘ └─────────────────┘ └─────────────────┘
```

**Skills** contain the trading logic — a scanner that embodies a thesis about how to make money. Each skill is a self-contained directory with a scanner script, runtime.yaml, SKILL.md (agent instructions), and README.

**The Plugin Runtime** (runtime.yaml) manages position tracking and exits. The `position_tracker` polls onchain state every 10 seconds. The DSL engine evaluates exit conditions every 30 seconds. This eliminates an entire class of bugs from the Python DSL cron system (missing state files, wallet field injection, silent cron deaths).

**Strategy Wallets** are funded wallets on Hyperliquid. Each agent gets its own strategy wallet with isolated capital.

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

## The Fleet

Every agent is named in the format **`Codename — What it trades / What it does`**. Tags show the runtime version each agent runs on.

> `[Runtime 2.0]` — uses the v2 declarative runtime (phase2 tiers, fee-optimized exits, fcntl reentrancy guard, env-driven decision model)
> `[Runtime 1.0]` — uses the original Python scanner + cron model

### Runtime 2.0 native agents

Built v2-runtime-native from the start, or rewritten to be.

| Agent | Asset | Status | Thesis |
|---|---|---|---|
| **Grizzly — BTC Hunter** `[Runtime 2.0]` | BTC | Live | v2-runtime-native BTC alpha hunter. v6 rewrite of legacy 1073-line full-agency Python scanner. |
| **Kodiak — ETH Hunter** `[Runtime 2.0]` | ETH | Live | ETH single-asset lifecycle hunter. v6 entry-side maker-first execution. |
| **Polar — SOL Hunter** `[Runtime 2.0]` | SOL | Live | SOL single-asset lifecycle hunter. v4 MIN_SCORE calibration. |
| **Wolverine — HYPE Hunter** `[Runtime 2.0]` | HYPE | Live | HYPE single-asset hunter. v4 gate calibration. |
| **Spider — Patient Anchor Sniper** `[Runtime 2.0]` | Multi | Live | v3 single-leg patient anchor (rewrite of v2's 2-leg portfolio operator). |
| **Turbine — Volume Generation Engine** `[Runtime 2.0]` | Multi | Live | High-notional volume at minimal HL-fee cost. |
| **Otter — OI Velocity Hunter** `[Runtime 2.0]` | Multi | Live | Trades fresh leveraged positioning detected via Open Interest velocity. |
| **Cheetah — Multi-Asset Velocity Hunter** `[Runtime 2.0]` | Multi | Live | Velocity scanner with structured ai_reasoning telemetry. |
| **Jackal — Smart-Money Quality Scanner** `[Runtime 2.0]` | Multi | Live | v2 quality-score formula with maker-exit timeout. |
| **Scorpion — Multi-Asset Maker-Exit Predator** `[Runtime 2.0]` | Multi | Live | v4 with post-close cooldown backstop. |
| **Pangolin — Long-Tail Momentum Scanner** `[Runtime 2.0]` | Multi | Live | v2 DSL T0 ladder calibration for long-tail winners. |
| **Cobra — Arena Sprint Predator** `[Runtime 2.0]` | Single | Live | Trades the #1 SM-dominant asset with maximum conviction. |
| **Owl — Pure Contrarian + Macro Gate** `[Runtime 2.0]` | Multi | Live | v7 contrarian unwind with macro trend gate. |
| **Roach — Striker Pyramider** `[Runtime 2.0]` | Multi | Live | v2 DSL ratchet T0/T1 patch for early Striker engagement. |
| **Vulture — Arena-Winner Template Clone** `[Runtime 2.0]` | Multi | Live | Architecture cloned from pr0br000 (Arena weeks 1-3 winner). |
| **Kestrel — XYZ Macro Breakout Rider** `[Runtime 2.0]` | XYZ | Live | v2-runtime-native XYZ macro breakout rider. |
| **Sentinel — Quality-Trader Convergence Scanner** `[Runtime 2.0]` | Multi | Live | v2.2 finds assets where multiple ELITE/RELIABLE traders converge. |
| **Mamba — Range-Bound + Regime Protection** `[Runtime 2.0]` | Multi | Live | v2 range scanner with BTC regime filter. |
| **Viper — Range-Bound Liquidity Sniper** `[Runtime 2.0]` | Multi | Live | v2.2 with DSL loosened for winners. |
| **Dog — Multi-Asset Exhaustion Fader** `[Runtime 2.0]` | Multi | Live | v2.5 exhaustion gate calibration. |
| **Grizzly Horribilis — BTC Contrarian Sniper** `[Runtime 2.0]` | BTC | Live | v2.1 sniper recalibration. Fades exhausted SM consensus moves. |

### Runtime 1.0 agents

Legacy Python scanner + cron architecture. Migration to Runtime 2.0 in flight.

| Agent | Asset | Status | Thesis |
|---|---|---|---|
| **Dire — BRENTOIL XYZ Specialist** `[Runtime 1.0]` | XYZ | Live | First non-crypto Kodiak-family port. v1.7 fleet-wide DSL T0/T1 patch. |
| **Mantis — Cross-Asset Catchup Hunter** `[Runtime 1.0]` | Multi | Live | v5 Slipstream — uses `market_get_cross_asset_flows` for laggard alts after BTC moves. |
| **Bison — Conviction Holder** `[Runtime 1.0]` | Multi | Live | v2.1 asset whitelist + conviction floor + time-cuts disabled. |
| **Lemon — Degen Fader** `[Runtime 1.0]` | Multi | Live | v1.3 degen fader with macro gate + XYZ unban. |
| **Python — Patient Multi-Asset Scanner** `[Runtime 1.0]` | Multi | Live | v1.2 disable weak_peak_cut completes patience thesis fix. |
| **Condor — High-Conviction Momentum Hunter** `[Runtime 1.0]` | Multi | Live | v3.4 gate calibration. v3.2's tightening over-corrected; v3.4 restores signal. |
| **Hydra — Squeeze Detector** `[Runtime 1.0]` | Multi | Live | Funding extreme + SM against crowd + price starting to move. |
| **Komodo — Momentum Event Consensus** `[Runtime 1.0]` | Multi | Live | Momentum event detection with SM consensus. |
| **Orca — Gen-2 Striker (FIRST_JUMP)** `[Runtime 1.0]` | Multi | Live | FIRST_JUMP detection enhanced with momentum event quality confirmation. |
| **Vixen — Multi-Asset Trend Scanner** `[Runtime 1.0]` | Multi | Live | Standard trend scanner. |
| **Rhino — Momentum Pyramider** `[Runtime 1.0]` | Multi | Live | Scales into winners — patient DSL, wide phase 2. |
| **Barracuda — Funding Decay Collector** `[Runtime 1.0]` | Multi | Live | Funding collector holds for hours while collecting 8h payments. |
| **Shark — Position Tracker + Liquidation Cascade Scanner** `[Runtime 1.0]` | Multi | Live | Liquidation cascade + SM conviction scanner. |
| **Phoenix — Contribution Velocity Scanner** `[Runtime 1.0]` ⚠ | Multi | Live | v3.0 contribution velocity scanner. *Note: `version: 3.0.0` in runtime.yaml is the agent semver mislabeled in the schema field — see Engineering notes.* |
| **Raptor — Hot Streak Follower** `[Runtime 1.0]` ⚠ | Multi | Live | v3.3 entry-price discipline tightened. *Same `version:` field issue as Phoenix.* |
| **Jaguar — Hot-Streak Striker** `[Runtime 1.0]` ⚠ | Multi | Live | v3.7 runtime risk.guard_rails: cap losers, ride winners. *Same `version:` field issue as Phoenix.* |
| **Bald Eagle — XYZ Alpha Hunter** `[Runtime 1.0]` | XYZ | In-flight edits | All 54 XYZ assets. Spread gate >0.1%. *Currently being modified — README not refreshed in this audit.* |

### Utilities, scanners, and infra

Non-trading-agent components used by the fleet.

| Component | Purpose |
|---|---|
| `senpi-trading-runtime/` | The runtime plugin itself. Position tracker + DSL exit engine. |
| `dsl-dynamic-stop-loss/` | DSL plugin — Phase 1/Phase 2 trailing exit logic shared by every agent. |
| `opportunity-scanner/` | Standalone scanner skill. |
| `emerging-movers/` | Skill for detecting emerging momentum across the leaderboard. |
| `fee-optimizer/` | Maker-exit fee optimization helper skill. |
| `whale-index/` | Whale-tracking index helper. |
| `wolf-howl/`, `wolf-strategy/`, `tiger-strategy/` | Reference strategy snippets. |
| `shared/` | Shared infra (`hyperfeed_scoring.py`). |
| `_analysis/` | Internal fleet-analysis docs (experiment registry, fleet dossiers, hypotheses). |
| `workspace/` | Agent template scaffold (AGENTS, BOOTSTRAP, IDENTITY, MEMORY, SOUL, TOOLS). |
| `senpi-onboard/`, `senpi-getting-started-guide/`, `senpi-entrypoint/`, `autonomous-trading/` | Onboarding and reference skills. |

### Skipped / legacy

- `croc/`, `panther/`, `ghost-fox-strategy/`, `hawk/` — legacy or external. Not refreshed in this audit.

### Engineering notes from this audit

- **`version:` field convention is inconsistent across the fleet.** Per the runtime spec, `version:` in runtime.yaml is the *plugin schema major version* (currently `1` only), not the agent semver. Many agents use it for the agent semver (`1.5.0`, `2.0.0`, etc.) and three (`jaguar`, `phoenix`, `raptor`) use `3.x` which would fail validation under a strict schema check. They run because the runtime is permissive. Worth aligning convention or fixing the validator.
- **Runtime classification in this README is by feature markers** (`phase2.tiers`, `${AGENT_DECISION_MODEL}`, `FEE_OPTIMIZED_LIMIT`, fcntl reentrancy guard), not by the `version:` field — because the field is unreliable.
- **bald-eagle/** has uncommitted in-flight edits and was not refreshed in this audit.

---

## Lessons from the Fleet

### 1. Scanners Enter. DSL Exits. Never Both.

When scanners re-evaluate open positions and close them on "thesis invalidation," they chop winners before DSL can trail them. The one trade you let run is worth more than all other winners combined.

**All v2.0+ agents output NO_REPLY when a position is active.** DSL is the only exit mechanism.

### 2. Fees Are the Silent Killer

Two agents can have nearly identical profit but wildly different fee loads. The difference: FEE_OPTIMIZED_LIMIT orders and wider DSL that doesn't churn in and out.

**Whereever possible use FEE_OPTIMIZED_LIMIT and target a profit-to-fee ratio above 5:1.**

### 3. XYZ Equities Banned at Parse Level

XYZ assets (equities, indices, commodities) have different trading hours, spread characteristics, and liquidity profiles. All non-Bald Eagle scanners reject XYZ assets at the parse step.


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
