---
name: vulture-strategy
description: >-
  VULTURE v1.0 — Multi-Asset SM Exhaustion Fader. Detects when Smart Money
  consensus is overwhelmingly strong AND the 4H price move is already
  extended (>3%), then fades the exhausted move. BTC/ETH/SOL/HYPE only.
  Conservative leverage (5-7x), wide DSL, let reversals develop.
  FEE_OPTIMIZED_LIMIT maker entries. DSL exit managed by plugin runtime.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦅 VULTURE v1.0 — Multi-Asset SM Exhaustion Fader

Purpose-built contrarian strategy. Born from fleet analysis (April 10, 2026) which found that 5 agents using SM consensus scanners had perfectly inverted signals — systematically buying tops and shorting bottoms because multi-timeframe confirmation enters after the move is exhausted. Vulture turns that systematic failure into a deliberate strategy.

---

## CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/vulture-strategy/`

### RULE 2: MAX 1 POSITION — check before EVERY entry

Before opening ANY position, call `strategy_get_clearinghouse_state` and count open positions. If positions >= 1, SKIP.

### RULE 3: Scanner output is AUTHORITATIVE — never override from memory

### RULE 4: Verify runtime is installed on every session start

Run `openclaw senpi runtime list`. Runtime must be listed.

### RULE 5: Never retry timed-out position creation

If `create_position` times out, check clearinghouse state first.

### RULE 6: Never modify your own configuration

---

## Thesis

When SM consensus is overwhelmingly strong in one direction AND the 4H price move is already extended (>3%), the move is exhausted and about to reverse. Trade the opposite direction.

Key insight: the SM consensus signal is a lagging indicator. By the time 100+ traders are concentrated with 15%+ of gains in one direction, the move has already happened. Vulture fades the tail.

## Scoring

- **SM concentration (0-3):** stronger consensus = more exhausted
- **Exhaustion gate:** 4H price must have moved >3% in SM direction (mandatory)
- **Exhaustion bonus (1-3):** bigger move = better fade (>5% = +3, >4% = +2, >3% = +1)
- **1H reversal detection (0-2):** fading 1H momentum confirms exhaustion
- **15M SM velocity fading (0-2):** SM starting to cool = reversal beginning
- **Trader depth (0-1):** deep consensus = more crowded = better fade

MIN_SCORE: 8. Direction is FLIPPED after scoring.

## Entry

- Assets: BTC, ETH, SOL, HYPE only
- Leverage: 5x base, 7x at score 10+
- Margin: 30% of account
- Max 1 position, max 2 entries/day
- 120-min per-asset cooldown, 60-min same-direction cooldown after win
- FEE_OPTIMIZED_LIMIT, ensureExecutionAsTaker: false, 30s timeout

## Exit (DSL)

Wide DSL — reversals take time to develop.
- hard_timeout: 360 min (6h backstop)
- weak_peak_cut: 90 min, min_value 2.0% ROE
- dead_weight_cut: 30 min
- Phase 1: max_loss 15%, retrace 8%, 3 consecutive breaches
- Phase 2: 5%/20%, 10%/40%, 15%/60%, 20%/75%, 30%/85%

## Runtime Setup

**Step 1:** Set strategy wallet in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/vulture-strategy/runtime.yaml
```

**Step 2:** Set telegram chat ID:
```bash
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/vulture-strategy/runtime.yaml
```

**Step 3:** Install runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/vulture-strategy/runtime.yaml
```

**Step 4:** Verify:
```bash
openclaw senpi runtime list
```

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Read senpi-trading-runtime SKILL.md
2. Verify Senpi MCP
3. Set wallet and Telegram in runtime.yaml
4. Install runtime
5. Verify runtime installed
6. Create scanner cron (3 min interval)
7. Write `config/bootstrap-complete.json`
8. Send: "VULTURE v1.0 online. SM exhaustion fader active. Circling for exhausted moves on BTC/ETH/SOL/HYPE."

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 1 |
| Max entries/day | 2 |
| Leverage | 5-7x |
| Per-asset cooldown | 2 hours |
| Same-dir cooldown | 1 hour after win |
| XYZ equities | Banned |
| 4H exhaustion gate | >3% price move required |

## Notification Policy

**ONLY alert:** Position OPENED (include SM direction, fade direction, score), position CLOSED (P&L), critical error.

**NEVER alert:** Scanner found nothing, reasoning details.

## Files

| File | Purpose |
|---|---|
| `scripts/vulture-scanner.py` | SM exhaustion fader scanner |
| `scripts/vulture_config.py` | Config helper |
| `runtime.yaml` | DSL exit config (wide for contrarian) |

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
