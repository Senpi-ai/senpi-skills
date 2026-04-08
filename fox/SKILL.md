---
name: fox-strategy
description: >-
  FOX v2.0 — Dual-mode emerging movers scanner. All live trading lessons from
  Fox v1.0 applied, plus one experimental tweak: Stalker minReasons = 3.
  Stalker entries must have at least 3 distinct scoring reasons, not just
  pass the score threshold. This forces breadth of confirmation beyond the
  auto-awarded base climb score + one bonus. Stalker minScore 7, minTotalClimb 8,
  tighter Phase 1 for low-score entries, consecutive-loss streak gate. XYZ banned.
  Leverage 7-10x. DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
  config_source: fox-v1-trade-data-plus-min-reasons-experiment
  requires:
    - senpi-trading-runtime
---

# 🦊 FOX v2.0 — Dual-Mode Scanner + minReasons Experiment

Fox reborn. Every lesson from v1.0's 20 closed positions baked in, plus one experimental tweak to test whether requiring breadth of confirmation reduces the weak-peak bleed.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/fox-strategy/`

The skill MUST be installed to exactly this path.

### RULE 2: MAX 3 POSITIONS — check before EVERY entry

Before opening ANY position, call `strategy_get_clearinghouse_state` and count open positions. If positions >= 3, SKIP.

### RULE 3: Scanner output is AUTHORITATIVE — never override from memory

The scanner is the single source of truth for all trading parameters.

### RULE 4: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. Runtime must be listed. The position tracker and DSL exit are handled by the plugin runtime.

### RULE 5: Never retry timed-out position creation

If `create_position` times out, check clearinghouse state first.

### RULE 6: Never modify your own configuration

No adjustments to leverage, margin, scoring, or any parameter.

### RULE 7: Record Stalker results for streak tracking

After every Stalker position closes, call `record_stalker_result(tc, is_win)`.

---

## The FOX v2.0 Experiment

**Hypothesis:** Fox v1.0's weak Stalker trades didn't just have low scores — they had thin confirmation. A score-7 entry from STALKER_CLIMB(+3) + SM_ACTIVE(+1) + time_bonus(+1) has 2 reasons beyond base. That's technically passing but only confirms "it's climbing with traders during good hours." No contribution acceleration, no deep start, no momentum signal. It's noise dressed as signal.

**The tweak:** Stalker entries require `len(reasons) >= 3`. This is the ONLY difference from the base scanner. Everything else — minScore, minTotalClimb, DSL tiers, Striker logic, streak gate — is identical.

**What this filters:** A Stalker at score 7 with reasons = [STALKER_CLIMB +8, SM_ACTIVE 15 traders] has 2 reasons → BLOCKED. The same signal with reasons = [STALKER_CLIMB +8, CONTRIB_ACCEL +0.002%/scan, SM_ACTIVE 15 traders] has 3 reasons → PASSES. The difference is contribution acceleration, which is a real confirmation that SM money is accelerating, not just present.

**Expected effect:** Fewer Stalker entries, higher quality. The question is whether this filters out too many winners (like NEAR SHORT +$18.10 which had 3 reasons and would pass) or just the chop.

---

## Dual-Mode Entry

### MODE A — STALKER (Accumulation) — Score >= 7, min 3 reasons
- SM rank climbing steadily over 3+ consecutive scans
- Total climb >= 8 ranks
- Contribution building each scan
- 4H trend aligned
- **v2.0: At least 3 distinct scoring reasons required**
- **Streak gate:** 3 consecutive Stalker losses → minScore raised to 9

### MODE B — STRIKER (Explosion) — Score >= 9, min 4 reasons
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Rank jump >= 15 OR velocity > 15
- Raw volume >= 1.5x of 6h average
- Unchanged

---

## Exit Management

DSL exit is handled by the plugin runtime via `runtime.yaml`. The `position_tracker` scanner auto-detects position opens/closes on-chain. See `runtime.yaml` for configuration details.

**Entry flow:**
1. Scanner outputs signal
2. Verify positions < 3
3. Verify exchange max leverage >= 7
4. Call `create_position`
5. Send ONE notification: position opened
6. `position_tracker` detects the new position automatically
7. Plugin DSL monitor applies trailing stop-loss protection

**Monitor positions:**
- `openclaw senpi dsl positions` — list all DSL-tracked positions
- `openclaw senpi dsl inspect <ASSET>` — full position details

**On position close:**
8. Call `record_stalker_result(tc, is_win)` if Stalker entry

---

## Runtime Setup

**Step 1:** Set your strategy wallet address in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/fox-strategy/runtime.yaml
```
Replace `<STRATEGY_WALLET_ADDRESS>` with the actual wallet address.

**Step 2:** Set telegram chat ID for notifications:
```bash
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/fox-strategy/runtime.yaml
```
Replace `<CHAT_ID>` with the actual Telegram chat ID.

**Step 3:** Install the runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/fox-strategy/runtime.yaml
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
3. Set wallet in runtime.yaml: `sed -i 's/${WALLET_ADDRESS}/ACTUAL_ADDRESS/' /data/workspace/skills/fox-strategy/runtime.yaml`
4. Set Telegram in runtime.yaml: `sed -i 's/${TELEGRAM_CHAT_ID}/CHAT_ID/' /data/workspace/skills/fox-strategy/runtime.yaml`
5. Install runtime: `openclaw senpi runtime create --path /data/workspace/skills/fox-strategy/runtime.yaml`
6. Verify runtime installed: `openclaw senpi runtime list`
7. Remove old DSL cron (if upgrading): run `openclaw crons list`, delete any cron containing `dsl-v5.py` via `openclaw crons delete <id>`
8. Create scanner cron (90s, main)
9. Write `config/bootstrap-complete.json`
10. Send: "🦊 FOX v2.0 online. minReasons experiment active. Score 7+, climb 8+, 3 reasons minimum. Silence = no conviction."

If bootstrap exists, still verify runtime and scanner cron on every session start.

---

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 6 |
| Leverage | 7-10x |
| Daily loss limit | 10% |
| Per-asset cooldown | 2 hours |
| XYZ equities | Banned |
| Stalker min reasons | 3 (experiment) |
| Stalker streak gate | 3 losses → minScore 9 |

---

## Notification Policy

**ONLY alert:** Position OPENED (include reason count), position CLOSED (P&L + reason), streak gate activated/deactivated, critical error.

**NEVER alert:** Scanner found nothing, any reasoning.

---

## Files

| File | Purpose |
|---|---|
| `scripts/fox-scanner.py` | Dual-mode scanner with minReasons experiment |
| `scripts/fox_config.py` | Config helper with stalkerResults tracking |
| `config/fox-config.json` | Config with Fox v2.0 thresholds |
| `runtime.yaml` | Runtime config for plugin (DSL exit + position tracker) |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
