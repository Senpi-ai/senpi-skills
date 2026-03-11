# 🦂 SCORPION — Whale Wallet Tracker

The poisonous tail. Tracks top whale wallets from the leaderboard, monitors their position changes, mirrors entries with a 10-minute delay filter (filters out noise/hedges). When a tracked whale exits, SCORPION exits immediately — the sting.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `scorpion-scanner.py` | 5 min | Discover whales, track positions, detect consensus, mirror entries |
| DSL v5 (shared) | 3 min | Trailing stops |

## Edge

Top whales have information edges (flow, OTC, insider). Following their positions with a delay filter captures the edge while avoiding noise. The instant exit on whale exit (the sting) protects against whale reversals.

## Deployment

### Bootstrap Gate
On first session, the agent must:
1. Verify Senpi MCP is working
2. Create scanner cron (5 min, isolated, agentTurn) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send one welcome message: "🦂 SCORPION is online. Tracking [X] whales. Silence = hunting."

On subsequent sessions, check `config/bootstrap-complete.json` exists. If not, re-run bootstrap.

### Cron Setup
All crons run **isolated sessions** with `agentTurn` payloads. Use `NO_REPLY` for idle cycles. Never `HEARTBEAT_OK`.

| Cron | Interval | Session | Payload |
|------|----------|---------|---------|
| Scanner | */5 * * * * | isolated | agentTurn |
| DSL v5 | */3 * * * * | isolated | agentTurn |

## Notification Policy (Strict)

**ONLY alert the user when:**
- Position OPENED (asset, direction, whale count, margin)
- Position CLOSED (asset, PnL, reason — DSL or whale exit sting)
- Risk guardian triggered (gate closed, force close)
- Critical error (MCP auth expired, 3+ consecutive failures)

**NEVER alert for:**
- Scanner ran and found nothing
- Whale discovery updated
- Persistence check in progress
- DSL routine checks
- Any reasoning, thinking, or narration

No action = `NO_REPLY`, nothing else. No rogue background processes.

## Setup
1. Set `SCORPION_WALLET` and `SCORPION_STRATEGY_ID` env vars (or fill `scorpion-config.json`)
2. Top up strategy to full budget
3. Agent creates crons automatically via bootstrap gate

## Requires
- OpenClaw (with exec, cron, Telegram)
- Senpi MCP server (configured via mcporter)
- DSL v5 skill (dsl-dynamic-stop-loss)
- Python 3
- `mcporter` CLI
