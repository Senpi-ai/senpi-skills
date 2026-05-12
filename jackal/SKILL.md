---
name: jackal-tracker
description: >-
  JACKAL v3.0.0 — The Smart Stalker (senpi_runtime_helpers migration).
  Plumbing-only port from v2.0: producer rewritten on
  `senpi_runtime_helpers` (in-process SenpiClient, no openclaw / mcporter
  subprocesses). NO thesis change. Architecture is identical to v2: pure
  producer + external_scanner + LLM-gated OPEN_POSITION action +
  declarative risk guardrails + runtime-managed DSL. Producer observes
  top-performing Senpi traders, detects new entries by pool members,
  enriches with consensus + TA + funding context, and lets an LLM
  (operator-selected via `$JACKAL_DECISION_MODEL`) evaluate each
  candidate before the runtime executes with our own DSL.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime@v2
---

# 🐺 JACKAL v2.0 — The Smart Stalker (v2-native)

The jackal watches bigger predators hunt. v2 splits the hunt into two
processes that can't step on each other:

- **The producer** runs on a cron, builds the trader pool, detects new
  entries, enriches with context, and pushes candidate signals. Nothing
  else. No execution, no DSL, no risk code.
- **The runtime** receives signals, gates each through an LLM decision
  prompt with `min_confidence: 7`. The model is required at deploy
  time via the `$JACKAL_DECISION_MODEL` env var (no default by design
  — operators pick whatever model they prefer from the runtime's
  registry: e.g. `gemini-2.5-pro`, `claude-sonnet-4-20250514`, etc.).
  The runtime executes approved entries, auto-manages DSL exits, and
  enforces declarative risk guardrails.

This is the fleet's first v2-runtime-native agent. Legacy v1 scanner +
pool code is preserved in `legacy-v1/` for reference.

---

## What changed from v1.1

| Layer | v1.1 | v2.0 |
|---|---|---|
| Scanner | 760-line Python scanner with execution + DSL + risk logic | 400-line producer that only emits signals |
| Entry decision | Hardcoded score thresholds in Python | LLM `decision_prompt` with `min_confidence: 7` (model via `actions.decision_model`) |
| DSL attach | Manual `ratchet_stop_add` call after every entry | Runtime manages via position_tracker lifecycle events |
| Risk gates | `MAX_POSITIONS`, `MAX_DAILY_ENTRIES`, cooldowns in code | `risk.guard_rails` YAML block |
| Pool maintenance | Two-tier (Watchlist + Active) with quality scoring | Single top-N pool from `discovery_get_top_traders` |
| Fresh-entry gate | Position age 15m–8h in scanner | `entry_age < 10 min` in producer (runtime handles cooldowns) |
| Signal shape | Internal dict passed to execute function | Typed JSON matching `config.fields` schema |
| Execution code | `create_position` + `ratchet_stop_add` in scanner | None — runtime handles |

---

## Why this thesis wants LLM gating

Jackal's thesis has subjective elements that don't compress into hard
scoring thresholds well:

- **Source quality is multidimensional.** Win rate, ROI, consistency,
  trader age, fee efficiency — a scoring formula flattens these into one
  number. An LLM can weigh them contextually (e.g., "win rate 60% is
  weaker for a 14-day trader than for a 90-day trader").
- **Consensus is nuanced.** 2 other pool members in the same trade is
  stronger if all 3 traders are independently high-quality, weaker if
  they share correlation (e.g., all subscribe to the same paid signal).
- **TA + fundamentals interact.** A LONG copy when 4h is BULLISH + funding
  is LONG_CROWDED is riskier than when funding is neutral. Hard scoring
  can't capture the interaction gracefully.
- **Regression risk is a feel call.** A trader on a 5-win streak might
  be peaking (regress incoming) or riding a genuine alpha period. An LLM
  can reason about which.

v2 moves these decisions from code to prompt — easier to iterate, and
decisions are auditable via `openclaw senpi action decisions`.

---

## Architecture

```
┌──────────────────────────────┐
│ jackal-producer.py (60s tick)│  ── 1. refresh pool daily via discovery_get_top_traders
│                              │  ── 2. diff positions against last-seen
│                              │  ── 3. enrich with consensus + TA + funding + BTC macro
│                              │  ── 4. push signal via
│                              │         client.push_signal() (direct HTTP POST)
└──────────────┬───────────────┘
               │
               ▼ POST /signals
┌──────────────────────────────┐
│ senpi-trading-runtime (v2)   │
│  jackal_signals scanner      │  ── receives typed signal payload
│  jackal_entry action         │  ── LLM (actions.decision_model) evaluates
│    decision_mode: llm        │      with decision_prompt
│    min_confidence: 7         │  ── executes if confidence >= 7
│  position_tracker            │  ── detects open → starts DSL
│  DSL exit engine             │  ── Phase 1/2, hard_timeout 72h,
│                              │      weak_peak 4h @ 3%, no dead_weight
│  risk.guard_rails            │  ── enforces daily_loss_limit,
│                              │      max_entries, consecutive_losses,
│                              │      drawdown_halt, per_asset_cooldown
└──────────────────────────────┘
```

---

## Pool selection (v2 simplified)

v1.1 maintained a two-tier pool (Watchlist + Active) with quality-score
promotion/demotion rules. v2 replaces that with a daily refresh from
`discovery_get_top_traders`:

- Filters: win_rate ≥ 0.50, roi_30d ≥ 10%, trader_age ≥ 14 days
- Sort: by composite quality score (win_rate, ROI, age, gain-to-pain)
- Size: top 25

The v1 complexity (velocity scoring, watchlist sustain windows,
demotion cooldowns) is replaced by the Senpi discovery API's own
ranking. If that ranking isn't good enough, we can reintroduce
trajectory scoring inside the producer — but starting simple and
validating the v2 plumbing first.

---

## Risk guard rails (runtime-enforced)

Declared in `runtime.yaml`, enforced before every entry:

```yaml
risk:
  guard_rails:
    daily_loss_limit_pct: 5
    max_entries_per_day: 4
    max_consecutive_losses: 3
    cooldown_minutes: 120
    drawdown_halt_pct: 20
    per_asset_cooldown_minutes: 240
```

No Python bookkeeping required. No risk of counter-reset bugs (like
Dire's daily-cap issue on 2026-04-23). Runtime is the source of truth.

---

## DSL (patience preserved)

Jackal's v1 patience profile survives:

| Control | v1.1 | v2.0 |
|---|---|---|
| hard_timeout | 72h | 72h |
| weak_peak_cut | 8h @ 3% | 4h @ 3% (tightened — v1 was too forgiving on fade) |
| dead_weight_cut | 4h | **disabled** (v2 runtime auto-disables once Phase 2 reached; single-decision thesis doesn't benefit from time-based loss cuts) |
| Phase 1 max_loss | 22% | 22% |
| Phase 2 tiers | 6 tiers | 6 tiers (same ladder) |

---

## Producer install (on OpenClaw host)

```bash
# 1. Pull the skill
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/runtime.yaml \
  -o /data/workspace/skills/jackal-tracker/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal-producer.py \
  -o /data/workspace/skills/jackal-tracker/scripts/jackal-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal_config.py \
  -o /data/workspace/skills/jackal-tracker/scripts/jackal_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jackal/scripts/jackal_state.py \
  -o /data/workspace/skills/jackal-tracker/scripts/jackal_state.py

# 2. Install the runtime
# JACKAL_DECISION_MODEL is REQUIRED — pick any model supported by the
# runtime's model registry. Examples: gemini-2.5-pro,
# claude-sonnet-4-20250514, etc. There is no default.
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
JACKAL_DECISION_MODEL=gemini-2.5-pro \
  openclaw senpi runtime create --path /data/workspace/skills/jackal-tracker/runtime.yaml

# 3. Launch the producer daemon (60s tick).
SENPI_AUTH_TOKEN=<your-token> \
JACKAL_WALLET=0x... \
  nohup python3 -u /data/workspace/skills/jackal-tracker/scripts/jackal-producer.py \
  > /tmp/jackal-producer.log 2>&1 &

# 4. Verify
openclaw senpi runtime list
openclaw senpi status --runtime jackal-tracker
senpi-helpers list                                       # daemon visible with recent LAST_TICK
senpi-helpers health jackal-<wallet-suffix>              # exit 0 = healthy
senpi-helpers stats jackal-<wallet-suffix> --hours 1     # signals posted + error histogram
```

## Verify the LLM gate is doing work

```bash
# All decisions (LLM reasoning JSON per candidate)
openclaw senpi action decisions jackal_entry

# Only executed entries
openclaw senpi action history jackal_entry
```

A healthy Jackal v2 will show LLM decisions regularly but only a
fraction (~20–40%) at `execute: true`. If every decision is
`execute: true` the threshold is too loose; if none are, the pool or
context is too strict.

---

## First-run behavior

1. Producer fetches top 25 traders (daily refresh) — first run takes
   ~30s as it populates the pool.
2. `last-seen.json` is seeded with current positions — no false signals
   from existing positions the producer has never seen before.
3. First real signals arrive when a pool member opens a new position.
4. LLM gates each one. Expect 2–5 signals/day at current pool size,
   with ~30% passing the confidence threshold.
5. Risk guardrails cap entries at 4/day regardless of signal volume.

---

## Cost & latency

- Producer: ~30 MCP calls/run × 60s cadence = ~30k calls/day. Mostly
  discovery_get_trader_state (batched) + market_get_asset_data.
- LLM: 1 Sonnet call per signal × ~5 signals/day = ~5 calls/day. Trivial.
- Entry latency: ~2-3s from signal emission to order placement (LLM
  call + runtime execution). Fine for Jackal's 4h+ hold profile.

---

## Changelog

### v3.0.0 (2026-05-08) — senpi_runtime_helpers migration (plumbing-only)
- Producer rewritten on `senpi_runtime_helpers.SenpiClient` — direct HTTPS
  to MCP, direct POST to runtime `/signals`. No more openclaw /
  mcporter subprocesses.
- `producer_daemon` long-lived loop manages the producer process
  (60s tick interval, 120s tick timeout).
- `JACKAL_WALLET` env var (replaces banned generic `STRATEGY_ADDRESS`,
  with deprecation fallback per v2.0.9 contamination rule).
- Producer reentrancy guard via fcntl lockfile at `state/producer.lock`.
- NO thesis change. NO decision-prompt change. NO DSL change.

### v2.0 (2026-04-23) — V2-RUNTIME-NATIVE REWRITE
- First fleet agent on senpi-trading-runtime v2
- Producer-only Python (400 lines vs v1's 760 line scanner)
- LLM `decision_mode: llm` replaces hardcoded score thresholds
- `risk.guard_rails` YAML replaces Python risk code
- Runtime-managed DSL (no ratchet_stop_add bookkeeping)
- Simplified top-N pool replaces two-tier Watchlist/Active architecture

### v1.1 (2026-04-22) — promotion threshold loosen (dormant, 0 trades)
### v1.0 (2026-04-17) — first ship

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
