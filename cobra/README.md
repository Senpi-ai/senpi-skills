# 🐍 COBRA — Volume-Momentum Convergence

The profit maximizer. COBRA only strikes when three independent signals converge: multi-timeframe momentum (5m + 15m + 1h all agreeing), volume confirmation (real buying/selling pressure), and open interest growth (new money entering, not just repositioning). Optional boosters from smart money alignment and funding direction.

The thesis: most price moves are noise. The ones that follow through are confirmed by volume AND new capital. COBRA ignores everything else.

## What Makes COBRA Different

Every other Senpi skill relies on one primary signal type — leaderboard (WOLF/FOX), technicals (TIGER), SM consensus (SHARK), crowding (OWL), funding (CROC), range boundaries (VIPER), correlation breaks (EAGLE), breakouts (PANTHER), whale wallets (SCORPION). COBRA doesn't have its own signal. It has a convergence filter that combines three signals no other skill requires simultaneously:

1. **All three timeframes agree.** 5m up, 15m up, 1h up — or all down. If any timeframe disagrees, skip. This eliminates fakeout wicks (5m spike, 15m flat) and counter-trend noise (5m up, 1h bearish).

2. **Volume confirms the move.** Current bar volume must be 1.3x the 10-bar average minimum. A price move without volume is market maker repositioning, not conviction.

3. **New money is entering.** Open interest trending up means new positions are being opened — fresh conviction. OI declining means the move is driven by closing/liquidations, which reverses faster.

No other skill requires all three. TIGER checks some of these individually as part of its 5-scanner confluence, but never as a mandatory triple gate.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `cobra-scanner.py` | 3 min | Scan top 25 assets for triple convergence, score signals, output best |
| DSL v5 (shared) | 3 min | Trailing stops with 8-tier ratchet |

## Downside Management

No time-based exits. COBRA uses structural invalidation only:

- **Absolute floor:** 1.5% notional (0.015/leverage). At 10x = ~15% ROE max loss. If the structure breaks, the thesis is dead.
- **G5 backstop:** Any position losing > 5% of account → force close.
- **SM hard block:** Never enter against smart money. If SM is short and the signal says long → skip. Period.
- **RSI filter:** Don't long above RSI 75. Don't short below RSI 25. Don't enter into exhausted moves.

## Upside Management

Breakeven lock + wide trailing for runners:

- **+10% ROE:** Lock +2% (covers fees, guarantees green close)
- **+35% ROE:** Lock +22% with 1.2% retrace
- **+75% ROE:** Lock +55% with 0.8% retrace
- **+100-200% ROE:** Tiers go up to 200% for explosive convergence moves
- **Stagnation TP:** If ROE ≥ 8% but high water stale 30min → close. Don't round-trip.

## Dynamic Slots

COBRA rewards winning days:

| Day P&L | Max Entries |
|---------|------------|
| Base | 4 |
| ≥ $75 | 5 |
| ≥ $150 | 6 |
| ≥ $250 | 7 |
| ≥ $400 | 8 |

Losing days stay at 4. Winning days unlock more — because the convergence filter is working and more trades at that quality level compound the edge.

## Conviction-Scaled Margin

| Score | Margin |
|-------|--------|
| 8-9 | 20% of account (base) |
| 10-11 | 25% of account |
| 12+ | 30% of account |

Higher conviction = bigger position. The triple convergence filter already ensures quality — when the score is high, the setup is real.

## Deployment

### Bootstrap Gate
On first session, the agent must:
1. Verify Senpi MCP is working
2. Create scanner cron (3 min, isolated, agentTurn) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send one welcome message: "🐍 COBRA is online. Scanning for triple convergence. Silence = no convergence."

On subsequent sessions, check `config/bootstrap-complete.json` exists. If not, re-run bootstrap.

### Notification Policy
**ONLY alert:** Position OPENED or CLOSED, risk guardian triggered, critical error.
**NEVER alert:** Scanner results, DSL checks, health checks, reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

## Setup
1. Set `COBRA_WALLET` and `COBRA_STRATEGY_ID` env vars (or fill `cobra-config.json`)
2. Top up strategy to full budget
3. Agent creates crons via bootstrap gate
4. Uses shared DSL v5 for trailing stops

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 3-4 base, up to 8 on winning days |
| Win rate | ~55-65% (triple filter = higher quality entries) |
| Avg winner | 20-50%+ ROE (structural invalidation only, no time cuts) |
| Avg loser | -12 to -15% ROE (1.5% notional floor) |
| Fee drag/day | $10-20 (3-4 maker entries, market exits) |
| Profit factor | Target 1.3-1.8 |

## License

MIT — Built by Senpi (https://senpi.ai). 
Source: https://github.com/Senpi-ai/senpi-skills
