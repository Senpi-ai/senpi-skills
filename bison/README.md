# 🦬 Bison v2.1 — BTC/ETH/SOL Conviction Holder

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

**BTC/ETH/SOL whitelist. Conviction floor (minScore 11). No time-cuts.**
DSL ratchet ladder owns all exits. One position at a time, conviction-scaled
margin (25-37%), conservative leverage (operator default 5x).

v2.1 (2026-05-07): three changes from v2.0 to align the agent with its
"Macro Conviction" thesis instead of the "Midnight Calendar Entry" pattern
that v2.0 was empirically falling into:

- **Hard asset whitelist** (was top-10 by volume) — removes small-cap
  volume-spikes from hijacking the daily slot. Only BTC/ETH/SOL by default.
  Override via `"allowedAssets": [...]` in `bison-config.json`.
- **minScore 8 → 11** — demands real conviction (5/6 components fire),
  not first-bar-crossing. Risk: dormant days when no setup hits 11.
  That's the design intent.
- **Time-cuts disabled** — `hard_timeout` and `dead_weight_cut` removed
  from runtime DSL preset. Phase 1 max_loss + Phase 2 ratchet ladder own
  all exits. weak_peak_cut kept as the only time-based cut (self-limiting).

v2.0 architecture preserved: scanner enters via `create_position` internally
(Wolverine pattern), RatchetStop exits, no scanner-side thesis re-evaluation.

## Install

```bash
mkdir -p /data/workspace/skills/bison-strategy/{config,scripts,state}

# Pull all package files from the senpi-skills main branch
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/runtime.yaml -o /data/workspace/skills/bison-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/SKILL.md -o /data/workspace/skills/bison-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/config/bison-config.json -o /data/workspace/skills/bison-strategy/config/bison-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/scripts/bison-scanner.py -o /data/workspace/skills/bison-strategy/scripts/bison-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/scripts/bison_config.py -o /data/workspace/skills/bison-strategy/scripts/bison_config.py
```

## Configure

Set your wallet address and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/bison-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/bison-strategy/runtime.yaml
```

Or set them in `config/bison-config.json` directly:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/bison-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

Run the scanner once manually:

```bash
python3 /data/workspace/skills/bison-strategy/scripts/bison-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat (no signal) — the scanner is intentionally selective.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost, matches Turbine pattern):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/bison-strategy/scripts/bison-scanner.py >> /tmp/bison-loop.log 2>&1; sleep 180; done' > /tmp/bison-nohup.log 2>&1 &

# Confirm running
ps aux | grep bison-scanner | grep -v grep
tail -5 /tmp/bison-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked unless an entry fires.

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. **Avoid `sessionTarget: main`** — that pattern is a known cost time-bomb that drifts expensive as the main session accumulates context.

## What's in this package

```
bison/
├── README.md                       # This file (user-facing)
├── SKILL.md                        # LLM-facing thesis + agent rules
├── runtime.yaml                    # OpenClaw runtime config + DSL preset
├── config/
│   └── bison-config.json      # Wallet, strategy ID, chat ID
└── scripts/
    ├── bison-scanner.py       # Main scanner
    └── bison_config.py     # Helper module (atomic write, MCP, state I/O)
```

For full thesis details, scoring tables, DSL configuration, and operational notes, see [SKILL.md](./SKILL.md).

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/bison-config.json`, or via the appropriate environment variable.

**Scanner imports fail:** Make sure both the scanner and `bison_config.py` helper module are in the `scripts/` directory. The scanner imports the helper via `import bison_config as cfg`.

**Scanner hasn't fired in hours:** This agent is intentionally selective. Check the scanner output for `note: "no <type> signal"` to confirm it's running and just not finding setups. Forcing the scanner to fire on weak signals is a known way to lose money — see fleet audit notes in SKILL.md.

**Trade history lost after session clear:** Newer agents in the fleet write to `state/entry-log.jsonl` which survives session clears. If this agent doesn't yet have that pattern, the trade history lives only in scanner stdout logs (`/tmp/bison-loop.log`).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
