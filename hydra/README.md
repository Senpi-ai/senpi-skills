# 🐉 HYDRA v2.0 — Single-Coin Conviction Fund (cross-asset hedge)

A **long-term conviction book on one coin**, cushioned by a **diversified short of
*other* assets** — protect against the market risk that would also hurt your coin,
*without betting against your own thesis*. See [SKILL.md](SKILL.md) for the full thesis.

**Shipped as named variants over ONE shared engine** — pick yours:

| Variant | Thesis coin |
|---|---|
| **Hydra-ETH** | ETH |
| **Hydra-SOL** | SOL |
| **Hydra-HYPE** | HYPE |

Each variant = the same producer/runtimes + its own per-coin configs
(`config/hydra-<coin>-<leg>-config.json`). `HYDRA_COIN` picks the thesis coin,
`HYDRA_LEG` picks the head. In v2.0 the variants differ only by the thesis coin —
the hedge shorts a blend of *other* assets (thesis coin auto-excluded), so its
params are uniform.

| Head | Role | Direction | DSL |
|---|---|---|---|
| `core` | long-term thesis — trend-confirmed | LONG up / SHORT down | catastrophic-only (ride it) |
| `dip` | tactical add — buy pullbacks in an uptrend | LONG only | moderate |
| `hedge` | cross-asset cushion — short a blend of *other* breaking assets | SHORT only (never the thesis coin) | tight-ish |

> **v2.0 in one line:** the thesis *rides* on a loose stop, and the hedge shorts a
> diversified blend of *other* assets that are actually breaking down (vol-parity
> sized, scaled up when the thesis coin is breaking). It auto-scales — light in a
> calm uptrend, heavy in a broad selloff. **Funding split is your dial** — default
> ~50 core / 25 dip / 25 hedge of the variant's budget.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)
```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Hydra (shared engine + all variant configs)
```bash
mkdir -p /data/workspace/skills/hydra-strategy/{config,scripts,state,references}
for f in scripts/hydra-producer.py scripts/hydra_config.py \
         runtime-core.yaml runtime-dip.yaml runtime-hedge.yaml \
         config/hydra-eth-core-config.json config/hydra-eth-dip-config.json config/hydra-eth-hedge-config.json \
         config/hydra-sol-core-config.json config/hydra-sol-dip-config.json config/hydra-sol-hedge-config.json \
         config/hydra-hype-core-config.json config/hydra-hype-dip-config.json config/hydra-hype-hedge-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/hydra/$f" \
    -o "/data/workspace/skills/hydra-strategy/$f"
done
```
(Pull only your variant's three configs if you prefer — e.g. just the `hydra-eth-*` set.)

### Step 3 — Required env vars
```bash
export SENPI_AUTH_TOKEN=...
export HYDRA_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional
```
`HYDRA_COIN` (the variant), `HYDRA_LEG` (the head), and `HYDRA_WALLET` are set
**per daemon** below. `HYDRA_COIN` selects which per-coin config set loads —
`HYDRA_COIN=ETH` + `HYDRA_LEG=core` → `config/hydra-eth-core-config.json`.

### Step 4 — Start the three heads for your variant (example: Hydra-ETH)
Each head is its own wallet. Set `HYDRA_WALLET` to that head's wallet.
```bash
COIN=ETH                                      # ETH | SOL | HYPE
for LEG in core dip hedge; do
  HYDRA_COIN=$COIN HYDRA_LEG=$LEG \
  HYDRA_WALLET=$(eval echo \$HYDRA_${COIN}_${LEG}_WALLET) \
  SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
    setsid nohup python3 -u /data/workspace/skills/hydra-strategy/scripts/hydra-producer.py \
    > /tmp/hydra-${COIN}-$LEG.log 2>&1 < /dev/null &
  disown
done
```
**For Hydra-SOL / Hydra-HYPE:** set `COIN=SOL` or `COIN=HYPE` and use their own
three wallets. (Each variant's coin is also pinned in its config files, so the
`coin` field is set even if you don't export `HYDRA_COIN`.)

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM; an OpenClaw/shell
teardown SIGTERMs its process tree. `setsid` re-parents each daemon into a new
session so it survives; `disown` detaches it from the job table. Add a cron
keepalive for durability — the per-(coin,leg) fcntl lock makes a double-launch a
safe no-op.

---

## Verify
```bash
pgrep -af hydra-producer.py            # expect 3 daemons per coin you deployed
tail -5 /tmp/hydra-eth-*.log
```
Each tick emits JSON with `leg`, `coin`, `direction`, `score`, `signals_pushed`.
Idle heads print their reason: core `WAITING — no confirmed trend`; dip `WAITING —
no pullback in an uptrend`; hedge `WAITING — no confirmed downtrend + stress`
(expected most of the time — the hedge is episodic).

---

## Notes
- **One position per head.** Each wallet holds at most one position in its coin.
- **Heads don't fight** — regime-gated to disjoint lanes (uptrend / downtrend); the
  fund is net-long, with the hedge leading the core's slower trend-flip on a break.
- **Conviction sizing on the core** — `stdLeverage` → `maxLeverage` at `apexScore`.
- **The hedge is insurance** — it sits idle (tiny bleed) in uptrends and fires only
  on a confirmed downtrend + a fast drawdown, capitulation-guarded. Don't expect it
  to trade often; that's the point.
- **Per-coin tuning** — for higher-vol coins (HYPE) raise the hedge's `stressDropPct`
  so it doesn't arm on normal noise; leverage is clamped to the venue max anyway.
- **Single-asset DSL** — time-cuts off, let winners run; Phase 1 + Phase 2 own exits.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License
Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
