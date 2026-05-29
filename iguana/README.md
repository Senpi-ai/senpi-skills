# 🦎 Iguana — XYZ Macro Index Trend

**Stock-market exposure on Hyperliquid without picking stocks.** Iguana trend-follows `xyz:SP500` + `xyz:XYZ100`. Two assets, one decision per tick. Closest thing to an index-fund equivalent, but 24/7.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Bobcat picks 12 stocks. Bald-Eagle / Kestrel do macro multi-asset contrarian fade. Dire is single-commodity. Lemur / Falcon handle pre-IPO. None give the *simplest* equity exposure — *"I just want the broad market."* Iguana is that.

## Key parameters

| Parameter | Default |
|---|---|
| Whitelist | `xyz:SP500` · `xyz:XYZ100` |
| Tick interval | 300s (5 min) |
| Trend lookback | 24 × 4h bars (4 days) |
| Min trend (gate) | 1.5% |
| Strong trend (bonus) | 4% |
| Leverage | 3x default, max 5x |
| Margin per slot | 20% of equity |
| Max entries per day | 2 |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 12% |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (balanced — XYZ-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **48h (weekend gap)** |
| Time cuts | weak_peak_cut | **8h / 3.0** |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +5/0 · +10/40 · +18/60 · +30/75 · +50/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist, XYZ subset). Primary MCP call: `market_get_asset_data(candle_intervals=["4h"])` per index. Pure functions unit-tested in `tests/test_signal.py` (`python3 iguana/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/iguana-producer.py | Long-lived daemon; emits IGUANA_INDEX_TREND signals |
| scripts/iguana_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/iguana-config.json | Operator-tunable defaults (whitelist, trend thresholds) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Iguana

```bash
mkdir -p /data/workspace/skills/iguana-strategy/{config,scripts,state,references}
for f in scripts/iguana-producer.py scripts/iguana_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/iguana-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/iguana/$f" \
    -o "/data/workspace/skills/iguana-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/iguana-strategy/config/iguana-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export IGUANA_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export IGUANA_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/iguana-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/iguana-strategy/scripts/iguana-producer.py \
  > /tmp/iguana-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320
tail -3 /tmp/iguana-producer.log | jq '._iguana_producer_version, .note // null, .best.trend_pct // null'
```

A healthy first tick usually outputs:
- `"note": "WAITING — neither index has a 4d move past 1.5%"` (chop)
- `"signals_pushed": 1, "best": { "coin": "xyz:SP500", "trend_pct": ..., "direction": "LONG"|"SHORT" }`

## Changelog

### v1.0.0 (2026-05-28) — initial release

The simplest possible XYZ exposure in the fleet. No stock-picking; just broad indices in trend direction. Balanced DSL + 48h hard_timeout for weekend gap. Taker-true entry, disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
