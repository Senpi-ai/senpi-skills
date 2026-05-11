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
SENPI_RUNTIME_NPM_SPEC=@senpi/runtime@2.0.0-dev.runtime-phase-2.20260511075330
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

The two pins above (`SENPI_RUNTIME_NPM_SPEC` + `SENPI_SKILLS_BRANCH`) are a matched pair. The `2.0.0-dev.runtime-phase-2.20260511075330` build is a 2.0 dev tag with the senpi-stack `/signals` + `/audit` envelope, the `/state` endpoint that `helper-mcp-envelope-aligned`'s daemon needs for scanner-level liveness, the restored `senpi.{listActions,getActionState,getActionHistory}` gateway methods, and the 90 s LLM-decision abort cap. Pinning a 1.x runtime would produce silent envelope-parse failures and missing `/state` 404s; pinning a 2.x runtime against the older `wrapped-skills` branch (which doesn't expect the new envelope) goes the other way. Keep both halves on 2.x.

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

## Restarting the producer daemon after a container restart

**Read this first if you see the runtime up but no `daemon_tick_started` lines in the producer log, or if `ps axo args` shows no `python3 …-producer.py` process.** The helper-based daemon is a `nohup`'d long-lived Python process. A Railway redeploy, `docker restart`, OOM kill, or any other container respawn kills it. **Nothing on the box restarts it automatically.** You restart it manually with the canonical recipe below.

### The recipe (one box at a time)

```sh
# 1. Discover the strategy wallet for this box.
#    The runtime records every installed runtime's wallet under installed_runtimes.json:
cat /data/.openclaw/senpi-state/installed_runtimes.json | jq -r '.runtimes[] | "\(.id) \(.wallet)"'

# 2. Identify the producer script + log path. Convention:
#      script:  /data/workspace/skills/<skill>-strategy/scripts/<skill>-producer.py
#      log:     /tmp/<skill>-producer.log
#    (the pangolin reference deployment uses a different parent dir — see the
#    Pangolin exception below.)

# 3. Launch the daemon with the per-skill env-var matrix (see table below) and
#    redirect stdout+stderr to the log file. nohup + `&` detaches.
nohup env <SKILL>_WALLET=0x… [other env vars] \
  python3 -u /data/workspace/skills/<skill>-strategy/scripts/<skill>-producer.py \
  > /tmp/<skill>-producer.log 2>&1 < /dev/null &
echo "started PID=$!"

# 4. Verify it ticked. Should see a daemon_tick_started + daemon_tick_finished
#    pair within `interval_seconds` (default 180s = 3 min):
sleep 15
grep -E '"event": "daemon_tick_(started|finished)"' /tmp/<skill>-producer.log | tail -3
```

### Per-skill env-var matrix

| Skill | Wallet env var (required\*) | Decision-model env var | Producer script path on box |
|---|---|---|---|
| Kodiak | `KODIAK_WALLET` *(required, no config fallback)* | `KODIAK_DECISION_MODEL` (optional; runtime.yaml has it baked in) | `/data/workspace/skills/kodiak-strategy/scripts/kodiak-producer.py` |
| Polar | `POLAR_WALLET_ADDRESS` *(optional — falls back to `config/polar-config.json`)* | `POLAR_DECISION_MODEL` (substituted into runtime.yaml at install time) | `/data/workspace/skills/polar-strategy/scripts/polar-producer.py` |
| Cheetah | `CHEETAH_WALLET` *(optional — falls back to `config/cheetah-config.json`)* | `CHEETAH_DECISION_MODEL` | `/data/workspace/skills/cheetah-strategy/scripts/cheetah-producer.py` |
| Wolverine | `WOLVERINE_WALLET_ADDRESS` *(optional — falls back to `config/wolverine-config.json`)* | `WOLVERINE_DECISION_MODEL` | `/data/workspace/skills/wolverine-strategy/scripts/wolverine-producer.py` |
| Turbine | `TURBINE_WALLET` or `STRATEGY_ADDRESS` *(optional — falls back to `config/turbine-config.json`; Turbine runs ONE daemon that manages both `volume` + `runners` wallets via config)* | `TURBINE_RUNNERS_DECISION_MODEL` / `TURBINE_VOLUME_DECISION_MODEL` | `/data/workspace/skills/turbine-strategy/scripts/turbine-producer.py` |
| Pangolin (the reference wrapper-test) | `PANGOLIN_WALLET` *(required)* | `PANGOLIN_DECISION_MODEL` | `/data/workspace/senpi-skills-src/pangolin/scripts/pangolin-producer.py` |

\* Each producer reads the wallet via `os.environ.get("<SKILL>_WALLET", "")`. "Required" means no config fallback; "optional" means the producer falls through to the wallet field in its own `config/<skill>-config.json`. If you're unsure, run the producer once without the env var and it will print a single-tick error message that names the env var it wants.

### Idempotent helper script

To avoid double-starting the daemon, drop this on the box and re-use it across restarts:

```sh
# /tmp/start-producer.sh
#!/bin/sh
SCRIPT_PATH="$1"; LOG_PATH="$2"; shift 2
PRODUCER_NAME=$(basename "$SCRIPT_PATH")
ALIVE=$(pgrep -af "python3.*$PRODUCER_NAME" | grep -v start-producer.sh | head -1)
if [ -n "$ALIVE" ]; then echo "ALREADY-RUNNING $ALIVE"; exit 0; fi
for kv in "$@"; do export "$kv"; done
nohup python3 -u "$SCRIPT_PATH" > "$LOG_PATH" 2>&1 < /dev/null &
echo "started PID=$!"

# Invocation:
/tmp/start-producer.sh /data/workspace/skills/kodiak-strategy/scripts/kodiak-producer.py \
  /tmp/kodiak-producer.log \
  KODIAK_WALLET=0x5ea74e44a81161e97fc3b77366b21b22b08c5df0 \
  KODIAK_DECISION_MODEL=gemini-3.1-pro-preview
```

### What does NOT restart the daemon (common false leads)

- Railway redeploy / `railway redeploy`: rebuilds the container; bootstrap installs the plugin but **does not start the producer**. Bootstrap source is `/app/src/bootstrap.mjs` on every box; grep it for `python3` — zero hits is the intentional state.
- `openclaw gateway restart` / `pkill -TERM -f openclaw-gateway`: restarts the gateway only; producer is a separate process tree.
- `openclaw senpi runtime create` / `runtime delete`: registers / unregisters the runtime in the gateway. Has zero effect on the producer process.
- `openclaw cron add senpi-producer-…`: this is the **pre-helper** path described in `senpi-trading-runtime/skills/.../references/external-producers.md`. **Do not use it on helper-migrated boxes** — it dispatches a fresh subprocess every interval (the fork-storm pattern the daemon was written to eliminate).
