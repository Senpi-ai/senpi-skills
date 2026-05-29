# 🐯 Lynx — Adaptive MIN_SCORE Self-Tuner

**The Vulture v4.1 story productized.** Lynx is a momentum agent that audits its own closed-trade history every 6 hours and raises its own MIN_SCORE when low-conviction buckets bleed. **First fleet agent that modifies its own behavior based on its own trade history.**

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When Vulture's agent ran the 30-trade audit that led to [v4.1's MIN_SCORE 7→9 cull](https://github.com/Senpi-ai/senpi-skills/pull/337), it did so by hand. The fix worked. Lynx asks: *why is that operation manual?*

Lynx bakes the same audit into a scheduled cron. Every 6h it pulls its own `audit_query` history, buckets trades by entry score, and auto-raises MIN_SCORE if a bucket at-or-above the current floor is bleeding (avg ROE below threshold, with enough samples).

## Key parameters

| Parameter | Default |
|---|---|
| Whitelist | BTC · ETH · SOL · HYPE |
| `initialMinScore` | 4 (permissive — gives the audit data to work with) |
| `maxMinScore` | 7 (hard ceiling) |
| `auditEverySec` | 21600 (6h) |
| `auditLimit` | 200 (max closed trades per audit) |
| `minBucketN` | 8 (need at least this many trades to act on a bucket) |
| `bucketBleedThresholdPct` | -1.0 (avg ROE below this → cull) |
| Tick interval | 300s (5 min) |
| Leverage | 3x default, max 5x |
| Margin per slot | 20% of equity |
| Max entries per day | 4 |
| Per-asset cooldown | 240 min |
| Entry order type | FEE_OPTIMIZED_LIMIT (ensureExecutionAsTaker: **true**) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker-true) |

## DSL preset (`let_winners_run` — momentum-class)

Standard momentum let-winners-run. The Lynx innovation is in entry, not exit.

## Scanner pattern

NEW archetype #15: **Self-tuning / adaptive-threshold agent**. Primary MCP calls: `market_get_asset_data` per whitelisted asset, `leaderboard_get_markets` (SM gate), `audit_query` (the self-tuning audit — every 6h). Pure functions unit-tested in `tests/test_signal.py` (`python3 lynx/tests/test_signal.py` — 19 tests covering both self-tuning and scoring logic).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, let-winners-run DSL, `risk.guard_rails`) |
| scripts/lynx-producer.py | Long-lived daemon; emits LYNX_ADAPTIVE_MOMENTUM + runs the periodic audit |
| scripts/lynx_config.py | SDK probe + SenpiClient wrapper + lynx-state cache (MIN_SCORE + audit log) |
| config/lynx-config.json | Operator-tunable defaults (audit interval, thresholds, whitelist) |
| tests/test_signal.py | 19 unit tests covering self-tuning + scoring |

## Install

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Lynx

```bash
mkdir -p /data/workspace/skills/lynx-strategy/{config,scripts,state,references}
for f in scripts/lynx-producer.py scripts/lynx_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/lynx-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lynx/$f" \
    -o "/data/workspace/skills/lynx-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID, AND Senpi user ID

The `senpiUserId` is what enables the self-tuning audit. Without it, Lynx runs with `initialMinScore` forever.

Find your Senpi user ID via `user_get_me` or by checking your arena leaderboard entry (the `senpiUserId` field).

Edit `/data/workspace/skills/lynx-strategy/config/lynx-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  "senpiUserId": "M..."
}
```

### Step 4 — Required env vars

```bash
export LYNX_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=...                         # required (user-scope; needed for audit_query + leaderboard_get_markets)
export LYNX_DECISION_MODEL=<your-preferred-model>
```

### Step 5 — Create the runtime + start the daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/lynx-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/lynx-strategy/scripts/lynx-producer.py \
  > /tmp/lynx-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320  # wait one full tick
tail -3 /tmp/lynx-producer.log | jq '._lynx_producer_version, .current_min_score, .audit // null'
```

Watch the state file to see Lynx learn:

```bash
cat /data/workspace/skills/lynx-strategy/state/lynx-state.json | jq '.current_min_score, .adjustments | length'
```

Initial state: `current_min_score: 4`, `adjustments: []`. After Lynx has accumulated enough trades in losing buckets, the audit fires and the adjustment is logged.

## Watching Lynx adapt

The interesting thing to watch isn't any single trade — it's the **adjustments log over time**. Each entry looks like:

```json
{
  "ts": 1717000000.0,
  "prev": 4,
  "new": 5,
  "trades_examined": 47,
  "bleeding_buckets": [
    {
      "score": 4,
      "stats": { "n": 12, "avg_roe_pct": -2.3, "win_rate_pct": 25.0 }
    }
  ]
}
```

Read that as: *"At this timestamp, after examining 47 closed trades, Lynx noticed Score-4 trades had 12 samples averaging -2.3% ROE with a 25% win rate. So Lynx raised its floor from 4 to 5."*

That's the agent learning, in your state file.

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent to **modify its own behavior based on its own trade history**. Productizes the Vulture v4.1 manual cull as a scheduled audit. Introduces archetype #15 (self-tuning agent). 19/19 unit tests covering both the self-tuning logic and the scoring logic. Taker-true entry, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
