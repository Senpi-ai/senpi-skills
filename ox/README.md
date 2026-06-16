# 🐂 OX v1.0 — Risk-Parity / All-Weather Hedge Fund

Two **books** on two wallets, one producer. Ox is the fund line's **core
holding** — its edge is **risk balancing**, not a view. Each sleeve is sized by
**inverse realized volatility**, so no single asset class dominates portfolio
risk (true risk parity). The **core** book holds a vol-balanced LONG basket
across asset-class sleeves; the **ballast** book holds defensives and scales
them up when the tape turns risk-off. Always invested, low leverage, low
turnover. See [SKILL.md](SKILL.md) for the full mechanic.

> **Funding default: CORE 70% / BALLAST 30%.** The core is the all-weather
> holding and carries the larger share; the ballast is the smaller defensive
> overlay.

> **Sizing dependency:** Ox emits a *different* `marginUsd` per sleeve (the
> inverse-vol weight). Its risk parity depends on the runtime honoring
> per-signal `signal.data.marginUsd`, not a flat `margin_pct`.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Ox (both books)

```bash
mkdir -p /data/workspace/skills/ox-strategy/{config,scripts,state,references}
for f in scripts/ox-producer.py scripts/ox_config.py \
         runtime-core.yaml runtime-ballast.yaml \
         config/ox-core-config.json config/ox-ballast-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/ox/$f" \
    -o "/data/workspace/skills/ox-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export OX_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                          # optional

export OX_CORE_WALLET=<your-core-book-wallet>        # or set wallet in config/ox-core-config.json
export OX_BALLAST_WALLET=<your-ballast-book-wallet>  # or set wallet in config/ox-ballast-config.json
```

Each book also accepts an optional `OX_CORE_STRATEGY_ID` /
`OX_BALLAST_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — CORE 70% / BALLAST 30%** of the combined pool (operator guideline;
the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${OX_*}` resolve), then launch one daemon per book.

```bash
# CORE book
OX_LEG=core \
OX_CORE_WALLET=$OX_CORE_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/ox-strategy/scripts/ox-producer.py \
  > /tmp/ox-core.log 2>&1 < /dev/null &
disown

# BALLAST book
OX_LEG=ballast \
OX_BALLAST_WALLET=$OX_BALLAST_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/ox-strategy/scripts/ox-producer.py \
  > /tmp/ox-ballast.log 2>&1 < /dev/null &
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
pgrep -af ox-producer.py          # expect 2 daemons (core + ballast)
tail -5 /tmp/ox-core.log /tmp/ox-ballast.log
```

Each tick emits JSON with the inverse-vol `emitted` list (each sleeve's
`weight_pct` + `margin_usd`). On first deploy the core fills the basket; after
that it mostly holds and rebalances slowly. The ballast prints its live
`risk_off` read and scales its budget accordingly.

---

## Notes

- **Inverse-vol weighting is the product.** Low-vol sleeves (gold/indices) get
  more notional, high-vol sleeves (crypto) less — risk is balanced, not capital.
  Weights are computed over the **full basket**, so a re-entering sleeve gets its
  correct fractional weight, never the whole budget.
- **Always invested, never shorts.** The all-weather behavior comes from
  diversification + the defensive sleeves + the ballast overlay, not from going
  to cash or shorting.
- **Low leverage (3x), low turnover (600s).** This is a core holding, not a trade.
- **XYZ is 24/7** — most sleeves trade weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
