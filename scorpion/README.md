# 🦂 SCORPION — Multi-Market Active Trader

Universe trend-follower hunting altcoin swarms across both crypto and XYZ DEX.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The only fleet predator that hunts across BOTH crypto and XYZ DEX (commodities / indices). SM concentration + 4H price trend alignment gates a multi-factor score. Producer emits candidates whenever ≥ MIN_SCORE; the runtime LLM gates every entry, risk guardrails enforce daily caps and cooldowns declaratively, and the DSL uses maker-preferred exits.

Scorpion's edge is breadth — 15 crypto perps + 4 XYZ macro assets (CL, BRENTOIL, GOLD, SPX) under one scorer. Asymmetric MIN_SCORE thresholds (crypto 11 / XYZ 9) reflect that XYZ flow is slower and crowd-heavy while crypto needs higher conviction to clear noise. Held-asset dedup + post-close cooldown prevent re-entry storms; LLM gating at min_confidence 7 expects ~30-40% pass rate against producer candidates.

## Key parameters

| Parameter | Value |
|---|---|
| Asset universe | 15 crypto + 4 XYZ (CL, BRENTOIL, GOLD, SPX) |
| Tick interval | 60s |
| MIN_SCORE (producer) | 9 (crypto 11 / XYZ 9 asymmetric) |
| LLM decision gate | min_confidence 7 |
| Decision model | Required via `$SCORPION_DECISION_MODEL` env var — no default |
| Max concurrent | 2 slots |
| Margin per slot | $250 |
| Max entries per day | 5 (runtime-enforced, no bypass) |
| Per-asset cooldown | 120 min (runtime-enforced) |
| Daily loss cap | 5% |
| Consecutive loss pause | 3 → 90 min cooldown |
| Drawdown halt | 20% |
| Entry order type | FEE_OPTIMIZED_LIMIT |
| Exit order type | **FEE_OPTIMIZED_LIMIT** (maker-first, taker fallback) |
| DSL hard_timeout | 12h (time cuts auto-disable in Phase 2 per v2 spec) |
| DSL Phase 1 max_loss | 15% |

## Scanner pattern

This strategy uses the **universe trend-follower / altcoin-swarm** scanner pattern — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP call: `leaderboard_get_markets`.

## Architecture

```
scorpion-producer.py (60s daemon)         senpi-trading-runtime
  score all crypto + XYZ markets           scorpion_signals scanner
  emit candidates at score >= 9       →    scorpion_entry action (LLM-gated)
  enrich w/ BTC macro + funding +          position_tracker + DSL
    current positions                      risk.guard_rails
                                           exit: FEE_OPTIMIZED_LIMIT
```

## Files

| File | Purpose |
|---|---|
| runtime.yaml | Runtime spec (unchanged from v4.x) |
| scripts/scorpion-producer.py | Long-lived producer daemon |
| scripts/scorpion_config.py | SDK probe + SenpiClient wrapper |
| config/scorpion-config.example.json | Operator-tunable defaults |

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

### Step 2 — Pull Scorpion

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
# per-agent wallet env var (v2.0.9 rule)
export SCORPION_WALLET=<your-scorpion-wallet>
unset STRATEGY_ADDRESS                          # banned; v5.0 emits deprecation warning if set

export SENPI_AUTH_TOKEN=...
export SCORPION_DECISION_MODEL=<your-preferred-model>
```

### Step 4 — Stop any prior cron, start the daemon

```bash
openclaw cron list | grep scorpion
openclaw cron delete <scorpion-cron-id>

nohup python3 -u /data/workspace/skills/scorpion-tracker/scripts/scorpion-producer.py \
  > /tmp/scorpion-producer.log 2>&1 &
```

## Verification

```bash
tail -f /tmp/scorpion-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (60s interval). Tick `duration_ms` should drop from ~30-60s (v4.x mcporter) to ~1-3s.

## Changelog

### v5.0.0 — senpi_runtime_helpers migration

**Plumbing-only migration. NO thesis change.** v4.1.2's scoring tables, gates, MIN_SCORE asymmetry (crypto 11 / XYZ 9), held-asset dedup, post-close cooldown all preserved verbatim.

- `scorpion-producer.py` and `scorpion_config.py` migrate to `senpi_runtime_helpers` (direct HTTPS for MCP, direct HTTP POST to runtime `/signals`, long-lived `producer_daemon`).
- `runtime.yaml` unchanged from v4.x.
- Per Rachin's review of Cheetah PR #209: dead fields stripped from payload; `signal_type="SCORPION_TREND_FOLLOW"` passed explicitly.
- **Bonus security fix:** v4.x read wallet from BANNED generic `STRATEGY_ADDRESS` env var (v2.0.9 contamination rule violation). v5.0.0 reads from per-agent `SCORPION_WALLET` env var, with backward-compat fallback to `STRATEGY_ADDRESS` (emits deprecation warning to stderr). Operator migration: rename env var.

### v4.0 — runtime-native enforcement

v3.2 logged 43 fills / 18h / -$79.84 in Arena Week 5 despite `MAX_DAILY_ENTRIES=3` in code. The scalp-reentry bypass path and in-Python trade counter were silently leaking. v4.0 removed all that bookkeeping:

- **Producer has no execution authority.** No create_position, no trade counters, no cooldown state.
- **Runtime enforces max_entries_per_day: 5 via `risk.guard_rails`.** No bypass path.
- **LLM gates every entry.** ~30-40% expected pass rate at min_confidence 7.
- **DSL uses FEE_OPTIMIZED_LIMIT on exits** (the big v2 win). At ~40 trades/day pre-gating, saves ~$20/week in fee drag.

### What's different from v3.2

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
