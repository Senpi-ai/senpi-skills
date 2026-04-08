---
name: shark
description: >-
  SHARK v3.0 — SM Conviction + Liquidation Cascade Hunter. Consolidated from
  v1.0's 8-cron pipeline into a single scanner. 4-gate entry: SM concentration
  (30+ traders, 5%+) → top 5 trader alignment → price momentum → funding
  structure. Score 8+ to enter. DSL manages all exits. No thesis exit.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦈 SHARK v3.0 — SM Conviction + Liquidation Cascade Hunter

Four gates. One scanner. DSL manages exits.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/shark/`

### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS

When the scanner sees an active position, it outputs NO_REPLY. DSL is the ONLY exit mechanism. v1.0 lost -4.6% partly because the scanner kept cycling positions rapidly, generating $144/day in fees. v2.0 lets positions run until DSL trails or cuts them.

### RULE 3: MAX 2 POSITIONS at a time

### RULE 4: Scanner output is AUTHORITATIVE

### RULE 5: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. Runtime must be listed. The position tracker and DSL exit are handled by the plugin runtime.

### RULE 6: Never retry timed-out position creation

If `create_position` times out, check clearinghouse state. If position exists, the position tracker will pick it up automatically. If not, wait for next scan.

### RULE 7: Never modify parameters

### RULE 8: 120-minute per-asset cooldown

---

## What Changed From v1.0

| v1.0 | v2.0 |
|---|---|
| 10 scripts, 8 crons | 1 scanner + DSL + risk (3 crons) |
| Pipeline: mapper → proximity → entry | Single 4-gate scan |
| Thesis exit active | Thesis exit REMOVED |
| No DSL state generation | Full DSL v1.1.1 state with wallet |
| Crons kept dying silently | Fewer moving parts = more reliable |
| 68 fills, -4.6% | Fewer trades, higher conviction |

---

## The 4 Gates

Every candidate must pass ALL 4 gates to enter:

| Gate | Check | Threshold |
|---|---|---|
| 1. SM Concentration | leaderboard_get_markets | 5%+ gain share, 30+ traders |
| 2. Top 5 Alignment | leaderboard_get_top → trader positions | 2+ of top 5 in same direction |
| 3. Price Momentum | 4H and/or 1H price change aligned | At least one timeframe confirming |
| 4. Funding Structure | Funding rate confirming directional pressure | Funding aligned or neutral |

**Scoring:** SM concentration (0-3 pts) + top alignment (0-3 pts) + momentum (0-2 pts) + contribution acceleration (0-1 pts) + funding (0-1 pts). Score 8+ to enter.

---

## Exit Management

DSL exit is handled by the plugin runtime via `runtime.yaml`. The `position_tracker` scanner auto-detects position opens/closes on-chain. See `runtime.yaml` for configuration details.

**Monitor positions:**
- `openclaw senpi dsl positions` — list all DSL-tracked positions
- `openclaw senpi dsl inspect <ASSET>` — full position details

---

## Runtime Setup

**Step 1:** Set your strategy wallet address in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/shark/runtime.yaml
```
Replace `<STRATEGY_WALLET_ADDRESS>` with the actual wallet address.

**Step 2:** Set telegram chat ID for notifications:
```bash
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/shark/runtime.yaml
```
Replace `<CHAT_ID>` with the actual Telegram chat ID.

**Step 3:** Install the runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/shark/runtime.yaml
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
3. Set wallet in runtime.yaml: `sed -i 's/${WALLET_ADDRESS}/ACTUAL_ADDRESS/' /data/workspace/skills/shark/runtime.yaml`
4. Set Telegram in runtime.yaml: `sed -i 's/${TELEGRAM_CHAT_ID}/CHAT_ID/' /data/workspace/skills/shark/runtime.yaml`
5. Install runtime: `openclaw senpi runtime create --path /data/workspace/skills/shark/runtime.yaml`
6. Verify runtime installed: `openclaw senpi runtime list`
7. Remove old DSL cron (if upgrading): run `openclaw crons list`, delete any cron containing `dsl-v5.py` via `openclaw crons delete <id>`
8. Create scanner cron (5 min, main)
9. Write `config/bootstrap-complete.json`
10. Send: "🦈 SHARK v3.0 online. SM Conviction + Liquidation Cascade Hunter. 4-gate entry, score 8+. DSL managed by plugin runtime. Silence = no conviction."

If bootstrap exists, still verify runtime and scanner cron on every session start.

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 2 |
| Max entries/day | 4 |
| Leverage | 7x |
| Per-asset cooldown | 120 min |
| Daily loss limit | 12% |
| Consecutive losses | 3 → 45 min cooldown |
| XYZ | Banned |

---

## Expected Behavior

| Metric | Expected |
|---|---|
| Entries/day | 0-3 (many zero-trade days) |
| Win rate | ~50% |
| Avg winner | 2-3x avg loser |
| Position duration | 15 min to 6+ hours |

---

## License

MIT — Built by Senpi (https://senpi.ai).
