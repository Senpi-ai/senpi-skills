# 🦎 Chameleon — Relative-Value / Pairs (Ratio Mean-Reversion)

Trades the **spread between two coins**, not the market. When a pair's price ratio (ETH/BTC, SOL/ETH, SOL/BTC) stretches far from its mean (high z-score), Chameleon bets on the snap-back — taking a directional position on the high-beta leg in the reversion direction.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Correlated majors move as a pack, but their *ratios* oscillate around a mean — and ratios revert far more reliably than outright prices. A 3-sigma ETH/BTC extension is a cleaner edge than guessing ETH's absolute direction. Chameleon measures the stretch (z-score over a lookback) and trades the reversion on the high-beta leg. **Single-position note:** a textbook pairs trade is two legs; the runtime is single-position, so Chameleon takes the directional high-beta leg instead, capturing most of the reversion edge.

## Key parameters

| Parameter | Value |
|---|---|
| Pairs | ETH/BTC (trade ETH), SOL/ETH (trade SOL), SOL/BTC (trade SOL) |
| Tick interval | 300s (5 min) |
| MIN_SCORE (producer) | 4 (out of ~7 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 5x |
| Margin per trade | 15% of equity |
| Ratio lookback | 48 × 1h bars (~2 days) |
| z entry floor | 2.0 |
| z strong | 3.0 |
| SM tilt minimum (bonus) | 55% |
| Max entries per day | 3 |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (mean-reversion — bank the snapback)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 6 |
| Time cuts | hard_timeout | **48h** |
| Time cuts | weak_peak_cut | **120 min / 2%** |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +5/30 · +10/50 · +15/65 · +25/80 · +40/90 |

## Scanner pattern

**Relative-value / pairs** archetype — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (1h candles for *both* legs of each pair), `leaderboard_get_markets` (SM on the leg). Pure functions unit-tested in `tests/test_signal.py` (`python3 chameleon/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/chameleon-producer.py | Long-lived daemon; emits CHAMELEON_RATIO_REVERSION signals |
| scripts/chameleon_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/chameleon-config.json | Operator-tunable defaults (wallet, pairs, z thresholds, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Chameleon

```bash
mkdir -p /data/workspace/skills/chameleon-strategy/{config,scripts,state,references}
for f in scripts/chameleon-producer.py scripts/chameleon_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/chameleon-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/chameleon/$f" \
    -o "/data/workspace/skills/chameleon-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/chameleon-strategy/config/chameleon-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export CHAMELEON_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                              # required
export CHAMELEON_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/chameleon-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/chameleon-strategy/scripts/chameleon-producer.py \
  > /tmp/chameleon-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/chameleon-producer.log | jq '._chameleon_producer_version, .note // null, .best.zscore // null'
# Expected: _chameleon_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no ratio extended past z-threshold with reversion starting"` — common (extensions are rare by design)
- `"signals_pushed": 1, "best": { "coin": ..., "pair": "ETH/BTC", "zscore": ..., "direction": "LONG"|"SHORT" }` — reversion fired

## Changelog

### v1.0.0 (2026-05-22) — initial release

First fleet agent to trade relative value (ratio mean-reversion) rather than a single asset's direction; introduces the relative-value/pairs archetype. Mean-reversion DSL class (tight ladder + time-cuts on), taker-fallback entry, no null numeric signal fields, disown-safe launch, unit-tested signal functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
