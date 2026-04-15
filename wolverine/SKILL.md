---
name: wolverine-strategy
description: >-
  WOLVERINE v2.3 — HYPE Alpha Hunter (chop-hardened). Single-asset
  HYPE momentum scanner combining Smart Money conviction, 15m/1h velocity
  acceleration, and 4H/1H price confirmation. Self-executing, conviction-
  scaled leverage 7x/10x, persistent entry log, chop-detection lockout
  prevents whipsaw losses on choppy days. Best held 2-4 hours per trade.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.3"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦡 WOLVERINE v2.3 — HYPE Alpha Hunter

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


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
