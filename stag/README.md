# 🦌 Stag — Parabolic-Run Hunter

**Ride HYPE-class runs without getting chopped on the gyrations.** Stag is the entry-side pair for the new `parabolic_runner` DSL preset. Strict 5-gate entry filter, then the widest DSL in the catalog holds the position through the 5–8% intraday pullbacks that would kill a normal trend trail.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

HYPE 2026-05: $40 → $65 over 16 days, with at least 5 distinct 5–10% intraday gyrations through the middle. Standard DSL trails would chop out on any of them. Stag's answer: **enter only when there's a real parabolic setup, then accept the gyrations.**

**Trade-off named honestly:** the `parabolic_runner` preset bleeds in chop. If the parabolic doesn't materialize, Stag gives back more than `let_winners_run` would. The 5-gate filter is strict for exactly that reason — the only way the wide DSL pays is if you've correctly identified the setup.

## Key parameters

| Parameter | Default |
|---|---|
| Whitelist | BTC · ETH · SOL · HYPE (configurable; single-asset is also a common deploy) |
| Tick interval | 600s (10 min) — parabolic conditions don't appear in 5 min |
| 200-bar 4h SMA gate | required |
| 7d high freshness gate | within last 48h (12 × 4h bars) |
| 7d trend gate | ≥ 25% (the "parabolic" threshold) |
| Volume surge gate | recent 24h ≥ 1.5× trailing 7d |
| Acceleration gate | 4d move ≥ 7d move ÷ 2 |
| SM LONG gate | ≥ 60% |
| Direction | LONG only |
| Leverage | 4x default, max 5x |
| Margin per slot | 25% of equity |
| Max entries per day | **1** (parabolic setups are rare) |
| Per-asset cooldown | **1440 min (24h)** — don't re-enter after a bad take |
| Daily loss limit | 18% |
| Drawdown halt | 25% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (`parabolic_runner` — widest in the catalog)

| Phase | Component | Setting | vs `let_winners_run` |
|---|---|---|---|
| Phase 1 | max_loss_pct | **25%** | 20% |
| Phase 1 | retrace_threshold | **18** | 12 |
| Phase 1 | consecutive_breaches_required | **2** | 1 |
| Time cuts | hard_timeout | **14d** | (off) |
| Time cuts | weak_peak_cut | disabled | disabled |
| Time cuts | dead_weight_cut | disabled | disabled |
| Phase 2 | T0 → T4 | +15/0 · +30/30 · +60/55 · +120/72 · +250/85 | +10/0 · +20/25 · +30/40 · +50/60 · +100/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist) with strict 5-gate parabolic filtering. Primary MCP calls: `market_get_asset_data(candle_intervals=["4h"])` (needs ≥ 200 candles for the 200-bar SMA), `leaderboard_get_markets`. Pure functions unit-tested in `tests/test_signal.py` (`python3 stag/tests/test_signal.py`).

## Operator workflow

The point isn't just the code — it's the workflow:

1. You notice a parabolic setting up on one asset (e.g. HYPE +30% over 7d, volume surging).
2. Deploy Stag with that single asset: `whitelist: ["HYPE"]`, 25% margin, 4x leverage.
3. Stag fires when its gates trip — could be same hour, could be next day.
4. The wide DSL holds the position through the full run, including consolidations.
5. When the run finishes (T4 tier ratchets in, or `max_loss_pct: 25` trips on a real reversal), Stag closes.
6. Pause Stag until the next regime.

Running a 4-asset basket as a passive scan also works, but the strict gates mean most ticks are silent.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, `parabolic_runner` DSL, `risk.guard_rails`) |
| scripts/stag-producer.py | Long-lived daemon; emits STAG_PARABOLIC_RUNNER signals |
| scripts/stag_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/stag-config.json | Operator-tunable defaults (whitelist, all 5 gate thresholds, sizing) |
| tests/test_signal.py | 18 unit tests covering all gates |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Stag

```bash
mkdir -p /data/workspace/skills/stag-strategy/{config,scripts,state,references}
for f in scripts/stag-producer.py scripts/stag_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/stag-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/stag/$f" \
    -o "/data/workspace/skills/stag-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID — and optionally a single asset

Edit `/data/workspace/skills/stag-strategy/config/stag-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "whitelist": ["HYPE"]
}
```

For operator-driven single-asset deploys, narrow `whitelist` to that asset.

### Step 4 — Required env vars

```bash
export STAG_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                         # required (user-scope; needed for leaderboard_get_markets)
export STAG_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/stag-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/stag-strategy/scripts/stag-producer.py \
  > /tmp/stag-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 620  # wait one full tick
tail -3 /tmp/stag-producer.log | jq '._stag_producer_version, .note // null, .best.trend_pct // null'
```

A healthy first tick usually outputs:
- `"note": "WAITING — no asset cleared all five parabolic gates"` — by far the most common state (intentional)
- `"signals_pushed": 1, "best": { "coin": "HYPE", "trend_pct": 28.5, "vol_ratio": 1.8, ... }` when a setup is in place

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent paired with the new `parabolic_runner` DSL preset. Strict 5-gate filter (structural trend + 25% strength + 1.5× volume surge + acceleration + SM ≥60% LONG). LONG only. Aggressive risk tier with 1 max entry/day + 24h per-asset cooldown after a bad take. Taker-true entry, disown-safe launch, 18-test pure-function suite covering all gates.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
