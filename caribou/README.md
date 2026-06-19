# 🦌 CARIBOU v1.0 — Cross-Asset Trend Fund (Managed Futures / CTA)

Trend-follow **every asset class on Hyperliquid** — crypto, xyz stocks, indices,
metals, energy — long the uptrends and short the downtrends, each position sized to
**equal risk** (volatility parity) and capped per asset class. Two independent
sleeves on separate wallets. See [SKILL.md](SKILL.md) for the full thesis.

| Sleeve | Role | Direction | Wallet |
|---|---|---|---|
| `long` | trend-follow uptrends across all classes | LONG only | one |
| `short` | trend-follow downtrends (crisis-alpha engine) | SHORT only | one |

> **Two wallets, fully independent** — the fund can hold the same asset **long in
> one sleeve and short in the other** (clean trend-flip handling). Long uptrends +
> short downtrends across uncorrelated classes → **net beta ~0, crisis-positive.**
> Funding split default ~50/50 (your dial).

---

## Install

### Step 1 — Install the runtime plugin (provides `senpi_runtime_helpers`)
```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

### Step 2 — Pull Caribou (both sleeves)
```bash
mkdir -p /data/workspace/skills/caribou-strategy/{config,scripts,state,references}
for f in scripts/caribou-producer.py scripts/caribou_config.py \
         runtime-long.yaml runtime-short.yaml \
         config/caribou-long-config.json config/caribou-short-config.json \
         SKILL.md README.md; do
  curl -fsSL "https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/caribou/$f" \
    -o "/data/workspace/skills/caribou-strategy/$f"
done
```

### Step 3 — Required env vars
```bash
export SENPI_AUTH_TOKEN=...
export CARIBOU_DECISION_MODEL=<your-preferred-model>    # bare model name, NO provider prefix
export TELEGRAM_CHAT_ID=...                              # optional
```

### Step 4 — Start both sleeves (separate wallets)
```bash
# LONG sleeve
CARIBOU_LEG=long CARIBOU_LONG_WALLET=<wallet A> SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/caribou-strategy/scripts/caribou-producer.py \
  > /tmp/caribou-long.log 2>&1 < /dev/null &
disown

# SHORT sleeve
CARIBOU_LEG=short CARIBOU_SHORT_WALLET=<wallet B> SENPI_AUTH_TOKEN=$SENPI_AUTH_TOKEN \
  setsid nohup python3 -u /data/workspace/skills/caribou-strategy/scripts/caribou-producer.py \
  > /tmp/caribou-short.log 2>&1 < /dev/null &
disown
```

**Why `setsid` + `disown`:** `nohup` blocks SIGHUP, not SIGTERM; an OpenClaw/shell
teardown SIGTERMs its process tree. `setsid` re-parents each daemon into its own
session so it survives; `disown` detaches it from the job table. Add a cron
keepalive pointing at these exact paths for durability — the per-(leg,wallet) fcntl
lock makes a redundant relaunch a safe no-op.

---

## Verify
```bash
pgrep -af caribou-producer.py     # expect 2 daemons (long + short)
tail -5 /tmp/caribou-*.log
```
Each tick emits JSON with `leg`, per-class candidate counts, `emitted` (with
`class`, `vol_pct`, `margin_usd`), `class_deployed` vs `class_cap_usd`, and
`account_value`. Idle ticks print `WAITING — no asset cleared min score 5` (normal
when nothing is in a confirmed trend).

---

## Notes
- **Diversification is the edge.** When crypto chops, gold/oil/indices may be
  trending. The vol-parity sizing + per-class cap keep the book balanced across
  classes — don't expect it to be crypto-heavy.
- **Equal risk per position** — `margin = equity × baseRiskPct × (referenceVol /
  ATR%)`, clamped [3%, 15%]. Calm assets get more margin, wild ones less. No
  hardcoded $.
- **Cut losers fast, let winners run** — tight Phase-1 stop, wide Phase-2 ladder
  (locks 84% only at +150%), time-cuts OFF. Trend-following needs to let trends run.
- **The short sleeve is your hedge** — it's quiet in a broad bull and lights up when
  markets break, shorting the fallers across every class. That asymmetry is the
  crisis alpha; don't be surprised when it sits mostly idle in an uptrend.
- **Per-coin/per-class tuning** — for very high-vol classes you can lower
  `maxMarginPct` or raise `referenceVolPct`; leverage is venue-clamped regardless.
- The producer **only opens** positions; the DSL ratchet engine owns all exits.

## License
Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
