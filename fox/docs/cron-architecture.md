# FOX Cron Architecture

All crons run via OpenClaw's cron system. `sessionTarget: "main"` injects into the main chat session. `sessionTarget: "isolated"` runs in a disposable sub-agent session.

## Active Crons (Copy Trading Mode)

### Copy Trading Monitor (15min, isolated)
```json
{
  "name": "FOX — Copy Trading Monitor (15min)",
  "schedule": {"kind": "cron", "expr": "*/15 * * * *", "tz": "UTC"},
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Copy Trading Monitor: Read /data/workspace/copy-strategies.json for strategy IDs and wallets.\n\nFor each strategy, call strategy_get_clearinghouse_state with the wallet address. Build a table of:\n- Strategy name, budget, current account value, positions (coin, direction, leverage, ROE%, unrealizedPnl), total PnL\n\nAlso call account_get_portfolio forceFetch=true for overall balance.\n\nFormat as code block table and send to telegram:{CHAT_ID}.\n\nIf any strategy has PnL < -20% of budget, add ⚠️ WARNING.\nIf any strategy has PnL < -40% of budget, add 🚨 CRITICAL — consider closing.\n\nIf all strategies have $0 balance and 0 positions (still initializing), just say 'Copy strategies still initializing...' and HEARTBEAT_OK."
  },
  "delivery": {"mode": "none"}
}
```

### Market Regime (1h, isolated)
```json
{
  "name": "FOX — Market Regime (1h)",
  "schedule": {"kind": "cron", "expr": "0 * * * *", "tz": "UTC"},
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "FOX Regime: Run `PYTHONUNBUFFERED=1 python3 /data/workspace/scripts/market-regime.py`, capture output JSON.\n\nSave the full output to /data/workspace/market-regime-last.json.\nHEARTBEAT_OK after saving."
  },
  "delivery": {"mode": "none"}
}
```

## Autonomous Trading Crons (DISABLED — enable for autonomous mode)

### Emerging Movers Scanner (3min, isolated)
The primary entry signal scanner. Runs `emerging-movers.py`, evaluates FJ signals against v1.0 entry filters, and enters positions autonomously.

Full cron mandate includes: slot guard, entry gate, blacklist, FJ tracking, confirmation filter, v0.9 entry filters, scoring, market regime check, position entry (ALO), DSL setup, and re-entry logic.

### Opportunity Scanner (15min, isolated)
Deep scanner using `opportunity-scan-v6.py`. Same entry rules as Emerging Movers but catches signals the 3min scanner might miss.

### SM Flip Detector (5min, isolated)
Runs `sm-flip-check.py`. Closes positions if smart money direction flips against us (FLIP_NOW alert).

### Watchdog (5min, isolated)
Runs `wolf-monitor.py`. Monitors liquidation buffers. Closes positions if buffer < 30%.

### Health Check (10min, isolated)
Runs `job-health-check.py`. Reconciles DSL state files with actual positions. Auto-deactivates orphaned DSL states.

### Portfolio (15min, isolated)
Reports current positions, PnL, and account value to Telegram.

### DSL v5.3.1 (3min, per-strategy, isolated, DYNAMIC)
Created/removed dynamically when positions open/close. Runs `dsl-v5.py` for trailing stop management and Hyperliquid SL sync.
