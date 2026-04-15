# 🦈 Shark v3.0 — Aggressive Multi-Asset Hunter

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

SHARK v3.0 — SM Conviction + Liquidation Cascade Hunter. Consolidated from v1.0's 8-cron pipeline into a single scanner. 4-gate entry: SM concentration (30+ traders, 5%+) → top 5 trader alignment → price momentum → funding structure.

## Install

```bash
mkdir -p /data/workspace/skills/shark-strategy/{config,scripts,state}

# Pull all package files from the senpi-skills main branch
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/shark/runtime.yaml -o /data/workspace/skills/shark-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/shark/SKILL.md -o /data/workspace/skills/shark-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/shark/config/shark-config.json -o /data/workspace/skills/shark-strategy/config/shark-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/shark/scripts/shark-scanner.py -o /data/workspace/skills/shark-strategy/scripts/shark-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/shark/scripts/shark_config.py -o /data/workspace/skills/shark-strategy/scripts/shark_config.py
```

## Configure

Set your wallet address and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/shark-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/shark-strategy/runtime.yaml
```

Or set them in `config/shark-config.json` directly:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/shark-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

Run the scanner once manually:

```bash
python3 /data/workspace/skills/shark-strategy/scripts/shark-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat (no signal) — the scanner is intentionally selective.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost, matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/shark-strategy/scripts/shark-scanner.py >> /tmp/shark-loop.log 2>&1; sleep 180; done' > /tmp/shark-nohup.log 2>&1 &

# Confirm running
ps aux | grep shark-scanner | grep -v grep
tail -5 /tmp/shark-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. **Avoid `sessionTarget: main`** — that pattern is a known cost time-bomb that drifts expensive as the main session accumulates context.

## What's in this package

```
shark/
├── README.md                       # This file (user-facing)
├── SKILL.md                        # LLM-facing thesis + agent rules
├── runtime.yaml                    # OpenClaw runtime config + DSL preset
├── config/
│   └── shark-config.json      # Wallet, strategy ID, chat ID
└── scripts/
    ├── shark-scanner.py       # Main scanner
    └── shark_config.py     # Helper module (atomic write, MCP, state I/O)
```

For full thesis details, scoring tables, DSL configuration, and operational notes, see [SKILL.md](./SKILL.md).

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/shark-config.json`, or via the appropriate environment variable.

**Scanner imports fail:** Make sure both the scanner and `shark_config.py` helper module are in the `scripts/` directory. The scanner imports the helper via `import shark_config as cfg`.

**Scanner hasn't fired in hours:** This agent is intentionally selective. Check the scanner output for `note: "no <type> signal"` to confirm it's running and just not finding setups. Forcing the scanner to fire on weak signals is a known way to lose money — see fleet audit notes in SKILL.md.

**Trade history lost after session clear:** Newer agents in the fleet write to `state/entry-log.jsonl` which survives session clears. If this agent doesn't yet have that pattern, the trade history lives only in scanner stdout logs (`/tmp/shark-loop.log`).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
