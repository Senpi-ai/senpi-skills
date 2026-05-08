# Runtime deployment

A wrapper-based deployment runs the OpenClaw gateway + the senpi-trading-runtime plugin + a producer skill on a single Railway service. The producer ticks every 5 minutes via `producer_daemon`, talks directly to senpi-prod MCP over HTTPS, and POSTs signals to the runtime on `127.0.0.1:8787` — no openclaw cron, no per-call subprocess fan-out.

## Prerequisites

- Railway account (Hobby tier is enough — the service fits under the 22 GB cgroup).
- Senpi MCP bearer token for the wallet you'll trade with (issued by `senpi-auth-service`).
- A funded Hyperliquid strategy wallet address. If you don't have one, create it via Senpi MCP's `strategy_create_custom_strategy`.
- Telegram bot token + chat id (optional — only needed if you want runtime notifications).

## The three steps

### 1. Clone the Railway template

Open https://railway.app/template/openclaw and deploy. The template provisions an OpenClaw gateway service with the right Dockerfile + a 22 GB volume mounted at `/data`.

This step gives you a service ID, a project ID, and an environment ID — keep them; you'll need them in step 2.

### 2. Set these environment variables

```bash
# Pin the runtime + skills to the verified-working pair
SENPI_RUNTIME_NPM_SPEC=@senpi/runtime@2.0.0-dev.runtime-phase-2.20260508074726
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

Hit "Deploy" — Railway will rebuild and the bootstrap script will install `@senpi/runtime` from the pinned npm spec and clone `senpi-skills` at the pinned branch.

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

The two pins above (`SENPI_RUNTIME_NPM_SPEC` + `SENPI_SKILLS_BRANCH`) are a matched pair. The `2.0.0-dev.runtime-phase-2.20260508074726` build is the first 2.0 dev tag with the senpi-stack `/signals` + `/audit` envelope and the `/state` endpoint that `helper-mcp-envelope-aligned`'s daemon needs for scanner-level liveness. Pinning a 1.x runtime would produce silent envelope-parse failures and missing `/state` 404s; pinning a 2.x runtime against the older `wrapped-skills` branch (which doesn't expect the new envelope) goes the other way. Keep both halves on 2.x.

## Useful diagnostic one-liners

```bash
# Confirm runtime build was loaded (no plugin load errors)
grep 'plugin registered\|plugin failed' /tmp/openclaw/openclaw-*.log | tail -5

# All [senpi_helpers] events from the producer (filter Railway log search by this prefix)
grep '\[senpi_helpers\]' /tmp/pangolin-daemon.log | tail -20

# Did push_signal succeed? (only relevant once a candidate clears the gates)
grep '"event": "signal_post"' /tmp/pangolin-daemon.log
```

## What's not portable across deployments

- Wallet-specific PnL history, DSL state, position-tracker archives. These accumulate per-wallet and don't transfer.
- Producer dispatch via openclaw cron + `agentTurn`. The wrapper-based path uses `producer_daemon` instead — that's a property of the deployment, not configurable per-tick.
