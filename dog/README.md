# 🐕 DOG v2.0 — The Contrarian Pup

Part of [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

Multi-asset contrarian fader. Trades AGAINST Smart Money consensus when the move is exhausted on BTC/ETH/SOL/HYPE. Wide DSL gives reversals time to develop.

## Why Dog flipped from v1.0 to v2.0

Dog v1.0 was the "Loyal Consistent Performer" — a multi-asset SM consensus FOLLOWER. Fleet audit on 2026-04-10 found the v1.0 signal was perfectly inverted: real performance was -$61, mathematically inverted would have been +$61. HYPE alone caused -$91 of the -$105 net loss. The scanner was systematically buying tops and selling bottoms.

**v2.0 is a complete direction flip.** Instead of chasing SM consensus, fade it. When the crowd is overwhelmingly committed AND the move is already extended (4H price > 2-3% in the SM direction), Dog enters the OPPOSITE direction. The unwind is the alpha.

## What Dog does

- **Scans BTC, ETH, SOL, HYPE** every 3 minutes via `leaderboard_get_markets`
- **Identifies SM dominant direction** on each asset
- **Verifies move exhaustion** — 4H price has moved at least 2-3% in the SM direction (mean reversion setup)
- **Verifies SM is no longer building** — 15m velocity must be ≤ 0.1 (move is exhausting, not still building)
- **Scores the contrarian setup** across SM concentration, exhaustion, velocity, funding alignment
- **Fires the FADE entry** at 7x leverage (10x at score 12+), 30% margin, FEE_OPTIMIZED_LIMIT
- **Hands off to DSL** — wide tiers let the reversal develop over hours (max hold 360 min)

## Why the contrarian thesis works on Hyperliquid

Hyperliquid is dominated by leverage traders chasing momentum. When a coin moves 3%+ in 4 hours and SM consensus piles in 15%+, the crowd is already maximum exposed. The unwind happens because:

1. **Funding pressure** — extreme positioning generates extreme funding rates, forcing late entrants to capitulate
2. **Mean reversion** — overextended moves draw counter-trend liquidity from contrarian traders

Dog's edge is being on the unwind side BEFORE the capitulation accelerates.

## Install

```bash
mkdir -p /data/workspace/skills/dog-strategy/{config,scripts,state}

# Pull all package files
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/runtime.yaml -o /data/workspace/skills/dog-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/SKILL.md -o /data/workspace/skills/dog-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/config/dog-config.json -o /data/workspace/skills/dog-strategy/config/dog-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/scripts/dog-scanner.py -o /data/workspace/skills/dog-strategy/scripts/dog-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dog/scripts/dog_config.py -o /data/workspace/skills/dog-strategy/scripts/dog_config.py
```

## Configure

Set wallet and Telegram chat ID in `runtime.yaml`:

```bash
sed -i 's/${WALLET_ADDRESS}/<YOUR_STRATEGY_WALLET>/' /data/workspace/skills/dog-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<YOUR_TELEGRAM_CHAT_ID>/' /data/workspace/skills/dog-strategy/runtime.yaml
```

Or in `config/dog-config.json`:

```json
{
  "strategyId": "your-strategy-id",
  "wallet": "0xYourStrategyWallet",
  "chatId": "your-telegram-chat-id"
}
```

## Install the runtime in OpenClaw

```bash
openclaw senpi runtime create --path /data/workspace/skills/dog-strategy/runtime.yaml
openclaw senpi runtime list
```

## Verify

```bash
python3 /data/workspace/skills/dog-strategy/scripts/dog-scanner.py
```

Expected: clean exit, JSON output. Most likely first run shows a heartbeat (no fade signal) — contrarian setups are intentionally selective.

## Run on a recurring schedule

Recommended: detached bash loop (zero LLM wake cost):

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/dog-strategy/scripts/dog-scanner.py >> /tmp/dog-loop.log 2>&1; sleep 180; done' > /tmp/dog-nohup.log 2>&1 &

ps aux | grep dog-scanner | grep -v grep
tail -5 /tmp/dog-loop.log
```

3-minute cadence. The Python scanner does all work; no LLM is invoked.

## Key settings

| Setting | Value | Notes |
|---|---|---|
| Assets | BTC, ETH, SOL, HYPE | Liquid majors only |
| Max positions | 1 | Concentration |
| Margin per trade | 30% | Smaller bets, survive drawdowns |
| Leverage | 7x base / 10x at score 12+ | Conservative — contrarian needs room |
| Min score | 8 | Tunable in scanner |
| Per-asset cooldown | 120 min | Patience between trades |
| Max daily entries | 3 | Quality over quantity |
| DSL hard timeout | 360 min | Reversals take time |
| DSL Phase 1 max loss | 15% ROE | Tighter than fleet 25% |
| DSL Phase 2 tier 1 | +5% / 20% lock | Don't bank too early |

## Operational notes

**`dead_weight_cut` at 30 minutes:** if the fade hasn't started moving in 30 min, exit. Contrarian theses get expensive when they're early — the cut keeps losses small.

**`weak_peak_cut` at 90 minutes, min 2.0%:** if the peak ROE never broke 2% in 90 minutes, the reversal isn't materializing. Exit.

**Hard timeout 360 minutes (6 hours):** safety net only. The Phase 2 trailing tiers should exit winners before this fires.

**No XYZ DEX trades.** Dog is for crypto majors only — XYZ equities don't follow the same crowding/exhaustion dynamics.

## Troubleshooting

**Scanner exits with `no wallet`:** Set the wallet in `runtime.yaml`, in `config/dog-config.json`, or via the `DOG_WALLET` environment variable.

**Scanner fires LONG when SM is SHORT (or vice versa):** That's the contrarian thesis working. Dog enters OPPOSITE to SM direction by design. If you wanted a momentum follower, use Phoenix, Wolverine, or Condor instead.

**Dog hasn't fired in days:** Contrarian setups are rare. The scanner needs SM concentration ≥ 3%, traders ≥ 20, 15m velocity ≤ 0.1, AND a score ≥ 8 from the full scoring stack. In choppy markets without clear exhaustion, Dog is intentionally inactive. Check the scanner output for `note: "no fade signal"` to confirm it's running and just not finding setups.

**Scanner imports fail:** Make sure both `dog-scanner.py` AND `dog_config.py` are in the `scripts/` directory.

**Trades closing in profit too fast:** Phase 2 tier 1 locks at 5% ROE → 20% high-water lock. Tier 2 at 10% → 40%. Tier 3 at 15% → 60%. The locks ratchet up as ROE grows. If you're seeing exits at +1-2%, that's `weak_peak_cut` (peak < 2% at 90 min) or `dead_weight_cut` — those are loss-protection cuts, not profit-taking.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). The Contrarian Pup.
