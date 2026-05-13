# 🦔 PANGOLIN — Funding Rate Fader

Fades crowded perpetuals when funding stays elevated, collecting funding while waiting for the crowded side to capitulate.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When funding rates are elevated (>0.015%/8h ≈ 20% annualized), the crowd is paying to hold their position. Pangolin enters opposite to the funding direction — collecting funding every 8 hours while waiting for the crowded side to capitulate. Conservative 3-5x leverage, very wide DSL (12h hard timeout, 30% Phase 1 max_loss). Scans every Hyperliquid perp with OI > $1M (~60 assets), persistence ≥ 3 hours, regime-confirmed.

Pangolin's horizon (24-48h funding fade) is the longest in the fleet. The bet is patient: the longer crowding persists at extreme funding, the more violent the unwind, and the more 8h funding payments accumulate as a base-rate carry. Phase 1 max_loss 30% (10% price buffer at 3x), Phase 2 ladder starts at 12% ROE (above MAVIA's normal wick noise), `weak_peak_cut` disabled (funding fade takes 24-48h).

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | All Hyperliquid perps with OI ≥ $1M (~60 assets); XYZ banned |
| Tick interval | 300s (5 min) |
| MIN_SCORE | 9 |
| Funding threshold | ≥ 0.00015 (per-8h rate) |
| Persistence | ≥ 3 hours, regime confirms or neutral |
| Leverage tiers | 3-5x (conservative) |
| Per-asset cooldown | 240 min |
| Daily loss limit | Runtime `risk.guard_rails` |
| Drawdown halt | Runtime `risk.guard_rails` |
| Entry order type | FEE_OPTIMIZED_LIMIT, `ensure_execution_as_taker: false` (patience preserved from v1) |
| Exit order type | FEE_OPTIMIZED_LIMIT (maker-first 60s, taker fallback) |
| DSL hard_timeout | 12h |
| DSL Phase 1 max_loss | 30% |
| `weak_peak_cut` | disabled (funding fade takes 24-48h) |

## Scanner pattern

This strategy uses the **funding-regime fade** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `market_get_funding_regime`.

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec |
| scripts/pangolin-producer.py | Long-lived producer daemon (canonical reference for the `senpi_runtime_helpers` SDK wrapper pattern) |
| scripts/pangolin_config.py | SDK probe + SenpiClient wrapper |
| config/pangolin-config.json | Operator-tunable defaults |

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

### Step 2 — Pull Pangolin

```bash
mkdir -p /data/workspace/skills/pangolin-strategy/{config,scripts,state,references}
for f in scripts/pangolin-producer.py scripts/pangolin_config.py \
         runtime.yaml SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/$f" \
    -o "/data/workspace/skills/pangolin-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export PANGOLIN_WALLET=<your-pangolin-wallet>
export SENPI_AUTH_TOKEN=...
export PANGOLIN_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Stop any prior cron, start the daemon

```bash
openclaw cron list | grep pangolin
openclaw cron delete <pangolin-cron-id>

nohup python3 -u /data/workspace/skills/pangolin-strategy/scripts/pangolin-producer.py \
  > /tmp/pangolin-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/pangolin-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (300s interval — Pangolin's longer cadence reflects the 24-48h funding-fade thesis horizon).

## Changelog

### v3.0.0 — senpi_runtime_helpers migration

**Plumbing-only migration from v2.2.0. NO thesis change.** Pangolin is the canonical reference producer for the `senpi_runtime_helpers` SDK wrapper pattern.

### v2.0 architecture (preserved)

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner + calls `create_position` | Producer pushes signals via `SenpiClient.push_signal()` direct HTTP POST; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT, taker fallback OFF | Same — `ensure_execution_as_taker: false` preserved (v1 patience) |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v2 mattered for Pangolin:** v1 entries were already maker-first, but v1 EXITS used MARKET orders (taker fees). v2 brought maker-first to exits too. Fee saving per trade is small (~$0.10-0.20) given Pangolin's small notional, but architectural alignment + runtime-managed lifecycle + declarative risk gates are the real win.

**Thesis preserved verbatim from v1.5/v1.7:** funding rate ≥ 0.00015, persistence ≥ 3h, regime confirms or neutral, OI ≥ $1M, score ≥ 9, per-asset 240min cooldown, XYZ banned. Phase 1 max_loss 30% (10% price buffer at 3x), Phase 2 ladder starts at 12% ROE (above MAVIA's normal wick noise), `weak_peak_cut` disabled (funding fade takes 24-48h).

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — Built by Senpi (https://senpi.ai).
