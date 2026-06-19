# 🐋 WHALEHUNTERHEDGE v1.0 — Patient-Whale Conviction Copy (long/short)

Follow the **single biggest bet** of the patient winners. WhaleHunterHedge shadows
traders tagged **ELITE** (consistency) + **PATIENT** (activity) on Senpi Discover and
strikes only when one opens a **new position that's a large share of their own
balance** — their highest-conviction read — then rides it wide. Two independent
sleeves on separate wallets. See [SKILL.md](SKILL.md) for the full thesis.

| Sleeve | Role | Direction | Wallet |
|---|---|---|---|
| `long` | mirror whales' high-conviction LONG strikes | LONG only | one |
| `short` | mirror whales' high-conviction SHORT strikes | SHORT only | one |

> **Two wallets** → the book can hold the same asset **long in one sleeve and short in
> the other** (different whales, opposite conviction). Funding default **50/50** — no
> directional bias. **Requires a USER-scoped `SENPI_AUTH_TOKEN`** (the `discovery_*`
> tools need a user id).

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)
```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull WhaleHunter (both sleeves)
```bash
mkdir -p /data/workspace/skills/whalehunter-strategy/{config,scripts,state,references}
for f in scripts/whalehunter-producer.py scripts/whalehunter_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/whalehunter-long-config.json config/whalehunter-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/whalehunter/$f" \
    -o "/data/workspace/skills/whalehunter-strategy/$f"
done
```

### Step 3 — Required env vars
```bash
export SENPI_AUTH_TOKEN=...                                # MUST be USER-scoped (discovery_* needs a user id)
export WHALEHUNTER_DECISION_MODEL=<your-preferred-model>   # bare model name, NO provider prefix
export TELEGRAM_CHAT_ID=...                                # optional
```

### Step 4 — Start both sleeves (separate wallets)
```bash
# LONG sleeve
WHALEHUNTER_LEG=long WHALEHUNTER_LONG_WALLET=<wallet A> SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/whalehunter-strategy/scripts/whalehunter-producer.py \
  > /tmp/whalehunter-long.log 2>&1 < /dev/null &
disown

# SHORT sleeve
WHALEHUNTER_LEG=short WHALEHUNTER_SHORT_WALLET=<wallet B> SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/whalehunter-strategy/scripts/whalehunter-producer.py \
  > /tmp/whalehunter-short.log 2>&1 < /dev/null &
disown
```
`setsid` + `disown` so each daemon survives a shell teardown (nohup blocks SIGHUP,
not SIGTERM). Add a cron keepalive pointing at these exact paths; the per-(leg,wallet)
lock makes a redundant relaunch a safe no-op.

---

## Verify
```bash
pgrep -af whalehunter-producer.py     # expect 2 daemons (long + short)
tail -5 /tmp/whalehunter-*.log
```
Each tick emits JSON with `pool_size`, `strikes`, `emitted` (with `from`,
`conviction_pct`, `consensus`, `margin_usd`). Most ticks print `WAITING — no
consistent+patient whale opened a high-conviction strike` — **that's expected**;
patient whales rarely trade, so signals are sparse by design. If `pool_size` is 0,
your token likely isn't USER-scoped (the `discovery_*` calls need a user id).

---

## Notes
- **Sparse by design.** ELITE + PATIENT is a small, pristine pool that rarely fires —
  quality over quantity. Too thin to ever fire? Widen `activityTags` to
  `["PATIENT","TACTICAL"]` in the config (one line, no rebuild).
- **Conviction = capital at risk** — `marginUsed / accountValue` on the whale's *new*
  position, default ≥25% of their book. Raise for a sharper sniper, lower for more signals.
- **Consensus, not duplication** — two whales on the same coin+direction size the ONE
  position up; they don't open two (within-tick dedup).
- **Ride wide** — wide disaster stop, no early profit-locks, time-cuts off. Patient
  whales hold; you hold with them. (v1.1: close when the whale closes.)
- **You're inherently a bit late** — `trader_state` shows the entry after it's placed;
  the conviction filter and a 6h freshness window keep it sane for slow patient trades.
- The producer **only opens** positions; the DSL owns all exits.

## License
Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
