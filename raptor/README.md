# 🦅 Raptor v3.0 — Hot Streak Follower

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

RAPTOR v3.0 — Hot Streak Follower. Finds ELITE/RELIABLE traders currently on a 4-hour hot streak via leaderboard_get_top + discovery_get_top_traders, identifies their strongest position by delta PnL, confirms SM alignment, and follows them in. Self-executing, runtime.yaml standard deployment, fleet-standard guardrails.

## Install

```bash
mkdir -p /data/workspace/skills/raptor-strategy/{config,scripts,state}

# Pull all package files from the senpi-skills main branch
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/runtime.yaml -o /data/workspace/skills/raptor-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/SKILL.md -o /data/workspace/skills/raptor-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/config/raptor-config.json -o /data/workspace/skills/raptor-strategy/config/raptor-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor-scanner.py -o /data/workspace/skills/raptor-strategy/scripts/raptor-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor_config.py -o /data/workspace/skills/raptor-strategy/scripts/raptor_config.py
```

## Configure

Set your wallet address and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/raptor-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/raptor-strategy/runtime.yaml
```

Or set them in `config/raptor-config.json` directly:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/raptor-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

Run the scanner once manually:

```bash
python3 /data/workspace/skills/raptor-strategy/scripts/raptor-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat (no signal) — the scanner is intentionally selective.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost, matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/raptor-strategy/scripts/raptor-scanner.py >> /tmp/raptor-loop.log 2>&1; sleep 180; done' > /tmp/raptor-nohup.log 2>&1 &

# Confirm running
ps aux | grep raptor-scanner | grep -v grep
tail -5 /tmp/raptor-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. **Avoid `sessionTarget: main`** — that pattern is a known cost time-bomb that drifts expensive as the main session accumulates context.

## What's in this package

```
raptor/
├── README.md                       # This file (user-facing)
├── SKILL.md                        # LLM-facing thesis + agent rules
├── runtime.yaml                    # OpenClaw runtime config + DSL preset
├── config/
│   └── raptor-config.json      # Wallet, strategy ID, chat ID
└── scripts/
    ├── raptor-scanner.py       # Main scanner
    └── raptor_config.py     # Helper module (atomic write, MCP, state I/O)
```

For full thesis details, scoring tables, DSL configuration, and operational notes, see [SKILL.md](./SKILL.md).

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/raptor-config.json`, or via the appropriate environment variable.

**Scanner imports fail:** Make sure both the scanner and `raptor_config.py` helper module are in the `scripts/` directory. The scanner imports the helper via `import raptor_config as cfg`.

**Scanner hasn't fired in hours:** This agent is intentionally selective. Check the scanner output for `note: "no <type> signal"` to confirm it's running and just not finding setups. Forcing the scanner to fire on weak signals is a known way to lose money — see fleet audit notes in SKILL.md.

**Trade history lost after session clear:** Newer agents in the fleet write to `state/entry-log.jsonl` which survives session clears. If this agent doesn't yet have that pattern, the trade history lives only in scanner stdout logs (`/tmp/raptor-loop.log`).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
