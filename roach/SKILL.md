---
name: roach-strategy
description: >-
  ROACH v1.0 — Striker-only scanner. Stalker mode disabled entirely. Tests
  whether Stalker adds any value or is pure drag. Only trades violent FIRST_JUMP
  explosions backed by 1.5x volume. Will have long stretches of silence — that
  patience is the edge. All hardened gates preserved. XYZ banned. Leverage 7-10x.
  DSL exit managed by plugin runtime via runtime.yaml.
license: MIT
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
  config_source: striker-only-experiment
  requires:
    - senpi-trading-runtime
---

# 🪳 ROACH v1.0 — Striker Only. Stalker Disabled.

Cockroaches survive anything. ROACH survives by not trading when there's no explosion.

---

## ⛔ CRITICAL AGENT RULES — READ BEFORE ANYTHING ELSE

### RULE 1: Install path is `/data/workspace/skills/roach-strategy/`

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

### RULE 7: Do NOT enable Stalker mode

Stalker is disabled by design. This is an experiment. Do not re-enable it, do not build your own accumulation detection, do not invent alternative entry logic. If the scanner outputs `"stalkerDisabled": true`, that means it's working correctly. Long periods of silence with zero trades are EXPECTED and CORRECT.

---

## The ROACH Experiment

**Hypothesis:** Stalker mode is pure drag. Fox v1.0 produced 17 Stalker trades at score 6-7 with a 17.6% win rate and -$91.32 net P&L. The one Striker signal (ZEC LONG, score 11) was the only explosive entry worth taking. If ROACH outperforms or matches the other variants with dramatically fewer trades, Stalker should be permanently demoted or removed from the scanner.

**What this means in practice:** ROACH will be quiet. Striker signals require FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+), rank jump >= 15 OR velocity > 15, volume >= 1.5x, score >= 9 with 4+ reasons. In choppy markets, this might fire 0-1 times per day. In trending markets with SM explosions, maybe 2-3.

**The question:** Does fewer + higher quality beat more + mixed quality? Polar proved it at the macro level (29 trades, +28.1% vs Ghost Fox's 1,078 trades, -58.5%). ROACH tests it at the signal-type level within the same scanner framework.

---

## Single Entry Mode

### STRIKER (Explosion) — Score >= 9, min 4 reasons
- FIRST_JUMP or IMMEDIATE_MOVER (10+ rank jump from #25+)
- Rank jump >= 15 OR contribution velocity > 15
- Raw volume >= 1.5x of 6h average
- Score >= 9 with at least 4 distinct reasons
- 4H trend aligned, XYZ banned, asset not in cooldown

### STALKER — DISABLED
- Scanner still builds scan history (needed for Striker's rank jump detection)
- Stalker signals are never generated or emitted
- Output always contains `"stalkerDisabled": true` and `"stalkerSignals": []`

---

## Exit Management

DSL exit is handled by the plugin runtime via `runtime.yaml`. The `position_tracker` scanner auto-detects position opens/closes on-chain. See `runtime.yaml` for configuration details.

**Entry flow:**
1. Scanner outputs Striker signal
2. Verify positions < 3
3. Verify exchange max leverage >= 7
4. Call `create_position`
5. Send ONE notification: position opened
6. `position_tracker` detects the new position automatically
7. Plugin DSL monitor applies trailing stop-loss protection

**Monitor positions:**
- `openclaw senpi dsl positions` — list all DSL-tracked positions
- `openclaw senpi dsl inspect <ASSET>` — full position details

---

## Runtime Setup

**Step 1:** Set your strategy wallet address in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/roach-strategy/runtime.yaml
```
Replace `<STRATEGY_WALLET_ADDRESS>` with the actual wallet address.

**Step 2:** Set telegram chat ID for notifications:
```bash
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/roach-strategy/runtime.yaml
```
Replace `<CHAT_ID>` with the actual Telegram chat ID.

**Step 3:** Install the runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/roach-strategy/runtime.yaml
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
3. Set wallet in runtime.yaml: `sed -i 's/${WALLET_ADDRESS}/ACTUAL_ADDRESS/' /data/workspace/skills/roach-strategy/runtime.yaml`
4. Set Telegram in runtime.yaml: `sed -i 's/${TELEGRAM_CHAT_ID}/CHAT_ID/' /data/workspace/skills/roach-strategy/runtime.yaml`
5. Install runtime: `openclaw senpi runtime create --path /data/workspace/skills/roach-strategy/runtime.yaml`
6. Verify runtime installed: `openclaw senpi runtime list`
7. Remove old DSL cron (if upgrading): run `openclaw crons list`, delete any cron containing `dsl-v5.py` via `openclaw crons delete <id>`
8. Create scanner cron (90s, main)
9. Write `config/bootstrap-complete.json`
10. Send: "🪳 ROACH v1.0 online. Striker only. Stalker disabled. Waiting for explosions. Silence = no explosion."

If bootstrap exists, still verify runtime and scanner cron on every session start.

---

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day (chop) | 0-1 |
| Trades/day (trending) | 1-3 |
| Days with zero trades | Common and correct |
| Win rate | Higher than Stalker variants (60-70% target) |
| Avg winner | Larger (explosive entries with 1.5x volume) |
| Avg loser | Similar to other variants (DSL-managed) |
| Total P&L | Hypothesis: matches or beats Stalker variants with far fewer trades |

**SILENCE IS CORRECT.** If ROACH goes 24-48 hours without a trade, that means there were no FIRST_JUMP explosions worth taking. That's the experiment working, not a bug.

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
| Stalker mode | DISABLED |

---

## Notification Policy

**ONLY alert:** Position OPENED (Striker signal with score + reasons), position CLOSED (P&L + reason), critical error.

**NEVER alert:** Scanner found no Striker signals (this is normal and expected), any reasoning about whether to enable Stalker, any analysis of market conditions.

---

## Files

| File | Purpose |
|---|---|
| `scripts/roach-scanner.py` | Striker-only scanner (Stalker disabled in run()) |
| `scripts/roach_config.py` | Config helper |
| `config/roach-config.json` | Config (Stalker settings present but unused) |
| `runtime.yaml` | Runtime config for plugin (DSL exit + position tracker) |

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
