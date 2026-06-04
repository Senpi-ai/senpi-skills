# 🐘 ELEPHANT v1.0 — Global-Macro Hedge Fund

Two macro **books** on two wallets, one producer, over the cross-asset macro
complex — equity indices, precious metals, energy, FX (all on XYZ) plus BTC.
The **trend** book rides the medium-term macro trend; the **fade** book fades
macro over-extensions back to regime. Both trade both directions. See
[SKILL.md](SKILL.md) for the full thesis and scoring.

> **Funding default: TREND 60% / FADE 40%.** The trend book is the
> fee-efficient let-it-run engine and carries the larger share; the fade book
> turns over faster and is funded smaller.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Elephant (both books)

```bash
mkdir -p /data/workspace/skills/elephant-strategy/{config,scripts,state,references}
for f in scripts/elephant-producer.py scripts/elephant_config.py \
         runtime-trend.yaml runtime-fade.yaml \
         config/elephant-trend-config.json config/elephant-fade-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/elephant/$f" \
    -o "/data/workspace/skills/elephant-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export ELEPHANT_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                               # optional

export ELEPHANT_TREND_WALLET=<your-trend-book-wallet>     # or set wallet in config/elephant-trend-config.json
export ELEPHANT_FADE_WALLET=<your-fade-book-wallet>       # or set wallet in config/elephant-fade-config.json
```

Each book also accepts an optional `ELEPHANT_TREND_STRATEGY_ID` /
`ELEPHANT_FADE_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — TREND 60% / FADE 40%** of the combined pool (operator guideline;
the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${ELEPHANT_*}` resolve), then launch one daemon per book.

```bash
# TREND book
ELEPHANT_LEG=trend \
ELEPHANT_TREND_WALLET=$ELEPHANT_TREND_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/elephant-strategy/scripts/elephant-producer.py \
  > /tmp/elephant-trend.log 2>&1 < /dev/null &
disown

# FADE book
ELEPHANT_LEG=fade \
ELEPHANT_FADE_WALLET=$ELEPHANT_FADE_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/elephant-strategy/scripts/elephant-producer.py \
  > /tmp/elephant-fade.log 2>&1 < /dev/null &
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
pgrep -af elephant-producer.py          # expect 2 daemons (trend + fade)
tail -5 /tmp/elephant-trend.log /tmp/elephant-fade.log
```

Each tick emits JSON: `scanned`, `candidates`, `signals_pushed`, `emitted`. A
book with no qualifying macro name prints `WAITING — no macro name cleared min
score N` (normal — the macro complex moves slowly).

---

## Notes

- **Universe is a curated macro whitelist** (indices/metals/energy/FX + BTC),
  intersected with the live board so unavailable names are skipped — it does
  NOT pull in AI/Tech equities (that's Spider's domain).
- **XYZ is 24/7** — the macro books trade weekends (indices/metals/oil stay active).
- **Trend book = wide DSL** (let macro trends run, 7d timeout); **fade book =
  tight DSL** (bank the snapback, 2d timeout).
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
