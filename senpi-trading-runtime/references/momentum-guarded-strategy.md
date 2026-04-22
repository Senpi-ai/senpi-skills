# Momentum-Guarded Strategy — Quick Start

End-to-end example strategy that exercises every major feature of the senpi-trading-runtime plugin:

- `position_tracker` scanner for tracking your own positions on Hyperliquid and have DSL feature support
- `external_scanner` receiving pushed `MOMENTUM_BREAKOUT` signals from the shipped momentum producer
- LLM-gated `OPEN_POSITION` action (`decision_mode: llm` with `min_confidence`)
- Risk guard rails (daily loss, max entries/day, consecutive-losses cooldown, drawdown halt, per-asset cooldown)
- DSL exit engine with two-phase trailing stops + time-based cuts
- Telegram lifecycle notifications

Use it as a starting point for your own strategies. The YAML below is production-ready; tune values to taste.

For schema details see [yaml-schema.md](yaml-schema.md). For the generic producer-ops pattern (custom producers, alternative schedulers), see [external-producers.md](external-producers.md).

---

## 1. Install the trading strategy in the Senpi runtime

Each trading strategy is defined by a YAML file. Save the YAML below as `momentum-guarded.yaml`, then:

```bash
openclaw senpi runtime create --path ./momentum-guarded.yaml
```

## 2. Strategy YAML

```yaml
name: momentum-guarded
version: 1.0.0
description: >
  Risk-managed external momentum strategy. An out-of-process producer
  pushes MOMENTUM_BREAKOUT signals into the runtime; an LLM gauntlet
  decides which breakouts deserve capital; risk gates protect against
  daily loss, drawdown, and over-trading; the DSL exit engine manages
  trailing exits on accepted fills.

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"

strategy:
  wallet: "${STRATEGY_WALLET_ADDRESS}"
  slots: 3
  margin_per_slot: 300
  trading_risk: moderate
  enabled: true

risk:
  data_retention_hours: 72
  guard_rails:
    daily_loss_limit_pct: 4
    max_entries_per_day: 6
    bypass_max_entries_per_day_on_profit: false
    max_consecutive_losses: 3
    cooldown_minutes: 90
    drawdown_halt_pct: 20
    drawdown_reset_on_day_rollover: false
    per_asset_cooldown_minutes: 45

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

  - name: external_momentum
    type: external_scanner
    outputs:
      signals: true
      context: false
    config:
      fields:
        sourceScannerId: { type: string, required: true }
        sourceSignalType: { type: string, required: true }
        sourceTimestamp: { type: number, required: true }
        sourceFactors: { type: object, required: true }
        sourceMeta: { type: object, required: true }

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

  - name: momentum_entry
    action_type: OPEN_POSITION
    decision_mode: llm
    decision_model: claude-sonnet-4-20250514
    scanners: [external_momentum]
    min_confidence: 7
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options:
        ensure_execution_as_taker: true
        execution_timeout_seconds: 15
    context:
      - type: signal
        scanner: external_momentum
    decision_prompt: |
      You are a trade-decisioning evaluator. Given a MOMENTUM_BREAKOUT
      signal, decide whether to open a position or skip.

      SIGNAL DATA:
      {{signal_external_momentum}}

      Evaluate: move magnitude, direction, liquidity, leverage, score.
      Respond with a JSON object:
      {
        "execute": true|false,
        "actionType": "OPEN_POSITION",
        "confidence": 1-10,
        "reasoning": "one sentence",
        "payload": {
          "signals": [
            { "asset": "<ticker>", "direction": "LONG"|"SHORT", "reason": "..." }
          ]
        }
      }

exit:
  engine: dsl
  interval_seconds: 30
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 15
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 120
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 45
      min_value: 2
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 30
    phase1:
      enabled: true
      max_loss_pct: 2.5
      retrace_threshold: 7
      consecutive_breaches_required: 3
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 6,  lock_hw_pct: 35 }
        - { trigger_pct: 10, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 70 }
        - { trigger_pct: 20, lock_hw_pct: 85 }
```

## 3. Set up the momentum producer

Without a producer, the `external_momentum` scanner stays silent. The shipped producer analyzes candles via the Senpi MCP server and pushes `MOMENTUM_BREAKOUT` signals into the runtime using `openclaw senpi external-scanner ingest`.

### Required environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SENPI_API_KEY` | **yes** | Senpi API key for MCP access |
| `STRATEGY_ADDRESS` | **yes** | Must match the wallet in your strategy YAML |
| `SENPI_MCP_URL` | no | MCP server URL (default: `https://mcp.prod.senpi.ai/mcp`) |

### Momentum-specific tuning variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TIMEFRAME` | `1h` | Candle timeframe to analyze |
| `MIN_MOVE_PCT` | `1.5` | Minimum % move to trigger a signal |
| `MIN_DAY_VOLUME` | `1000000` | Minimum daily notional volume |
| `MIN_LEVERAGE` | `5` | Minimum available leverage |
| `MAX_CANDIDATES` | `20` | Max instruments evaluated per run |
| `MIN_SIGNAL_SCORE` | `0.2` | Minimum score threshold to push |

### Producer path

| Environment | Path |
|-------------|------|
| Local (after `npm run build`) | `<project-root>/dist/scanners/external/momentum/producer.mjs` |
| Railway OpenClaw host template | `/data/.openclaw/extensions/runtime/dist/scanners/external/momentum/producer.mjs` |

## 4. Schedule the producer

Run the producer every 5 minutes via `openclaw cron add`:

```bash
openclaw cron add \
  --name "external-momentum-guarded" \
  --cron "*/5 * * * *" \
  --session isolated \
  --wake now \
  --message "Run \`SENPI_API_KEY=<YOUR-SENPI-API-KEY> STRATEGY_ADDRESS=<YOUR-STRATEGY-WALLET-ADDRESS> node <PATH-TO-PRODUCER>/producer.mjs >> <PATH-TO-LOG>/external_momentum_guarded.log 2>&1\` and report success/failure in this log." \
  --no-deliver
```

Substitute:
- `<YOUR-SENPI-API-KEY>` — your Senpi API key
- `<YOUR-STRATEGY-WALLET-ADDRESS>` — the wallet address from your strategy YAML
- `<PATH-TO-PRODUCER>` — the producer path from the table above
- `<PATH-TO-LOG>` — a directory you can tail (e.g. `/tmp` or `/var/log/openclaw`)

## 5. Verify the strategy is live

```bash
openclaw senpi runtime list                        # Runtime should show status: running
openclaw senpi status                              # Lightweight health check
openclaw senpi action history momentum_entry      # See LLM decisions as they arrive
openclaw senpi action decisions momentum_entry    # Inspect the LLM reasoning JSON
openclaw senpi dsl positions                       # Positions the DSL is tracking
tail -f <PATH-TO-LOG>/external_momentum_guarded.log   # Producer output
```

If `action history` shows no rows after several producer runs, check the producer log for errors or confirm the scanner `name` in your YAML matches the name the producer pushes to (`external_momentum` by default; override with the `EXTERNAL_SCANNER_NAME` env var).
