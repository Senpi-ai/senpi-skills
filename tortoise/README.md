# 🐢 Tortoise — DCA Scheduler

**Slow and steady wins the race.** Tortoise buys a fixed % of your budget on a strict time cadence (every 24 hours by default) on a small basket (BTC/ETH/SOL by default). No prediction, no timing, no second-guessing — the most-overdue asset wins each tick.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

DCA (dollar-cost averaging) is the most accessible trade in crypto. Every other Senpi agent makes a prediction. Tortoise predicts nothing — it just buys on cadence. For users intimidated by *"which signal, which timeframe, which side,"* Tortoise is the answer.

## Key parameters

| Parameter | Default |
|---|---|
| Assets | BTC · ETH · SOL (configurable) |
| Cadence | every 24 hours per asset |
| Direction | LONG only |
| Tick interval | 1800s (30 min) — slow by design |
| Margin per buy | 8% of equity |
| Leverage | 2x default, max 3x |
| Slots | 3 (one per asset) |
| Per-asset cooldown | 60 min (race-window safety) |
| Daily loss limit | 10% |
| Drawdown halt | 18% |
| Entry order type | FEE_OPTIMIZED_LIMIT (maker-preferred — DCA isn't urgent) |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-preferred) |

## DSL preset (let-winners-run — accumulation-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **30 days** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +25/50 · +50/70 · +100/85 · +200/92 |

## Scanner pattern

A **time-trigger variant** of archetype #4 (Multi-asset whitelist) — see `senpi-trading-runtime/references/producer-patterns.md`. Unlike Bison/Hawk/Salamander, Tortoise doesn't call `market_get_asset_data` for scoring — its "scanner" is a clock. State: persisted DCA-history cache. Pure functions unit-tested in `tests/test_signal.py` (`python3 tortoise/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/tortoise-producer.py | Long-lived daemon; emits TORTOISE_DCA signals |
| scripts/tortoise_config.py | SDK probe + SenpiClient wrapper + DCA-history cache |
| config/tortoise-config.json | Operator-tunable defaults (assets, intervalHours, marginPct) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Tortoise

```bash
mkdir -p /data/workspace/skills/tortoise-strategy/{config,scripts,state,references}
for f in scripts/tortoise-producer.py scripts/tortoise_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/tortoise-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/tortoise/$f" \
    -o "/data/workspace/skills/tortoise-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/tortoise-strategy/config/tortoise-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

Optional: change `assets`, `intervalHours`, `marginPct` to suit your plan.

### Step 4 — Required env vars

```bash
export TORTOISE_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export TORTOISE_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/tortoise-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/tortoise-strategy/scripts/tortoise-producer.py \
  > /tmp/tortoise-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 1820  # wait one full tick
tail -3 /tmp/tortoise-producer.log | jq '._tortoise_producer_version, .note // null, .best.coin // null'
```

A healthy first tick on a brand-new setup fires immediately (never-DCA'd assets are always due) — you should see `"signals_pushed": 1` and `best.coin = "BTC"` (or whatever's first in your `assets` list).

Subsequent ticks within the same interval window output `"WAITING — no asset past its DCA interval"` with `next_due_in_min`. This is correct — cadence is the whole signal.

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent that makes **no price prediction**. Time-trigger DCA on a small whitelist, persisted history cache for cadence tracking, always-LONG, let-winners-run DSL with a 30-day hard_timeout so accumulation compounds. Maker-preferred entry (DCA isn't urgent → save fees). Disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
