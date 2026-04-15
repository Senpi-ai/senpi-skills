# 🦏 Rhino v1.0 — Momentum Pyramider

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/skill-hub-senpi).

The only skill in the fleet that scales into winners instead of entering full size and hoping. Top 10 assets by OI + volume. Starts with 30% of max position, adds at +10% ROE (40% more), adds at +20% ROE (final 30%). Thesis re-validated before every add.

## Install

```bash
mkdir -p /data/workspace/skills/rhino-strategy/{config,scripts,state}

# Pull all package files from the senpi-skills main branch
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/runtime.yaml -o /data/workspace/skills/rhino-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/SKILL.md -o /data/workspace/skills/rhino-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/config/rhino-config.json -o /data/workspace/skills/rhino-strategy/config/rhino-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/scripts/rhino-scanner.py -o /data/workspace/skills/rhino-strategy/scripts/rhino-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/scripts/rhino_config.py -o /data/workspace/skills/rhino-strategy/scripts/rhino_config.py
```

## Configure

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/rhino-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/rhino-strategy/runtime.yaml
```

Or in `config/rhino-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime

```bash
openclaw senpi runtime create --path /data/workspace/skills/rhino-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

```bash
python3 /data/workspace/skills/rhino-strategy/scripts/rhino-scanner.py
```

Expected: clean exit, JSON output. First run usually shows a heartbeat — the scanner is selective by design.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost, matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/rhino-strategy/scripts/rhino-scanner.py >> /tmp/rhino-loop.log 2>&1; sleep 180; done' > /tmp/rhino-nohup.log 2>&1 &

ps aux | grep rhino-scanner | grep -v grep
tail -5 /tmp/rhino-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

## What's in this package

```
rhino/
├── README.md                       # This file
├── SKILL.md                        # LLM-facing thesis + agent rules
├── runtime.yaml                    # OpenClaw runtime + DSL preset
├── config/
│   └── rhino-config.json      # Wallet, strategy ID, chat ID
└── scripts/
    ├── rhino-scanner.py       # Main scanner
    └── rhino_config.py     # Helper module
```

For full thesis details, scoring tables, and DSL configuration, see [SKILL.md](./SKILL.md).

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/rhino-config.json`, or via environment variable.

**Scanner imports fail:** Make sure both the scanner and the helper module are in the `scripts/` directory.

**Scanner hasn't fired recently:** This agent is intentionally selective. Check scanner output for rejection reasons.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
