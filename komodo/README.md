# 🦎 Komodo v1.0 — Momentum Event Consensus

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/skill-hub-senpi).

Detects 2+ quality smart money traders crossing momentum thresholds on the same asset within 60 minutes. Confirmed by market concentration + volume. Replaces older momentum-detection logic.

## Install

```bash
mkdir -p /data/workspace/skills/komodo-strategy/{config,scripts,state}

# Pull all package files from the senpi-skills main branch
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/komodo/runtime.yaml -o /data/workspace/skills/komodo-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/komodo/SKILL.md -o /data/workspace/skills/komodo-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/komodo/config/komodo-config.json -o /data/workspace/skills/komodo-strategy/config/komodo-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/komodo/scripts/komodo-scanner.py -o /data/workspace/skills/komodo-strategy/scripts/komodo-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/komodo/scripts/komodo_config.py -o /data/workspace/skills/komodo-strategy/scripts/komodo_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/komodo-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/komodo-strategy/runtime.yaml
```

Or in `config/komodo-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/komodo-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

```bash
python3 /data/workspace/skills/komodo-strategy/scripts/komodo-scanner.py
```

Expected: clean exit, JSON output. First run usually shows a heartbeat — the scanner is selective by design.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost, matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/komodo-strategy/scripts/komodo-scanner.py >> /tmp/komodo-loop.log 2>&1; sleep 180; done' > /tmp/komodo-nohup.log 2>&1 &

ps aux | grep komodo-scanner | grep -v grep
tail -5 /tmp/komodo-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

## What's in this package

```
komodo/
├── README.md                       # This file
├── SKILL.md                        # LLM-facing thesis + agent rules
├── runtime.yaml                    # OpenClaw runtime + DSL preset
├── config/
│   └── komodo-config.json      # Wallet, strategy ID, chat ID
└── scripts/
    ├── komodo-scanner.py       # Main scanner
    └── komodo_config.py     # Helper module
```

For full thesis details, scoring tables, and DSL configuration, see [SKILL.md](./SKILL.md).

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/komodo-config.json`, or via environment variable.

**Scanner imports fail:** Make sure both the scanner and the helper module are in the `scripts/` directory.

**Scanner hasn't fired recently:** This agent is intentionally selective. Check scanner output for rejection reasons.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
