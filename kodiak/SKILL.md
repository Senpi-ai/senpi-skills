---
name: kodiak-strategy
description: >-
  KODIAK v2.0 — SOL alpha hunter with position lifecycle. Thesis exit removed.
  DSL manages all exits. Leverage capped at 7x. Retrace widened to 0.08.
  v1.1.1's SOL SHORT ran 13 hours unprotected due to missing wallet fields —
  v2.0 prevents this.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  base_skill: grizzly-v2.0
  requires:
    - senpi-trading-runtime
---

# 🐻 KODIAK v2.0 — SOL Alpha Hunter

One asset. Every signal. Scanner enters. DSL exits.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/kodiak-strategy/`

### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS

When the scanner sees an active SOL position, it outputs NO_REPLY. DSL is the
ONLY exit mechanism. v1.1.1 had a thesis exit that chopped positions before
DSL could trail them. v2.0 removes it entirely.

### RULE 3: MAX 1 POSITION — SOL only

### RULE 4: Scanner output is AUTHORITATIVE

### RULE 5: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. Runtime must be listed. The position tracker and DSL exit are handled by the plugin runtime.

### RULE 6: Never modify parameters. Never increase leverage above 7x.

### RULE 7: 120-minute cooldown after consecutive losses

---

## What Changed From v1.1.1

| v1.1.1 | v2.0 |
|---|---|
| Thesis exit active in RIDING mode | **Removed** — DSL manages all exits |
| DSL state missing wallet + size | **All fields included** |
| Leverage 10-12x | **Capped at 7x** |
| Retrace 0.03 (3% ROE = 0.3% price at 10x) | **0.08 (8% ROE = 1.14% price at 7x)** |
| `strategy_id` discarded in run() | **Captured and passed to DSL builder** |
| SOL SHORT ran 13h unprotected | **Every position protected from second 1** |

---

## v1.1.1 Proof of Concept

Kodiak's best trade: SOL SHORT, entry $90.36, DSL trailed to Tier 4 (+44% ROE),
exit at $85.86, realized **+$134** profit. The scanner found the setup. DSL
managed the exit perfectly — locked 85% of peak, gave back only $3 from top.

The problem: DSL was manually patched onto this trade 13 hours after entry
because the state file was missing wallet fields. v2.0 fixes this permanently.

---

## The Three-Mode Lifecycle

### MODE 1 — HUNTING (default)

Scan SOL every 3 minutes. All signals must align (4h trend, 1h momentum, SM,
funding, OI, volume). Score 10+ to enter. When a position opens, switch to MODE 2.

### MODE 2 — RIDING

Active position. **DSL manages the exit via the plugin runtime. Scanner outputs NO_REPLY.**
The scanner does NOT re-evaluate the thesis. It does NOT close positions.
The plugin DSL trails the position through Phase 1 protection and Phase 2
trailing tiers. When DSL closes the position → switch to MODE 3.

### MODE 3 — STALKING

DSL locked profits. Watch for a reload opportunity. ALL reload conditions
must pass: fresh momentum impulse, OI stable, volume present, funding not
crowded, SM still aligned, 4h trend intact.

If reload fires → re-enter same direction, switch to MODE 2.
If kill conditions trigger → reset to MODE 1.

---

## Cron Setup

Scanner (3 min, main):
```
python3 /data/workspace/skills/kodiak-strategy/scripts/kodiak-scanner.py
```

---

## How KODIAK Trades

### Entry (score >= 10 required)

Every 3 minutes, the scanner evaluates SOL across all signal sources:

| Signal | Points | Required? |
|---|---|---|
| 4h trend structure (higher lows / lower highs) | 3 | **Yes** |
| 1h trend agrees with 4h | 2 | **Yes** |
| 15m momentum confirms direction | 0-1 | **Yes** |
| 5m alignment (all 4 timeframes agree) | 1 | No |
| SM aligned with direction | 2-3 | **Hard block if opposing** |
| Funding pays to hold the direction | 2 | No |
| Volume above average | 1-2 | No |
| OI growing | 1 | No |
| BTC confirms move | 1 | No |
| RSI has room | 1 | No (blocks overbought/oversold) |
| 4h momentum strength | 1 | No |

Maximum score: ~18. Minimum to enter: 10.

### Conviction-Scaled Leverage

| Score | Leverage |
|---|---|
| 10-11 | 7x |
| 12+ | 7x |

### Conviction-Scaled Margin

| Score | Margin |
|---|---|
| 10-11 | 20% of account |
| 12-13 | 25% |
| 14+ | 30% |

## Exit Management

DSL exit is handled by the plugin runtime via `runtime.yaml`. The `position_tracker` scanner auto-detects position opens/closes on-chain. See `runtime.yaml` for configuration details.

**Monitor positions:**
- `openclaw senpi dsl positions` — list all DSL-tracked positions
- `openclaw senpi dsl inspect <ASSET>` — full position details

## Why SOL-Only at 7x Leverage

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 1 (SOL only) |
| Max leverage | 7x |
| Phase 1 retrace | 0.08 |
| Daily loss limit | 10% |
| Cooldown | 120 min after 3 consecutive losses |

---

## Runtime Setup

**Step 1:** Set your strategy wallet address in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/kodiak-strategy/runtime.yaml
```
Replace `<STRATEGY_WALLET_ADDRESS>` with the actual wallet address.

**Step 2:** Set telegram chat ID for notifications:
```bash
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/kodiak-strategy/runtime.yaml
```
Replace `<CHAT_ID>` with the actual Telegram chat ID.

**Step 3:** Install the runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/kodiak-strategy/runtime.yaml
```

**Step 4:** Verify:
```bash
openclaw senpi runtime list
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Read the senpi-trading-runtime skill: `cat /data/workspace/skills/senpi-trading-runtime/SKILL.md` — this provides all CLI commands for runtime management and DSL position inspection.
2. Verify Senpi MCP
3. Set wallet in runtime.yaml: `sed -i 's/${WALLET_ADDRESS}/ACTUAL_ADDRESS/' /data/workspace/skills/kodiak-strategy/runtime.yaml`
4. Set Telegram in runtime.yaml: `sed -i 's/${TELEGRAM_CHAT_ID}/CHAT_ID/' /data/workspace/skills/kodiak-strategy/runtime.yaml`
5. Install runtime: `openclaw senpi runtime create --path /data/workspace/skills/kodiak-strategy/runtime.yaml`
6. Verify runtime installed: `openclaw senpi runtime list`
7. Remove old DSL cron (if upgrading): run `openclaw crons list`, delete any cron containing `dsl-v5.py` via `openclaw crons delete <id>`
8. Create scanner cron (3 min, isolated)
9. Write `config/bootstrap-complete.json`
10. Send: "KODIAK is online. Watching SOL. DSL managed by plugin runtime. Silence = no conviction."

If bootstrap exists, still verify runtime and scanner cron on every session start.

---

## Notification Policy

**ONLY alert:** Position OPENED (direction, leverage, score, reasons), position CLOSED (DSL exit with P&L), risk guardian triggered, critical error.
**NEVER alert:** Scanner found no thesis, thesis re-eval passed, any reasoning.

---

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 1-3 |
| Avg hold time | 1-12 hours |
| Win rate | ~45-55% |
| Avg winner | 20-50%+ ROE |
| Avg loser | -20 to -40% ROE |

---

## Files

| File | Purpose |
|---|---|
| `scripts/kodiak-scanner.py` | SOL thesis builder + stalk/reload |
| `scripts/kodiak_config.py` | Config helper (MCP, state, cooldowns) |
| `config/kodiak-config.json` | Wallet, strategy ID, configurable variables |
| `runtime.yaml` | Runtime config for plugin (DSL exit + position tracker) |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
