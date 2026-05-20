# 🐦‍⬛ Albatross — Multi-week Arena Conviction Mirror

Mirror the **consistent winners** from the Senpi Arena, not the lucky-week winners. Albatross pulls 4 weekly leaderboards + the current monthly leaderboard, computes a composite conviction score, and mirrors trades from the top-N traders by that score.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/skills).

## Thesis

Mirroring last week's Arena winner is a survivor-bias trap — a trader can post +98% from one good week and three flat weeks and still top the board. Albatross corrects for this with a multi-week composite that rewards persistence and penalizes variance:

```
conviction = 0.3 × monthly_roe
            + 0.7 × mean(weekly_roe across last 4 weeks)
            − 0.5 × stdev(weekly_roe)
```

A trader posting +30%/+25%/+20%/+15% beats a trader with one +98% week and three flat weeks, because the first one has both higher expected return AND lower variance.

## Key parameters

| Parameter | Value |
|---|---|
| Leader pool size | 5 (top by conviction) |
| Pool refresh | every 4h |
| Min weeks traded | 3 of last 4 |
| Min weekly notional | $50K (filters toy accounts) |
| Excluded xHandles | `["betashop"]` (no Senpi-fleet self-mirror) |
| Tick interval | 300s (5 min) |
| Margin per trade | 15% of equity |
| Leverage | 5x default, max 5x |
| Max entries per day | 5 |
| Per-asset cooldown | 120 min |
| Daily loss limit | 15% |
| Drawdown halt | 20% |
| Hard timeout | 96h (4d cap) |
| Recent-signals dedup | 240s TTL |

## ⚠️ REQUIRES USER-SCOPE AUTH TOKEN

Albatross calls `strategy_list({userIds: [leader_user_id]})` and `discovery_get_trader_state(other_user_wallet)`. Both endpoints gate on requester user identity. **Service tokens will not work** — you'll see `SERR031: User not authorized` and `INVALID_TOKEN: Token does not contain a valid user ID` errors.

The operator must provide a **Privy-issued Senpi user token** as `SENPI_AUTH_TOKEN`. Typically you'd:
1. Log into senpi.ai as the user account that owns this strategy wallet
2. Grab the bearer token from devtools (Network tab, any GraphQL request)
3. Set as `SENPI_AUTH_TOKEN` env var on the producer host

## Scanner pattern

This strategy uses the **Trader follower (Jackal family)** scanner pattern with multi-week selection — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `arena_leaderboard` (×5 for pool composition), `strategy_list` (per-leader wallet resolution), `discovery_get_trader_state` (per-leader position fetch).

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (scanners, actions, DSL preset, `risk.guard_rails`) |
| scripts/albatross-producer.py | Long-lived daemon |
| scripts/albatross_config.py | SDK probe + SenpiClient wrapper + leader pool / position state |
| config/albatross-config.json | Operator-tunable defaults (wallet, weights, pool size) |

## Install

### Step 0 — Register the runtime plugin (one-time per host)

```json
{
  "plugins": {
    "entries": {
      "runtime": {
        "enabled": true,
        "config": {
          "stateDir": "/data/.openclaw/senpi-state",
          "apiKey": "<your USER-SCOPE SENPI_AUTH_TOKEN>",
          "autoUpdate": { "enabled": false }
        }
      }
    }
  }
}
```

Restart the gateway: `openclaw gateway restart`

### Step 1 — Install senpi-trading-runtime (one-time per host)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Albatross

```bash
mkdir -p /data/workspace/skills/albatross-strategy/{config,scripts,state,state/leader-positions,references}
for f in scripts/albatross-producer.py scripts/albatross_config.py \
         runtime.yaml SKILL.md README.md references/skill-attribution.md \
         config/albatross-config.json; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/albatross/$f" \
    -o "/data/workspace/skills/albatross-strategy/$f"
done
```

### Step 3 — Configure wallet, strategy ID, chat ID

Edit `config/albatross-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId"
}
```

### Step 4 — Set required env vars

```bash
export ALBATROSS_WALLET=<your-strategy-wallet>
export SENPI_AUTH_TOKEN=<USER-SCOPE Privy token, NOT a service token>
export ALBATROSS_DECISION_MODEL=<your-preferred-model>   # bare model name
```

### Step 5 — Create runtime + start daemon

```bash
openclaw senpi runtime create --path /data/workspace/skills/albatross-strategy/runtime.yaml
openclaw senpi runtime list

nohup python3 -u /data/workspace/skills/albatross-strategy/scripts/albatross-producer.py \
  > /tmp/albatross-producer.log 2>&1 &
disown
```

## Verification

```bash
sleep 320
tail -3 /tmp/albatross-producer.log | jq '._albatross_producer_version, .leader_pool_size, .top_leaders[0:3]'
# Expected: _albatross_producer_version = "1.0.0", leader_pool_size between 1-5
```

If you see `"note": "no qualifying leaders"` after multiple ticks, either:
- No traders in the current Arena window have positive composite score with 3+ weeks of activity
- Your auth token is service-scoped (check producer log for SERR031 errors)

## Changelog

### v1.0.0 (2026-05-20) — initial release

Multi-week Arena conviction mirror. Composite weighting replaces naive last-week-winner mirroring. Default 5-leader pool, 4h refresh, 96h hard timeout on mirrored positions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
