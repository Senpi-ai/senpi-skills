# 🪙 MONGOOSE v1.0 — On-Chain Finance vs Legacy

Two **books** on two wallets, one producer. The **on-chain** book longs the
on-chain financial rails — `HYPE` (the venue), `CRCL` (Circle/stablecoins),
`COIN` (Coinbase), `HOOD` (Robinhood), `MSTR` + `PURRDAT` (crypto treasuries) —
the disruptors eating legacy finance. The **legacy** book shorts the incumbents +
broad financial-beta (`BX` + `SP500`). The P&L is the **disruptor-vs-incumbent
spread**. See [SKILL.md](SKILL.md) for the full thesis.

> **Honest caveat:** the hedge is **cross-sector and thin** — few pure
> legacy-finance names list on the venue yet, so the short leg leans on `SP500`.
> It nets down market beta rather than perfectly isolating the pair; the long book
> is the strong side. Sharpen the short by adding legacy names to `config.universe`
> as they list.

> **The long/short balance is YOUR funding split** (config `_hedge_note`). The two
> books run on separate wallets. **Default = net-long tilt** (the thesis is bullish
> the disruptors; on-chain: 5 slots / 18% / 5x, legacy: 2 slots / 15% / 4x). Fund
> 50/50 for a tighter hedge.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Mongoose (both books)

```bash
mkdir -p /data/workspace/skills/mongoose-strategy/{config,scripts,state,references}
for f in scripts/mongoose-producer.py scripts/mongoose_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/mongoose-long-config.json config/mongoose-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mongoose/$f" \
    -o "/data/workspace/skills/mongoose-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export MONGOOSE_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export MONGOOSE_LONG_WALLET=<your-onchain-book-wallet>     # or set wallet in config/mongoose-long-config.json
export MONGOOSE_SHORT_WALLET=<your-legacy-book-wallet>     # or set wallet in config/mongoose-short-config.json
```

Each book also accepts an optional `MONGOOSE_LONG_STRATEGY_ID` /
`MONGOOSE_SHORT_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding sets your long/short balance** — see the note above. Default is a
net-long tilt, achieved by funding the on-chain wallet larger than the legacy
wallet and by the per-leg defaults (on-chain: 5 slots / 18% margin / 5x; legacy:
2 slots / 15% margin / 4x). The runtime does not enforce a budget.

### Step 4 — Register both runtimes, start both daemons

```bash
# LONG on-chain book
MONGOOSE_LEG=long \
MONGOOSE_LONG_WALLET=$MONGOOSE_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/mongoose-strategy/scripts/mongoose-producer.py \
  > /tmp/mongoose-long.log 2>&1 < /dev/null &
disown

# SHORT legacy book
MONGOOSE_LEG=short \
MONGOOSE_SHORT_WALLET=$MONGOOSE_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/mongoose-strategy/scripts/mongoose-producer.py \
  > /tmp/mongoose-short.log 2>&1 < /dev/null &
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
pgrep -af mongoose-producer.py            # expect 2 daemons (long + short)
tail -5 /tmp/mongoose-long.log /tmp/mongoose-short.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its conviction `weight` + `excess`). A book with no
qualifying name prints `WAITING — no name cleared min score N`.

---

## Notes

- **Disruptor vs incumbent.** Long the on-chain rails + short legacy finance/index;
  the P&L is the spread between them.
- **The hedge is loose** — cross-sector, thin short universe, index-led. It nets
  down market beta; add legacy-finance names to the short `config.universe` as they
  list to sharpen it. The long book is the strong, high-conviction side.
- **Conviction sizing** via `sizingWeights` (HYPE 1.3×, CRCL 1.2×, COIN/HOOD 1.0×,
  MSTR 0.7×, PURRDAT 0.6×) — no hardcoded dollars; everything scales with the budget.
- **Treasury proxies sized down** — MSTR/PURRDAT are levered BTC/HYPE proxies
  (double-count risk vs HYPE), so they carry smaller weights.
- **Legacy book runs tighter** — lower leverage (4x), tighter max-loss, faster
  stall-cuts.
- **Absolute trend is the gate** — never longs a downtrend, never shorts an
  uptrend; a capitulation guard avoids shorting an exhausted bottom.
- **Cross-asset on one wallet** — each book mixes xyz equities and main-DEX crypto
  (HYPE); sizes off `max(main, xyz)` account value, never the sum. ~24/7.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
