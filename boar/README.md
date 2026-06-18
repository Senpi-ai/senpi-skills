# 🥇 BOAR v1.0 — Hard Money vs Paper (the debasement bet)

Two **books** on two wallets, one producer. The **hard-money** book longs scarce
real assets — `GOLD`, `BTC` (digital gold), `SILVER`, `PLATINUM`, `PALLADIUM`. The
**paper** book shorts the broad market (`SP500`) + rate-sensitive long-duration
growth (`RIVN`/`DKNG`/`HIMS`) — the fiat-denominated claims that lose most as the
term premium rises. The P&L is the **real-assets-beat-paper-claims spread**. See
[SKILL.md](SKILL.md) for the full thesis.

> **⚠️ Loosest hedge of the family.** In a pure liquidity *melt-up* gold AND stocks
> both rise — the legs can correlate positively. The short tilts to rate-sensitive
> names (RIVN/DKNG/HIMS, which underperform even in a melt-up as the term premium
> compresses multiples) to tighten it, but the hedge is imperfect. Boar works best
> in a "real assets outperform" regime, not a blow-off. Size accordingly.

> **The long/short balance is YOUR funding split** (config `_hedge_note`). Default a
> **net-long-hard-money tilt** (hard-money: 4 slots / 18% / 5x; paper: 4 slots /
> 15% / 4x). Fund 50/50 for a tighter (still imperfect) hedge.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Boar (both books)

```bash
mkdir -p /data/workspace/skills/boar-strategy/{config,scripts,state,references}
for f in scripts/boar-producer.py scripts/boar_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/boar-long-config.json config/boar-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/boar/$f" \
    -o "/data/workspace/skills/boar-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export BOAR_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export BOAR_LONG_WALLET=<your-hardmoney-book-wallet>   # or set wallet in config/boar-long-config.json
export BOAR_SHORT_WALLET=<your-paper-book-wallet>      # or set wallet in config/boar-short-config.json
```

Each book also accepts an optional `BOAR_LONG_STRATEGY_ID` /
`BOAR_SHORT_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding sets your long/short balance** — see the note above. Default is a
net-long-hard-money tilt, achieved by funding the hard-money wallet larger than
the paper wallet and by the per-leg defaults (hard-money: 4 slots / 18% / 5x;
paper: 4 slots / 15% / 4x). The runtime does not enforce a budget.

### Step 4 — Register both runtimes, start both daemons

```bash
# LONG hard-money book
BOAR_LEG=long \
BOAR_LONG_WALLET=$BOAR_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/boar-strategy/scripts/boar-producer.py \
  > /tmp/boar-long.log 2>&1 < /dev/null &
disown

# SHORT paper book
BOAR_LEG=short \
BOAR_SHORT_WALLET=$BOAR_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/boar-strategy/scripts/boar-producer.py \
  > /tmp/boar-short.log 2>&1 < /dev/null &
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
pgrep -af boar-producer.py            # expect 2 daemons (long + short)
tail -5 /tmp/boar-long.log /tmp/boar-short.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its conviction `weight` + `excess`). A book with no
qualifying name prints `WAITING — no name cleared min score N`.

---

## Notes

- **Real assets vs paper.** Long hard money + short paper claims; the P&L is the
  spread between them, not market direction.
- **The hedge is loose** — in a liquidity melt-up gold AND stocks rise together
  (positive correlation). The rate-sensitive short tilt (RIVN/DKNG/HIMS) tightens
  it, but Boar is best as a tilt, not a market-neutral bet.
- **Conviction sizing** via `sizingWeights` (GOLD 1.2×, BTC 1.2×, SILVER 1.0×,
  PLATINUM 0.7×, PALLADIUM 0.6×) — no hardcoded dollars; everything scales with budget.
- **Paper book runs tighter** — lower leverage (4x), tighter max-loss, faster
  stall-cuts; the rate-sensitive growth shorts (RIVN/DKNG/HIMS) are small + squeeze-prone.
- **Absolute trend is the gate** — never longs a downtrend, never shorts an
  uptrend, even for a thesis name.
- **Cross-asset on one wallet** — metals (xyz) + BTC (main DEX); sizes off `max(main, xyz)`. Commodities/crypto trade ~24/7.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
