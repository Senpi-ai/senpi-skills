# 🐾 CUB v1.0 — Lion + Pre-IPO

A variation of **[Lion](../lion/)** that allocates **~90%** to the Lion two-speed
AI long/short engine and **~10%** to a **pre-IPO ramp satellite**. THREE books on
three wallets, one producer:

- **`long` + `short` (≈90%)** — the Lion engine: LONG the "haves" (AI complex +
  HYPE/SOL) and SHORT the "have-nots" (broad U.S. market via SP500 + laggard
  alts). A K-shaped dispersion bet, trend-confirmed and conviction-sized.
- **`preipo` (≈10%)** — auto-discovers pre-IPO perpetuals (IPOPs) by their funding
  signature (Lemur method) and LONGS the ones ramping into their listing (the
  SpaceX / Cerebras pattern). Catches the next AI winner before it converts.

See [SKILL.md](SKILL.md) for the full thesis and scoring.

> **The 90/10 split is YOUR funding allocation.** The three books run on separate
> wallets, so you set the split by how much you fund into each — not a hardcoded
> constant. **Default:** ~90% across the `long`+`short` wallets (split by Lion's
> net-exposure dial, default modest net-long ~60/40), ~10% into the `preipo`
> wallet. Fund only long+short to run the AI engine pure; raise the preipo share
> to lean into pre-IPO optionality.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Cub (all three books)

```bash
mkdir -p /data/workspace/skills/cub-strategy/{config,scripts,state,references}
for f in scripts/cub-producer.py scripts/cub_config.py \
         runtime-long.yaml runtime-short.yaml runtime-preipo.yaml \
         config/cub-long-config.json config/cub-short-config.json config/cub-preipo-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cub/$f" \
    -o "/data/workspace/skills/cub-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export CUB_DECISION_MODEL=<your-preferred-model>      # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional

export CUB_LONG_WALLET=<your-haves-book-wallet>        # ~90% (with short), or set wallet in config
export CUB_SHORT_WALLET=<your-havenots-book-wallet>    # ~90% (with long), or set wallet in config
export CUB_PREIPO_WALLET=<your-preipo-satellite-wallet> # ~10%, or set wallet in config
```

Each book also accepts an optional `CUB_<LEG>_STRATEGY_ID` (falls back to
`strategyId` in the config).

**Funding sets your 90/10 allocation + net exposure** — see the note above. Fund
the `long`+`short` wallets with ~90% of the pool (split by Lion's net-exposure
dial; per-leg defaults long: 5 slots / 18% / 5x, short: 4 slots / 15% / 4x) and
the `preipo` wallet with ~10% (3 slots / 15% / 5x). The runtime does not enforce
a budget. Skip the preipo wallet entirely to run just the Lion engine.

### Step 4 — Register all runtimes, start all daemons

```bash
# LONG "haves" book (part of the ~90%)
CUB_LEG=long \
CUB_LONG_WALLET=$CUB_LONG_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/cub-strategy/scripts/cub-producer.py \
  > /tmp/cub-long.log 2>&1 < /dev/null &
disown

# SHORT "have-nots" book (part of the ~90%)
CUB_LEG=short \
CUB_SHORT_WALLET=$CUB_SHORT_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/cub-strategy/scripts/cub-producer.py \
  > /tmp/cub-short.log 2>&1 < /dev/null &
disown

# PREIPO ramp satellite (~10%)
CUB_LEG=preipo \
CUB_PREIPO_WALLET=$CUB_PREIPO_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/cub-strategy/scripts/cub-producer.py \
  > /tmp/cub-preipo.log 2>&1 < /dev/null &
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
pgrep -af cub-producer.py            # expect 3 daemons (long + short + preipo)
tail -5 /tmp/cub-long.log /tmp/cub-short.log /tmp/cub-preipo.log
```

Each tick emits JSON: `scanned`, `mean_rs_24h`, `candidates`, `signals_pushed`,
`emitted` (each with its conviction `weight` + `excess`). The `long`/`short`
books print `WAITING — no name cleared min score N` when idle; the `preipo` book
prints `WAITING — no live IPOPs ramping` (episodic — most ticks find 0–2 IPOPs).

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
- **Pre-IPO satellite is episodic** — the `preipo` book discovers IPOPs by funding
  signature (no static list) and longs the ones ramping; most ticks find 0–2.
  Liquidity is budget-relative (no $ floor). When an IPOP converts to a standard
  equity it graduates out of this book (into the haves book if it's an AI name).
- **XYZ is 24/7** — tokenized equities trade weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
