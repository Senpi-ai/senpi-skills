# 🐺 Coyote — Regime Classifier / Meta-Router

**The agent that asks "what kind of market are we in?" before anything else.** Coyote watches macro conditions and classifies the market into TREND_UP / TREND_DOWN / CHOP. Takes LONG BTC in TREND_UP, SHORT BTC in TREND_DOWN, stays out in CHOP. Publishes the regime view on every tick.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Every Senpi agent re-implements its own version of "should I be active right now?" — but the macro answer is shared across the fleet. Coyote centralizes it. **NEW archetype #16: Regime classifier / meta-router** — its classification is emitted on every tick output for both operators (today) and future regime-subscription runtime features (soon).

## Key parameters

| Parameter | Default |
|---|---|
| BTC asset | `BTC` |
| Dispersion universe | BTC · ETH · SOL · HYPE |
| Tick interval | 900s (15 min — regimes don't flip in 5 min) |
| Trend lookback | 42 × 4h bars (7 days) |
| Vol lookback | 42 × 4h bars (7 days) |
| `trendUpThresholdPct` | 5.0 |
| `trendDownThresholdPct` | 5.0 |
| `maxVolForTrendPct` | 80% (TREND_UP requires vol ≤ this) |
| `minVolForCrashPct` | 60% (TREND_DOWN requires vol ≥ this) |
| Direction | LONG in TREND_UP, SHORT in TREND_DOWN, no trade in CHOP |
| Leverage | 3x default, max 5x |
| Margin per slot | 25% of equity |
| Max entries per day | 2 (regimes are persistent) |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker-true) |

## DSL preset (`balanced`)

Standard balanced — regimes hold for days; Coyote's positional bet rides them.

## Scanner pattern

NEW archetype #16: **Regime classifier / meta-router**. Primary MCP call: `market_get_asset_data` per universe asset (just for close prices). Pure functions unit-tested in `tests/test_signal.py` (`python3 coyote/tests/test_signal.py` — 14 tests).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, balanced DSL, `risk.guard_rails`) |
| scripts/coyote-producer.py | Long-lived daemon; classifies regime + emits COYOTE_REGIME |
| scripts/coyote_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/coyote-config.json | Operator-tunable defaults (regime thresholds) |
| tests/test_signal.py | Unit tests for the regime-classification logic |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Coyote

```bash
mkdir -p /data/workspace/skills/coyote-strategy/{config,scripts,state,references}
for f in scripts/coyote-producer.py scripts/coyote_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/coyote-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/coyote/$f" \
    -o "/data/workspace/skills/coyote-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/coyote-strategy/config/coyote-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export COYOTE_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export COYOTE_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/coyote-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/coyote-strategy/scripts/coyote-producer.py \
  > /tmp/coyote-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 920  # wait one full tick
tail -3 /tmp/coyote-producer.log | jq '._coyote_producer_version, .regime, .btc_7d_move_pct'
```

You should see the regime classification on every tick — even when there's no trade. Example outputs:

- `{"regime": "CHOP", "btc_7d_move_pct": 1.2, "realized_vol_pct": 45.0}` — quiet market, no trade
- `{"regime": "TREND_UP", "best": {"coin": "BTC", "direction": "LONG", ...}}` — uptrend confirmed, BTC LONG fired
- `{"regime": "UNKNOWN", "btc_7d_move_pct": null}` — data unavailable (transient)

## Reading Coyote's regime view in your other agents (operator workflow)

For now (until regime-subscription lands in the runtime), the workflow is manual:

```bash
# Quick check from the command line:
tail -1 /tmp/coyote-producer.log | jq '.regime'

# Or watch it live:
tail -f /tmp/coyote-producer.log | jq '.regime'
```

If Coyote says **TREND_UP**, your trend-followers (Beaver, Wolverine, Sheep) are most likely to pay. If **TREND_DOWN**, your contrarian agents are at higher risk (the crowd was on the wrong side). If **CHOP**, your faders (Egret, Owl) are in their happy place. Use the read to decide which agents to deploy or pause.

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent built around **macro-regime classification as a first-class capability**. Introduces archetype #16 (regime classifier / meta-router). Publishes regime on every tick. 14/14 unit tests covering all pure functions. Taker-true entry, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
