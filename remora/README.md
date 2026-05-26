# 🐟 Remora — Whale Single-Position Mirror

**Ride the whales you choose.** Remora attaches to a small, hand-picked set of whale traders, finds each whale's biggest-conviction bet, and mirrors the strongest one — with a consensus boost when several whales pile into the same trade.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The broad trader-followers (Raptor, Jackal, Spider) scan a *leaderboard universe* and synthesize a pick. Remora is the opposite: **you name the whales.** It mirrors their single highest-conviction position, so your exposure tracks specific traders you trust, and it leans hardest when those whales *agree*. **Distinct from Spider/Jackal/Raptor** (universe scanners) — Remora is a focused mirror of a set you control.

## Key parameters

| Parameter | Value |
|---|---|
| Whale set | **operator-supplied** (`config.whales`, no default) |
| Tick interval | 600s (10 min) — whale positions change slowly |
| Conviction metric | largest-notional open position per whale |
| Min notional (dust filter) | $5,000 |
| Consensus bonus | 2 whales → +2 · 3+ → +3 |
| Whale-quality bonus | ELITE / RELIABLE tier → +1 |
| MIN_SCORE (producer) | 4 (out of ~7 max) |
| LLM min_confidence | 7 |
| Leverage | 4x default, max 10x |
| Margin per trade | 15% of equity |
| Max entries per day | 3 |
| Per-asset cooldown | 480 min (8h) |
| Daily loss limit | 15% |
| Drawdown halt | 22% |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: true) |

## DSL preset (let-winners-run — with a staleness cap)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **120h (staleness cap)** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

> ⚠️ Remora does **not yet mirror the whale's EXIT** — the 120h hard_timeout prevents holding a stale mirror indefinitely. A whale-exit mirror is a planned v1.1 enhancement.

## Scanner pattern

A focused variant of the **Trader-follower / hot-streak** archetype (#5) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `leaderboard_get_trader_positions` (per whale; unwraps the nested `data.positions.positions` shape), `discovery_get_trader_state` (whale quality, optional). Pure functions unit-tested in `tests/test_signal.py` (`python3 remora/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/remora-producer.py | Long-lived daemon; emits REMORA_WHALE_MIRROR signals |
| scripts/remora_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/remora-config.json | Operator config — **set `whales`** + thresholds + sizing |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Remora

```bash
mkdir -p /data/workspace/skills/remora-strategy/{config,scripts,state,references}
for f in scripts/remora-producer.py scripts/remora_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/remora-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/remora/$f" \
    -o "/data/workspace/skills/remora-strategy/$f"
done
```

### Step 3 — Configure wallet, chat ID, AND your whale set

Edit `/data/workspace/skills/remora-strategy/config/remora-config.json`. **Remora trades nothing until you set `whales`:**

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "whales": [
    { "trader_id": "0xWhaleYouWantToRide1" },
    { "trader_id": "0xWhaleYouWantToRide2" }
  ]
}
```

> Find good whales to ride with `discovery_get_top_traders` (historical track record) or `leaderboard_get_top` (current momentum), then paste their `trader_id` / wallet here.

### Step 4 — Required env vars

```bash
export REMORA_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                           # required (user-scope; needed for leaderboard_get_trader_positions)
export REMORA_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/remora-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/remora-strategy/scripts/remora-producer.py \
  > /tmp/remora-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 620  # wait one full tick
tail -3 /tmp/remora-producer.log | jq '._remora_producer_version, .note // null, .best.coin // null'
# Expected: _remora_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "no whales configured — set config.whales ..."` — you haven't set whales yet
- `"note": "WAITING — no qualifying whale position to mirror"` — whales are flat / sub-dust
- `"signals_pushed": 1, "best": { "coin": ..., "whale_count": ..., "direction": "LONG"|"SHORT" }` — a mirror fired

## Changelog

### v1.0.0 (2026-05-26) — initial release

A focused single-position mirror of a hand-picked whale set, with a consensus multiplier — a deliberate contrast to the universe-scanning trader-followers. Let-winners-run DSL class (wide ladder, time-cuts off except a 120h staleness cap), taker-true entry, no null numeric signal fields, defensive nested-shape unwrapping, disown-safe launch, unit-tested signal functions. Whale-exit mirroring is a planned v1.1 enhancement.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
