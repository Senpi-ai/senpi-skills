---
name: wolverine-strategy
description: >-
  WOLVERINE v5.0.0 — HYPE alpha hunter, senpi_runtime_helpers migration.
  Plumbing-only flip from openclaw-CLI subprocess + mcporter subprocess
  to in-process SenpiClient (direct HTTPS for MCP, direct HTTP POST to
  runtime /signals, long-lived producer_daemon). Thesis preserved
  verbatim from v4.2.0: HYPE single-asset hybrid, six-gate entry
  validation (4h trend, 4h strength ≥0.65, 1h-4h alignment, 15m
  momentum ≥0.15, base-tech floor, 4h magnitude ≥1.0%), SM hard-block
  on opposing direction, RSI hard gates (74/26), multi-factor scoring
  (~17 max), conviction-tiered leverage (3x standard / 5x apex),
  MIN_SCORE 9, FP-001 quiet hours.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.0.98
    - senpi_runtime_helpers
---

# 🦡 WOLVERINE v5.0.0 — HYPE Alpha Hunter (senpi_runtime_helpers)

**v3 → v4 architectural rewrite.** v3.x was a full-agency scanner. v4.0 flips to the standard senpi-trading-runtime v2 pattern: producer emits signals, runtime owns execution + state.

**What changed structurally:**
- `wolverine-producer.py` (NEW) replaces `wolverine-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits per-trade telemetry — chain DB visibility on Wolverine for the first time
- Python-state-crash class of bug (load_tc / set_cooldown / has_resting_orders) is structurally impossible in v4.0

**What's preserved from v3.0.3/v3.0.4 EXACTLY:**
- HYPE single-asset thesis
- **Six-gate entry validation:**
  1. 4h trend != NEUTRAL
  2. 4h structural strength ≥ 0.75 (Kodiak v5.1 pattern)
  3. 1h direction matches 4h
  4. 15m momentum ≥ MIN_MOM_15M (0.15) in direction
  5. Base-tech floor (strong_15m OR aligned_5m)
  6. **4h MAGNITUDE ≥ 1.5%** — the v3.0.3 fix that rejects dead-flat chop. Wolverine's own 2026-04-23 self-diagnostic: all 6 Week 5 trades died because trend_strength_4h is structural (lower-highs count) and passes even when 4h price magnitude is nearly flat.
- SM HARD BLOCK if direction opposes
- RSI hard gates (74 LONG / 26 SHORT)
- Multi-factor scoring (~17 max points): base-tech + SM concentration + SM velocity + funding alignment + funding regime + funding persistence + volume + OI velocity + BTC correlation + RSI room + 4h momentum bonus + move-exhaustion penalty
- MIN_SCORE = 9 (config-overridable)
- Conviction-tiered leverage: 5x apex (score ≥11) / 3x standard (≥9)
- DSL preset preserved: time-cuts ALL DISABLED, Phase 1 max_loss 20% / retrace 8 / 3 breaches, Phase 2 ladder (10/15, 20/35, 35/55, 55/70, 80/85)

**v3.0.1/3.0.2/3.0.4 v1-DSL fixes preserved:**
- `dead_weight_cut`: DISABLED (single-asset has no rotation cost)
- `hard_timeout`: DISABLED (v1 DSL fired this in Phase 2 incorrectly)
- `weak_peak_cut`: DISABLED (v3.0.4 — completes time-cuts sweep)
- All exits now 100% price-action

---

## ⛔ Hard Rules (Fleet Patches)

### RULE FP-002: User-conversation Claude sessions MUST NOT trade

**Hard rule, not a heuristic.** When responding to a user message, the Claude Code session MUST NOT call any of:

- `create_position`
- `close_position`
- `edit_position`
- `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
- `cancel_order`
- `strategy_close` / `strategy_close_positions`

These tools are reserved for the **producer cron** (wolverine-producer.py) and the **DSL ratchet engine**. The cron is the only entry path. The DSL is the only exit path. User-conversation sessions are read-only.

### RULE FP-001: Quiet hours for low-liquidity windows

Producer skips emission during 00:00-04:00 UTC by default. Apex setups (score >= `quietHours.apexBypassScore`, default 11) bypass.

Configurable via `quietHours.{startUtc,endUtc,apexBypassScore}` in `wolverine-config.json`. Set `startUtc == endUtc` to disable.

---

# 🦡 WOLVERINE — Original Thesis

Single asset. One thesis. Smart money commits, Wolverine pounces, DSL trails the trend.

## What it does

Wolverine scans HYPE on Hyperliquid every 3 minutes for a confluence of momentum signals:

- **Smart Money consensus** — pct of top traders gain ≥ minimum, trader count above threshold
- **Velocity acceleration** — 15m and 1h contribution change positive AND building (15m > 1h)
- **Price confirmation** — 4H and 1H both moving in the signal direction
- **Volume confirmation** — bonus when volume spikes above 6h average
- **15m freshness gate** — penalizes stale signals (15m velocity < 0)

When the score reaches 8+, Wolverine fires a HYPE entry at conviction-scaled leverage (7x or 10x) with FEE_OPTIMIZED_LIMIT order type. Position management is delegated entirely to the DSL exit engine — Wolverine never closes a position itself, only opens.

## v2.3 — chop-hardened

On 2026-04-14 Wolverine v2.2 took 5 consecutive losing HYPE trades over ~3 hours of chop for -$113 total. Every entry was a "fresh signal" by v2.2's criteria, but the scanner had no concept of "we just lost N times on this same coin." v2.3 adds three protections:

### 1. Chop-detection lockout
After 2 losses on HYPE within 3 hours, Wolverine refuses any new entry on HYPE for 6 hours from the last loss. The scanner emits `CHOP_LOCKED` and waits out the chop. Constants:
- `CHOP_WINDOW_HOURS = 3`
- `CHOP_MAX_LOSSES = 2`
- `CHOP_LOCKOUT_HOURS = 6`

### 2. Direction-flip hard gate
If the new signal is OPPOSITE direction to the most recent trade on HYPE within 2 hours AND that trade was a loss, Wolverine hard-gates the new entry. Catches the LONG → SHORT → LONG whipsaw pattern even before the chop-lockout threshold fires. Winning flips are still allowed (legit reversal catch); losing flips are blocked (chop chasing).

### 3. Persistent entry log
Every ENTRY, EXIT, CHOP_LOCKOUT, and FLIP_BLOCKED event writes a JSON line to `state/entry-log.jsonl`. **This log survives `openclaw sessions clear --current`** — the data lives on disk, not in LLM context. When you ask "what scored that trade?" after a session reset, you can answer by tailing the log:

```bash
tail -20 /data/workspace/skills/wolverine-strategy/state/entry-log.jsonl | jq
```

### 4. Exit tracking hook
`sync_closed_positions(wallet, state)` runs at the start of every scan, compares current positions to the previous scan's snapshot, and appends EXIT events linked to prior ENTRY metadata. Closes the loop on chop detection so the scanner sees its own realized PnL per trade.

## Fleet-standard guardrails (all present)

- Self-executing via `create_position` (no external action layer needed)
- Dynamic P&L-aware daily entry cap (PR #176)
- `has_resting_orders()` auto-cancels stale maker orders >10 min (PR #177)
- Stale-date bug fix in `load_trade_counter()` (PR #177)
- Per-asset cooldown (180 min default)
- Conviction-scaled leverage capped at 10x (fleet H12 audit)

## Key settings

| Setting | Value | Why |
|---|---|---|
| Asset | HYPE | Single-asset focus, no parallel bets |
| Max positions | 1 | Concentration |
| Margin per trade | 50% | High conviction commits high capital |
| Max leverage | 10x | Fleet cap (H12 audit found >10x destroys edge via fees) |
| Min score | 8 | Above this, conviction is real |
| Per-asset cooldown | 180 min | Patience between trades |
| DSL hard timeout | 240 min | HYPE winners often need 2-4 hours to develop |
| DSL Phase 1 max loss | 25% ROE | Standard fleet floor |
| DSL Phase 2 tier 1 | +8% / 25% lock | Earlier than fleet standard |

## ⛔ Critical agent rules

1. **Install path** is `/data/workspace/skills/wolverine-strategy/`
2. **THE SCANNER DOES NOT EXIT POSITIONS** — DSL handles all exits
3. **MAX 1 POSITION** — HYPE only
4. **Do not modify scanner constants** without testing — fleet-wide thresholds were tuned by audit
5. **Do not rebase the cron to a faster cadence than 3 min** — 3 min is the right cadence for HYPE momentum signals

## Setup

### Step 1 — Install path

The skill must live at `/data/workspace/skills/wolverine-strategy/`. The package contains:

```
wolverine/
├── README.md                     # User-facing summary
├── SKILL.md                      # This file (LLM-facing)
├── runtime.yaml                  # OpenClaw runtime config + DSL preset
├── config/
│   └── wolverine-config.json     # Wallet, strategy ID, chat ID
└── scripts/
    ├── wolverine-scanner.py      # Main scanner (v2.3)
    └── wolverine_config.py       # Helper module (atomic write, MCP, state I/O)
```

Pull from the senpi-skills GitHub repo:

```bash
mkdir -p /data/workspace/skills/wolverine-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/runtime.yaml \
  -o /data/workspace/skills/wolverine-strategy/runtime.yaml

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/SKILL.md \
  -o /data/workspace/skills/wolverine-strategy/SKILL.md

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/config/wolverine-config.json \
  -o /data/workspace/skills/wolverine-strategy/config/wolverine-config.json

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine-scanner.py \
  -o /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine_config.py \
  -o /data/workspace/skills/wolverine-strategy/scripts/wolverine_config.py
```

### Step 2 — Set wallet and chat ID

Set your strategy wallet address in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/wolverine-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/wolverine-strategy/runtime.yaml
```

OR set them in `config/wolverine-config.json` directly (the scanner will fall back to the JSON values if env vars / runtime placeholders aren't set):

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

The scanner also supports environment variables: `WOLVERINE_WALLET` and `WOLVERINE_STRATEGY_ID`.

### Step 3 — Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/wolverine-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status
```

### Step 4 — Verify with a single manual scan

```bash
python3 /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py
```

Expected: clean exit, JSON output includes `"_wolverine_version": "2.3"`. Most likely first run shows a heartbeat (no signal) — that's normal. Wolverine fires on HYPE momentum confluence, which is rare.

### Step 5 — Run the scanner on a recurring schedule

The recommended pattern is a detached bash loop with zero LLM wake cost (matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py >> /tmp/wolverine-loop.log 2>&1; sleep 180; done' > /tmp/wolverine-nohup.log 2>&1 &

# Verify the loop is running
ps aux | grep wolverine-scanner | grep -v grep
tail -5 /tmp/wolverine-loop.log
```

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. Avoid `sessionTarget: main` — that pattern was a $287/day bug on Sentinel and a $200/day timer-bomb on Scorpion before we caught it.

## Operational notes

### Tailing the entry log

After Wolverine takes its first trade, you can read the structured entry log to see exact scores, reasons, leverage, and outcomes:

```bash
tail -10 /data/workspace/skills/wolverine-strategy/state/entry-log.jsonl | jq
```

The log persists across session clears, so you can answer "what was the last entry score?" even after `openclaw sessions clear --current`.

### Checking chop-lockout state

If Wolverine outputs `CHOP_LOCKED` notes, it has detected 2+ losses on HYPE within 3 hours and is sitting out for 6 hours from the last loss. To see when it unlocks:

```bash
grep CHOP_LOCKOUT /data/workspace/skills/wolverine-strategy/state/entry-log.jsonl | tail -1 | jq '.unlock_ts'
```

This is intentional behavior, not a bug. Wolverine is protecting capital during chop.

### Verifying the resting-order auto-cancel

Wolverine cancels stale FEE_OPTIMIZED_LIMIT maker orders older than 10 minutes. If you see `Auto-cancel attempted on stale order ...` in the log, that's the safety mechanism working.

## Best for

- Operators who want a single-asset HYPE momentum specialist
- Trading environments where 2-4 trades per day is the right cadence (not high-frequency)
- Accounts where one bad chop day could be expensive without circuit breakers
- Operators who want the agent to be self-managing — no manual position closing required

## Not for

- Multi-asset diversification (use Phoenix, Condor, or Polar instead)
- High-frequency scalping
- Anyone who wants the scanner to make exit decisions (DSL handles all exits)
- XYZ DEX equities (Wolverine is HYPE-only)

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
