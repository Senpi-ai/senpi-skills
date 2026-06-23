# Spider — AI/Tech Hedge Fund 🕷️

A **two-leg** trading strategy **package** for the Senpi runtime. Two genuinely different books,
each on its **own wallet**, deployed and torn down as one strategy:

| Leg | Thesis | Wallet | Cadence | Direction |
|-----|--------|--------|---------|-----------|
| **swing** (60%) | Tech/AI multi-day momentum — curated XYZ equities + crypto alts, trend/RS/RSI/funding/smart-money scoring | `${SPIDER_SWING_WALLET}` | 300s | long only |
| **scalp** (40%) | Macro & majors fast mean-reversion — BTC/ETH/SOL/HYPE + energy, 15m RSI extreme + MA stretch | `${SPIDER_SCALP_WALLET}` | 60s | long & short |

## Layout

```
spider/
  strategy.yaml            # deploy manifest (id, version, catalog, instances[]) — single source of truth
  swing/
    runtime.yaml           # self-contained runtime spec (name: spider-swing, group: spider)
    scanners/{scan.py,scoring.py}
  scalp/
    runtime.yaml           # name: spider-scalp, group: spider
    scanners/{scan.py,scoring.py}
```

Each `runtime.yaml` is consumed by the runtime, which **spawns and supervises** its
`scanners/scan.py` — calling `scan(inputs, ctx)` every `interval_seconds`. There is **no separate
scanner daemon**: the runtime owns signal validation, sizing/execution, the two-phase DSL exit, and
risk guard-rails. Both legs run rule-mode actions (no LLM, no decision-model needed).

## Deploy / close

Lifecycle is owned by **senpi-strategy-ops** (one command each):

```bash
# create two fresh wallets (60/40 split), deploy both legs, cross-verify each scanner ticks
python3 senpi-strategy-ops/scripts/deploy.py spider --budget 200

# stop both runtimes and close both strategies (flattens positions, returns funds)
python3 senpi-strategy-ops/scripts/close.py spider
```

Deploy **always** creates fresh wallets (refuses if already deployed); close **always** closes the
strategy. Redeploy = close then deploy. See `senpi-strategy-ops/SKILL.md`.
