# 🦅 Vulture v3.0.0 — Long-Tail Momentum Rider (v2-runtime-native)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**v3.0.0 is a v2-runtime-native rewrite.** Scans 25+ small/mid-cap Hyperliquid perps (HEMI, WLD, MON, XPL, AIXBT, ARB, ASTER, ZEC, LIT, TAO, etc.) that no other Senpi predator covers. Producer emits LONG_TAIL_MOMENTUM signals via `external-scanner ingest`; runtime owns execution, DSL, risk gates, and trade-chain telemetry. Hold winners for days (7-day hard_timeout), cut losers fast (90-min dead_weight_cut). Built from the #1 Arena winner's 3-week playbook (38.6% win rate, 6.15x profit factor).

## What changed in v3.0

- `vulture-producer.py` (NEW) replaces `vulture-scanner.py` (DELETED)
- v2-runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade — per-trade telemetry restored
- Scoring + DSL preset preserved exactly from v2.4 (proved correct on the live ZEC LONG +$117 unrealized; T0 lock fired venue stop at $347.17)
- The `cfg.set_cooldown` silent-crash class of bug from v2.x is structurally impossible in v3.0 (state owned by runtime, not Python)

## Install

```bash
mkdir -p /data/workspace/skills/vulture-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/runtime.yaml -o /data/workspace/skills/vulture-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/SKILL.md -o /data/workspace/skills/vulture-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/config/vulture-config.json -o /data/workspace/skills/vulture-strategy/config/vulture-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture-producer.py -o /data/workspace/skills/vulture-strategy/scripts/vulture-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture_config.py -o /data/workspace/skills/vulture-strategy/scripts/vulture_config.py
```

## Configure

Set the strategy wallet, decision model, and chat ID via environment variables (runtime.yaml uses `${WALLET_ADDRESS}` / `${VULTURE_DECISION_MODEL}` / `${TELEGRAM_CHAT_ID}` placeholders):

```bash
export WALLET_ADDRESS=0xYourStrategyWallet
export VULTURE_WALLET_ADDRESS=0xYourStrategyWallet            # used by producer for context fetches
export VULTURE_DECISION_MODEL=gemini-2.5-pro                  # bare model name; NO provider prefix
export TELEGRAM_CHAT_ID=YourTelegramChatId
```

Edit `config/vulture-config.json` and set `wallet`, `strategyId`, and `chatId`. Optional: tune `quietHours.{startUtc,endUtc,apexBypassScore}` to override the default 00:00-04:00 UTC defer window.

## Install runtime + create producer cron

```bash
openclaw senpi runtime create --path /data/workspace/skills/vulture-strategy/runtime.yaml
openclaw senpi runtime list
```

Add 3-minute cron:

```cron
*/3 * * * * cd /data/workspace/skills/vulture-strategy && python3 scripts/vulture-producer.py >> state/producer.log 2>&1
```

## Key parameters

| Parameter | Value |
|---|---|
| Universe | 25 small/mid-cap perps (see SKILL.md) |
| Banned | BTC, ETH, SOL, all XYZ |
| Max positions | 2 concurrent |
| Margin per slot | $400 |
| Leverage | 3x / 5x / 7x (score-scaled) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |
| hard_timeout | 7 days |
| weak_peak_cut | 180 min |
| dead_weight_cut | 90 min |
| MIN_SCORE (producer) | 7 |
| LLM min_confidence | 7 |
| Per-asset cooldown | 240 min (4h) |
| Daily entry cap | 6 |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 11+ bypasses) |

## DSL Phase 2 ladder

Preserved exactly from v2.3 (proved correct on the live ZEC trade):

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +15% | 20% |
| T1 | +30% | 60% |
| T2 | +40% | 75% (v2.3 pre-arm) |
| T3 | +75% | 75% |
| T4 | +100% | 85% |
| T5 | +150% | 92% |

## Migrating from v2.x

If you're running Vulture v2.x:

```bash
cd /data/workspace/skills/vulture-strategy
rm -f scripts/vulture-scanner.py                       # replaced by producer
# Update cron to point at vulture-producer.py instead
# Pull the new files (curl commands above)
# Reload runtime: openclaw senpi runtime delete <old-id>; openclaw senpi runtime create --path runtime.yaml
```

The runtime swap retains DSL state on any open position via venue-side stops — your live trade is not at risk during the upgrade. State files (`state/trade-counter.json`, `state/cooldowns.json`) are vestigial in v3.0 and can be deleted.

## License

MIT — Built by Senpi (https://senpi.ai).
