# 🦫 Beaver — BTC Trend Follower (SM-confirmed)

Onboarding-tier strategy. Single asset (BTC). Long OR short based on the confluence of 4h trend structure + Senpi Smart-Money leaderboard direction. Wide DSL ladder so winning trends can ride.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Beaver is the default first-trade strategy for new Senpi users. It answers one question — "is BTC trending?" — by looking at the 4h candle structure (higher-lows = bullish, lower-highs = bearish) and the Senpi Smart-Money leaderboard (do top traders agree?). If the 4h trend exists and Smart Money is positioned in the same direction with at least 60% concentration, Beaver opens a single conviction position and hands it to the DSL.

There are NO funding-rate filters, NO multi-asset whitelist tuning, NO XYZ universe to manage. One asset, one direction gate, one exit mechanism. Designed to be operable by someone who just learned what a perp is.

## Key parameters

| Parameter | Value |
|---|---|
| Asset | BTC (single-asset) |
| Tick interval | 300s (5 min) |
| MIN_SCORE (producer) | 5 (out of ~9 max) |
| LLM min_confidence | 7 |
| Leverage | 5x default, max 5x |
| Margin per trade | 25% of equity |
| Max positions | 1 |
| Max entries per day | 2 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| SM tilt minimum | 60% |
| SM strong-tilt threshold | 70% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: false) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |
| Recent-signals dedup | 240s TTL |

## DSL preset (wide, Bison-pattern)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 20% |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **DISABLED** |
| Time cuts | weak_peak_cut | **DISABLED** |
| Time cuts | dead_weight_cut | **DISABLED** |
| Phase 2 | T0 | +10% / 0% lock |
| Phase 2 | T1 | +20% / 25% lock |
| Phase 2 | T2 | +30% / 40% lock |
| Phase 2 | T3 | +50% / 60% lock |
| Phase 2 | T4 | +75% / 75% lock |
| Phase 2 | T5 | +100% / 85% lock (apex) |

## Scanner pattern

This strategy uses the **Single-asset trend follower (Kodiak family)** scanner pattern with a Smart-Money direction gate — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `market_get_asset_data` (BTC candles), `leaderboard_get_markets` (SM direction).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/beaver-producer.py | Long-lived daemon; emits BEAVER_BTC_TREND signals |
| scripts/beaver_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/beaver-config.json | Operator-tunable defaults (wallet, asset, minScore, sizing) |

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

The senpi-trading-runtime plugin won't bind its API port (`127.0.0.1:8787`) unless `plugins.entries.runtime` is present in `/data/.openclaw/openclaw.json`. Without that block the plugin logs `No plugin config found — skipping registration` and the producer daemon's `signal_post` calls fail with `[Errno 111] Connection refused`. Confirm or add:

```json
{
  "plugins": {
    "entries": {
      "runtime": {
        "enabled": true,
        "config": {
          "stateDir": "/data/.openclaw/senpi-state",
          "apiKey": "<your SENPI_AUTH_TOKEN>",
          "autoUpdate": { "enabled": false }
        }
      }
    }
  }
}
```

Restart the gateway after editing so the plugin re-registers:

```bash
openclaw gateway restart
sleep 10
curl -s -m 5 http://127.0.0.1:8787/state | head -c 200
# Expected: a JSON response with "success":true,"data":{"runtimes":[...]}
```

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Beaver

```bash
mkdir -p /data/workspace/skills/beaver-strategy/{config,scripts,state,references}
for f in scripts/beaver-producer.py scripts/beaver_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/beaver-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/beaver/$f" \
    -o "/data/workspace/skills/beaver-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/beaver-strategy/config/beaver-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

This is the canonical source of truth — producer reads from here on every tick.

### Step 4 — Required env vars

```bash
export BEAVER_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                              # required
export BEAVER_DECISION_MODEL=<your-preferred-model>      # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/beaver-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/beaver-strategy/scripts/beaver-producer.py \
  > /tmp/beaver-producer.log 2>&1 &
disown
```

After first launch, manage the daemon via the `senpi-helpers` CLI: `senpi-helpers list`, `senpi-helpers health <name>`, `senpi-helpers restart <name>`.

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/beaver-producer.log | jq '._beaver_producer_version, .note // null, .best.score // null'
# Expected: _beaver_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no qualifying setup (4h trend + SM gate not aligned)"` — common; BTC isn't in a clean trend
- `"signals_pushed": 1, "best": { "coin": "BTC", "direction": "LONG"|"SHORT", "score": 5-9 }` — entry signal fired

## Changelog

### v1.0.0 (2026-05-20) — initial release

First skill in the onboarding-tier trio (Beaver = BTC, Heron = ETH, Hummingbird = HYPE). Single asset, simple 5-component scoring, Smart-Money direction gate, wide Bison-pattern Phase 2 ladder. Designed as the default "I'm new to Senpi, where do I start?" strategy.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
