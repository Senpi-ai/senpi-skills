# 🦡 WOLVERINE v2.3 — HYPE Alpha Hunter

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

Single-asset HYPE momentum scanner. Fires on a confluence of Smart Money consensus, multi-window velocity acceleration, and 4H/1H price confirmation. Self-executing. DSL trails the trend. Chop-detection lockout prevents whipsaw losses.

## What Wolverine does

- **Scans HYPE** every 3 minutes via Senpi MCP `leaderboard_get_markets`
- **Scores momentum signals**: SM consensus + 15m/1h velocity + 4H/1H price + volume
- **Fires entries at score 8+**: conviction-scaled leverage (7x or 10x at score 10+)
- **Self-executes** via `create_position` with `FEE_OPTIMIZED_LIMIT`
- **Delegates exits** to the DSL engine via `runtime.yaml` (Phase 1 max-loss + Phase 2 trailing tiers)
- **Logs every trade** to `state/entry-log.jsonl` (survives session clears)
- **Locks out HYPE** for 6 hours after 2 consecutive losses within 3 hours (chop protection)

## Why v2.3

On 2026-04-14 Wolverine v2.2 took 5 consecutive losing HYPE trades over 3 hours of chop, losing $113. Every entry was a "fresh signal" by the v2.2 criteria but the scanner had no concept of "we just lost N times on this same coin." v2.3 adds:

1. **Chop-detection lockout** — 2 losses in 3h → 6h asset lockout
2. **Direction-flip hard gate** — refuses LONG → SHORT → LONG whipsaw after a loss
3. **Persistent entry log** — every trade event written to disk, survives `openclaw sessions clear --current`
4. **Exit tracking hook** — scanner reconciles its own realized PnL after each position closes

## Install

```bash
mkdir -p /data/workspace/skills/wolverine-strategy/{config,scripts,state}

# Pull all package files
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/runtime.yaml -o /data/workspace/skills/wolverine-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/SKILL.md -o /data/workspace/skills/wolverine-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/config/wolverine-config.json -o /data/workspace/skills/wolverine-strategy/config/wolverine-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine-scanner.py -o /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine_config.py -o /data/workspace/skills/wolverine-strategy/scripts/wolverine_config.py
```

## Configure

Set your wallet and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/wolverine-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/wolverine-strategy/runtime.yaml
```

Or set them in `config/wolverine-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/wolverine-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

Run the scanner once manually:

```bash
python3 /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py
```

Expected output: clean exit, JSON contains `"_wolverine_version": "2.3"`. First run usually shows a heartbeat (no signal) — the confluence threshold is intentionally tight.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/wolverine-strategy/scripts/wolverine-scanner.py >> /tmp/wolverine-loop.log 2>&1; sleep 180; done' > /tmp/wolverine-nohup.log 2>&1 &

# Confirm running
ps aux | grep wolverine-scanner | grep -v grep
tail -5 /tmp/wolverine-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. Avoid `sessionTarget: main` — that pattern is a known cost time-bomb that drifts expensive as the main session accumulates context.

## Tail the entry log

After Wolverine fires its first trade:

```bash
tail -20 /data/workspace/skills/wolverine-strategy/state/entry-log.jsonl | jq
```

Each line is a structured JSON event (ENTRY, EXIT, CHOP_LOCKOUT, FLIP_BLOCKED) with full metadata: timestamp, score, reasons, leverage, margin, PnL, exit reason. **The log survives session clears** — your trade history is on disk, not in LLM context.

## Key settings

| Setting | Value | Notes |
|---|---|---|
| Asset | HYPE | Single-asset focus |
| Max positions | 1 | Concentration |
| Margin per trade | 50% | High conviction commits high capital |
| Max leverage | 10x | Fleet cap |
| Min score | 8 | Tunable in scanner if needed |
| Per-asset cooldown | 180 min | Default time between HYPE trades |
| Chop window | 3 hours | Window for counting consecutive losses |
| Chop max losses | 2 | After 2 losses in window → lockout |
| Chop lockout | 6 hours | Sit out HYPE after 2 losses |
| DSL hard timeout | 240 min | Safety net only — Phase 2 trailing handles winners |

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml` or `config/wolverine-config.json` or the `WOLVERINE_WALLET` environment variable.

**Scanner outputs `CHOP_LOCKED`:** Wolverine detected 2 losses on HYPE within 3 hours and is locked out. This is intentional. Will unlock 6 hours after the last loss. Check the unlock timestamp in `state/cooldowns.json`.

**Scanner outputs `RESTING ORDER: limit order pending`:** A previous FEE_OPTIMIZED_LIMIT order is still resting on the book. Wolverine auto-cancels orders older than 10 minutes; this message just means a recent order is still active. Wait for it to fill or be cancelled.

**Scanner imports fail:** Make sure both `wolverine-scanner.py` AND `wolverine_config.py` are in the `scripts/` directory. The scanner imports the helper module via `import wolverine_config as cfg`.

**Trade history lost after session clear:** That's exactly what `state/entry-log.jsonl` was added for in v2.3. Tail the log file to recover the trade events.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
