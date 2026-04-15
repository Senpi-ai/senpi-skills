# 🍋 LEMON v1.1 — Degen Fader

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

Multi-asset contrarian fader. Counter-trades CHOPPY/DEGEN traders when their consensus is fading on 12 liquid crypto majors. Mean-reversion edge against low-quality crowding. One of the two consistently green agents in the Senpi Predators fleet.

## What Lemon does

- **Scans 12 crypto majors** every 5 minutes via `leaderboard_get_markets` — BTC, ETH, SOL, HYPE, AVAX, DOGE, LINK, XRP, ADA, NEAR, UNI, AAVE
- **Identifies SM dominant direction** on each asset
- **Verifies the move is exhausting** — 15m velocity must be ≤ 0.1 (no longer building)
- **Verifies SM concentration** — pct ≥ 3%, traders ≥ 20
- **Scores the contrarian setup** across SM concentration, exhaustion velocity, 4H overextension, 1H reversal, funding alignment
- **Fires the FADE entry** at conviction-scaled leverage (7x → 20x), 50% margin, FEE_OPTIMIZED_LIMIT
- **Hands off to DSL** — wide tiers let the reversal develop over hours (max hold 8h)

## Why fading degens works

Hyperliquid attracts a lot of low-quality, high-leverage retail traders. The Senpi MCP `discovery_get_top_traders` tool classifies them by Trading Consistency Score (TCS) — the bottom buckets are CHOPPY and DEGEN. These traders consistently lose money over time. Counter-trading their consensus when their momentum is fading is a positive-edge strategy.

The structural reason: when degens pile into a move that's already 3%+ extended, they're providing the exit liquidity for smarter capital. The unwind is the alpha.

## Install

```bash
mkdir -p /data/workspace/skills/lemon-strategy/{config,scripts,state}

# Pull all package files
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/runtime.yaml -o /data/workspace/skills/lemon-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/SKILL.md -o /data/workspace/skills/lemon-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/config/lemon-config.json -o /data/workspace/skills/lemon-strategy/config/lemon-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/scripts/lemon-scanner.py -o /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lemon/scripts/lemon_config.py -o /data/workspace/skills/lemon-strategy/scripts/lemon_config.py
```

## Configure

Set wallet and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/lemon-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/lemon-strategy/runtime.yaml
```

Or in `config/lemon-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

Environment variables also supported: `LEMON_WALLET`, `LEMON_STRATEGY_ID`.

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/lemon-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

Run the scanner once manually:

```bash
python3 /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py
```

Expected: clean exit, JSON output contains `"_lemon_version": "1.1"`. Most likely first run shows a heartbeat (no fade signal) — fade setups are intentionally rare.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/lemon-strategy/scripts/lemon-scanner.py >> /tmp/lemon-loop.log 2>&1; sleep 300; done' > /tmp/lemon-nohup.log 2>&1 &

ps aux | grep lemon-scanner | grep -v grep
tail -5 /tmp/lemon-loop.log
```

5-minute cadence (slower than other agents because fade setups are rarer).

Alternative: configure an OpenClaw cron with `sessionTarget: isolated`. Avoid `sessionTarget: main` — that pattern is a known cost time-bomb that drifts expensive as the main session accumulates context.

## Key settings

| Setting | Value | Notes |
|---|---|---|
| Tracked assets | 12 crypto majors | BTC, ETH, SOL, HYPE, AVAX, DOGE, LINK, XRP, ADA, NEAR, UNI, AAVE |
| Max positions | 1 | Concentration |
| Margin per trade | 50% | High conviction commits high capital |
| Min score | 8 | Tunable in scanner |
| Per-asset cooldown | 120 min | Patience between trades |
| Max daily entries | 3 (dynamic cap aware) | Quality over quantity |
| Leverage | 7x → 20x conviction-scaled | Fleet cap 20x |
| XYZ DEX | Banned | Equities don't follow the thesis |
| DSL hard timeout | 480 min (8h) | Mean reversion takes time |
| DSL Phase 1 max loss | 15% ROE | Tighter than fleet 25% |

## Operational notes

**Lemon trades OPPOSITE to SM consensus by design.** When the scanner reports `note: "fade signal: SHORT BTC"`, that means smart money is LONG BTC and Lemon is fading them. Direction inversion is intentional, not a bug.

**Heartbeats are normal.** The 15m velocity gate is strict — if SM is still actively building (positive 15m velocity), Lemon refuses to enter. Most scans report `no fade signal`. That's the selectivity working.

**Fade trades take time.** Mean reversion happens over hours, not minutes. The DSL hard_timeout is 8 hours and `weak_peak_cut` waits 60 min before culling stalled trades. Don't expect quick scalps.

**No XYZ trades.** SP500, NVDA, GOOGL, etc. are not in TRACKED_ASSETS and the scanner explicitly bans XYZ DEX. Macro instruments don't follow the same crowding dynamics as crypto majors.

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/lemon-config.json`, or via the `LEMON_WALLET` environment variable.

**Scanner reports `no fade signal` constantly:** Normal. Lemon's gates are strict — SM concentration ≥ 3%, traders ≥ 20, 15m velocity ≤ 0.1, AND a score ≥ 8 from the full scoring stack. In choppy markets without clear exhaustion, Lemon is intentionally inactive.

**Scanner imports fail:** Make sure both `lemon-scanner.py` AND `lemon_config.py` are in the `scripts/` directory.

**Trade exits with `dead_weight_cut`:** The trade didn't develop in 20 minutes. This is the DSL doing its job — bounded loss, move on.

**Trade exits with `weak_peak_cut`:** The trade peaked below 2% ROE in 60 minutes and didn't develop further. DSL cut it before fees ate the small gain. This is normal — fade setups don't always work.

## Track record

Lemon has been one of two consistently green agents in the Senpi Predators fleet (37 red / 2 green as of 2026-04-14). The thesis works because the underlying behavior pattern (degens piling into exhausted moves) is structural, not market-regime-dependent.

## Best for

- Operators who believe momentum chasing on Hyperliquid is a losing edge
- Multi-asset diversification across crypto majors
- Mean-reversion / contrarian strategies
- Patient holds (1-8 hours per trade)

## Not for

- Momentum followers (use Phoenix, Wolverine, Condor, or Raptor)
- Single-asset specialists
- High-frequency scalping
- XYZ DEX equities (use Bald Eagle or Kestrel)

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). The Degen Fader.
