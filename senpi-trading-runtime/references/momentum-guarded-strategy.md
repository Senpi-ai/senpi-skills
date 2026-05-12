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

## 3. Build a momentum producer with the Python SDK

Without a producer, the `external_momentum` scanner stays silent. Build the producer on the [Python Producer SDK](../SKILL.md#python-producer-sdk) bundled with this skill. A minimal momentum producer:

```python
# scripts/momentum-producer.py
import os, sys
from pathlib import Path

_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache, producer_daemon

WALLET = os.environ["STRATEGY_WALLET_ADDRESS"]
SCANNER_NAME = "external_momentum"
LOCK_NAME = f"momentum-{WALLET[2:10]}"

TIMEFRAME = os.environ.get("TIMEFRAME", "1h")
MIN_MOVE_PCT = float(os.environ.get("MIN_MOVE_PCT", "1.5"))
MIN_DAY_VOLUME = float(os.environ.get("MIN_DAY_VOLUME", "1000000"))
MIN_SIGNAL_SCORE = float(os.environ.get("MIN_SIGNAL_SCORE", "0.2"))

client = SenpiClient()
mcp = tick_cache(client)

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        markets = mcp("leaderboard_get_markets", limit=100)
        # ... your candle / momentum gating using mcp("market_get_asset_data", ...) ...
        for candidate in qualifying_candidates:
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset=candidate["asset"],
                direction=candidate["direction"],          # "LONG" or "SHORT"
                score=candidate["score"],                  # 0..1
                signal_type="MOMENTUM_BREAKOUT",
                data={"move_pct": candidate["move_pct"], "timeframe": TIMEFRAME},
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,
        name=LOCK_NAME,
        wallet=WALLET,
        scanner=SCANNER_NAME,
    )
```

### Required environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SENPI_AUTH_TOKEN` | **yes** | Senpi MCP bearer token used by `SenpiClient` |
| `STRATEGY_WALLET_ADDRESS` | **yes** | Must match the wallet in your strategy YAML |
| `SENPI_MCP_URL` | no | MCP server URL (default: `https://mcp.prod.senpi.ai/mcp`) |
| `OPENCLAW_WORKSPACE` | no | Workspace root (default `/data/workspace`); the SDK import shim probes `~/.openclaw/skills/senpi-trading-runtime/` first and falls back to `${OPENCLAW_WORKSPACE}/skills/senpi-trading-runtime/` |

### Momentum-specific tuning variables (read by the script above)

| Variable | Default | Description |
|----------|---------|-------------|
| `TIMEFRAME` | `1h` | Candle timeframe to analyze |
| `MIN_MOVE_PCT` | `1.5` | Minimum % move to trigger a signal |
| `MIN_DAY_VOLUME` | `1000000` | Minimum daily notional volume |
| `MIN_SIGNAL_SCORE` | `0.2` | Minimum score threshold to push |

## 4. Launch the producer daemon

```bash
SENPI_AUTH_TOKEN=<your-token> \
STRATEGY_WALLET_ADDRESS=0x... \
TIMEFRAME=1h MIN_MOVE_PCT=1.5 \
  nohup python3 -u ./scripts/momentum-producer.py \
  > /tmp/momentum-producer.log 2>&1 &
```

The daemon stays alive across ticks. `senpi-helpers list` / `senpi-helpers health momentum-<wallet-suffix>` manage it from then on.

## 5. Verify the strategy is live

```bash
openclaw senpi runtime list                        # Runtime should show status: running
openclaw senpi status                              # Lightweight health check
senpi-helpers list                                  # Producer daemon visible with recent LAST_TICK
senpi-helpers health momentum-<wallet-suffix>      # Exit 0 = healthy
openclaw senpi action history momentum_entry      # See LLM decisions as they arrive
openclaw senpi action decisions momentum_entry    # Inspect the LLM reasoning JSON
openclaw senpi dsl positions                       # Positions the DSL is tracking
```

If `action history` shows no rows after several producer ticks, check `senpi-helpers stats momentum-<wallet-suffix> --hours 1` for error histograms or tail the producer log. Confirm the scanner `name` in your YAML matches what the producer pushes to (`external_momentum` here).
