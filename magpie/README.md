# 🐦 MAGPIE v1.0 — IPO / New-Listing Event Hedge Fund

Two **books** on two wallets, one producer, trading the full pre-IPO → listing →
graduation arc of tokenized equities on Hyperliquid XYZ (trade.xyz IPOPs like
SpaceX). The **pre-listing** book auto-discovers IPOPs by their funding
signature and rides the ramp into the IPO; the **graduation** book detects the
IPOP→equity conversion and rides the explosive first-days momentum — the SpaceX
$1.4B-day-1 pattern. See [SKILL.md](SKILL.md) for the full thesis.

> **Funding default: PRE-LISTING 50% / GRADUATION 50%.** The pre-listing book
> accumulates into the IPO; the graduation book holds dry powder for the
> conversion pop.

> **Requires user-scope auth** for `leaderboard_get_markets` (Smart-Money
> confirmation).

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Magpie (both books)

```bash
mkdir -p /data/workspace/skills/magpie-strategy/{config,scripts,state,references}
for f in scripts/magpie-producer.py scripts/magpie_config.py \
         runtime-pre_listing.yaml runtime-graduation.yaml \
         config/magpie-pre_listing-config.json config/magpie-graduation-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/magpie/$f" \
    -o "/data/workspace/skills/magpie-strategy/$f"
done
```

### Step 3 — Required env vars

```bash
export SENPI_AUTH_TOKEN=...
export MAGPIE_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                              # optional

export MAGPIE_PRE_LISTING_WALLET=<your-pre-listing-book-wallet>   # or set wallet in config/magpie-pre_listing-config.json
export MAGPIE_GRADUATION_WALLET=<your-graduation-book-wallet>     # or set wallet in config/magpie-graduation-config.json
```

Each book also accepts an optional `MAGPIE_PRE_LISTING_STRATEGY_ID` /
`MAGPIE_GRADUATION_STRATEGY_ID` (falls back to `strategyId` in the config).

**Funding — PRE-LISTING 50% / GRADUATION 50%** of the combined pool (operator
guideline; the runtime does not enforce a budget).

### Step 4 — Register both runtimes, start both daemons

```bash
# PRE-LISTING book
MAGPIE_LEG=pre_listing \
MAGPIE_PRE_LISTING_WALLET=$MAGPIE_PRE_LISTING_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/magpie-strategy/scripts/magpie-producer.py \
  > /tmp/magpie-pre_listing.log 2>&1 < /dev/null &
disown

# GRADUATION book
MAGPIE_LEG=graduation \
MAGPIE_GRADUATION_WALLET=$MAGPIE_GRADUATION_WALLET \
SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/magpie-strategy/scripts/magpie-producer.py \
  > /tmp/magpie-graduation.log 2>&1 < /dev/null &
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
pgrep -af magpie-producer.py          # expect 2 daemons (pre_listing + graduation)
tail -5 /tmp/magpie-pre_listing.log /tmp/magpie-graduation.log
```

Both books are **episodic by design** — most ticks are empty. Pre-listing prints
its `ipop_universe`; graduation prints `ipops_now` + `conversions_in_window` and
`WAITING — no IPOP→equity conversion inside the eligibility window` until a
listing actually converts.

---

## Notes

- **Funding signature is the detector.** IPOP = `|funding| ≤ ~1e-7` AND
  `max_leverage ≤ 5`; a conversion is an IPOP that flips to STANDARD (funding
  jumps ~100×, cap lifts). The graduation book persists a class cache to detect
  the flip against the prior tick.
- **Auto-expands.** Today the IPOP universe is ~just SPCX; new trade.xyz
  pre-IPO listings (ANTHROPIC / OPENAI / STRIPE / …) join automatically.
- **Conversion window** keeps a freshly-converted name tradeable for days, not
  just the single flip tick.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
