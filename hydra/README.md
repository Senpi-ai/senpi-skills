# 🐉 HYDRA v1.0 — Single-Coin Portfolio Fund

A **complete book on one coin** — the thesis bet + a complementary dip-buyer + a
stress-gated short hedge, each on its own wallet ("head"). One producer;
`HYDRA_COIN` picks the asset, `HYDRA_LEG` picks the head. Deploy it for ETH, SOL,
HYPE (or any major) — three wallets each. See [SKILL.md](SKILL.md) for the full thesis.

| Head | Role | Direction | Wallet |
|---|---|---|---|
| `core` | thesis bet — trend-momentum | LONG up / SHORT down | one per (coin) |
| `dip` | complement — buy pullbacks in an uptrend | LONG only | one per (coin) |
| `hedge` | stress-gated short — cushions breaks | SHORT only | one per (coin) |

> **The heads are regime-gated so they never fight** (uptrend → core long + dip,
> hedge idle; downtrend → core + hedge short, dip idle). Net-long the coin, pressed
> on dips, cushioned on breaks. **Funding split is your dial** — default
> ~50 core / 25 dip / 25 hedge of the coin's budget.

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)
```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Hydra (all three heads)
```bash
mkdir -p /data/workspace/skills/hydra-strategy/{config,scripts,state,references}
for f in scripts/hydra-producer.py scripts/hydra_config.py \
         runtime-core.yaml runtime-dip.yaml runtime-hedge.yaml \
         config/hydra-core-config.json config/hydra-dip-config.json config/hydra-hedge-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/hydra/$f" \
    -o "/data/workspace/skills/hydra-strategy/$f"
done
```

### Step 3 — Required env vars
```bash
export SENPI_AUTH_TOKEN=...
export HYDRA_DECISION_MODEL=<your-preferred-model>     # bare model name, no provider prefix
export TELEGRAM_CHAT_ID=...                             # optional
```
`HYDRA_COIN`, `HYDRA_LEG`, and `HYDRA_WALLET` are set **per daemon** below (one
deployment = one coin + one head + one wallet).

### Step 4 — Start the three heads for a coin (example: ETH)
Each head is its own wallet. Set `HYDRA_WALLET` to that head's wallet.
```bash
for LEG in core dip hedge; do
  HYDRA_COIN=ETH HYDRA_LEG=$LEG \
  HYDRA_WALLET=$(eval echo \$HYDRA_ETH_${LEG}_WALLET) \
  SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
    setsid nohup python3 -u /data/workspace/skills/hydra-strategy/scripts/hydra-producer.py \
    > /tmp/hydra-eth-$LEG.log 2>&1 < /dev/null &
  disown
done
```
Set `coin` in each config instead of `HYDRA_COIN` if you prefer. **Repeat for SOL
and HYPE** with `HYDRA_COIN=SOL` / `HYDRA_COIN=HYPE` and their own three wallets.

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
