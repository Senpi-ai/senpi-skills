# 🐋 WHALEHUNTERHEDGE v2.0 — Smart-Money-vs-Crowd Divergence (long/short)

Position **with the smartest money, against the crowd.** WhaleHunterHedge segments
Hyperliquid traders into cohorts by **lifetime realized gains** (smart money = >$1M,
crowd = $10k–$100k), measures each cohort's **net positioning** per asset, tracks
whether the smart cohort is **adding daily**, and strikes when the smart money diverges
hard from the crowd — *e.g. the winners shorting a rally the crowd is buying*. Two
independent sleeves on separate wallets. See [SKILL.md](SKILL.md) for the full thesis.

**The four-step engine** (all from Senpi Discover):

| Step | What |
|---|---|
| 1. Cohorts | One ALL-TIME realized-PnL ranking, bucketed: smart ≥ $1M, crowd $10k–$100k |
| 2. Net positioning | `bias = net/gross ∈ [-1,+1]` per asset, per cohort (+1 all long, -1 all short) |
| 3. Adding daily | A daily ledger of the smart cohort's net per coin → growth (`requireGrowing`) |
| 4. Divergence strike | Smart net-directional ≥ `biasThreshold` + growing, scored higher when the crowd's net-opposite |

| Sleeve | Role | Direction | Wallet |
|---|---|---|---|
| `long` | assets the smart cohort is net-long + adding | LONG only | one |
| `short` | assets the smart cohort is net-short + adding | SHORT only | one |

> **Two wallets** → the book can hold the same asset **long in one sleeve and short in
> the other** (smart money net-long one asset, net-short another). Funding default
> **50/50** — no directional bias. **Requires a USER-scoped `SENPI_AUTH_TOKEN`** (the
> `discovery_*` tools need a user id). **~1-day warmup** — the growth gate needs ≥2 daily
> ledger snapshots, so day 1 emits no divergence strikes by design.

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
Each tick emits JSON with `engine: "cohort"`, `smart_n` / `crowd_n` (cohort sizes),
`candidates`, `signals_pushed`, `emitted` (with `smart_bias`, `crowd_bias`, `growth`,
`margin_usd`), and an `insight` array — the human-readable divergences (e.g.
`"HYPE: smart -0.85 vs crowd +0.65, Δ-2100000 → SHORT"`). On day 1 (and most ticks)
you'll see `WAITING — no smart/crowd divergence cleared the gate` — **expected** until
the smart cohort is both lopsided *and* adding. If `smart_n` is 0 / "cohort too small",
your token likely isn't USER-scoped (the `discovery_*` calls need a user id).

---

## Notes
- **Cohorts by realized $, not tags.** Smart = lifetime realized ≥ `smartMinRealizedUsd`
  ($1M); crowd = `crowdMinRealizedUsd`..`crowdMaxRealizedUsd` ($10k–$100k). Tune the
  thresholds in config (one line, no rebuild).
- **Net positioning is the signal** — `bias = net/gross ∈ [-1,+1]` per asset. The smart
  cohort must clear `biasThreshold` (0.50) in this sleeve's direction. Always available
  (no waiting on a rare individual trade).
- **Require growth, not just lopsidedness** — `requireGrowing` demands the smart cohort
  is *adding* to the position day over day (the daily ledger). That's the conviction.
- **Crowd divergence is a booster** — fading the crowd alone is unreliable, so smart
  money drives and "crowd net-opposite ≥ `crowdDivergenceMin`" only raises the score.
- **Ride wide** — wide disaster stop, no early profit-locks, time-cuts off. You hold while
  the smart cohort holds. (Planned: close when the smart cohort flips/unwinds.)
- **Revivable v1.x copier** — the per-whale conviction copier (tiered by consistency×style)
  is retained behind `enableIndividualCopy` (OFF by default).
- The producer **only opens** positions; the DSL owns all exits.

## License
Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
