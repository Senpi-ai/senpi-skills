# 🐆 COUGAR v1.0 — U.S. Equity Long/Short Hedge Fund

Two **books** on two wallets, one producer, over the tokenized U.S. equity
universe on Hyperliquid XYZ (trade.xyz: NVDA, TSLA, AAPL, AMZN, … + index
products). The **long** book longs the relative-strength leaders; the **short**
book shorts the laggards — both trend-confirmed. Funded equally, the pair is
**~market-neutral** and harvests **equity dispersion** (leaders minus laggards).
See [SKILL.md](SKILL.md) for the full thesis and scoring.

> **Funding default: LONG 50% / SHORT 50%.** Fund the two books equally so their
> notional offsets and the fund stays beta-neutral.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Cougar (both books)

```bash
mkdir -p /data/workspace/skills/cougar-strategy/{config,scripts,state,references}
for f in scripts/cougar-producer.py scripts/cougar_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/cougar-long-config.json config/cougar-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cougar/$f" \
    -o "/data/workspace/skills/cougar-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export COUGAR_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                              # optional

export COUGAR_LONG_WALLET=<your-long-book-wallet>        # or set wallet in config/cougar-long-config.json
export COUGAR_SHORT_WALLET=<your-short-book-wallet>      # or set wallet in config/cougar-short-config.json
```

Each book also accepts an optional `COUGAR_LONG_STRATEGY_ID` /
`COUGAR_SHORT_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — LONG 50% / SHORT 50%** of the combined pool (operator guideline;
the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

```bash
# LONG book
COUGAR_LEG=long \
COUGAR_LONG_WALLET=$COUGAR_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/cougar-strategy/scripts/cougar-producer.py \
  > /tmp/cougar-long.log 2>&1 < /dev/null &
disown

# SHORT book
COUGAR_LEG=short \
COUGAR_SHORT_WALLET=$COUGAR_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/cougar-strategy/scripts/cougar-producer.py \
  > /tmp/cougar-short.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM. When an
OpenClaw/shell session is torn down it SIGTERMs its process tree. `setsid`
re-parents each daemon into a new session so it survives; `disown` detaches it
from the shell job table. **For durable persistence**, add a cron keepalive that
relaunches a book if its process is gone — the per-book fcntl lock makes a
double-launch a safe no-op.

---

## Verify

```bash
pgrep -af cougar-producer.py          # expect 2 daemons (long + short)
tail -5 /tmp/cougar-long.log /tmp/cougar-short.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its `excess` RS). A book with no qualifying name prints
`WAITING — no equity cleared min score N`.

---

## Notes

- **Dispersion, not direction.** Long-leaders + short-laggards nets to
  ~beta-neutral; the P&L is the spread between the best and worst stocks.
- **Curated equity whitelist** intersected with the live board — new trade.xyz
  listings auto-join once added to `config.equities` and live.
- **XYZ is 24/7** — tokenized equities trade weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
