# 🎯 THESIS FUND v1.0 — bet your view, run with discipline

One configurable engine that expresses a **macro view** of your choosing. Pick
a `THESIS` preset, fund one wallet, and the engine trades the long/short basket
that expresses it — pressing each name only when the market confirms the thesis,
and de-risking via the DSL when it doesn't. See [SKILL.md](SKILL.md) for the
presets and scoring.

> **One wallet = one bet.** Unlike the two-wallet style funds, a Thesis Fund is
> a single coherent view held in one wallet. Deploy one per thesis you want to run.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull the Thesis Fund engine

```bash
mkdir -p /data/workspace/skills/thesis-fund-strategy/{config,scripts,state,references}
for f in scripts/thesis-producer.py scripts/thesis_config.py \
         runtime.yaml \
         config/thesis-presets.json config/thesis-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/thesis-fund/$f" \
    -o "/data/workspace/skills/thesis-fund-strategy/$f"
done
```

### Step 3 — Required env vars (pick your thesis)

```bash
export SENPI_AUTH_TOKEN=...
export THESIS_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export THESIS=risk_off                                  # the preset you want — see thesis-presets.json
export THESIS_WALLET=<your-wallet>                       # or set wallet in config/thesis-config.json
```

Valid `THESIS` values (out of the box): `risk_off`, `recovery`, `war_escalation`,
`war_recovery`, `hype_vs_market`, `gold_over_btc`, `btc_over_gold`. Add your own
in `thesis-presets.json`. Optional `THESIS_STRATEGY_ID` falls back to `strategyId`
in the config.

### Step 4 — Register the runtime, start the daemon

Register `runtime.yaml` with the gateway (set env vars **before** registering so
`${THESIS_WALLET}` / `${THESIS_DECISION_MODEL}` resolve), then launch one daemon:

```bash
THESIS=$THESIS \
THESIS_WALLET=$THESIS_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/thesis-fund-strategy/scripts/thesis-producer.py \
  > /tmp/thesis-$THESIS.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM. When an
OpenClaw/shell session is torn down it SIGTERMs its process tree. `setsid`
re-parents the daemon into a new session so it survives; `disown` detaches it
from the shell job table. **For durable persistence**, add a cron keepalive that
relaunches the daemon if it's gone — the fcntl lock makes a double-launch a safe no-op.

Want to run more than one thesis? Deploy the engine again on a separate wallet
with a different `THESIS`.

---

## Verify

```bash
pgrep -af thesis-producer.py
tail -5 /tmp/thesis-$THESIS.log
```

Each tick emits JSON: `thesis`, `preset`, `basket_size`, `candidates`,
`signals_pushed`, `emitted`. When no basket name is confirming the view it prints
`WAITING — no basket name confirmed the thesis` (expected — the fund waits for
the view to start working rather than fighting the tape).

---

## Notes

- **It's confirmation-gated, not a blind hold** — a basket name only opens when
  trend + momentum confirm the thesis direction; names fighting the thesis are
  skipped, and the DSL recycles names that stop working.
- **The drawdown gate (20%)** halts the whole fund if the thesis is broadly failing.
- **Theses have a shelf life** — event-driven presets (war, election cycle) should
  be retired/updated as the situation resolves. The basket lives in `thesis-presets.json`.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
