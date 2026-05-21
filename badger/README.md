# 🦡 Badger — OI-Divergence Breakout Anticipator

Advanced-tier strategy. Multi-asset (BTC/ETH/SOL/HYPE). Takes a price breakout **only when rising open interest confirms it** — new money committing, not a fakeout — with Smart-Money agreement. Wide DSL ladder so a confirmed breakout can run.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Most range breakouts fail. The single best filter for follow-through is **open interest**: a breakout on rising OI means new positions are being opened in the breakout direction (real conviction); a breakout on flat/declining OI is shorts covering or stops triggering (a fakeout). Badger fires only when price breaks the prior 24h range **and** OI is rising **and** Smart Money agrees — then hands the position to a wide DSL ladder. It's the OI-confirmed version of a breakout buyer.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL, HYPE |
| Tick interval | 300s (5 min) |
| MIN_SCORE (producer) | 5 (out of ~10 max) |
| LLM min_confidence | 7 |
| Leverage | 5x default, max 5x |
| Margin per trade | 20% of equity |
| Breakout lookback | 24h |
| OI-rising floor | 2% (1h) |
| OI strong-build bonus | 5% |
| SM tilt minimum | 55% |
| Max entries per day | 3 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (wide, Bison-pattern — let winners run)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 20% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout / weak_peak_cut / dead_weight_cut | **all DISABLED** |
| Phase 2 | T0 | +10% / 0% lock |
| Phase 2 | T1 | +20% / 25% lock |
| Phase 2 | T2 | +30% / 40% lock |
| Phase 2 | T3 | +50% / 60% lock |
| Phase 2 | T4 | +75% / 75% lock |
| Phase 2 | T5 | +100% / 85% lock (apex) |

## Scanner pattern

**Multi-asset whitelist scanner with OI-confirmed breakout scoring** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (candles + `asset_context.openInterest` + `oi_velocity`), `leaderboard_get_markets` (SM direction). When `oi_velocity` is null, the producer self-computes OI velocity from a persisted last-OI cache (`state/oi-state.json`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/badger-producer.py | Long-lived daemon; emits BADGER_OI_BREAKOUT signals |
| scripts/badger_config.py | SDK probe + SenpiClient wrapper + recent-signals + OI-state cache |
| config/badger-config.json | Operator-tunable defaults (wallet, universe, OI/SM thresholds, sizing) |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Badger

```bash
mkdir -p /data/workspace/skills/badger-strategy/{config,scripts,state,references}
for f in scripts/badger-producer.py scripts/badger_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/badger-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/badger/$f" \
    -o "/data/workspace/skills/badger-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/badger-strategy/config/badger-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export BADGER_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                          # required
export BADGER_DECISION_MODEL=<your-preferred-model>  # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/badger-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/badger-strategy/scripts/badger-producer.py \
  > /tmp/badger-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon from the shell job table so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/badger-producer.log | jq '._badger_producer_version, .note // null, .best.score // null'
# Expected: _badger_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no OI-confirmed breakout with SM agreement on universe"` — common
- `"signals_pushed": 1, "best": { "coin": ..., "direction": "LONG"|"SHORT", "score": 5-10 }` — entry fired

Note: the OI-velocity cache warms up after one tick per asset, so the first tick may show `WAITING` even on a clean breakout until a prior OI reading exists.

## Changelog

### v1.0.0 (2026-05-21) — initial release

First fleet agent to gate breakouts on open-interest velocity. Built with the 2026-05-21 HYPE post-mortem guidelines baked in: wide "let winners run" Phase 2 ladder, taker-fallback entries, exit timeout 30s, no null numeric signal fields, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
