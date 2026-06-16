# 🐙 OCTOPUS v1.0 — Market-Neutral Hedge Fund

Two single-direction **books** on two wallets, one producer. The **long**
book longs the relative leaders of the liquid crypto cross-section; the
**short** book shorts the relative laggards. Equal funding → the notionals
offset → the fund is ~beta-neutral, and the return is driven by the
**dispersion** (leaders minus laggards). See [SKILL.md](SKILL.md) for the
full thesis and scoring.

> **Fund-level neutrality requires equal funding.** Fund the two wallets
> 50/50. If one book is over-funded the fund becomes directional.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Octopus (both books)

```bash
mkdir -p /data/workspace/skills/octopus-strategy/{config,scripts,state,references}
for f in scripts/octopus-producer.py scripts/octopus_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/octopus-long-config.json config/octopus-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/octopus/$f" \
    -o "/data/workspace/skills/octopus-strategy/$f"
done
```

### Step 3 — Required env vars

Both books share the auth token, decision model, and (optional) Telegram chat
id. Each book binds its own wallet:

```bash
export SENPI_AUTH_TOKEN=...
export OCTOPUS_DECISION_MODEL=<your-preferred-model>    # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional, for trade notifications

export OCTOPUS_LONG_WALLET=<your-long-book-wallet>      # or set wallet in config/octopus-long-config.json
export OCTOPUS_SHORT_WALLET=<your-short-book-wallet>    # or set wallet in config/octopus-short-config.json
```

Each book also accepts an optional `OCTOPUS_LONG_STRATEGY_ID` /
`OCTOPUS_SHORT_STRATEGY_ID` (falls back to the `strategyId` field in the
matching config file).

**Funding — LONG 50% / SHORT 50% of the combined pool.** Equal funding is
what keeps the fund market-neutral; the two books' notional exposures offset.
This is an operator funding guideline (documented in each `runtime-*.yaml`
`strategy:` block); the runtime does not pull or enforce a budget.

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (per your host's runtime-register
flow — set the env vars **before** registering so `${OCTOPUS_*}` resolve), then
launch one producer daemon per book. `OCTOPUS_LEG` selects the book.

```bash
# LONG book
OCTOPUS_LEG=long \
OCTOPUS_LONG_WALLET=$OCTOPUS_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/octopus-strategy/scripts/octopus-producer.py \
  > /tmp/octopus-long.log 2>&1 < /dev/null &
disown

# SHORT book
OCTOPUS_LEG=short \
OCTOPUS_SHORT_WALLET=$OCTOPUS_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/octopus-strategy/scripts/octopus-producer.py \
  > /tmp/octopus-short.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` only blocks SIGHUP, not SIGTERM. When an
OpenClaw/shell session is torn down it SIGTERMs its process tree. `setsid`
re-parents each daemon into a new session so it survives session teardown;
`disown` detaches it from the shell job table. Env vars are passed inline so
each daemon's environment is self-contained.

**For durable persistence** (survives host restarts + auto-restarts on death),
add a cron keepalive that relaunches a book if its process is gone — the
producer's per-book fcntl lock makes a double-launch a safe no-op.

---

## Verify

```bash
pgrep -af octopus-producer.py            # expect 2 daemons (long + short)
tail -5 /tmp/octopus-long.log /tmp/octopus-short.log
```

Each tick emits JSON: `scanned`, `ranked_pool`, `candidates`, `signals_pushed`,
`mean_rs_24h`, `emitted` (with per-name `excess` = relative strength). A book
that finds no qualifying name prints `WAITING — no name cleared min score 5`
(normal when the cross-section has no trend-confirmed leader/laggard).

---

## Sizing sanity check

`margin_usd` per slot should be **≈20% of that book's wallet** (`margin_pct`
20). The minimum-order floor **scales with the wallet** — a book places an order
when `margin_usd × leverage ≥ max(account_value × minNotionalPctOfEquity 1%, the
HL venue minimum ~$10)`. The position itself is `account_value × 0.20 × 5 ≈ 1×
account_value` in notional, so any funded wallet above the exchange minimum
trades (the floor is never a hardcoded dollar amount). Each book fills up to 4
slots (4 × 20% = 80% committed, 20% buffer).

---

## Notes

- **Universe** is the live liquid main-DEX crypto board (`dayNtlVlm ≥ $20M`),
  rebuilt every tick — no hardcoded asset list to go stale.
- **XYZ equities are excluded** — Octopus ranks crypto dispersion; XYZ is
  Spider's domain.
- **Stall-cuts are ON** (weak_peak / dead_weight): a position whose relative
  trend mean-reverts is recycled into a fresher name rather than ridden.
- The producer **only opens** positions; the DSL ratchet engine owns all
  exits. Restarting a producer never touches an open position's stop.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
