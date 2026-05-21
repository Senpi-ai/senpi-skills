# 🐦 Egret — Smart-Money Divergence Fader

Multi-asset (BTC/ETH/SOL/HYPE). When the Smart-Money crowd is extremely concentrated one way but price won't confirm it, the crowded side is exhausted — Egret fades the unwind. Tight DSL, maker-only entry (the mean-reversion profile, opposite the fleet's momentum agents).

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

SM concentration is usually a *with-the-trend* confirmation. At an extreme (70–85% of top traders on one side) it flips contrarian — **if price refuses to follow.** A maximally-long crowd while price stalls has no one left to buy; the unwind is the edge. Egret fades extreme crowding that price is diverging from, with RSI exhaustion as confirmation, and banks the bounded snapback with a tight DSL.

## Key parameters

| Parameter | Value |
|---|---|
| Universe | BTC, ETH, SOL, HYPE |
| Tick interval | 300s (5 min) |
| MIN_SCORE (producer) | 5 (out of ~9 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 5x |
| Margin per trade | 15% of equity |
| SM crowding floor | 70% |
| SM ultra-crowded | 80% |
| Divergence lookback | 4h |
| Max entries per day | 3 |
| Per-asset cooldown | 360 min (6h) |
| Daily loss limit | 12% |
| Drawdown halt | 18% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **false** — fader, maker-only) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (tight — bank the snapback)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 6 |
| Time cuts | hard_timeout | **48h (enabled)** |
| Time cuts | weak_peak_cut | **120min / 2% (enabled)** |
| Phase 2 | T0 | +5% / 30% lock |
| Phase 2 | T1 | +10% / 50% lock |
| Phase 2 | T2 | +15% / 65% lock |
| Phase 2 | T3 | +25% / 80% lock |
| Phase 2 | T4 | +40% / 90% lock |

This is the **inverse** of the momentum agents' wide ladder — a fade is a bounded unwind, not a trend, so it banks fast.

## Scanner pattern

**Contrarian fader** archetype (same family as Owl / Bald-eagle / Pangolin) with an SM-vs-price divergence trigger — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `leaderboard_get_markets` (SM concentration), `market_get_asset_data` (price momentum + RSI).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, tight DSL preset, `risk.guard_rails`) |
| scripts/egret-producer.py | Long-lived daemon; emits EGRET_SM_FADE signals |
| scripts/egret_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/egret-config.json | Operator-tunable defaults (wallet, universe, crowding/RSI thresholds, sizing) |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Egret

```bash
mkdir -p /data/workspace/skills/egret-strategy/{config,scripts,state,references}
for f in scripts/egret-producer.py scripts/egret_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/egret-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/egret/$f" \
    -o "/data/workspace/skills/egret-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/egret-strategy/config/egret-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export EGRET_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                         # required
export EGRET_DECISION_MODEL=<your-preferred-model>  # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/egret-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/egret-strategy/scripts/egret-producer.py \
  > /tmp/egret-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/egret-producer.log | jq '._egret_producer_version, .note // null, .best.score // null'
# Expected: _egret_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no exhausted-crowd divergence on universe"` — common (extreme crowding + divergence is rare by design)
- `"signals_pushed": 1, "best": { "coin": ..., "direction": "LONG"|"SHORT", "score": 5-9 }` — fade fired

## Changelog

### v1.0.0 (2026-05-21) — initial release

Adds an SM-divergence trigger to the contrarian-fader archetype. Tight ladder + maker-only entry + enabled time-cuts (the fader profile), the deliberate inverse of the momentum agents' wide ladder + taker-fallback.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
