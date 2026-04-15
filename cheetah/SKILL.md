---
name: cheetah-strategy
description: >-
  CHEETAH v5.0 APEX — Multi-signal confluence sniper. Purpose-built to win
  the Senpi Arena by taking ~5 high-conviction trades per week at 80% margin
  and 10x leverage. Only fires when ALL major signals align (score >=14/15).
  Fewer trades, higher ROE%, maximum edge per entry. Previous Cheetah versions
  (v1-v4) iterated thesis on HYPE and failed; v5.0 is a complete architectural
  redesign optimizing for asymmetric ROE% rather than absolute PnL.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0-APEX"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🐆 CHEETAH v5.0 APEX — The Arena Sniper

**SM commits. Quality traders commit. Price confirms. Volume commits. All at once. Cheetah pounces once.**

## Why v5.0 is a complete rewrite

Cheetah iterated through four theses, all on HYPE, all on Wolverine's turf:

| Version | Thesis | Result |
|---|---|---|
| v2.0 | HYPE SM consensus momentum | +7.6% (top of fleet briefly) |
| v2.1 | HYPE momentum (hardened) | 33% win rate, -$175 gross |
| v3.0 | HYPE contrarian SM fade | 40% win rate, -$39 in 20h |
| v4.0 | HYPE funding rate extremes | -35% drawdown → HARD STOP |

**The pattern:** Cheetah kept fighting for Wolverine's HYPE territory. Fleet analysis showed Wolverine (HYPE momentum), Pangolin (funding fader), and Owl (crowding fader) already cover every HYPE thesis Cheetah was trying. Cheetah was redundant.

v5.0 does something no predator in the fleet does: it **refuses to trade** unless every major signal aligns simultaneously. MIN_SCORE is 14 out of 15 — the highest gate in the fleet by a wide margin.

## Arena thesis

The Senpi Arena ranks by **ROE %**, not absolute PnL, with a **$25k weekly volume minimum**. Looking at the current week's leaderboard top 5:

- #1 pr0br000: **67% ROE, 19 trades** — sniper pattern (high conviction, low frequency)
- #2 0xschelling: 34% ROE, 57 trades
- #3 dih: 30% ROE, 191 trades — scalper pattern (high frequency, tiny per-trade)
- #4 ysr: 21% ROE, 133 trades
- #5 Magi300a: 17% ROE, 190 trades

**Two winning shapes exist.** The scalper path is fee-drag death for a fleet agent — our fleet is 37 red / 2 green precisely because Hyperliquid fees eat high-frequency strategies. The sniper path (pr0br000) works because each trade has maximum edge, and 19 trades × ~3.5% ROE per trade = 67% ROE total.

**APEX is purpose-built to replicate the sniper shape.**

## Signal pipeline

Every scan iterates through all non-XYZ assets from `leaderboard_get_markets(limit=100)` and scores each one. An entry fires only when score ≥ 14 AND all hard gates pass.

### Hard gates (any failure = reject)

1. **SM consensus** — `pct_of_top_traders_gain` ≥ 10% AND `trader_count` ≥ 25
2. **Velocity gate** — `contribution_pct_change_15m` ≥ 1.0 OR `contribution_pct_change_1h` ≥ 3.0
3. **Not currently held** — APEX has no existing position on this coin
4. **Not in cooldown** — the asset passed the per-asset cooldown window
5. **Not XYZ DEX** — XYZ equities/indices banned

### Scoring (max = 15, threshold = 14)

| Signal | Points |
|---|---|
| SM_STRONG (pct ≥10%, traders ≥25) | **4** |
| Velocity gate passed (one of 15m/1h above minimum) | **2** |
| Accelerating (15m > 1h > 0, inflow building) | **2** |
| Dual price confirmation (4h ≥2% + 1h agrees) | **2** |
| Volume spike (≥2× 6h average) | **1** |
| Quality trader alignment (≥1 ELITE/RELIABLE in same direction) | **3** |
| Rank climbed ≥5 positions in last 2 scans | **1** |

**Score = 14** requires all signals except either the rank climb OR dropping one of the velocity subcomponents. **Score = 15** requires everything. Partial signal stacks (score ≤13) never fire.

## Position sizing

| Parameter | Value | Rationale |
|---|---|---|
| Starting budget | **$648** | Cheetah's current equity post-drawdown (not rebased to $1000) |
| Margin % per trade | **80%** | Maximum conviction commits maximum capital |
| Max leverage | **10x** | Fleet cap per H12 audit hypothesis |
| Max positions | **1** | Concentration, no parallel bets |
| Max daily entries | **5** (from dynamic cap) | Matches Arena $25k/week volume floor |
| Notional per trade | **~$5,184** | 80% × $648 × 10x |
| Target weekly volume | **~$25,920** | 5 trades × $5,184 = clears $25k Arena floor |

### Leverage tiers

- Score 14: **8x leverage**
- Score 15: **10x leverage** (perfect setup)

### Dynamic daily cap (rebased to $648)

```python
if pnl_pct >= 5:     return 8   # Hot hand — more shots
elif pnl_pct >= 0:   return 5   # Target rate for Arena volume floor
elif pnl_pct >= -5:  return 3   # Careful
elif pnl_pct >= -15: return 2   # Defensive
elif pnl_pct >= -25: return 1   # Preserve
else:                return 0   # HARD STOP
```

Starting at 0% PnL baseline = 5 entries/day cap = matches target weekly volume.

## DSL configuration (aggressive ratcheting)

Designed for the Arena's ROE% optimization:

| Parameter | Value | vs. Fleet standard |
|---|---|---|
| `max_loss_pct` | **5.0** | Fleet = 15-25 (APEX much tighter) |
| `retrace_threshold` | 5 | Fleet = 8 (tighter) |
| `hard_timeout` | 360 min | Fleet = 240-480 (standard) |
| `weak_peak_cut` | 35 min / 3% min | Fleet = 60 / 2 (faster cut if stalled) |
| `dead_weight_cut` | 25 min | Fleet = 30-45 (faster) |
| Phase 2 Tier 1 | +6% → 30% HW lock | Fleet = +8% → 25% (earlier, wider lock) |
| Phase 2 Tier 2 | +12% → 55% HW lock | Fleet = +15% → 50% (tighter) |
| Phase 2 Tier 5 | +60% → 92% HW lock | Fleet = +50% → 85% (let monsters run, protect 92% of peak) |

**The 5% max loss is the single most important setting.** A 5% ROE loss is survivable in a 7-day Arena window; a 20% loss ends your week.

## Fleet-standard guardrails (all present)

- **Self-executing** — scanner calls `create_position` via mcporter directly (Wolverine pattern)
- **Dynamic P&L-aware daily cap** (rebased to $648, fleet PR #176 pattern)
- **Auto-cancel stale resting orders** — non-reduceOnly orders older than 10 min get cancelled (fleet PR #177 pattern)
- **Persistent entry log** — `state/entry-log.jsonl` records every ENTRY/EXIT event, survives `openclaw sessions clear --current` (Wolverine v2.3 pattern)
- **Per-asset cooldown** — 240 min standard, 120 min extended after a loss
- **Stale-date bug fix** — load_trade_counter checks date on every call (fleet PR #177)

## Runtime Setup

```bash
# Set wallet and chat ID
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/cheetah-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/cheetah-strategy/runtime.yaml

# Install runtime
openclaw senpi runtime create --path /data/workspace/skills/cheetah-strategy/runtime.yaml
openclaw senpi runtime list && openclaw senpi status

# Pull latest scanner via curl
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/cheetah-scanner.py -o /data/workspace/skills/cheetah-strategy/scripts/cheetah-scanner.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/scripts/cheetah_config.py -o /data/workspace/skills/cheetah-strategy/scripts/cheetah_config.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cheetah/config/cheetah-config.json -o /data/workspace/skills/cheetah-strategy/config/cheetah-config.json

# Verify with a manual run
python3 /data/workspace/skills/cheetah-strategy/scripts/cheetah-scanner.py
```

Expected first-run output: no exceptions, `_cheetah_version: "5.0-APEX"` in the JSON, and either `note: "no score >= 14 candidates"` (most common) or a rare `action: "ENTRY"` if a confluence setup exists right now.

## Cron configuration

**Use the Turbine pattern** — detached bash loop, zero LLM wake:

```bash
nohup bash -c 'while true; do python3 /data/workspace/skills/cheetah-strategy/scripts/cheetah-scanner.py >> /tmp/cheetah-loop.log 2>&1; sleep 180; done' > /tmp/cheetah-nohup.log 2>&1 &
```

3-minute cadence. Zero LLM wake cost. Scanner decides everything in Python.

## Arena week expectations

| Scenario | Probability | Winners | Losers | Net ROE | Arena outcome |
|---|---|---|---|---|---|
| Disaster | ~15% | 0 | 5 × -5% | -25% | HARD STOP triggers |
| Bad | ~20% | 1 × +15% | 4 × -4% | -1% | outside money |
| Modest | ~30% | 2 × +15% | 3 × -4% | +18% | ~#4-5 ($448-672 prize) |
| Good | ~25% | 3 × +18% | 2 × -4% | +46% | ~#2-3 ($1,000-1,400) |
| Great | ~10% | 3 × +25% | 2 × -4% | +67% | **#1 ($2,240)** |

**Expected value of week 1 prize: ~$530.** That's the cost-weighted average across outcomes. The sniper design means APEX either wins significantly or wins nothing — it does not grind out mediocre returns.

## Why this will work as one of the 10 consistent winners

APEX isn't just an Arena one-shot. The thesis is a permanent edge:

1. **Low trade frequency = low fee drag.** The fleet's biggest hidden cost (7.6-8.4 bps fee/vol) barely affects APEX at 5 trades/week.
2. **Hyperfeed data refreshes every 15 min** — the 3-min scan cadence catches the early window of any genuine confluence setup.
3. **Confluence setups don't get arbitraged away** — markets always have rare moments when everything aligns, and those moments always pay.
4. **Tight -5% floor is self-correcting.** Bad calibration means APEX simply doesn't trade — it won't bleed like over-eager agents. We get a clean signal if the score thresholds need tuning.
5. **Letting 99% of signals pass is the feature, not a bug.** Selectivity IS the edge.

## License

MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
