# 🐫 CAMEL v1.0 — Carry Hedge Fund

Two single-direction **books** on two wallets, one producer, harvesting
**funding carry**. The **harvest** book shorts the most-positive-funding
names (longs pay shorts → short collects); the **payout** book longs the
most-negative-funding names (shorts pay longs → paid to hold). Both are gated
to *exhausting* crowds so price doesn't fight the carry. See [SKILL.md](SKILL.md)
for the full thesis and scoring.

> **Fund the two books ~equally (50/50).** Both harvest a recurring funding
> inefficiency; equal funding balances the carry book and the slight net-neutral
> skew it produces.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Camel (both books)

```bash
mkdir -p /data/workspace/skills/camel-strategy/{config,scripts,state,references}
for f in scripts/camel-producer.py scripts/camel_config.py \
         runtime-harvest.yaml runtime-payout.yaml \
         config/camel-harvest-config.json config/camel-payout-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/camel/$f" \
    -o "/data/workspace/skills/camel-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export CAMEL_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export CAMEL_HARVEST_WALLET=<your-harvest-book-wallet>  # or set wallet in config/camel-harvest-config.json
export CAMEL_PAYOUT_WALLET=<your-payout-book-wallet>    # or set wallet in config/camel-payout-config.json
```

Each book also accepts an optional `CAMEL_HARVEST_STRATEGY_ID` /
`CAMEL_PAYOUT_STRATEGY_ID` (falls back to `strategyId` in the matching config).

**Funding — HARVEST 50% / PAYOUT 50%** of the combined pool. This is an
operator funding guideline (documented in each `runtime-*.yaml` `strategy:`
block); the runtime does not pull or enforce a budget.

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${CAMEL_*}` resolve), then launch one daemon per book.

```bash
# HARVEST book
CAMEL_LEG=harvest \
CAMEL_HARVEST_WALLET=$CAMEL_HARVEST_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/camel-strategy/scripts/camel-producer.py \
  > /tmp/camel-harvest.log 2>&1 < /dev/null &
disown

# PAYOUT book
CAMEL_LEG=payout \
CAMEL_PAYOUT_WALLET=$CAMEL_PAYOUT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/camel-strategy/scripts/camel-producer.py \
  > /tmp/camel-payout.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM. When an
OpenClaw/shell session is torn down it SIGTERMs its process tree. `setsid`
re-parents each daemon into a new session so it survives; `disown` detaches it
from the shell job table. **For durable persistence**, add a cron keepalive
that relaunches a book if its process is gone — the producer's per-book fcntl
lock makes a double-launch a safe no-op.

---

## Verify

```bash
pgrep -af camel-producer.py             # expect 2 daemons (harvest + payout)
tail -5 /tmp/camel-harvest.log /tmp/camel-payout.log
```

Each tick emits JSON: `scanned`, `ranked_pool`, `candidates`, `signals_pushed`,
`top_funding_annpct`, `emitted` (with per-name `funding_annpct`). A book that
finds no qualifying name prints `WAITING — no name cleared min score 4` (normal
when no extreme-funding crowd is also exhausting).

---

## Notes

- **Funding source** is the always-available instrument board (`context.funding`,
  hourly decimal). The ClickHouse-backed `funding_history` endpoint is NOT
  required (it can 503 / need elevated scope) — used only as optional enrichment.
- **Tighter DSL** than a momentum leg: carry P&L per period is small, so price
  losses are cut fast (phase1 10%) and gains are locked as the fade/bounce works.
- **Fee discipline:** carry is income that compounds slowly; keep turnover modest
  (`per_asset_cooldown` 180m, `max_entries_per_day` 6) so fees don't eat the funding.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
