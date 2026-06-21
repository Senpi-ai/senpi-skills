# 🦅 Vulture — Long-Tail Momentum Rider

Trader-follower / hot-streak rider that holds winners for days across 25+ small/mid-cap Hyperliquid perps no other Senpi predator covers.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Scans 25+ small/mid-cap Hyperliquid perps (HEMI, WLD, MON, XPL, AIXBT, ARB, ASTER, ZEC, LIT, TAO, etc.) that no other Senpi predator covers. Hold winners for days, cut losers fast. Built from the #1 Arena winner's 3-week playbook (38.6% win rate, 6.15x profit factor).

The long-tail edge is twofold: most predators cluster on majors + a tight altcoin set, leaving the broader long-tail uncovered; and the hot-streak shape — measured across funding, recent trader history, and on-chain momentum — persists longer in low-attention names than in BTC/ETH/SOL. Vulture rides those streaks with score-scaled leverage (3x/5x/7x), wide DSL (7-day hard timeout), and an apex ladder that locks 85% of peak ROE on monster winners.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 25 small/mid-cap perps (see SKILL.md) |
| Banned | BTC, ETH, SOL, all XYZ |
| Tick interval | 60-180s |
| MIN_SCORE (producer) | **10** (raised from 9 in v4.2.0; 7→9 in v4.1.0 — see SKILL.md changelog for the 100-trade analysis) |
| LLM min_confidence | **9** (raised from 8 in v4.2.0) |
| Max positions | 2 concurrent |
| Margin per slot | 45% of equity (was a fixed $400; switched to budget-relative `margin_pct: 45`) |
| Leverage tiers | 5x / 7x (score-scaled — `cautious` tier 3x removed in v4.1.0) |
| Max entries per day | 6 |
| Per-asset cooldown | 240 min (4h) |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Quiet hours | 00:00-04:00 UTC (apex score 11+ bypasses) |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | FEE_OPTIMIZED_LIMIT |
| hard_timeout | 7 days |
| weak_peak_cut | 180 min |
| dead_weight_cut | 90 min |

## Scanner pattern

This strategy uses the **trader-follower / hot-streak** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `market_get_funding_history` plus trader-history / momentum enrichment.

## DSL Phase 2 ladder

Preserved from v2.3 (proved correct on the live ZEC trade), with one v3.0.1 adjustment: v2.x's T5 (`trigger_pct: 150`) was dropped because the runtime validator rejects `trigger_pct > 100`. Apex protection now ends at T4 (100% / 85% lock); monster-winner scenarios (peak >> 100% margin ROE) still get the same 85% × peak lock at T4.

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +15% | 20% |
| T1 | +30% | 60% |
| T2 | +40% | 75% (v2.3 pre-arm) |
| T3 | +75% | 75% |
| T4 (apex) | +100% | 85% |

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (unchanged from v3.x) |
| scripts/vulture-producer.py | Long-lived producer daemon |
| scripts/vulture_config.py | SDK probe + SenpiClient wrapper |
| config/vulture-config.json | Operator-tunable defaults |

## Install

### Step 0 — Register the runtime plugin in `openclaw.json` (one-time per host)

The senpi-trading-runtime plugin won't bind its API port (`127.0.0.1:8787`) unless `plugins.entries.runtime` is present in `/data/.openclaw/openclaw.json`. Without that block the plugin logs `No plugin config found — skipping registration` and the producer daemon's `signal_post` calls fail with `[Errno 111] Connection refused`. Confirm or add:

```json
{
  "plugins": {
    "entries": {
      "runtime": {
        "enabled": true,
        "config": {
          "stateDir": "/data/.openclaw/senpi-state",
          "apiKey": "<your SENPI_AUTH_TOKEN>",
          "autoUpdate": { "enabled": false }
        }
      }
    }
  }
}
```

Restart the gateway after editing so the plugin re-registers:

```bash
openclaw gateway restart
sleep 10
curl -s -m 5 http://127.0.0.1:8787/state | head -c 200
# Expected: a JSON response with "success":true,"data":{"runtimes":[...]}
```

If `curl` returns Connection refused, the plugin still isn't registered — check `openclaw plugin list` shows the runtime entry as loaded and re-verify the JSON.

### Step 1 — Install the senpi-trading-runtime skill (one-time per host)

The Python Producer SDK (`senpi_runtime_helpers`) ships inside the senpi-trading-runtime skill. Install it once per host:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Vulture

```bash
mkdir -p /data/workspace/skills/vulture-strategy/{config,scripts,state,references}
for f in scripts/vulture-producer.py scripts/vulture_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/$f" \
    -o "/data/workspace/skills/vulture-strategy/$f"
done
```

`runtime.yaml` unchanged from v3.x.

### Step 3 — Required env vars

```bash
# Per fleet v2.0.9 rule: per-agent wallet env var (canonical name).
# Legacy VULTURE_WALLET_ADDRESS still works as a fallback for older
# launch scripts — both resolve to the same wallet.
export VULTURE_WALLET=<your-vulture-wallet>
export SENPI_AUTH_TOKEN=...
export VULTURE_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Stop any prior cron, start the daemon

```bash
openclaw cron list | grep vulture
openclaw cron delete <vulture-cron-id>

nohup python3 -u /data/workspace/skills/vulture-strategy/scripts/vulture-producer.py \
  > /tmp/vulture-producer.log 2>&1 &
```

## Configure

**Set wallet, strategy ID, and chat ID in `config/vulture-config.json`** — this is the canonical source of truth. Producer reads from here on every tick; runtime reads from here at startup.

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "YourTelegramChatId",
  ...
}
```

Set the LLM decision model via env var at runtime-create time (resolved once into runtime.yaml's `${VULTURE_DECISION_MODEL}` placeholder):

```bash
export VULTURE_DECISION_MODEL=<your-preferred-model>    # bare model name; NO provider prefix
```

Optional: tune `quietHours.{startUtc,endUtc,apexBypassScore}` in config.json to override the default 00:00-04:00 UTC defer window.

## Verification

```bash
tail -f /tmp/vulture-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60s interval).

## Changelog

### v4.2.0 (2026-06-01) — MIN_SCORE 9 → 10

A 100-trade aggregate showed every score bucket ≥10 net-positive (10/11/12 = **+$416.78** / 41 trades) and every bucket ≤9 net-negative (7/8/9 = **−$645.12** / 41 trades). Score 9 ran a 27.3% win rate at −3.32% avg ROE; score 10 was the single best bucket (+8.70% avg ROE, +$259.67 net). Raised `MIN_SCORE` 9→10 (producer) and `min_confidence` 8→9 (LLM gate); conviction sizing tier floor moved to 10. See SKILL.md changelog for the full table. NO thesis change — floor tightening only.

### v4.1.0 (2026-05-29) — MIN_SCORE 7 → 9 (low-conviction cull)

A 30-trade aggregate showed score 7–8 (n=15) ran 12.5%/28.6% win rates at −3.94%/−3.52% avg ROE. Raised `MIN_SCORE` 7→9 (producer) and `min_confidence` 7→8 (LLM gate); removed the `cautious` 3x sizing tier. NO thesis change — floor tightening only.

### v4.0.0 — senpi_runtime_helpers migration

**Plumbing-only migration from v3.1.1. NO thesis change.** v3.x scoring + universe + DSL preset preserved verbatim. Producer flips to in-process `SenpiClient`, daemon replaces cron.

### v3.0 — runtime-native (preserved through v4.0)

- `vulture-producer.py` (NEW) replaces `vulture-scanner.py` (DELETED)
- runtime-native: external_scanner + LLM-pass-through gate + native `risk.guard_rails`
- DSL exits via `FEE_OPTIMIZED_LIMIT` (saves ~0.020-0.030% per maker-filled close)
- Trade chain DB emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade — per-trade telemetry restored
- Scoring + DSL preset preserved exactly from v2.4 (proved correct on the live ZEC LONG +$117 unrealized; T0 lock fired venue stop at $347.17)
- The `cfg.set_cooldown` silent-crash class of bug from v2.x is structurally impossible in v3.0+ (state owned by runtime, not Python)

## License

MIT — Built by Senpi (https://senpi.ai).
