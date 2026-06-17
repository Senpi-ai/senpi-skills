# 🦁 LION v1.0 — Two-Speed-Market (K-Shaped) Cross-Asset Long/Short

Two thematic **books** on two wallets, one producer. The **long "haves"** book
longs the structural winners of a K-shaped world — the AI complex on Hyperliquid
XYZ (trade.xyz: NVDA, AMD, MRVL, TSM, ASML, ARM, AVGO, CRWV, PLTR, ORCL, …) plus
the crypto winners (HYPE large, SOL modest). The **short "have-nots"** book
shorts the laggards — the broad U.S. market via the SP500 index product plus a
curated, gated basket of laggard crypto alts. The P&L is the **dispersion**
between the two speeds, not market direction. See [SKILL.md](SKILL.md) for the
full thesis and scoring.

> **Net exposure is YOUR decision** (see each config's `_net_exposure_note`). The
> two books run on separate wallets, so you set the posture by the funding split
> + per-leg knobs (`maxSlots`, `marginPct`, `sizingWeights`). **Default = modest
> net-long tilt (~60/40)** matching the directional AI/HYPE conviction. Fund the
> wallets equally to run market-neutral; tilt toward the short book to run
> net-short.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Lion (both books)

```bash
mkdir -p /data/workspace/skills/lion-strategy/{config,scripts,state,references}
for f in scripts/lion-producer.py scripts/lion_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/lion-long-config.json config/lion-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lion/$f" \
    -o "/data/workspace/skills/lion-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export LION_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export LION_LONG_WALLET=<your-haves-book-wallet>        # or set wallet in config/lion-long-config.json
export LION_SHORT_WALLET=<your-havenots-book-wallet>    # or set wallet in config/lion-short-config.json
```

Each book also accepts an optional `LION_LONG_STRATEGY_ID` /
`LION_SHORT_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding sets your net exposure** — see the note above. Default tilt is achieved
by funding the long ("haves") wallet larger than the short ("have-nots") wallet
and by the per-leg defaults (long: 5 slots / 18% margin / 5x; short: 4 slots /
15% margin / 4x). The runtime does not enforce a budget.

### Step 4 — Register both runtimes, start both daemons

```bash
# LONG "haves" book
LION_LEG=long \
LION_LONG_WALLET=$LION_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/lion-strategy/scripts/lion-producer.py \
  > /tmp/lion-long.log 2>&1 < /dev/null &
disown

# SHORT "have-nots" book
LION_LEG=short \
LION_SHORT_WALLET=$LION_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/lion-strategy/scripts/lion-producer.py \
  > /tmp/lion-short.log 2>&1 < /dev/null &
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
pgrep -af lion-producer.py            # expect 2 daemons (long + short)
tail -5 /tmp/lion-long.log /tmp/lion-short.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its conviction `weight` + `excess`). A book with no
qualifying name prints `WAITING — no name cleared min score N`.

---

## Notes

- **Dispersion, not direction.** Long-haves + short-have-nots; the P&L is the
  spread between the two speeds of a K-shaped market.
- **Cross-asset on one wallet.** Each book holds both xyz equities and main-DEX
  crypto; sizes off `max(main, xyz)` account value, never the sum.
- **Long-AI + short-SP500 is intentional** — it isolates the AI-vs-broad-market
  spread (the index contains the AI names).
- **Conviction sizing** via `sizingWeights` (HYPE 1.5×, SOL 0.6×, SP500 1.2×,
  alts 0.7×) — no hardcoded dollars; everything scales with the budget.
- **Short book runs tighter** — lower leverage (4x), tighter max-loss, faster
  stall-cuts; BTC omitted from the default short basket (too reflexive). Tune
  `config.universe` to your view of the laggards.
- **Absolute trend is the gate** — never longs a downtrend, never shorts an
  uptrend, even for a thesis name.
- **XYZ is 24/7** — tokenized equities trade weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
