# 🐨 Koala — Set-and-Forget Trail HODL

**The simplest possible Senpi agent.** Pick an asset. Fire LONG once. Hold with an ultra-wide DSL trail. Done.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

For users whose entire trading thesis is *"I want to own BTC and have a safety net, and I don't want anything else."* No scoring, no scheduling, no multi-timeframe analysis. One asset, one entry, one stop.

## Key parameters

| Parameter | Default |
|---|---|
| Asset | BTC (configurable: ETH/SOL/HYPE/anything) |
| Direction | LONG only |
| fireOnceMode | **true** (lifetime one-shot) |
| reEntryCooldownHours | 168 (7d, only if fireOnceMode=false) |
| Margin per position | 50% of equity |
| Leverage | 2x default, max 3x |
| Tick interval | 1800s (30 min) — no urgency |
| Max entries per day | 1 |
| Daily loss limit | 20% (permissive — Koala rides drawdowns) |
| Drawdown halt | 35% |
| Entry order type | FEE_OPTIMIZED_LIMIT (**maker-preferred** — save fees, no urgency) |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-preferred) |

## DSL preset (ultra-wide custom — the widest in any Senpi agent)

| Component | Setting | vs `parabolic_runner` |
|---|---|---|
| `max_loss_pct` | **30%** | 25% |
| `retrace_threshold` | **25** | 18 |
| `consecutive_breaches_required` | **3** | 2 |
| `hard_timeout` | **90d** | 14d |
| `weak_peak_cut` | disabled | disabled |
| `dead_weight_cut` | disabled | disabled |
| Phase 2 T0 → T4 | +20/0 · +50/30 · +100/50 · +200/70 · +500/85 | +15/0 · +30/30 · +60/55 · +120/72 · +250/85 |

This preset is **not** in `dsl-presets.yaml` — it's Koala-specific. The point is to *not* cut on normal corrections; it only releases on catastrophic reversal or after 3 months.

## Scanner pattern

A **state-trigger variant** of archetype #2 (Single-asset alpha hunter). No `market_get_asset_data` call — Koala's "scanner" is a check against the persisted state file. Pure functions unit-tested in `tests/test_signal.py` (`python3 koala/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, ultra-wide DSL, `risk.guard_rails`) |
| scripts/koala-producer.py | Long-lived daemon; emits a single KOALA_HODL signal |
| scripts/koala_config.py | SDK probe + SenpiClient wrapper + entry-state cache |
| config/koala-config.json | Operator-tunable defaults (asset, mode, sizing) |
| tests/test_signal.py | Unit tests for the entry-decision logic |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Koala

```bash
mkdir -p /data/workspace/skills/koala-strategy/{config,scripts,state,references}
for f in scripts/koala-producer.py scripts/koala_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/koala-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/koala/$f" \
    -o "/data/workspace/skills/koala-strategy/$f"
done
```

### Step 3 — Configure (the only meaningful choice is `asset`)

Edit `/data/workspace/skills/koala-strategy/config/koala-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "asset": "BTC"
}
```

### Step 4 — Required env vars

```bash
export KOALA_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...
export KOALA_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/koala-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/koala-strategy/scripts/koala-producer.py \
  > /tmp/koala-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 1820  # wait one full tick
tail -3 /tmp/koala-producer.log | jq '._koala_producer_version, .note // null, .best.coin // null'
```

A healthy first tick fires immediately (state file doesn't exist yet, fire-once condition met). You'll see `"signals_pushed": 1, "best": { "coin": "BTC", ... }`. After that, every subsequent tick will output `"HOLDING — BTC is currently in the position; DSL owns exits"` until either the DSL exits (90d at the outer bound) or you tear it down.

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent with **no price-side scoring at all**. State-file-driven single-shot entry, ultra-wide custom DSL (max_loss 30%, retrace 25, 90d hard_timeout, super-late Phase 2 ladder), fire-once mode by default. Maker-preferred entry. Disown-safe launch, 8/8 unit tests covering both fire-once and re-entry-cooldown branches.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
