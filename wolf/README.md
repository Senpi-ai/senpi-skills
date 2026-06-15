# 🐺 WOLF v1.0 — Event-Driven / Regime-Rotation Hedge Fund

Two **books** on two wallets, one producer, that both read a shared cross-asset
**regime** detector (equities + oil + gold + BTC + the dollar) and fire only
when the regime agrees with their mandate. The **risk-on** book longs
beaten-down beta in a confirmed risk-on regime; the **risk-off** book longs
defensives and shorts risk in a confirmed risk-off regime. Capital rotates to
whichever book the regime favors. See [SKILL.md](SKILL.md) for the full thesis
and scoring.

> **Funding default: RISK-ON 50% / RISK-OFF 50%.** Wolf rotates — usually only
> one book is active at a time (the one the regime favors). A 50/50 split gives
> each regime equal firepower, since you can't know which will dominate the period.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Wolf (both books)

```bash
mkdir -p /data/workspace/skills/wolf-strategy/{config,scripts,state,references}
for f in scripts/wolf-producer.py scripts/wolf_config.py \
         runtime-risk_on.yaml runtime-risk_off.yaml \
         config/wolf-risk_on-config.json config/wolf-risk_off-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolf/$f" \
    -o "/data/workspace/skills/wolf-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export WOLF_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                            # optional

export WOLF_RISK_ON_WALLET=<your-risk-on-book-wallet>   # or set wallet in config/wolf-risk_on-config.json
export WOLF_RISK_OFF_WALLET=<your-risk-off-book-wallet>  # or set wallet in config/wolf-risk_off-config.json
```

Each book also accepts an optional `WOLF_RISK_ON_STRATEGY_ID` /
`WOLF_RISK_OFF_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — RISK-ON 50% / RISK-OFF 50%** of the combined pool (operator
guideline; the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

Register each runtime YAML with the gateway (set env vars **before**
registering so `${WOLF_*}` resolve), then launch one daemon per book.

```bash
# RISK-ON book
WOLF_LEG=risk_on \
WOLF_RISK_ON_WALLET=$WOLF_RISK_ON_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/wolf-strategy/scripts/wolf-producer.py \
  > /tmp/wolf-risk_on.log 2>&1 < /dev/null &
disown

# RISK-OFF book
WOLF_LEG=risk_off \
WOLF_RISK_OFF_WALLET=$WOLF_RISK_OFF_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/wolf-strategy/scripts/wolf-producer.py \
  > /tmp/wolf-risk_off.log 2>&1 < /dev/null &
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
pgrep -af wolf-producer.py          # expect 2 daemons (risk_on + risk_off)
tail -5 /tmp/wolf-risk_on.log /tmp/wolf-risk_off.log
```

Each tick emits JSON with the live `regime` read. Expect **one book trading and
one `STANDING DOWN`** at any time — that's the rotation working. A book whose
regime is in force but with no qualifying name prints `WAITING — {REGIME}
confirmed but no name cleared min score N`.

---

## Notes

- **The regime detector is the brain.** Both books read the same cross-asset
  read (equities/oil/gold/BTC/dollar 4h trends); `regimeThreshold` (default 2)
  is the net vote needed to declare a regime, so no single asset flips the book.
- **Rotation, not a fixed bet.** In RISK-ON the risk-on book longs beaten-down
  beta; in RISK-OFF the risk-off book longs defensives + shorts risk; in NEUTRAL
  both wait. A regime flip stops the losing book from *adding* — open winners
  still trail out via the DSL ladder.
- **XYZ is 24/7** — the regime probes and defensives trade weekends.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
