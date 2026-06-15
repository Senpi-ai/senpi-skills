# 🦏 RHINO v1.0 — Tail-Risk / Crisis-Alpha Hedge Fund

Two **books** on two wallets, one producer, built to carry cheap convexity:
bleed a little in calm, pay big in shocks. The **hedge** book holds a small,
always-on long carry in the crisis-beneficiary complex (gold / oil / dollar /
yen); the **escalation** book is dormant until a shared cross-asset **stress**
detector confirms a shock, then fires hard — long the spiking crisis assets,
short the cratering risk assets. See [SKILL.md](SKILL.md) for the full thesis
and scoring.

> **Funding default: HEDGE 50% / ESCALATION 50%.** The hedge book runs a small
> margin_pct (10%), so even at half the pool it's lightly deployed — that's the
> point (cheap standing insurance). The other half is dry powder the escalation
> book deploys only under stress.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Rhino (both books)

```bash
mkdir -p /data/workspace/skills/rhino-strategy/{config,scripts,state,references}
for f in scripts/rhino-producer.py scripts/rhino_config.py \
         runtime-hedge.yaml runtime-escalation.yaml \
         config/rhino-hedge-config.json config/rhino-escalation-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/rhino/$f" \
    -o "/data/workspace/skills/rhino-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export RHINO_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export RHINO_HEDGE_WALLET=<your-hedge-book-wallet>           # or set wallet in config/rhino-hedge-config.json
export RHINO_ESCALATION_WALLET=<your-escalation-book-wallet>  # or set wallet in config/rhino-escalation-config.json
```

Each book also accepts an optional `RHINO_HEDGE_STRATEGY_ID` /
`RHINO_ESCALATION_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — HEDGE 50% / ESCALATION 50%** of the combined pool (operator
guideline; the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${RHINO_*}` resolve), then launch one daemon per book.

```bash
# HEDGE book (always on)
RHINO_LEG=hedge \
RHINO_HEDGE_WALLET=$RHINO_HEDGE_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/rhino-strategy/scripts/rhino-producer.py \
  > /tmp/rhino-hedge.log 2>&1 < /dev/null &
disown

# ESCALATION book (stress-gated)
RHINO_LEG=escalation \
RHINO_ESCALATION_WALLET=$RHINO_ESCALATION_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/rhino-strategy/scripts/rhino-producer.py \
  > /tmp/rhino-escalation.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM. When an
OpenClaw/shell session is torn down it SIGTERMs its process tree. `setsid`
re-parents each daemon into a new session so it survives; `disown` detaches it
from the shell job table. **For durable persistence**, add a cron keepalive
that relaunches a book if its process is gone — the per-book fcntl lock makes a
double-launch a safe no-op.

---

## Verify

```bash
pgrep -af rhino-producer.py          # expect 2 daemons (hedge + escalation)
tail -5 /tmp/rhino-hedge.log /tmp/rhino-escalation.log
```

Each tick emits JSON with the live `stress` read. In calm, expect the
escalation book to print `DORMANT — no stress (fired N/threshold); hedge book
carries the standing insurance` — that's correct. The hedge book trades a small
defensive position whenever one is trending up.

---

## Notes

- **The stress detector is the brain.** Cross-asset probes (oil/equities/gold/
  BTC) + a BTC vol-expansion flag; `stressThreshold` (default 2) is the count
  needed to declare STRESS and wake the escalation book.
- **Asymmetric by design.** The hedge book is sized small (cheap insurance); the
  escalation book sits in cash until stress, then deploys the convex add and
  banks the spike (crises reverse violently — a ceasefire dumps oil/gold).
- **XYZ is 24/7** — the hedge carries and the stress detector watches weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
