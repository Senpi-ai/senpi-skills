# 🐠 Sailfish — Relative-Strength Rotator (Crypto Majors)

**Always hold the strongest major.** Sailfish ranks BTC/ETH/SOL/HYPE by ~2.7-day relative strength every tick and longs the leader. When the held position eventually exits via the DSL trail, the next tick re-evaluates — and *that's* the rotation.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Chameleon trades mean-reversion between paired majors. Sailfish trades the momentum half of relative strength — when one major decisively outperforms, leadership tends to extend. The fleet had no momentum-rotation agent; Sailfish fills it.

## Key parameters

| Parameter | Default |
|---|---|
| Whitelist | BTC · ETH · SOL · HYPE |
| Tick interval | 300s (5 min) |
| RS lookback | 16 × 4h bars (~2.7 days) |
| Min leader RS | 1.0% |
| Leader margin vs runner-up | 1.5pp |
| Direction | LONG only |
| Leverage | 3x default, max 5x |
| Margin per slot | 20% of equity |
| Max entries per day | 2 |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 12% |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (balanced — leadership-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **96h** |
| Time cuts | weak_peak_cut | **8h / 3.0** |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +8/0 · +15/40 · +25/60 · +40/75 · +70/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist) with relative-strength scoring. Primary MCP call: `market_get_asset_data(candle_intervals=["4h"])` per asset. Pure functions unit-tested in `tests/test_signal.py` (`python3 sailfish/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/sailfish-producer.py | Long-lived daemon; emits SAILFISH_RS_LEADER signals |
| scripts/sailfish_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/sailfish-config.json | Operator-tunable defaults (whitelist, RS lookback, gates) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Sailfish

```bash
mkdir -p /data/workspace/skills/sailfish-strategy/{config,scripts,state,references}
for f in scripts/sailfish-producer.py scripts/sailfish_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/sailfish-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/sailfish/$f" \
    -o "/data/workspace/skills/sailfish-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/sailfish-strategy/config/sailfish-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export SAILFISH_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export SAILFISH_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/sailfish-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/sailfish-strategy/scripts/sailfish-producer.py \
  > /tmp/sailfish-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320
tail -3 /tmp/sailfish-producer.log | jq '._sailfish_producer_version, .note // null, .best.leader_rs_pct // null'
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no clear leader (insufficient RS or margin)"` (chop / tight race)
- `"note": "HOLDING — <ASSET> is the current leader and we already own it"`
- `"signals_pushed": 1, "best": { "coin": ..., "leader_rs_pct": ..., "margin_vs_runner_up_pp": ... }`

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent for momentum rotation across the majors. Margin-vs-runner-up gate prevents whipsaw on tight races. DSL-mediated rotation. Taker-true entry, disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
