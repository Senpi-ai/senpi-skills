# 🐟🦅 Osprey — Cross-Venue Lag (Crypto Leader → XYZ Equity Proxy)

When **BTC moves and the crypto stocks haven't caught up yet.** Coinbase (COIN), MicroStrategy (MSTR) and miners trade on Hyperliquid XYZ, priced on a different venue from spot crypto — so a sharp BTC move shows up in those equities late. Osprey measures *how far behind* each proxy is (its catch-up gap) and bets it closes the gap.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

A crypto-correlated equity has a known beta to BTC (COIN ≈ 1.8×, MSTR ≈ 2.5×). When BTC jumps, the expected proxy move is `BTC move × beta`; XYZ pricing can trail spot crypto, so the actual proxy move lags. That difference is a measurable gap. **Distinct from Mantis:** Mantis trades crypto→crypto laggards from `market_get_cross_asset_flows`; Osprey trades the cross-**VENUE** crypto→XYZ-equity lag and self-computes the gap from candles.

## Key parameters

| Parameter | Value |
|---|---|
| Leader | BTC (configurable) |
| Proxies | `xyz:COIN` (β 1.8) · `xyz:MSTR` (β 2.5) — operator-tunable |
| Tick interval | 300s (5 min) |
| Move lookback | 4 × 1h bars |
| Min leader move | 2% |
| Min catch-up gap | 2% (strong 5%) |
| MIN_SCORE (producer) | 4 (out of ~7 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 10x |
| Margin per trade | 15% of equity |
| SM tilt minimum (bonus) | 55% (strong 70%) |
| Max entries per day | 3 |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (let-winners-run — ride the catch-up)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **96h** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

Extends the **Cross-asset lag detector** archetype (#9, Mantis) across venues — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (1h candles for the leader **and** each proxy), `leaderboard_get_markets` (SM). Stateless gap math. Pure functions unit-tested in `tests/test_signal.py` (`python3 osprey/tests/test_signal.py`).

**Tuning:** the proxy list + betas live in `config/osprey-config.json`. Add/remove crypto-proxy XYZ equities as trade.xyz lists them, and calibrate each beta to its sensitivity to the leader. A proxy that returns no candles is skipped gracefully.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/osprey-producer.py | Long-lived daemon; emits OSPREY_CROSS_VENUE_LAG signals |
| scripts/osprey_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/osprey-config.json | Operator-tunable defaults (leader, proxies, betas, thresholds, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Osprey

```bash
mkdir -p /data/workspace/skills/osprey-strategy/{config,scripts,state,references}
for f in scripts/osprey-producer.py scripts/osprey_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/osprey-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/osprey/$f" \
    -o "/data/workspace/skills/osprey-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/osprey-strategy/config/osprey-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export OSPREY_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                           # required (user-scope; needed for leaderboard_get_markets)
export OSPREY_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/osprey-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/osprey-strategy/scripts/osprey-producer.py \
  > /tmp/osprey-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/osprey-producer.log | jq '._osprey_producer_version, .note // null, .best.gap_pct // null'
# Expected: _osprey_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — leader BTC move ... below 2% threshold"` — common (the leader is quiet)
- `"note": "WAITING — BTC moved +X% but no proxy still owes a catch-up gap"` — proxies already tracked
- `"signals_pushed": 1, "best": { "coin": "xyz:COIN", "gap_pct": ..., "direction": "LONG"|"SHORT" }` — a lag fired

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to trade a cross-VENUE lag (crypto spot → XYZ equity), extending Mantis's cross-asset-lag archetype. Let-winners-run DSL class (wide ladder, time-cuts off except a 96h hard_timeout), taker-true entry, no null numeric signal fields, self-computed catch-up gap (no dependence on cross_asset_flows), disown-safe launch, unit-tested signal functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
