# ⚡ EEL v1.0 — Electrons vs Hydrocarbons (the AI-power pair)

Two **books** on two wallets, one producer — an **energy-sector-neutral** pair.
The **power** book longs the AI-power complex on Hyperliquid XYZ — uranium
(`URNM`), gas-fired power (`NATGAS`), grid copper (`COPPER`), fuel cells (`BE`),
rare-earth (`USAR`). The **oil** book shorts crude (`BRENTOIL` + `CL`/WTI). Both
legs are energy, so an energy-wide move washes out and the P&L is the
**electrons-vs-barrels spread** — the bet that AI datacenter electricity demand
structurally favors power over oil. See [SKILL.md](SKILL.md) for the full thesis.

> **The long/short balance is YOUR funding split** (see config `_hedge_note`). The
> two books run on separate wallets, so you set the posture by how much you fund
> each. **Default = slight long-power tilt (~55/45)** (power: 4 slots / 18% / 5x;
> oil: 2 slots / 15% / 4x). Fund 50/50 for a cleaner market-neutral spread, or
> tilt to the oil book if you expect a crude shock.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Eel (both books)

```bash
mkdir -p /data/workspace/skills/eel-strategy/{config,scripts,state,references}
for f in scripts/eel-producer.py scripts/eel_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/eel-long-config.json config/eel-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/eel/$f" \
    -o "/data/workspace/skills/eel-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export EEL_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export EEL_LONG_WALLET=<your-power-book-wallet>        # or set wallet in config/eel-long-config.json
export EEL_SHORT_WALLET=<your-oil-book-wallet>         # or set wallet in config/eel-short-config.json
```

Each book also accepts an optional `EEL_LONG_STRATEGY_ID` /
`EEL_SHORT_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding sets your long/short balance** — see the note above. Default tilt is a
slight long-power lean, achieved by funding the power wallet larger than the oil
wallet and by the per-leg defaults (power: 4 slots / 18% margin / 5x; oil: 2
slots / 15% margin / 4x). The runtime does not enforce a budget.

### Step 4 — Register both runtimes, start both daemons

```bash
# LONG power book
EEL_LEG=long \
EEL_LONG_WALLET=$EEL_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/eel-strategy/scripts/eel-producer.py \
  > /tmp/eel-long.log 2>&1 < /dev/null &
disown

# SHORT oil book
EEL_LEG=short \
EEL_SHORT_WALLET=$EEL_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/eel-strategy/scripts/eel-producer.py \
  > /tmp/eel-short.log 2>&1 < /dev/null &
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
pgrep -af eel-producer.py            # expect 2 daemons (long + short)
tail -5 /tmp/eel-long.log /tmp/eel-short.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its conviction `weight` + `excess`). A book with no
qualifying name prints `WAITING — no name cleared min score N`.

---

## Notes

- **Dispersion, not direction.** Long power + short oil; the P&L is the
  electrons-vs-hydrocarbons spread. An energy-wide move cancels across the legs.
- **Conviction sizing** via `sizingWeights` (URNM 1.2×, COPPER 1.1×, NATGAS 1.0×,
  USAR 0.7×, BE 0.6×) — no hardcoded dollars; everything scales with the budget.
- **Oil book runs tighter** — lower leverage (4x), tighter max-loss, faster
  stall-cuts; an oil supply-shock spike is violent.
- **Absolute trend is the gate** — never longs a downtrend, never shorts an
  uptrend; a capitulation guard avoids shorting an exhausted oil bottom.
- **Inverts on a supply shock** — a Mideast oil spike or cold-winter natgas spike
  can move hydrocarbons independent of the thesis; the DSL owns those drawdowns.
- **Commodities trade ~24/7** on Hyperliquid.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
