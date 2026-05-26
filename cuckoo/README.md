# 🐦 Cuckoo — Copy-the-Copiers (Meta-Strategy Follower)

**Let the best strategies do the work.** Cuckoo follows the platform's top-performing *strategies* — which are themselves copy/algo/trader-following strategies — and rides whatever they agree on most, weighted by how well each one is actually doing.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Following one trader (Remora) or a leaderboard universe (Raptor/Jackal/Spider) is one layer. Cuckoo is the layer *above*: it follows the top-performing **strategies** and captures the consensus of the consensus. When the best strategies independently pile into the same asset+direction, that agreement is a strong, self-cleaning signal — underperformers drop out of the top-N automatically, so the pool refreshes toward whatever is working. **Distinct from Remora** (operator-picked whales) — Cuckoo *auto-discovers* its pool.

## Key parameters

| Parameter | Value |
|---|---|
| Pool | top `topN` strategies by realized performance (auto-discovered) |
| Tick interval | 600s (10 min) — top-strategy consensus drifts slowly |
| topN | 12 |
| Min strategies in agreement | 2 |
| Min position notional (per vote) | $2,000 |
| Weight formula | `clamp(1 + roi/50, 0.5, weightCap)` |
| Weight cap (outlier guard) | 3.0 |
| High-weight bonus threshold | 6.0 |
| MIN_SCORE (producer) | 4 (out of ~6 max) |
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
| Time cuts | hard_timeout | **96h (staleness cap)** |
| Time cuts | weak_peak_cut | disabled |
| Time cuts | dead_weight_cut | disabled |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

> ⚠️ Cuckoo re-evaluates the consensus each tick but does **not yet mirror an EXIT** — the 96h hard_timeout prevents holding a stale consensus indefinitely. Exit-mirroring is a planned v1.1 enhancement.

## Scanner pattern

Introduces the **Meta-strategy follower / copy-the-copiers** archetype (#14) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `discovery_get_top_strategies` (defensive multi-key unwrap), `leaderboard_get_trader_positions` (each strategy's positions; nested `data.positions.positions` shape). Pure functions unit-tested in `tests/test_signal.py` (`python3 cuckoo/tests/test_signal.py`).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/cuckoo-producer.py | Long-lived daemon; emits CUCKOO_META_CONSENSUS signals |
| scripts/cuckoo_config.py | SDK probe + SenpiClient wrapper + recent-signals cache |
| config/cuckoo-config.json | Operator-tunable defaults (topN, minStrategies, weight cap, sizing) |
| tests/test_signal.py | Unit tests for the pure signal functions |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Cuckoo

```bash
mkdir -p /data/workspace/skills/cuckoo-strategy/{config,scripts,state,references}
for f in scripts/cuckoo-producer.py scripts/cuckoo_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/cuckoo-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cuckoo/$f" \
    -o "/data/workspace/skills/cuckoo-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `/data/workspace/skills/cuckoo-strategy/config/cuckoo-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Required env vars

```bash
export CUCKOO_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                           # required (user-scope; needed for discovery_get_top_strategies)
export CUCKOO_DECISION_MODEL=<your-preferred-model>   # bare model name; NO provider prefix
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/cuckoo-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/cuckoo-strategy/scripts/cuckoo-producer.py \
  > /tmp/cuckoo-producer.log 2>&1 &
disown
```

`disown` is essential — it detaches the daemon so a shell/session exit can't SIGTERM it.

## Verification

```bash
sleep 620  # wait one full tick
tail -3 /tmp/cuckoo-producer.log | jq '._cuckoo_producer_version, .note // null, .best.coin // null'
# Expected: _cuckoo_producer_version = "1.0.0"
```

A healthy first tick usually outputs one of:
- `"note": "WAITING — no asset held by >= 2 top strategies in agreement"` — common (the top strategies are diversified)
- `"signals_pushed": 1, "best": { "coin": ..., "strategy_count": ..., "direction": "LONG"|"SHORT" }` — a consensus fired

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to follow the auto-discovered top STRATEGIES (not individual traders) and trade their performance-weighted consensus — the copy-the-copiers meta-layer, a new archetype. Let-winners-run DSL class (wide ladder, time-cuts off except a 96h staleness cap), taker-true entry, no null numeric signal fields, defensive multi-key shape unwrapping, disown-safe launch, unit-tested signal functions. Exit-mirroring is a planned v1.1 enhancement.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
