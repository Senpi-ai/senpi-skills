# 🪳 ROACH v3.0.0 — Striker Only. senpi_runtime_helpers.

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**Plumbing-only migration from v2.1.0. NO thesis change.** Producer flips to in-process `SenpiClient` (direct HTTPS for MCP, direct HTTP POST to runtime `/signals`). `producer_daemon` replaces openclaw cron.

## Install

### Step 1 — Pull the helpers package (one-time per host)

> **Note:** The `_helpers/senpi_runtime_helpers/` package is currently only on the `helper-mcp-envelope-aligned` branch. Pull from there.

```bash
mkdir -p /data/workspace/skills/_helpers/senpi_runtime_helpers
for f in __init__.py _config.py _logging.py cache.py client.py \
         daemon.py lock.py parallel.py SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/helper-mcp-envelope-aligned/_helpers/senpi_runtime_helpers/$f" \
    -o "/data/workspace/skills/_helpers/senpi_runtime_helpers/$f"
done
```

### Step 2 — Pull Roach v3.0.0

```bash
mkdir -p /data/workspace/skills/roach-strategy/{config,scripts,state,references}
for f in scripts/roach-producer.py scripts/roach_config.py \
         SKILL.md README.md references/skill-attribution.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/roach/$f" \
    -o "/data/workspace/skills/roach-strategy/$f"
done
```

`runtime.yaml` unchanged from v2.x.

### Step 3 — Required env vars

```bash
export ROACH_WALLET=<your-roach-wallet>          # NOT STRATEGY_ADDRESS
export SENPI_AUTH_TOKEN=...
export ROACH_DECISION_MODEL=gemini-3.1-pro-preview
```

For **Roach-B** (variant): use the same skill files but set `ROACH_WALLET=<roach-b-wallet>` on that agent's host.

### Step 4 — Stop v2.x cron, start v3.0.0 daemon

```bash
openclaw cron list | grep roach
openclaw cron delete <roach-cron-id>

nohup python3 -u /data/workspace/skills/roach-strategy/scripts/roach-producer.py \
  > /tmp/roach-producer.log 2>&1 &
```

## Smoke test

```bash
tail -f /tmp/roach-producer.log | jq -c 'select(.event=="daemon_tick_finished")' | head -3
```

Expected: `status=ok` every tick (90s interval). Roach is intentionally quiet — heartbeat ticks dominate; Striker fires are rare and that's the design.

---

## The Strategy

ROACH disables Stalker entirely and only trades STRIKER signals — violent FIRST_JUMP / IMMEDIATE_MOVER explosions backed by 1.5x volume, 1h price alignment, and 4h trend agreement. Confirmed by Fox v1.0 data: 17 Stalker trades, 17.6% win rate, -$91 net; the one Striker (ZEC LONG score 11) was the only profitable explosive entry.

ROACH will be quiet. Days with zero trades are expected and correct. Striker signals require a 10+ rank jump from #25+, score >= 10 with 4+ reasons, cc_15m >= 0.5, 1h price aligned >= 0.1%, volume >= 1.5x. That's rare. The patience IS the edge.

## v2.0 architecture

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner + calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Exit | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v2 matters:** v1 used MARKET orders for every exit, paying ~3 bp/exit in HL taker fees. v2's maker-first exits target 50-70% recovery on HL exit fees with no thesis change.

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — see root repo LICENSE.
