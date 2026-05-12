---
name: scorpion-tracker
description: >-
  SCORPION v5.0.0 — Multi-Market Active Trader, senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess + mcporter
  subprocess to in-process SenpiClient. Plus security fix: v4.x read
  wallet from BANNED generic STRATEGY_ADDRESS env var; v5.0.0 uses
  per-agent SCORPION_WALLET (with backward-compat fallback + deprecation
  warning). Thesis preserved verbatim from v4.1.2: only Senpi predator
  hunting across both crypto AND XYZ DEX commodities/indices, SM
  concentration + 4H trend alignment scoring, asymmetric MIN_SCORE
  (crypto 11 / XYZ 9), v4.1.1 held-asset dedup, v4.1.2 post-close
  cooldown.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦂 SCORPION v5.0.0 — Multi-Market Active Trader (senpi_runtime_helpers)

The only predator that hunts across BOTH crypto AND XYZ DEX (commodities
and indices). v4.0 is the second fleet agent on senpi-trading-runtime
v2 (after Jackal v2).

## What changed from v3.2

v3.2 logged 43 fills / 18h / -$79.84 in Arena Week 5 despite
`MAX_DAILY_ENTRIES=3`. Root cause: the scalp-reentry bypass path and
Python trade counter leaked. v4.0 deletes all that bookkeeping.

| Layer | v3.2 | v4.0 |
|---|---|---|
| Scanner size | 549 lines, full agency | 280-line pure producer |
| Entry decision | Hardcoded scoring thresholds | LLM `decision_prompt` with `min_confidence: 7` |
| Daily cap enforcement | Python state file (silently leaked) | `risk.guard_rails.max_entries_per_day: 5` |
| Per-asset cooldown | Python (with scalp bypass) | `per_asset_cooldown_minutes: 120` (authoritative) |
| Scalp re-entry | Special bypass logic | Removed; runtime owns cooldowns |
| DSL exit order type | Market (taker) | **FEE_OPTIMIZED_LIMIT** (maker-preferred) |
| Phase-2 hard_timeout behavior | Fires regardless | Fires regardless (per Daniel's test) — outer-bound protection, not phase-gated. 12h is chosen so it doesn't truncate legitimate winners. |

## Architecture

```
┌──────────────────────────────┐
│ scorpion-producer.py (60s)   │  ── 1. scan leaderboard_get_markets
│                              │  ── 2. score SM+4TF candidates (MIN_SCORE 9)
│                              │  ── 3. enrich w/ BTC macro + funding +
│                              │         current positions
│                              │  ── 4. push via SenpiClient.push_signal
│                              │         (direct HTTP POST)
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ senpi-trading-runtime        │
│  scorpion_signals scanner    │  ── typed signal payload
│  scorpion_entry action       │  ── LLM evaluates with 4TF+SM+BTC
│    decision_mode: llm        │      context
│    min_confidence: 7         │  ── executes if confidence >= 7
│  position_tracker            │  ── lifecycle → DSL
│  DSL exit engine             │  ── FEE_OPTIMIZED_LIMIT exits
│    12h hard_timeout          │      (maker first, taker fallback)
│    30m dead_weight           │
│    60m weak_peak @ 2%        │
│  risk.guard_rails            │  ── daily_loss 5%, max 5 entries,
│                              │      per_asset cooldown 120m,
│                              │      drawdown_halt 20%
└──────────────────────────────┘
```

## Universe (preserved from v3.2)

| Market | Assets |
|---|---|
| Crypto majors | BTC, ETH, SOL, HYPE |
| Crypto mid-caps | ZEC, LIT, GRASS, FARTCOIN, TAO, ONDO, SUI, ARB, WLD, DOGE, AVAX |
| XYZ commodities | CL (crude oil), BRENTOIL, GOLD |
| XYZ indices | SPX |

XYZ assets use `xyz:` prefix in the runtime (e.g. `xyz:GOLD`). Producer
emits the full prefix; runtime handles it natively.

## Signal scoring (preserved from v3.2)

- SM concentration (0-3 pts): DOMINANT_SM (≥15%), STRONG_SM (≥10%), SM_PRESENT (≥5%)
- 4H price alignment (0-3 pts): big_move, med_move, ALIGNED — thresholds differ crypto vs XYZ
- 15m SM velocity (0-2, penalty -1): BUILDING / FRESH / FADING
- 1H acceleration (0-1)
- Trader depth (0-1, ≥50t)
- 4H contribution shift (0-1, ≥5%)

MIN_SCORE gate at 9. LLM further filters at min_confidence 7. Runtime
enforces daily cap at 5.

## LLM gate expectations

- Typical signal pass rate at MIN_SCORE=9: 5-15 signals/day depending on market
- LLM execute rate at min_confidence 7: ~30-40%
- Net entries/day after all gates: 1-4 (capped at 5)
- Large drop from v3.2's 43/18h by design — over-trading was the #1 killer

## Risk guard rails (runtime-enforced)

```yaml
risk:
  guard_rails:
    daily_loss_limit_pct: 5
    max_entries_per_day: 5
    max_consecutive_losses: 3
    cooldown_minutes: 90
    drawdown_halt_pct: 20
    per_asset_cooldown_minutes: 120
```

All enforced before every entry. No Python counters to drift.

## DSL (short-hold profile preserved; maker exits added)

| Control | Value |
|---|---|
| exit.order_type | **FEE_OPTIMIZED_LIMIT** ← v4.0 key win |
| execution_timeout_seconds | 15 (maker try window) |
| hard_timeout | 12h (forced rotation ceiling) |
| weak_peak_cut | 60 min @ 2% min_value |
| dead_weight_cut | 30 min |
| Phase 1 max_loss | 15% |
| Phase 1 retrace | 6% ROE |
| Phase 1 breaches req | 2 ticks |
| Phase 2 tiers | 5/30, 10/50, 15/70, 25/85, 40/90, 80/94 |

## Install (on OpenClaw host)

```bash
# 1. Pull the skill
mkdir -p /data/workspace/skills/scorpion-tracker/{config,scripts,state}
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/scorpion/runtime.yaml \
  -o /data/workspace/skills/scorpion-tracker/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/scorpion/scripts/scorpion-producer.py \
  -o /data/workspace/skills/scorpion-tracker/scripts/scorpion-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/scorpion/scripts/scorpion_config.py \
  -o /data/workspace/skills/scorpion-tracker/scripts/scorpion_config.py

# 2. Install the runtime
# SCORPION_DECISION_MODEL is REQUIRED — pick any model supported by the
# runtime's model registry. Examples: gemini-2.5-pro,
# claude-sonnet-4-20250514, etc. There is no default.
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
SCORPION_DECISION_MODEL=gemini-2.5-pro \
  openclaw senpi runtime create --path /data/workspace/skills/scorpion-tracker/runtime.yaml

# 3. Launch the producer daemon (60s tick).
SENPI_AUTH_TOKEN=<your-token> \
SCORPION_WALLET=0x... \
  nohup python3 -u /data/workspace/skills/scorpion-tracker/scripts/scorpion-producer.py \
  > /tmp/scorpion-producer.log 2>&1 &

# 4. Verify
openclaw senpi runtime list | grep scorpion
openclaw senpi status --runtime scorpion-tracker
senpi-helpers list                                          # daemon visible with recent LAST_TICK
senpi-helpers health scorpion-<wallet-suffix>               # exit 0 = healthy
senpi-helpers stats scorpion-<wallet-suffix> --hours 1      # signals posted + error histogram
```

## Verify the LLM gate is doing work

```bash
# All decisions (LLM reasoning JSON per candidate)
openclaw senpi action decisions scorpion_entry

# Only executed entries
openclaw senpi action history scorpion_entry
```

A healthy Scorpion v4 shows:
- Producer runs every 60s with `_scorpion_producer_version: "4.0"`
- 5-15 candidates detected per run during market activity
- LLM decisions with ~30-40% at `execute: true`
- Runtime ultimately executes ≤ 5 entries per UTC day

## Cost & latency

- Producer: 1 MCP call per run × 60s cadence = ~1,440 calls/day (market + enrich)
- LLM: 1 call per emitted signal × ~10/day = ~10 calls/day (after MIN_SCORE gate)
- Entry latency: ~2-3s from signal emit to order placement
- Fee savings from maker-preferred exits: ~$20/week at current turnover profile

## Changelog

### v4.0 (2026-04-23) — V2-RUNTIME-NATIVE REWRITE
- First multi-asset agent on the senpi-trading-runtime plugin
- Producer-only Python (280 lines vs v3.x's 549-line scanner)
- LLM `decision_mode: llm` replaces hardcoded score thresholds
- `risk.guard_rails` YAML replaces Python risk code — no more silent counter leaks
- FEE_OPTIMIZED_LIMIT on exits — maker-preferred for ~$20/week savings
- Runtime-managed DSL lifecycle

### v3.2 (2026-04-22) — over-trading fix (ineffective; pulled due to counter leak)
### v3.1 (2026-04-16) — scalp re-entry (pr0br000 copy)
### v3.0 (2026-04-12) — multi-market crypto + XYZ launch

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
