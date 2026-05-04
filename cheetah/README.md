# 🐆 CHEETAH v6.0 — Multi-Signal Confluence Sniper (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v6.0

- `cheetah-producer.py` (NEW) replaces `cheetah-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits per-trade telemetry — chain DB visibility on Cheetah for the first time
- **MIN_SCORE 11 → 10** — restores trade flow that produced +$182 in v5.0/v5.1 era
- Held-asset dedup (3-layer)
- Post-close cooldown (Pangolin v2.1.2 pattern; backstops the runtime per_asset_cooldown bug)
- All v5.2 scoring + leverage tiers + leverage-safety clamp preserved EXACTLY

## Thesis (preserved from v5.x)

Multi-signal confluence sniper. Refuses to trade unless ALL major signals align: SM consensus + velocity + acceleration + dual price confirmation + volume spike + quality-trader alignment + rank climb. Score 10/15 floor. Top-100 SM leaderboard universe. XYZ banned. Patience is the edge.

## Install

```bash
mkdir -p /data/workspace/skills/cheetah-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/runtime.yaml -o /data/workspace/skills/cheetah-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/SKILL.md -o /data/workspace/skills/cheetah-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/config/cheetah-config.json -o /data/workspace/skills/cheetah-strategy/config/cheetah-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/cheetah-producer.py -o /data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/cheetah_config.py -o /data/workspace/skills/cheetah-strategy/scripts/cheetah_config.py
```

## Configure

**Set wallet, strategyId, chatId in `config/cheetah-config.json`** — canonical source. Producer reads from here on every cron tick.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 10
}
```

LLM model env var (only at runtime-create time):

```bash
export CHEETAH_DECISION_MODEL=gemini-3.1-pro-preview    # bare model name; NO provider prefix
```

## Install runtime + producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime list
```

Cron (3-min cadence, no env vars needed — wallet read from config):

```cron
*/3 * * * * cd /data/workspace/skills/cheetah-strategy && python3 scripts/cheetah-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Universe | Top 100 SM leaderboard (XYZ banned) |
| Max positions | 1 |
| Margin per slot | $250 (30% of starting budget) |
| Leverage | 3x / 5x / 7x / 8x (score-tiered) |
| **MIN_SCORE** | **10** (down from v5.2's 11) |
| LLM min_confidence | 7 |
| Per-asset cooldown | 240 min (4h) |
| Post-close cooldown | 240 min (producer-side backstop) |
| Daily entry cap | 8 |
| Daily loss limit | 25% |
| Drawdown halt | 25% |
| drawdown_reset_on_day_rollover | false |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |

## DSL Phase 2 ladder (v6.0 — fleet-standard T0/T1)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% |
| T1 | +10% | 50% |
| T2 | +20% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 90% |

Phase 1: max_loss 15% / retrace 6 / 3 consecutive breaches.
Time cuts: hard_timeout 720min, weak_peak_cut 90min @ 3.0, dead_weight_cut 60min — all ENABLED (multi-asset rotation has opportunity cost).

## Migrating from v5.2

```bash
cd /data/workspace/skills/cheetah-strategy

# DO NOT delete the runtime if any positions are open — orphan-position
# bug applies (v2 runtime swap leaves baseline positions without DSL).
# Cheetah has been gate-paused at MIN_SCORE 11 for 8 days — expected
# state is zero positions.
openclaw senpi strategy_get_clearinghouse_state --strategy_wallet <wallet>

# Pull new files
rm -f scripts/cheetah-scanner.py                    # replaced by cheetah-producer.py
# (curl commands above)

# Reload runtime (safe: no positions to orphan)
openclaw senpi runtime delete <old runtime id>
openclaw senpi runtime create --path runtime.yaml

# Update cron: replace cheetah-scanner.py with cheetah-producer.py
```

State files (`state/entry-log.jsonl`, `state/scan-history.json`, `state/quality-cache.json`, `state/cooldowns.json`, `state/trade-counter.json`) — the new producer uses wallet-isolated subdirs (`state/<wallet-hash>/`) so v5.2 state files are vestigial. Safe to delete the legacy ones at the top of `state/`.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
