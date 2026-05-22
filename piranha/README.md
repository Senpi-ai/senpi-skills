# 🐟 Piranha — Liquidation-Cascade / Forced-Flow Hunter

Multi-asset (BTC/ETH/SOL/HYPE). Rides **forced flow** — when open interest is unwinding fast (positions being liquidated/force-closed) and price is moving violently, the cascade feeds on itself. Piranha reads the order flow underneath price (OI velocity + L2 book depth) to confirm the move is forced, then rides it.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

A liquidation cascade is the cleanest momentum in crypto — it's *forced*, not discretionary. Signature: **OI dropping fast + violent price move + volume spike + a thinning book** on the side price runs into. OI down + price up = short squeeze (ride **LONG**); OI down + price down = long liquidation (ride **SHORT**). Piranha catches the *continuation* of a liquidation event (OI unwind over 15m–1h with a >2% move), the tradeable part, and hands it to a wide DSL with a 24h outer bound.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL, HYPE |
| Tick interval | 180s (3 min) — forced-flow windows are short |
| MIN_SCORE (producer) | 5 (out of ~9 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 5x |
| Margin per trade | 15% of equity |
| OI-unwind floor | 3% drop (1h) |
| OI strong-unwind | 6% drop |
| Price-move floor | 2% (1h) |
| Volume-spike bonus | 50% |
| Max entries per day | 4 |
| Per-asset cooldown | 180 min (3h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true** — forced-flow entry must fill now) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (wide + 24h outer bound)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **24h (enabled)** |
| Time cuts | weak_peak_cut / dead_weight_cut | disabled |
| Phase 2 | T0 → T5 | +10/0 · +20/25 · +30/40 · +50/60 · +75/75 · +100/85 |

## Scanner pattern

**Microstructure / order-flow** archetype — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (candles + `asset_context.openInterest` + `oi_velocity` + L2 `order_book`), `leaderboard_get_markets` (SM). When `oi_velocity` is null, the producer self-computes OI velocity from a persisted last-OI cache (`state/oi-state.json`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/piranha-producer.py | Long-lived daemon; emits PIRANHA_FORCED_FLOW signals |
| scripts/piranha_config.py | SDK probe + SenpiClient wrapper + recent-signals + OI-state cache |
| config/piranha-config.json | Operator-tunable defaults (wallet, universe, OI/move thresholds, sizing) |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Piranha

```bash
mkdir -p /data/workspace/skills/piranha-strategy/{config,scripts,state,references}
for f in scripts/piranha-producer.py scripts/piranha_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/piranha-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/piranha/$f" \
    -o "/data/workspace/skills/piranha-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/piranha-strategy/config/piranha-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export PIRANHA_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                            # required
export PIRANHA_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/piranha-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/piranha-strategy/scripts/piranha-producer.py \
  > /tmp/piranha-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 200  # wait one full tick
tail -3 /tmp/piranha-producer.log | jq '._piranha_producer_version, .note // null, .best.score // null'
# Expected: _piranha_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no forced-flow / liquidation-unwind signature on universe"` — common (cascades are rare by design)
- `"signals_pushed": 1, "best": { "coin": ..., "direction": "LONG"|"SHORT", "score": 5-9 }` — forced-flow fired

Note: the OI-velocity cache warms after one tick per asset, so the first tick may show `WAITING` until a prior OI reading exists.

## Changelog

### v1.0.0 (2026-05-22) — initial release

First fleet agent to trade order-flow microstructure (OI velocity + L2 book depth). Wide "let winners run" ladder + short-horizon 24h hard_timeout, taker-fallback entry, exit timeout 30s, no null numeric signal fields, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
