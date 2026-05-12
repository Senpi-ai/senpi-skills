---
name: roach-strategy
description: >-
  ROACH v3.0.0 — Striker-only scanner, senpi_runtime_helpers migration.
  Plumbing-only flip from openclaw-CLI subprocess + mcporter subprocess
  to in-process SenpiClient. Thesis preserved verbatim from v2.1.0:
  Stalker permanently disabled, only trades violent FIRST_JUMP /
  IMMEDIATE_MOVER explosions backed by 1.5x volume, 1h price alignment,
  4h trend agreement. Long stretches of silence are EXPECTED — the
  patience is the edge.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0.0"
  platform: senpi
  exchange: hyperliquid
  config_source: striker-only-experiment
  requires:
    - senpi-trading-runtime>=2.0.0
    - senpi-runtime-helpers
---

# 🪳 ROACH v3.0.0 — Striker Only. senpi_runtime_helpers.

Cockroaches survive anything. ROACH survives by not trading when there's no explosion.

---

## What changed in v2.0

**Architecture (not thesis):**

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner, agent calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Exit | DSL via runtime.yaml (good) | Same DSL preset, but with **FEE_OPTIMIZED_LIMIT** order type — maker-first with 60s window, taker fallback |
| Risk gates | Agent enforces in scanner code | Declarative via `runtime.risk.guard_rails` |
| Per-asset cooldown | Producer state file | Runtime + producer (defense in depth) |

**Why v2:** v1 used MARKET orders for every exit, paying ~3 bp/exit in HL taker fees. Recent 25-trade sample: $46 in HL fees, 100% taker. v2's `FEE_OPTIMIZED_LIMIT` with `ensure_execution_as_taker: true` and 60s timeout prefers maker fill but accepts taker fallback when the position must close. Expected recovery: 50-70% of HL exit fees.

**Thesis preserved from v1.2:** Stalker disabled. Striker only. FIRST_JUMP from #25+, rank jump >= 15 OR contribVelocity >= 15, score >= 10 with 4+ reasons, 1.5x volume, 1h price aligned, 4h trend aligned, XYZ banned.

---

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | v2 runtime spec (scanners, actions, exit DSL, guard_rails) |
| `scripts/roach-producer.py` | Cron-driven producer — emits Striker signals to runtime |
| `scripts/roach_config.py` | Shared MCP helper + atomic state I/O |
| `config/roach-config.json` | Operator-tunable defaults (informational; producer constants WIN) |

---

## Producer behavior

Runs every 90s via cron. On each tick:

1. **Reentrancy guard:** acquires `state/<wallet-hash>/producer.lock`. If a prior run hasn't released it (cron faster than MCP latency), this run skips cleanly.
2. **Fetch markets:** `leaderboard_get_markets` (top 50, XYZ filtered out at parse time).
3. **Detect striker signals:** rank jumps, contribVelocity, contribution explosions vs scan history.
4. **Apply hard gates:** 4h trend aligned, cc_15m >= 0.5, 1h price aligned >= 0.1%, volume >= 1.5x, score >= 10, 4+ reasons, asset not in 120min cooldown.
5. **Emit signals** via `openclaw senpi external-scanner ingest`.
6. **Persist scan history + cooldown state** under `state/<wallet-hash>/`.

NO execution code. NO position-tracking. NO DSL state. The runtime owns all of that.

---

## Entry flow

```
Producer cron (90s)
  ↓ Striker signal detected
  ↓ external-scanner ingest --scanner roach_signals
Runtime
  ↓ Schema-validates fields against runtime.yaml
  ↓ LLM gate (decision_model = ${ROACH_DECISION_MODEL})
  ↓ Pass-through unless malformed (producer already filtered everything else)
  ↓ OPEN_POSITION via FEE_OPTIMIZED_LIMIT (maker-first, 60s, taker fallback)
DSL (runtime-managed)
  ↓ Phase 1 max_loss 18% / 3-breach
  ↓ Phase 2 tiers (7/40, 12/55, 15/75, 20/85)
  ↓ hard_timeout 120min, weak_peak 45min, dead_weight 25min
  ↓ Exit via FEE_OPTIMIZED_LIMIT (maker-first, 60s, taker fallback)
```

---

## Required env vars

The runtime YAML uses these substitutions:

| Var | Purpose |
|---|---|
| `${WALLET_ADDRESS}` | Strategy wallet address |
| `${TELEGRAM_CHAT_ID}` | Telegram chat ID for notifications |
| `${ROACH_DECISION_MODEL}` | Bare model name for LLM gate (e.g. `gemini-3.1-pro-preview`, `claude-sonnet-4-20250514`). NO provider prefix — OpenClaw double-prefixes and rejects. |

The producer reads:

| Var | Purpose | Default |
|---|---|---|
| `ROACH_WALLET` | Wallet (must match runtime YAML's wallet). **Agent-specific by design — do NOT use generic `STRATEGY_ADDRESS`.** Per Turbine v2.0.9 contamination fix: a shared env var is a fleet-wide vector if multiple agents share an install. | — (required; producer fails loud) |
| `OPENCLAW_BIN` | CLI binary name | `openclaw` |
| `EXTERNAL_SCANNER_NAME` | Scanner ID | `roach_signals` |
| `ROACH_LEVERAGE` | Leverage to assert in signal payload | `7` |
| `ROACH_MARGIN_USD` | Margin per slot to assert in signal payload | `250` |

---

## Producer install (on OpenClaw host)

Source path in the senpi-skills repo: `roach/`. Install destination on the
OpenClaw host: `/data/workspace/skills/roach-strategy/`. The two names
differ — repo uses `roach`, install dir uses the SKILL.md `name` field
(`roach-strategy`).

```bash
# 1. Pull the skill files (source: senpi-skills/main/roach/)
mkdir -p /data/workspace/skills/roach-strategy/scripts
mkdir -p /data/workspace/skills/roach-strategy/config

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/runtime.yaml \
  -o /data/workspace/skills/roach-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/scripts/roach-producer.py \
  -o /data/workspace/skills/roach-strategy/scripts/roach-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/scripts/roach_config.py \
  -o /data/workspace/skills/roach-strategy/scripts/roach_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/config/roach-config.json \
  -o /data/workspace/skills/roach-strategy/config/roach-config.json

# Remove the v1 scanner if it's still there from a prior install
rm -f /data/workspace/skills/roach-strategy/scripts/roach-scanner.py

# 2. Install the runtime
# ROACH_DECISION_MODEL is REQUIRED — pick any model supported by the runtime's
# model registry. Examples: gemini-3.1-pro-preview, claude-sonnet-4-20250514.
# Pass BARE model name only — NO provider prefix (OpenClaw double-prefixes).
WALLET_ADDRESS=0x... \
TELEGRAM_CHAT_ID=... \
ROACH_DECISION_MODEL=gemini-3.1-pro-preview \
  openclaw senpi runtime create --path /data/workspace/skills/roach-strategy/runtime.yaml

# 3. Schedule the producer (~90s cadence)
# ROACH_WALLET (NOT generic STRATEGY_ADDRESS) is required by the producer.
# Producer fails loud at startup if not set — misconfig is visible immediately.
#
# RECOMMENDED PATTERN: cron-as-Claude-turn with NO_REPLY filter.
# Each cron tick spins up an isolated session, runs the producer, and
# replies to TG ONLY if there's a non-zero event (signal pushed, error,
# unexpected state). On the silence-is-correct ticks (most of them for
# Roach), the agent stays quiet — zero TG noise, zero log file growth.
# Cron run records are still accessible via `openclaw cron runs --id <id>`.
openclaw cron add \
  --name "roach-v2-producer" \
  --cron "*/2 * * * *" \
  --session isolated \
  --wake now \
  --message $'export ROACH_WALLET=0x... && python3 /data/workspace/skills/roach-strategy/scripts/roach-producer.py\n\nCRITICAL INSTRUCTION: If the output shows \'candidates\': 0 and \'status\': \'ok\', you MUST reply EXACTLY with NO_REPLY to remain silent. Only report to the user if there is an error, a crash, or if signals are actually pushed.' \
  --no-deliver

# Note: openclaw cron resolution is 1 minute. The producer is designed for
# 90s cadence; */2 (every 2 min) is the closest cron-supported approximation.
# Some installs run faster via the underlying scheduler (~90s observed).
# The fcntl reentrancy guard handles any overlap safely.

# Alternative pattern: shell-redirect (cheaper, no Claude tokens per tick,
# but generates a log file that needs rotation). Use this if you prefer
# to grep raw producer JSON output:
#
#   --message "Run \`SENPI_API_KEY=<KEY> ROACH_WALLET=0x... python3 /data/workspace/skills/roach-strategy/scripts/roach-producer.py >> /var/log/openclaw/roach-v2.log 2>&1\` and report success/failure in this log."

# 4. Verify
openclaw senpi runtime list                        # expect: roach-tracker v1.3.0
openclaw senpi status --runtime roach-tracker

# Cron-as-Claude-turn pattern — verify via OpenClaw's structured run log:
openclaw cron list --json                          # find the cron id
openclaw cron runs --id <cron-id> --limit 5        # last 5 tick records (ts, status, durationMs, nextRunAtMs)

# Inspect producer state (this is the same regardless of cron pattern):
ls -la /data/workspace/skills/roach-strategy/state/<wallet-hash>/
# Expect: producer.lock + scan-history.json with mtime in last few minutes.
# A fresh scan-history.json (mtime < 5 min) confirms the cron is firing.
```

---

## Risk envelope (declarative, runtime-enforced)

| Setting | Value |
|---|---|
| Slots (max concurrent positions) | 2 |
| Margin per slot | $250 |
| Default leverage | 7x |
| Daily loss halt | 10% |
| Drawdown halt | 25% |
| Max entries per day | 6 |
| Max consecutive losses | 3 |
| Post-loss cooldown | 30 min |
| Per-asset cooldown | 120 min |
| XYZ equities | Banned at producer scan level |

---

## Expected behavior

| Metric | Expected |
|---|---|
| Trades/day (chop) | 0-1 |
| Trades/day (trending) | 1-3 |
| Days with zero trades | Common and correct |
| Win rate | 50-70% target (Striker quality) |
| Avg fee per close | Materially lower than v1 (maker fills) |
| Total fees / volume | ~50-70% reduction vs v1 baseline |

**SILENCE IS CORRECT.** If Roach goes 24-48 hours without a trade, that means there were no FIRST_JUMP explosions worth taking. That's the experiment working, not a bug.

---

## What was removed in v2

Removed (responsibilities moved to runtime):
- `roach-scanner.py` (replaced by `roach-producer.py`)
- Trade counter / daily-loss tracking (runtime guard_rails)
- Bootstrap gate / config/bootstrap-complete.json (runtime install handles state)
- Asset cooldown bookkeeping in scanner (still in producer as defense-in-depth, but runtime is the authority)
- Dynamic slots based on PnL (runtime owns slots)
- All `create_position` calls (runtime owns execution)

Permanently disabled (decided in v1.0):
- Stalker mode and all accumulation detection
- XYZ equities (banned at scan level)

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
