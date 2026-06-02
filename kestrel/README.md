# 🦅 KESTREL — XYZ Macro Breakout Rider

Universe trend-follower that rides 1H breakouts on commodities, indices, and high-volume equities 24/7.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When a macro asset moves >=1.5% in an hour with volume confirmation, the move usually continues for 1-3 hours. Ride the trend with wide DSL. 12-asset universe across commodities, indices, and high-volume equities. 24/7 trading on Hyperliquid XYZ DEX.

Unlike crypto-native rotation agents, Kestrel hunts on the XYZ DEX where stocks, commodities, metals, and indices trade around the clock — including weekends. A mandatory 1H breakout gate filters out chop, volume confirmation filters out fake-outs, and the universe-rank scanner picks the cleanest trending name available each tick.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 12 macro assets (XYZ DEX) |
| Tick interval | 300s (5 min — macro 1H candles change slowly) |
| MIN_SCORE | 5 (v2.0 calibration; preserved) |
| 1H breakout threshold | 1.5% (mandatory hard gate) |
| Spread gate | 0.35% |
| Max positions | 2 |
| Margin per slot | $300 (30%) |
| Leverage tiers | 3x or 5x (score-tiered) |
| Daily entry cap | dynamic (P&L-aware, 0-12) |
| Per-asset cooldown | 180 min (3h) |
| Post-close cooldown | 180 min |
| Daily loss limit | 10% |
| Drawdown halt | 25% |
| Entry order type | FEE_OPTIMIZED_LIMIT (taker fallback) |
| Exit order type | FEE_OPTIMIZED_LIMIT (taker fallback) |

## DSL Phase 2 ladder (fleet-standard)

| Tier | Trigger (margin ROE) | Lock (% of HW) |
|---|---|---|
| T0 | +5% | 35% |
| T1 | +10% | 50% |
| T2 | +20% | 65% |
| T3 | +35% | 80% |
| T4 (apex) | +50% | 90% |

Phase 1: max_loss 18% / retrace 8 / 3 consecutive breaches.
Time cuts: hard_timeout 480min, weak_peak_cut 60min @ 2.0, dead_weight_cut 45min — all ENABLED (catch false breakouts early).

## Scanner pattern

This strategy uses the **universe trend-follower** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `leaderboard_get_markets` (XYZ universe), then `market_get_asset_data` per ranked candidate.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/kestrel-producer.py | Long-lived daemon |
| scripts/kestrel_config.py | SDK probe + SenpiClient wrapper |
| config/kestrel-config.json | Operator-tunable defaults |

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

### Step 2 — Pull Kestrel

```bash
mkdir -p /data/workspace/skills/kestrel-strategy/{config,scripts,state,references}
for f in scripts/kestrel-producer.py scripts/kestrel_config.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kestrel/$f" \
    -o "/data/workspace/skills/kestrel-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export KESTREL_WALLET=<your-kestrel-wallet>      # or set wallet field in config/kestrel-config.json
export SENPI_AUTH_TOKEN=...
export KESTREL_DECISION_MODEL=<your-preferred-model>     # or any model the runtime supports
```

### Step 4 — Stop legacy cron, start the daemon

```bash
openclaw cron list | grep kestrel
openclaw cron delete <kestrel-cron-id>

nohup python3 -u /data/workspace/skills/kestrel-strategy/scripts/kestrel-producer.py \
  > /tmp/kestrel-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/kestrel-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s interval — macro 1H candles change slowly).

## Changelog

### v3.0.4 (2026-06-02) — dynamic daily-cap baseline fix (phantom-drawdown throttle)

**Bug.** `STARTING_BUDGET` was hardcoded to the `$1,000` fleet default and used as the drawdown baseline for the P&L-aware dynamic daily-entry cap. Any wallet funded **below** `$1,000` was read as a proportional loss — e.g. a `$499` deposit scored as `−50%` PnL and slammed the cap to `~0` entries/day, so the agent silently sat out valid breakouts (observed live: a `GOOGL` setup skipped with `note: "daily cap reached: 1/0 (PnL -50.9%)"` while the account was actually healthy).

**Fix.** The baseline is now resolved per-tick from the operator's **actual deployed capital** via `resolve_starting_budget()`: config `startingBudget` override → persisted first-tick equity (`state/equity-baseline.json`) → current account value. Funding **any** amount now reads as `~0%` PnL at deploy, so the cap reflects real performance, not deposit size. No thesis change; the cap tiers are untouched. After a top-up, reset the baseline by setting config `startingBudget` or deleting `state/equity-baseline.json`. The hardcoded `1000.0` constant remains only as a last-resort fallback and is never used on the live path.

### v3.0.3 (2026-06-01) — drop undeclared `_kestrel_producer_version` from the signal payload

**Bug.** `build_signal_data()` injected `_kestrel_producer_version` **inside the signal `data` block** — the object the runtime validates against the `external_scanner` `config.fields` declaration. That key is not (and should not be) a declared field, so the runtime rejected the signal:

> `INVALID_REQUEST: External scanner 'kestrel_signals' received undeclared data field '_kestrel_producer_version'.`

Kestrel was the **only** producer in the fleet that put its version tag in the validated payload — every other agent (cheetah, dire, …) keeps the tag in `cfg.output(...)` log lines only. The `senpi_runtime_helpers` SDK catches the rejection and lets the daemon tick finish `status:ok`, so the failure is invisible at the tick level — you only see it in the `signal_post … status:rejected` / `push_signal failed` log lines. Same *class* of silent-producer bug as v3.0.2 (a malformed signal field rejected by the runtime), different field.

**Fix.** Removed the tag from `build_signal_data()`. It stays in all six `cfg.output(...)` log sites, so the running version is still visible in `/tmp/kestrel-producer.log` without polluting the validated signal contract — fleet-standard.

**Do NOT "fix" this by declaring `_kestrel_producer_version` in `runtime.yaml`.** That bakes a producer-internal debug field into the scanner contract, which no other agent does. The producer-side removal is the correct fix; the data block must contain only declared fields.

### v3.0.2 (2026-05-29) — score-normalization bug fix (silent producer)

**Bug:** the producer was passing the raw strategy-specific score (5–10+) as the **top-level** `score` kwarg to `SenpiClient.push_signal()`. The runtime requires that field to be in `[0, 1]` (it's the runtime's confidence band, not the strategy's raw score). Every `push_signal()` call failed with:

> `push_signal: top-level score must be in [0, 1] (got 7.0)`

**Impact:** **Kestrel hadn't traded in over a week** before the agent diagnosed this on 2026-05-29. The scanner was actively running and finding 0 candidates at score >= 5 *during the recent flat XYZ regime* — so the bug was hidden by a quiet market, but it would have silenced the agent even in active conditions. Within minutes of the live hot-patch the producer fired Score-7 `SHORT xyz:NVDA` (1H -2.38%, 4H -1.92% aligned, SM 5.0%, spread 0%).

**Fix:** Normalize the raw score to `[0, 1]` for the top-level `score` kwarg (a Score-10 entry becomes 1.0, Score-5 becomes 0.5). The full raw score stays inside `data.score` so the LLM gate still sees the actual conviction tier when deciding execute/skip. Added a 12-line bug-explainer comment inside `push_signal()` so the contract is in-code for future edits.

### v3.0.0 — Plumbing-only migration from v2.0 (no thesis change)

NO scoring change. NO threshold change. Producer ports onto `senpi_runtime_helpers` (in-process `SenpiClient`, no openclaw / mcporter subprocesses). Long-lived `producer_daemon` replaces the openclaw cron entry. v2.0.9 contamination rule applied: `KESTREL_WALLET` is the canonical env var (with `STRATEGY_ADDRESS` deprecation fallback).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
