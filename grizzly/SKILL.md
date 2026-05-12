---
name: grizzly-strategy
description: >-
  GRIZZLY v7.0.0 — BTC alpha hunter, senpi_runtime_helpers migration.
  Plumbing-only flip from openclaw-CLI subprocess + mcporter subprocess
  to in-process SenpiClient (direct HTTPS for MCP, direct HTTP POST to
  runtime /signals, long-lived producer_daemon). Thesis preserved
  verbatim from v6.0.0: BTC single-asset trend hunter, six-gate entry
  validation including v5.5 macro V-recovery gate (block fades within
  1.25% of 24h extreme), RSI hard gates (70/30 — BTC-tuned),
  multi-factor scoring (~17 max), conviction-scaled leverage (7x
  default / 10x conviction / 10x apex), MIN_SCORE 12, FP-001 quiet
  hours, FP-003 requireAllConfirmations gate. Trades WITH SM consensus
  and trend, NOT contrarian. Family member alongside Kodiak (SOL),
  Wolverine (HYPE), Polar (ETH).
license: MIT
metadata:
  author: jason-goldberg
  version: "7.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
---

# 🐻 GRIZZLY v7.0.0 — BTC Alpha Hunter (senpi_runtime_helpers)

Trend-continuation on BTC, v2-runtime-native. Same Kodiak-family
architecture as Wolverine / Polar / Kodiak. Producer emits signals,
runtime executes via LLM gate, DSL manages exits via maker-first
FEE_OPTIMIZED_LIMIT.

**Trades WITH the trend and WITH the smart money.** Not contrarian.
SM-opposes is a hard block.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/grizzly-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION (BTC only)
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters without family-wide review
### RULE 6: HARD_STOP circuit breaker triggers at -25% drawdown

### RULE 7 (FP-002): User-conversation Claude sessions MUST NOT trade

**Hard rule, not a heuristic.** When responding to a user message (Telegram
ping, status check, "tell me about your trades", etc.), the Claude Code
session MUST NOT call any of:

- `create_position`
- `close_position`
- `edit_position`
- `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
- `cancel_order`
- `strategy_close` / `strategy_close_positions`

These tools are reserved for the **producer cron** (grizzly-producer.py)
and the **DSL ratchet engine**. The cron is the only entry path. The DSL
is the only exit path. User-conversation sessions are read-only.

If the user asks an action-implying question ("anything close to
triggering?", "should I take this trade?"), respond by reading current
state — DO NOT execute. The producer will fire on its next tick if a
real signal is there. Pattern observed across the fleet: agents have
been opening positions immediately after user pings because their
conversation sessions had MCP write access. This rule closes that gap.

### RULE 8 (FP-001): Quiet hours for low-liquidity windows

Producer skips emission during 00:00-04:00 UTC by default. Apex setups
(score >= `quietHoursApexBypassScore`, default 14) bypass — high-conviction
BTC trends can fire any hour; routine sub-apex entries wait until 04:00 UTC.

Configurable via `quietHoursStartUtc`, `quietHoursEndUtc`,
`quietHoursApexBypassScore` in `grizzly-config.json`. Set start == end
to disable.

Rationale: fleet-wide pattern of 00:00 UTC pile-ins after daily-cap reset.
For Grizzly specifically, BTC overnight liquidity is thinnest in this
window and entry slippage can eat a low-conviction edge.

---

## Thesis

**Pure trend continuation on BTC, never counter-trend.**

From Kodiak's lifetime top-3 SOL winners (+$133 / +$87 / +$78):

> "The absolute highest predictor of a massive directional swing is when
> the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a
> single direction, AND the Smart Money leaderboard is heavily lopsided
> (>65% directional consensus) in that exact same direction."

Grizzly v5.0 applies that exact thesis to BTC. BTC is slower and heavier
than SOL, so thresholds are tightened — but the pattern is identical.

---

## v5.0 — COMPLETE REWRITE

v4.x was a contrarian SM-fader. The April 10 inversion test read "if we
flipped direction we'd win 81.8%" as evidence that SM was broken on BTC.
The correct read was: the scanner was late, entering *after* the move
exhausted. Flipping direction made those same late entries profitable
temporarily, but it also set Grizzly up to fight every real trend.

v4.x lost $245. Grizzly was the only family member running a direction
opposite to the family intent.

v5.0 returns to the family pattern:

- **REMOVED:** CONTRARIAN_FLIP — Grizzly now trades WITH SM consensus
- **REMOVED:** CONTRARIAN_EXHAUSTION_GATE — gating contrarian behavior
- **ADDED:** 3-mode state machine (HUNTING / RIDING / STALKING)
- **ADDED:** 4-timeframe alignment (5m + 15m + 1h + 4h)
- **ADDED:** 4TF_aligned bonus scoring
- **ADDED:** SM strongly tilted bonus (>65% consensus)
- **ADDED:** SM opposes HARD BLOCK
- **ADDED:** Move-exhaustion + tiring penalties
- **ADDED:** RSI filter (70/30 BTC-tuned)
- **ADDED:** Volume trend + OI-proxy growth signal
- **ADDED:** STALKING mode — reload on dip after DSL exit
- **KEPT:** Dynamic daily cap (P&L-aware circuit breaker)
- **KEPT:** has_resting_orders with auto-cancel stale
- **KEPT:** 10x MAX_LEVERAGE cap

---

## Hard Gates (all must pass)

1. **4h trend structure ≠ NEUTRAL** (BULLISH or BEARISH, >60% higher-lows or lower-highs)
2. **1h trend agrees** with 4h direction
3. **15m momentum confirms** direction (>0.05% in direction)
4. **SM opposes = HARD BLOCK** (leaderboard_get_markets)
5. **RSI filter** — LONG requires RSI ≤ 70, SHORT requires RSI ≥ 30

---

## BTC-Specific Tuning vs Kodiak (SOL)

BTC moves ~0.6% in 4h; SOL moves 2–4%. Thresholds tightened ~3x:

| Parameter | Kodiak (SOL) | Grizzly (BTC) | Why |
|---|---|---|---|
| min_mom_15m | 0.10% | 0.05% | BTC slower |
| rsi_max_long | 74 | 70 | BTC rarely pushes 74 |
| rsi_min_short | 26 | 30 | BTC rarely pushes 26 |
| move_exhaustion | 4.0% | 2.5% | BTC exhaustion at lower % |
| move_tiring | 2.5% | 1.5% | |
| 4h_strong | 4.0% | 2.0% | |
| vol_ratio_min | 1.2 | 1.1 | BTC volume always deep |
| funding_extreme | 0.005 | 0.003 | BTC lower-magnitude |

MIN_SCORE = 10 (family standard). Dynamic daily cap same as family.

---

## Scoring (max ~18 pts, MIN_SCORE = 10)

| Signal | Points |
|---|---:|
| 4h trend structure | +3 |
| 1h trend agrees | +2 |
| 15m momentum strength | +1 |
| 4TF_aligned (5m agrees) | +1 |
| SM aligned | +2 |
| SM strongly tilted (>65%) | +1 |
| 15m velocity fresh (>0.5) | +1 |
| 15M_STALE_PENALTY (cc ≤ 0) | -3 |
| Funding pays direction | +2 |
| Funding crowded against | -1 |
| Volume ratio ≥ 1.1x | +1 |
| Volume rising >15% | +1 |
| OI growing >10% | +1 |
| RSI room | +1 |
| 4h strong (>2%) | +1 |
| MOVE_EXHAUSTION (>2.5%) | -2 |
| MOVE_TIRING (>1.5%) | -1 |

---

## Position Sizing

| Score | Leverage | Margin % |
|---|---:|---:|
| 10-11 | 10x (clamped to HL max) | 30% |
| 12-13 | 10x | 37.5% |
| **14+** | **10x** | **45%** |

Leverage auto-clamped to BTC's Hyperliquid max via `strategy_get_asset_trading_limits`.

---

## Exit (DSL) — BTC-Tuned per RatchetStop Timing Guide

| Mechanism | Value | Why |
|---|---|---|
| hard_timeout | 360 min (6h) | BTC trends take time |
| weak_peak_cut | 120 min @ 2% min | BTC peaks consolidate slowly |
| dead_weight_cut | 90 min | BTC can sit quietly within a trend |
| Phase 1 max_loss | 15% | Same as Kodiak |
| Phase 1 retrace | 8% | |
| Phase 2 tier 1 | +5% → 25% lock | First profit lock |
| Phase 2 tier 2 | +10% → 45% | |
| Phase 2 tier 3 | +15% → 65% | |
| Phase 2 tier 4 | +20% → 80% | |
| Phase 2 tier 5 | +30% → 90% | Apex lock |
| Phase 2 tier 6 | +50% → 94% | Monster winner trail |

---

## Lifecycle (v6.0 — runtime-owned)

```
producer.py  → [all gates pass, score >= MIN_SCORE] → client.push_signal()
runtime LLM  → [pass-through gate, min_confidence 7] → OPEN_POSITION
runtime DSL  → [tracks position, ratchets through Phase 2 ladder] → exit
runtime risk → [enforces daily caps + drawdown halt + cooldowns] → block next entry
```

**Producer (HUNTING):** scanner evaluates BTC every 3 minutes. All hard
gates must pass. NO Python state — emits a signal and exits.

**Runtime (RIDING):** position_tracker scanner detects open via
`POSITION_OPENED`, fires `ON_POSITION_OPENED`, DSL engine starts
trailing. Producer's held-asset check prevents duplicate emission while
position is open.

**Runtime (POST-EXIT):** when DSL closes, `per_asset_cooldown_minutes`
gate blocks new BTC entries for 60 min. v5.x's STALKING mode and
reload-on-dip logic are dropped — DSL owns position lifecycle and the
producer does NOT add to existing positions.

**v5.x Python state machine** (HUNTING/RIDING/STALKING) is gone. Runtime
owns all state. No Python state files to corrupt.

---

## Family Relationship

All four single-asset specialists run the same architecture:

| Agent | Asset | MIN_SCORE | DSL Profile |
|---|---|---:|---|
| Kodiak | SOL | 10 | Mid-beta (240/60/90min) |
| Wolverine | HYPE | 9 | High-beta, time-cuts disabled (v4.0+) |
| Polar | ETH | 10 | Mid-beta (240/60/90min) |
| **Grizzly** | **BTC** | **12** | **Low-beta, time-cuts disabled (v6.0)** |

Bug fixes and signal improvements propagate across the family. If one
member discovers an edge, all four get it. If one has a bleed, all four
get audited.

---

## Install

**Prerequisite:** plugin must be on `runtime-phase-2` build. The
senpi-trading-runtime skill must also be installed on this host — it
ships the Python Producer SDK (`senpi_runtime_helpers`) that this
producer imports. See README install steps.

```bash
mkdir -p /data/workspace/skills/grizzly-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml -o /data/workspace/skills/grizzly-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/SKILL.md -o /data/workspace/skills/grizzly-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/config/grizzly-config.json -o /data/workspace/skills/grizzly-strategy/config/grizzly-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-producer.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py -o /data/workspace/skills/grizzly-strategy/scripts/grizzly_config.py
```

## Configure

Edit `config/grizzly-config.json` with `wallet`, `startingBudget`,
optional `minScore` / `quietHours*` overrides. Runtime resolves
`${WALLET_ADDRESS}`, `${TELEGRAM_CHAT_ID}`, `${GRIZZLY_DECISION_MODEL}`
from environment.

## Install runtime + launch producer daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/grizzly-strategy/runtime.yaml
openclaw senpi runtime list

SENPI_AUTH_TOKEN=<your-token> \
GRIZZLY_WALLET=0x... \
  nohup python3 -u /data/workspace/skills/grizzly-strategy/scripts/grizzly-producer.py \
  > /tmp/grizzly-producer.log 2>&1 &

senpi-helpers list                                       # daemon visible with recent LAST_TICK
senpi-helpers health grizzly-<wallet-suffix>             # exit 0 = healthy
```

`<wallet-suffix>` = first 8 hex chars after `0x` of the strategy wallet (matches the daemon's `LOCK_NAME`).

---

## Changelog

### v6.0.0 (2026-05-06) — v2-RUNTIME-NATIVE REWRITE
- **Architecture:** v1 full-agency Python scanner (1073 lines) → v2 producer + LLM gate + native risk + DSL maker exits
- **Producer:** `grizzly-producer.py` emits via `SenpiClient.push_signal()` (direct HTTP POST). NO execution code.
- **LLM gate:** pass-through (min_confidence 7); honors producer signals unless structurally broken
- **risk.guard_rails:** declarative daily caps / drawdown halt / consecutive losses / per-asset cooldown — replaces Python `get_dynamic_daily_cap`, `set_cooldown`, etc.
- **DSL:** `FEE_OPTIMIZED_LIMIT` on entries AND exits (~0.020-0.030% fee recovery per maker close)
- **Trade chain DB:** `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` per trade
- **DROPPED:** 3-mode state machine (HUNTING/RIDING/STALKING), `evaluate_reload`, `has_resting_orders`, `create_position`, `get_safe_leverage`, `get_dynamic_daily_cap`
- **PRESERVED VERBATIM:** all 6 entry gates (4h trend != NEUTRAL, 4h structure ≥ 0.75, 1h matches 4h, 15m momentum, base-tech floor, v5.5 macro V-recovery), SM hard block, RSI 70/30 hard gates, multi-factor scoring (~17 max), MIN_SCORE 12, BTC-tuned thresholds, leverage tiers, DSL preset
- **Observability fix:** Vulture v3.1.1 forensic-logging pattern — `INGEST_FAILED` / `INGEST_REJECTED` / `INGEST_EXCEPTION` all log rc + stderr + stdout + payload

### v5.0 (2026-04-16) — COMPLETE REWRITE
- Direction logic flipped back from v4.x contrarian to trend-following
- Ported Kodiak's full 3-mode state machine (HUNTING/RIDING/STALKING)
- Added 4-timeframe alignment with 4TF_aligned bonus scoring
- Added SM-opposes HARD BLOCK
- Added move-exhaustion + tiring penalties
- Added RSI filter (70/30 BTC-tuned)
- Added volume trend + OI-proxy growth signals
- Added STALKING reload-on-dip mode
- DSL profile retuned per RatchetStop timing guide for BTC (low-beta)
- MIN_SCORE raised 8 → 10 (family standard)
- Margin tiers rebuilt with conviction scaling (30/37.5/45%)
- Inner-order success validation in execute_entry
- get_safe_leverage() clamps to BTC's HL max

### v4.x (deprecated) — contrarian fader
- Direction-inverted from family intent
- Lost $245 fighting real trends
- Replaced by v5.0

### v3.2 — trend-follower (pre-contrarian)

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
