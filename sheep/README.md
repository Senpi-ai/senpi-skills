# 🐑 Sheep — Long-Only Triple-EMA-Stacked Trend

**Buy crypto only when it's clearly going up across every timeframe.** Sheep fires LONG only when the fast EMA is above the slow EMA on all three timeframes (15m + 1h + 4h). Never shorts.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Most trend agents pick one timeframe. Sheep insists on three. Fewer trades, but each is "obviously up" to any chart reader. For beginners who want a long-only trend follower without learning what shorts are.

## Key parameters

| Parameter | Default |
|---|---|
| Whitelist | BTC · ETH · SOL · HYPE |
| EMA fast / slow | 9 / 21 |
| Timeframes | 15m + 1h + 4h |
| Min stacked frames | 3 (require all) |
| Direction | LONG only |
| Tick interval | 300s (5 min) |
| Leverage | 3x default, max 5x |
| Margin per slot | 20% of equity |
| Max entries per day | 3 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 12% |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (balanced — trend-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | 72h |
| Time cuts | weak_peak_cut | **6h / 3.0** |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +8/0 · +15/40 · +25/60 · +40/75 · +70/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist). Primary MCP calls: `market_get_asset_data(candle_intervals=["15m","1h","4h"])` + `leaderboard_get_markets`. Pure functions unit-tested in `tests/test_signal.py` (`python3 sheep/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/sheep-producer.py | Long-lived daemon; emits SHEEP_TRIPLE_EMA_LONG signals |
| scripts/sheep_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/sheep-config.json | Operator-tunable defaults (whitelist, EMA periods, thresholds) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Sheep

```bash
mkdir -p /data/workspace/skills/sheep-strategy/{config,scripts,state,references}
for f in scripts/sheep-producer.py scripts/sheep_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/sheep-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/sheep/$f" \
    -o "/data/workspace/skills/sheep-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/sheep-strategy/config/sheep-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export SHEEP_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export SHEEP_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/sheep-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/sheep-strategy/scripts/sheep-producer.py \
  > /tmp/sheep-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320
tail -3 /tmp/sheep-producer.log | jq '._sheep_producer_version, .note // null, .best.spread_4h_pct // null'
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no whitelisted asset has a full 15m+1h+4h EMA-stacked-bullish setup"` (most common in chop)
- `"signals_pushed": 1, "best": { "coin": ..., "stack_score": 3, "spread_4h_pct": ... }`

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent to require multi-timeframe agreement before any trade fires. Long-only by design (no short logic, no LLM-gate short branch). Balanced DSL with `weak_peak_cut` so stalled trends get cut. Taker-true entry, disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
