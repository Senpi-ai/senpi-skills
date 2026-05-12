# 🦂 SCORPION v5.0.0 — Multi-Market Active Trader (senpi_runtime_helpers)

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What changed in v5.0.0

**Plumbing-only migration. NO thesis change.** v4.1.2's scoring tables, gates, MIN_SCORE asymmetry (crypto 11 / XYZ 9), held-asset dedup, post-close cooldown all preserved verbatim.

- `scorpion-producer.py` and `scorpion_config.py` migrate to `senpi_runtime_helpers` (direct HTTPS for MCP, direct HTTP POST to runtime `/signals`, long-lived `producer_daemon`).
- `runtime.yaml` unchanged from v4.x.
- Per Rachin's review of Cheetah PR #209: dead fields stripped from payload; `signal_type="SCORPION_TREND_FOLLOW"` passed explicitly.
- **Bonus security fix:** v4.x read wallet from BANNED generic `STRATEGY_ADDRESS` env var (v2.0.9 contamination rule violation). v5.0.0 reads from per-agent `SCORPION_WALLET` env var, with backward-compat fallback to `STRATEGY_ADDRESS` (emits deprecation warning to stderr). Operator migration: rename env var.

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

Skip if already pulled for another v3+ skill.

### Step 2 — Pull Scorpion v5.0.0

```bash
mkdir -p /data/workspace/skills/scorpion-tracker/{config,scripts,state,references}
for f in scripts/scorpion-producer.py scripts/scorpion_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/scorpion/$f" \
    -o "/data/workspace/skills/scorpion-tracker/$f"
done
```

`runtime.yaml` is unchanged from v4.x — don't touch the existing runtime.

### Step 3 — Required env vars

```bash
# v5.0.0: per-agent wallet env var (v2.0.9 rule)
export SCORPION_WALLET=<your-scorpion-wallet>
unset STRATEGY_ADDRESS                          # banned; v5.0 emits deprecation warning if set

export SENPI_AUTH_TOKEN=...
export SCORPION_DECISION_MODEL=gemini-3.1-pro-preview
```

### Step 4 — Stop the v4.x cron, start the v5.0.0 daemon

```bash
openclaw cron list | grep scorpion
openclaw cron delete <scorpion-cron-id>

nohup python3 -u /data/workspace/skills/scorpion-tracker/scripts/scorpion-producer.py \
  > /tmp/scorpion-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/scorpion-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60s interval). Tick `duration_ms` should drop from ~30-60s (v4.x mcporter) to ~1-3s.

## Thesis

The only fleet predator that hunts across BOTH crypto and XYZ DEX (commodities / indices). SM concentration + 4H price trend alignment gates the multi-factor score. Producer emits signals, runtime LLM gates every entry, risk guardrails enforced declaratively, DSL uses maker-preferred exits.

## Architecture

```
scorpion-producer.py (60s daemon)         senpi-trading-runtime
  score all crypto + XYZ markets           scorpion_signals scanner
  emit candidates at score >= 9       →    scorpion_entry action (LLM-gated)
  enrich w/ BTC macro + funding +          position_tracker + DSL
    current positions                      risk.guard_rails
                                           exit: FEE_OPTIMIZED_LIMIT
```

## Why v4.0

v3.2 logged 43 fills / 18h / -$79.84 in Arena Week 5 despite `MAX_DAILY_ENTRIES=3` in code. The scalp-reentry bypass path and in-Python trade counter were silently leaking. v4.0 removes all that bookkeeping:

- **Producer has no execution authority.** No create_position, no trade counters, no cooldown state.
- **Runtime enforces max_entries_per_day: 5 via `risk.guard_rails`.** No bypass path.
- **LLM gates every entry.** ~30-40% expected pass rate at min_confidence 7.
- **DSL uses FEE_OPTIMIZED_LIMIT on exits** (the big v2 win). At ~40 trades/day pre-gating, saves ~$20/week in fee drag.

## Key Settings (v4)

| Setting | Value |
|---|---|
| Universe | 15 crypto + 4 XYZ (CL, BRENTOIL, GOLD, SPX) |
| Entry signal gate | MIN_SCORE ≥ 9 (producer-level) |
| Entry decision | LLM-gated via `decision_prompt`, min_confidence 7 |
| Decision model | Required via `$SCORPION_DECISION_MODEL` env var — no default |
| Max concurrent | 2 slots |
| Margin per slot | $250 |
| Max entries/day | 5 (runtime-enforced, no bypass) |
| Per-asset cooldown | 120 min (runtime-enforced) |
| Daily loss cap | 5% |
| Consecutive loss pause | 3 → 90 min cooldown |
| Drawdown halt | 20% |
| DSL exit order type | **FEE_OPTIMIZED_LIMIT** (maker-first, taker fallback) |
| DSL hard_timeout | 12h (time cuts auto-disable in Phase 2 per v2 spec) |
| DSL Phase 1 max_loss | 15% |

## What's different from v3.2

| | v3.2 | v4.0 |
|---|---|---|
| Scanner size | 549 lines | 280-line producer |
| Entry decision | Hardcoded thresholds | LLM decision prompt |
| Daily counter | Python state file (leaked to 43/18h) | Runtime `risk.guard_rails` |
| Scalp re-entry | Special bypass code | Removed; `per_asset_cooldown_minutes` authoritative |
| DSL exit fees | Taker (market orders) | **Maker-preferred with taker fallback** |
| Phase-2 time-cut bug | Fires inappropriately | Auto-disabled in Phase 2 by v2 spec |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
