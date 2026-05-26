# 🦦 Meerkat — Momentum-Event Sniper

**Stand sentry, pop the instant something moves.** Meerkat watches the Senpi momentum-event feed and snipes the freshest, highest-tier momentum events the instant they fire — entering in the move's direction before it's broadly known.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The momentum-event feed (`leaderboard_get_momentum_events`) surfaces assets that just made a sharp move in the 4h rolling window — the earliest, cleanest read on *fresh* momentum. The edge decays fast, so Meerkat's job is **speed + selectivity**: only the strongest tiers, only while they're fresh. **Distinct from the rank-jump strikers** (Jaguar/Orca/Roach, which score a leaderboard universe) — Meerkat reads the event feed directly.

## Key parameters

| Parameter | Value |
|---|---|
| Source | `leaderboard_get_momentum_events` (4h rolling-window events) |
| Tick interval | 120s (2 min) — momentum is time-sensitive |
| Tier classification | 3 ≥ 10% · 2 ≥ 5% · else 1 (by `|momentum|`) |
| Min tier (gate) | 2 (set 3 for a pure tier-3 sniper) |
| Freshness gate | event age ≤ 30 min |
| MIN_SCORE (producer) | 4 (out of ~7 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 10x |
| Margin per trade | 15% of equity |
| SM tilt minimum (bonus) | 55% (strong 70%) |
| Max entries per day | 4 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (let-winners-run — momentum-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **36h (short — momentum is time-bounded)** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

A focused, event-driven variant of the **Striker / rank-jump detector** archetype (#6) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `leaderboard_get_momentum_events` (defensive multi-key unwrap), `leaderboard_get_markets` (SM), `market_get_asset_data` (volume). Pure functions unit-tested in `tests/test_signal.py` (`python3 meerkat/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/meerkat-producer.py | Long-lived daemon; emits MEERKAT_MOMENTUM_EVENT signals |
| scripts/meerkat_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/meerkat-config.json | Operator-tunable defaults (tiers, freshness, thresholds, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Meerkat

```bash
mkdir -p /data/workspace/skills/meerkat-strategy/{config,scripts,state,references}
for f in scripts/meerkat-producer.py scripts/meerkat_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/meerkat-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/meerkat/$f" \
    -o "/data/workspace/skills/meerkat-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/meerkat-strategy/config/meerkat-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export MEERKAT_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                            # required (user-scope; needed for leaderboard_get_momentum_events)
export MEERKAT_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/meerkat-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/meerkat-strategy/scripts/meerkat-producer.py \
  > /tmp/meerkat-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 130  # wait one full tick
tail -3 /tmp/meerkat-producer.log | jq '._meerkat_producer_version, .note // null, .best.tier // null'
# Expected: _meerkat_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no momentum events in the feed"` — quiet market
- `"note": "WAITING — no fresh tier>=minTier momentum event cleared minScore"` — events exist but none strong/fresh enough
- `"signals_pushed": 1, "best": { "coin": ..., "tier": 3, "direction": "LONG"|"SHORT" }` — a snipe fired

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent driven directly off the momentum-event feed — a tier + freshness sniper, contrasting the universe-scanning rank-jump strikers. Let-winners-run DSL class (wide ladder, short 36h hard_timeout since momentum is time-bounded), taker-true entry, no null numeric signal fields, defensive multi-key shape unwrapping, disown-safe launch, unit-tested signal functions (including epoch-seconds-vs-milliseconds handling).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
