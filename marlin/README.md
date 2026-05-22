# 🐟 Marlin — Order-Book Imbalance Momentum

Multi-asset (BTC/ETH/SOL/HYPE). Reads the L2 order book — when resting depth is lopsided (bids ≫ asks or the reverse), that's directional pressure. Marlin enters in that direction **only when 15m momentum and Smart Money agree**, then holds the move with a wide DSL. It times the entry on the book; it does **not** scalp.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Order-book imbalance is a real microstructure edge, but trading it as a per-tick scalp just bleeds fees. Marlin uses it as **entry timing** on a momentum thesis: the book says which side has pressure now; momentum + SM confirm the move; then it holds with a wide stop ladder and lets it work. Bid-heavy + 15m up + SM long → LONG; ask-heavy + 15m down + SM short → SHORT.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL, HYPE |
| Tick interval | 180s (3 min) |
| MIN_SCORE (producer) | 5 (out of ~9 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 5x |
| Margin per trade | 15% of equity |
| Book levels summed | top 10 per side |
| Imbalance floor | 1.5× (bid/ask depth) |
| Strong-imbalance bonus | 2.5× |
| 15m momentum floor | 0.1% |
| SM tilt minimum | 55% |
| Max entries per day | 4 |
| Per-asset cooldown | 180 min (3h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
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

**Microstructure / order-flow** archetype (with Piranha) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (L2 `order_book` + candles), `leaderboard_get_markets` (SM). Pure signal functions are unit-tested in `tests/test_signal.py` (`python3 marlin/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/marlin-producer.py | Long-lived daemon; emits MARLIN_BOOK_IMBALANCE signals |
| scripts/marlin_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/marlin-config.json | Operator-tunable defaults (wallet, universe, imbalance/SM thresholds, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Marlin

```bash
mkdir -p /data/workspace/skills/marlin-strategy/{config,scripts,state,references}
for f in scripts/marlin-producer.py scripts/marlin_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/marlin-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/marlin/$f" \
    -o "/data/workspace/skills/marlin-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/marlin-strategy/config/marlin-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export MARLIN_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                            # required
export MARLIN_DECISION_MODEL=<your-preferred-model>    # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/marlin-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/marlin-strategy/scripts/marlin-producer.py \
  > /tmp/marlin-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 200  # wait one full tick
tail -3 /tmp/marlin-producer.log | jq '._marlin_producer_version, .note // null, .best.score // null'
# Expected: _marlin_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no book-imbalance + momentum + SM alignment on universe"` — common
- `"signals_pushed": 1, "best": { "coin": ..., "direction": "LONG"|"SHORT", "score": 5-9 }` — entry fired

## Changelog

### v1.0.0 (2026-05-22) — initial release

Second agent in the microstructure/order-flow family (with Piranha). Order-book imbalance as entry-timing on a momentum thesis — deliberately not a scalper. Wide "let winners run" ladder + 24h hard_timeout, taker-fallback entry, exit timeout 30s, no null numeric signal fields, disown-safe launch, unit-tested signal functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
