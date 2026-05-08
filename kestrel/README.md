# 🦅 KESTREL v2.0 — XYZ Macro Breakout Rider (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v2.0

- `kestrel-producer.py` (NEW) replaces `kestrel-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` with `ensure_execution_as_taker: true` (fixes resting-on-book bug)
- Trade chain DB emits per-trade telemetry — first time Kestrel has chain visibility
- **Calibration fix**: 1H base scores +1, MOVE_EXHAUSTION threshold 4%→6%, MIN_SCORE 6→5, spread gate 0.2%→0.35%. v1.1 only fired 2 trades / $60 volume in its lifetime due to over-strict math; v2.0 should fire 5-15 trades/month.
- Held-asset dedup (3-layer)
- Post-close cooldown (180min; Pangolin v2.1.2 pattern)
- SILVER removed from universe (HL doesn't support)

## Thesis (preserved)

When a macro asset moves >=1.5% in an hour with volume confirmation, the move usually continues for 1-3 hours. Ride the trend with wide DSL. 12-asset universe across commodities, indices, and high-volume equities. 24/7 trading on Hyperliquid XYZ DEX.

## Install

```bash
mkdir -p /data/workspace/skills/kestrel-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/runtime.yaml -o /data/workspace/skills/kestrel-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/SKILL.md -o /data/workspace/skills/kestrel-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/config/kestrel-config.json -o /data/workspace/skills/kestrel-strategy/config/kestrel-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/scripts/kestrel-producer.py -o /data/workspace/skills/kestrel-strategy/scripts/kestrel-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/scripts/kestrel_config.py -o /data/workspace/skills/kestrel-strategy/scripts/kestrel_config.py
```

## Configure

**Set wallet, strategyId, chatId in `config/kestrel-config.json`** — canonical source. Producer reads from here on every cron tick.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "minScore": 5
}
```

LLM model env var (only at runtime-create time):

```bash
export KESTREL_DECISION_MODEL=gemini-3.1-pro-preview    # bare model name; NO provider prefix
```

## Install runtime + producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/kestrel-strategy/runtime.yaml
openclaw senpi runtime list
```

Cron (5-min cadence — macro 1H candles change slowly):

```cron
*/5 * * * * cd /data/workspace/skills/kestrel-strategy && python3 scripts/kestrel-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Universe | 12 macro assets (XYZ DEX) |
| Max positions | 2 |
| Margin per slot | $300 (30%) |
| Leverage | 3x or 5x (score-tiered) |
| **MIN_SCORE** | **5** (down from v1.1's 6) |
| 1H breakout threshold | 1.5% (mandatory hard gate) |
| Spread gate | 0.35% (loosened from 0.2%) |
| Per-asset cooldown | 180 min (3h) |
| Post-close cooldown | 180 min |
| Daily entry cap | dynamic (P&L-aware, 0-12) |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| drawdown_reset_on_day_rollover | false |
| Entry order type | FEE_OPTIMIZED_LIMIT (taker fallback) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker fallback) |

## DSL Phase 2 ladder (v2.0 — fleet-standard)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% |
| T1 | +10% | 50% |
| T2 | +20% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 90% |

Phase 1: max_loss 18% / retrace 8 / 3 consecutive breaches.
Time cuts: hard_timeout 480min, weak_peak_cut 60min @ 2.0, dead_weight_cut 45min — all ENABLED (catch false breakouts early).

## Migrating from v1.1.1

```bash
cd /data/workspace/skills/kestrel-strategy

# Pre-flight: confirm zero open positions before runtime swap
strategy_get_clearinghouse_state for your wallet

# Pull new files (curl above)
rm -f scripts/kestrel-scanner.py            # replaced by kestrel-producer.py

# Reload runtime (safe if no open positions)
openclaw senpi runtime delete <old runtime id>
openclaw senpi runtime create --path runtime.yaml

# Update cron: replace kestrel-scanner.py with kestrel-producer.py
```

State files (`state/cooldowns.json`, `state/trade-counter.json`) are vestigial in v2.0; new producer uses wallet-isolated subdirs (`state/<wallet-hash>/`).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
