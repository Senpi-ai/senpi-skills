# 🐱 CARACAL v1.0 — Volatility Hedge Fund

Two volatility-expansion **books** on two wallets, one producer, trading
**both directions**. The **breakout** book rides coiled-spring breakouts in
liquid crypto; the **catalyst** book runs the same compression→expansion
engine on XYZ (equities / energy / metals / indices), capturing oil and
AI-infra moves as direction-agnostic vol events, 24/7. See [SKILL.md](SKILL.md)
for the full thesis and scoring.

> **Fund the two books ~equally (50/50).** They harvest the same edge
> (volatility expansion) on two universes; equal funding diversifies across
> crypto and macro/equity vol.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Caracal (both books)

```bash
mkdir -p /data/workspace/skills/caracal-strategy/{config,scripts,state,references}
for f in scripts/caracal-producer.py scripts/caracal_config.py \
         runtime-breakout.yaml runtime-catalyst.yaml \
         config/caracal-breakout-config.json config/caracal-catalyst-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/caracal/$f" \
    -o "/data/workspace/skills/caracal-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export CARACAL_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                               # optional

export CARACAL_BREAKOUT_WALLET=<your-breakout-book-wallet>  # or set wallet in config/caracal-breakout-config.json
export CARACAL_CATALYST_WALLET=<your-catalyst-book-wallet>  # or set wallet in config/caracal-catalyst-config.json
```

Each book also accepts an optional `CARACAL_BREAKOUT_STRATEGY_ID` /
`CARACAL_CATALYST_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — BREAKOUT 50% / CATALYST 50%** of the combined pool (operator
guideline; the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${CARACAL_*}` resolve), then launch one daemon per book.

```bash
# BREAKOUT book (crypto)
CARACAL_LEG=breakout \
CARACAL_BREAKOUT_WALLET=$CARACAL_BREAKOUT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/caracal-strategy/scripts/caracal-producer.py \
  > /tmp/caracal-breakout.log 2>&1 < /dev/null &
disown

# CATALYST book (XYZ)
CARACAL_LEG=catalyst \
CARACAL_CATALYST_WALLET=$CARACAL_CATALYST_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/caracal-strategy/scripts/caracal-producer.py \
  > /tmp/caracal-catalyst.log 2>&1 < /dev/null &
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
pgrep -af caracal-producer.py           # expect 2 daemons (breakout + catalyst)
tail -5 /tmp/caracal-breakout.log /tmp/caracal-catalyst.log
```

Each tick emits JSON: `scanned`, `candidates`, `signals_pushed`, `emitted`
(with per-name `squeeze` = coil ratio and `surge` = expansion multiple). A book
with no coiled breakout prints `WAITING — no coiled breakout cleared min score 5`
(normal — Caracal is episodic by design; most ticks are empty).

---

## Notes

- **Direction-agnostic** — both books trade LONG and SHORT; the direction is the
  break direction, never a fixed bias.
- **Episodic by design** — it only fires on a coil + break + surge, so most
  ticks return empty. That's the edge (selectivity), not a bug.
- **Tight DSL** — a failed breakout reverses fast, so phase1 cuts at 12% and
  phase2 locks early (8% → 30%).
- **XYZ is 24/7** — the catalyst book trades weekends (oil/indices/metals stay
  active); no market-hours gating.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
