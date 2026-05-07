# Replicate the wrapper-test environment

This is what runs in `Rachin Kapoor's Projects → pangolin-wrapper-test` on Railway. Anyone with Railway + npm + a Senpi MCP token can stand up an identical box in three steps.

## What you get

A single Railway service running the OpenClaw gateway + the senpi-trading-runtime plugin (a specific dev npm tag) + the pangolin producer skill, talking to senpi-prod MCP. Producer ticks every 5 minutes via `producer_daemon`, no openclaw cron, no per-call subprocess fan-out.

## Prerequisites

- Railway account (any plan; the test box fits in Hobby's 22 GB cgroup).
- Senpi MCP bearer token for the wallet you'll trade with — get it from `senpi-auth-service` (or copy yours from the existing wrapper-test).
- A funded Hyperliquid strategy wallet address (use Senpi MCP's `strategy_create_custom_strategy` if you don't have one).
- Telegram bot token + chat id (optional; only needed for runtime notifications).

## The three steps

### 1. Clone the Railway template

Open https://railway.app/template/openclaw and deploy. The template provisions an OpenClaw gateway service with the right Dockerfile + a 22 GB volume mounted at `/data`.

This step gives you a service ID, a project ID, and an environment ID — keep them; you'll need them in step 2.

### 2. Set these environment variables

Set every one of these on the Railway service. The first four are the wrapper-test pin set; everything else is wallet/token-specific.

```bash
# Pin the runtime + skills to the verified-working pair
SENPI_RUNTIME_NPM_SPEC=@senpi/runtime@1.0.95-dev.runtime-phase-2-api.20260507134852
SENPI_SKILLS_BRANCH=helper-mcp-envelope-aligned

# OpenClaw service config (defaults from the template; override only if needed)
OPENCLAW_VERSION=v2026.2.22
DISABLE_AUTO_UPDATE=true
SENPI_LOG_LEVEL=info

# MCP + auth (your wallet's token, your strategy wallet)
SENPI_MCP_URL=https://mcp.prod.senpi.ai/mcp
SENPI_AUTH_TOKEN=<your senpi-auth bearer token>
PANGOLIN_WALLET=<your strategy wallet address, 0x... lowercase>
PANGOLIN_DECISION_MODEL=gemini-3.1-pro-preview

# Notifications (optional; omit to silence)
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_CHAT_ID=<your chat id>
```

Hit "Deploy" — Railway will rebuild and the bootstrap script will install `@senpi/runtime` + clone `senpi-skills` at the pinned branch.

### 3. Register the pangolin runtime + start the producer

SSH in once with `railway ssh --project=<id> --environment=<id> --service=<id>` and run:

```bash
# Register the runtime against your wallet (uses pangolin/runtime.yaml from the cloned skills repo)
WALLET_ADDRESS=$PANGOLIN_WALLET \
  openclaw senpi runtime create \
    --path /data/workspace/senpi-skills-src/pangolin/runtime.yaml

# Start the producer daemon (5-min interval; logs go to /tmp/pangolin-daemon.log)
cd /data/workspace/senpi-skills-src
nohup python3 pangolin/scripts/pangolin-producer.py > /tmp/pangolin-daemon.log 2>&1 &

# Confirm
openclaw senpi runtime list                                # one runtime, status=running
tail -f /tmp/pangolin-daemon.log | grep daemon_tick_finished  # tick fires every 5 min
```

That's it. First tick should land within ~5 minutes; you'll see `daemon_tick_finished status=ok`.

## What to verify after first tick

| Signal | Where | What you want |
|---|---|---|
| Daemon alive | `ps -ef \| grep pangolin-producer` | one process |
| MCP keep-alive working | `grep mcp_initialized /tmp/pangolin-daemon.log \| wc -l` | `1` (per process lifetime) |
| Tick latency | `grep daemon_tick_finished /tmp/pangolin-daemon.log \| tail` | 3-5 s (not 30+ s) |
| MCP call latency | `grep mcp_call /tmp/pangolin-daemon.log \| tail` | 250-500 ms typical |
| Runtime registered | `openclaw senpi runtime list` | one row, status=running |
| Plugin version | gateway log | matches `SENPI_RUNTIME_NPM_SPEC` |

## Pin rationale

The two pins above (`SENPI_RUNTIME_NPM_SPEC` + `SENPI_SKILLS_BRANCH`) are a matched pair. The `runtime-phase-2-api.20260507134852` build ships the `/signals` + `/audit` envelope shape that `helper-mcp-envelope-aligned` parses for. Mixing pins from different shapes will produce silent envelope-parse failures or `INVALID_REQUEST` rejections. When in doubt, copy the wrapper-test variables verbatim.

## Useful diagnostic one-liners

```bash
# Confirm runtime build was loaded (no plugin load errors)
grep 'plugin registered\|plugin failed' /tmp/openclaw/openclaw-*.log | tail -5

# All [senpi_helpers] events from the producer (filter Railway log search by this prefix)
grep '\[senpi_helpers\]' /tmp/pangolin-daemon.log | tail -20

# Did push_signal succeed? (only relevant once a candidate clears the gates)
grep '"event": "signal_post"' /tmp/pangolin-daemon.log
```

## What doesn't replicate

- Wallet-specific PnL history, DSL state, or position-tracker archives. These accumulate per-wallet and don't transfer.
- Cron-driven runs from the senpi.ai legacy stack. The wrapper-test deliberately does not use openclaw cron + agentTurn; the producer_daemon scheduler replaces it.
