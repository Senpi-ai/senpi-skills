# 🦅 Falcon — Conversion-Event Momentum (XYZ Pre-IPO → Equity)

Trades the **moment a pre-IPO name goes public.** When a Pre-IPO Perpetual (IPOP) converts to a standard equity perp, the funding rate jumps ~100x, the leverage cap lifts, and the trade.xyz Discovery-Bounds price throttle is removed — opening free price discovery. Falcon detects that exact transition and rides the post-conversion momentum.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

A pre-IPO perp is deliberately dampened — Discovery Bounds throttle how fast it can move. Conversion removes those guardrails all at once, and the first hours-to-days are pure price discovery as a real spot reference arrives and leverage opens up. That regime change is the edge, and it is detectable from the instrument's funding signature. **Distinct from Lemur:** Lemur trades the IPOP basket *while it is still an IPOP*; Falcon sits out the pre-listing phase and only fires *around the conversion*.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | All `xyz:` instruments (auto-classified IPOP vs STANDARD) |
| Tick interval | 600s (10 min) — conversions are rare; a window lasts days |
| IPOP signature | `\|funding\| <= 1e-7` AND `max_leverage <= 5` |
| Conversion window | 72h after the IPOP→STANDARD flip |
| Momentum lookback | 6 × 1h bars |
| Min momentum | 3% (strong 8%) |
| MIN_SCORE (producer) | 5 (out of ~8 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 10x (auto-capped to the instrument) |
| Margin per trade | 15% of equity |
| SM tilt minimum (bonus) | 55% (strong 70%) |
| Max entries per day | 2 |
| Per-asset cooldown | 720 min (12h) |
| Daily loss limit | 15% |
| Drawdown halt | 25% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (let-winners-run — ride the discovery)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 20% |
| Phase 1 | retrace_threshold | 12 |
| Time cuts | hard_timeout | **7d** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +20/40 · +35/60 · +60/75 · +100/85 |

## Scanner pattern

Extends the **Single-asset XYZ specialist** archetype with an event-detection layer — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_list_instruments(dex="xyz")` (classify + detect flips every tick — the producer signature), `market_get_asset_data` (1h candles for momentum/volume), `leaderboard_get_markets` (SM). Carries a class-state cache + a conversion-window cache (Badger/Piranha pattern). Pure functions unit-tested in `tests/test_signal.py` (`python3 falcon/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/falcon-producer.py | Long-lived daemon; emits FALCON_CONVERSION_MOMENTUM signals |
| scripts/falcon_config.py | SDK probe + SenpiClient wrapper + recent-signals / class-state / conversion caches |
| config/falcon-config.json | Operator-tunable defaults (wallet, signature thresholds, window, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Falcon

```bash
mkdir -p /data/workspace/skills/falcon-strategy/{config,scripts,state,references}
for f in scripts/falcon-producer.py scripts/falcon_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/falcon-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/falcon/$f" \
    -o "/data/workspace/skills/falcon-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/falcon-strategy/config/falcon-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export FALCON_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                           # required (user-scope; needed for leaderboard_get_markets)
export FALCON_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/falcon-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/falcon-strategy/scripts/falcon-producer.py \
  > /tmp/falcon-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 620  # wait one full tick
tail -3 /tmp/falcon-producer.log | jq '._falcon_producer_version, .note // null, .best.coin // null'
# Expected: _falcon_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no IPOP→equity conversion inside the eligibility window"` — the common steady state (conversions are rare; the first tick only seeds the class cache)
- `"note": "WAITING — conversion(s) in window but no confirmed post-conversion momentum"` — a conversion happened, momentum hasn't developed yet
- `"signals_pushed": 1, "best": { "coin": "xyz:...", "momentum_pct": ..., "direction": "LONG"|"SHORT" }` — a conversion fired

**Note on first run:** Falcon detects flips by comparing each tick against the prior tick. The very first tick only *seeds* the class cache, so it can never report a conversion — that is expected. Detection begins on the second tick onward.

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to trade an instrument-lifecycle event (the IPOP→equity conversion) rather than a price/flow signal. Let-winners-run DSL class (wide ladder, time-cuts off except a 7d hard_timeout), taker-true entry, no null numeric signal fields, Badger/Piranha state-cache pattern, disown-safe launch, unit-tested signal functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
