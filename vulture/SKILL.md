---
name: vulture-strategy
description: >-
  VULTURE v4.0.0 — Long-Tail Momentum Rider, senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess +
  mcporter subprocess to in-process SenpiClient. Thesis preserved
  verbatim from v3.1.1: 25+ small/mid-cap Hyperliquid perps (HEMI,
  WLD, MON, XPL, AIXBT, ARB, ASTER, ZEC, LIT, TAO, POLYX, LDO, APT,
  DYDX, ONDO, SUI, etc.) that no other Senpi predator covers,
  LONG_TAIL_MOMENTUM scoring, v2.4 1h-alignment gate, FP-001 quiet
  hours, conviction-scaled leverage (3x cautious / 5x conviction /
  7x apex). Built from #1 Arena winner's 3-week playbook (38.6%
  win rate, 6.15x profit factor).
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
    - senpi-runtime-helpers
---

# 🦅 VULTURE v4.0.0 — Long-Tail Momentum Rider (senpi_runtime_helpers)

**v2 → v3 architectural rewrite.** v2.x was a full-agency scanner that called create_position directly and tracked state in Python. v3.0 flips to the standard senpi-trading-runtime v2 pattern: producer emits signals, runtime owns execution + state.

**What changed structurally:**
- `vulture-producer.py` (NEW) replaces `vulture-scanner.py` (DELETED). Pure producer — no execution, no counters, no cooldowns, no dynamic-slot bookkeeping.
- `runtime.yaml` is now v2-runtime-native: external_scanner + LLM-pass-through gate + native risk.guard_rails + DSL preset with FEE_OPTIMIZED_LIMIT.
- Trade chain DB now emits LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED for every trade. **Per-trade telemetry is restored.**
- The cfg.set_cooldown silent crash is structurally impossible in v3.0 (cooldowns are runtime-managed, not Python-managed).

**What's preserved from v2.4:**
- Asset universe (25+ small/mid-cap Hyperliquid perps)
- LONG_TAIL_MOMENTUM scoring (SM concentration tier + 4h momentum + 15m velocity + 1h acceleration + 4h continuation + trader depth + regime alignment + persistence)
- Hard gates: SM ≥3% / 15 traders, 4h aligned ≥1%, **1h aligned ≥0.1% (the v2.4 fix)**, 15m velocity ≥0.3
- Conviction-scaled leverage (3x cautious / 5x conviction / 7x apex)
- DSL Phase 2 ladder (15/20, 30/60, 40/75, 75/75, 100/85, 150/92) — proved correct on the live ZEC trade
- 90-min dead_weight_cut, 180-min weak_peak_cut, 7-day hard_timeout
- 38% WR target with 6.15x profit factor

**What this enables:**
- Per-trade scoring telemetry (was lost in v2.x's silent crashes)
- Maker-exit fee recovery (~$10-15/week at current cadence)
- Declarative risk gates (no Python state to drift)
- The senpi-trade-analysis skill can now drill Vulture trades the same way it drills Scorpion / Roach / RoachB

---

## ⛔ Hard Rules (Fleet Patches)

### RULE FP-002: User-conversation Claude sessions MUST NOT trade

**Hard rule, not a heuristic.** When responding to a user message (Telegram ping, status check, "tell me about your trades", etc.), the Claude Code session MUST NOT call any of:

- `create_position`
- `close_position`
- `edit_position`
- `ratchet_stop_add` / `ratchet_stop_edit` / `ratchet_stop_delete`
- `cancel_order`
- `strategy_close` / `strategy_close_positions`

These tools are reserved for the **producer cron** (vulture-producer.py) and the **DSL ratchet engine**. The cron is the only entry path. The DSL is the only exit path. User-conversation sessions are read-only.

If a user asks "should I take this trade?" or "anything close to triggering?", respond by reading current state — DO NOT execute. The producer will fire on its next tick if a real signal is there.

### RULE FP-001: Quiet hours for low-liquidity windows

Producer skips emission during 00:00-04:00 UTC by default. Apex setups (best signal score >= `quietHours.apexBypassScore`, default 12) bypass — high-conviction small-cap momentum can fire any hour; routine sub-apex entries wait until 04:00 UTC.

Configurable via `quietHours.startUtc`, `quietHours.endUtc`, `quietHours.apexBypassScore` in `vulture-config.json`. Set `startUtc == endUtc` to disable.

Rationale: fleet-wide pattern of midnight-UTC pile-ins after daily-cap reset. For Vulture specifically, small-cap order books are thinnest in this window and entry slippage compounds.

---

---

## Thesis

Small-cap Hyperliquid perps have thicker trader-count signal-to-noise ratios than the majors. When Smart Money concentration on a small cap ALIGNS with a fresh 4h price trend AND 15m velocity is accelerating, enter in the direction of the trend and hold it via the DSL tier curve for **days, not hours**.

**Key insight:** The Arena winner's asset universe (HEMI, WLD, MON, XPL, AIXBT, ARB) is a **fleet-wide blind spot**. No current Senpi predator scans small-cap alts — they're all locked to 4-12 majors or single assets. Vulture v2.0 fills the gap.

---

## Philosophy (copied from Arena winner #1)

- **Low win rate is fine** (~38%) if winners are 5-10x bigger than losers
- **Tight loss cuts** via DSL dead_weight_cut (60 min)
- **Long holds on winners** via hard_timeout=10080 min (7 days)
- **Moderate leverage (5-7x)** — small caps can't handle 20x (slippage kills you on low-liquidity books)
- **Signal-driven direction, NOT long-only bias.** The Arena winner was long because April 2026 was a bullish regime, not because LONG is a rule. Vulture v2.0 takes SHORTs when signals support.
- **Max 2 concurrent positions**

---

## Asset Universe (25 small/mid-caps)

```
HYPE, HEMI, WLD, MON, XPL, AIXBT, ARB, ASTER, POLYX, LDO,
APT, DYDX, ONDO, SUI, kBONK, kPEPE, TAO, GRASS, ZEC, LIT,
FARTCOIN, MORPHO, NEAR, INJ, AVAX, LINK, DOGE
```

**Banned:** BTC, ETH, SOL (handled by other predators). XYZ DEX assets (handled by Bald Eagle).

---

## Entry Scoring

### Hard gates (all must pass)
- Asset must be in TRACKED_ASSETS and not in BANNED_ASSETS
- SM direction must be LONG or SHORT (not neutral)
- `pct >= 3.0` AND `traders >= 15` (signal validity)
- 4h price ALIGNED with SM direction by at least 1% (momentum confirmation)
- 15m velocity `> 0.3` (actively building, not flat)

### Scoring contributors (max ~14 pts)

| Signal | Points |
|---|---:|
| SM concentration ≥18% (HEAVY_FLOW) | +4 |
| SM concentration ≥12% (STRONG_FLOW) | +3 |
| SM concentration ≥7% (MODERATE_FLOW) | +2 |
| SM concentration ≥3% (LIGHT_FLOW) | +1 |
| 4h price aligned ≥8% (TREND_RUNNING) | +3 |
| 4h price aligned ≥4% (TREND_STRONG) | +2 |
| 4h price aligned ≥2% (TREND_BUILDING) | +1 |
| 15m velocity ≥3.0 (15M_EXPLOSIVE) | +3 |
| 15m velocity ≥1.0 (15M_STRONG) | +2 |
| 15m velocity ≥0.5 (15M_BUILDING) | +1 |
| ACCELERATING pattern (15m > 1h > 0) | +2 |
| 1h velocity ≥1.0 (1H_POSITIVE) | +1 |
| 4h contribution ≥2.0 (4H_CONTINUATION) | +1 |
| Trader count ≥50 (DEEP_DEPTH) | +1 |
| **Move ≥15% in direction (LATE_ENTRY_PENALTY)** | **-3** |
| **Move ≥12% in direction (MOVE_EXTENDED)** | **-2** |

**MIN_SCORE: 7** — entry floor. The scoring dimensions (especially HEAVY_FLOW requiring ≥18% SM concentration) do the real quality work.

---

## Conviction-Scaled Leverage

| Score | Leverage |
|---|---:|
| ≥11 | 7x |
| ≥9 | 5x |
| ≥7 | 3x |

**Capped at 7x because small-cap liquidity can't support 20x.** Lemon can go 20x on BTC/ETH because slippage is <0.1%. Small caps can have 0.5%+ slippage per fill, which eats gains at 20x.

---

## Exit (DSL)

The DSL tier curve is the single biggest difference from other Senpi predators. **Winners need DAYS to develop.** Arena winner #1 held HEMI for 8 days, WLD for 4 days, MON for 5 days.

| Mechanism | Value | Rationale |
|---|---|---|
| hard_timeout | **10080 min (7 days)** | Let momentum runners run a full week |
| dead_weight_cut | **60 min** | Faster than Owl's 90, slower than Lemon's 20 (small-cap wick tolerance) |
| weak_peak_cut | 120 min @ 2% min | Catch fades that stalled |
| Phase 1 max_loss_pct | 20% | Wider than Lemon's 15%, tighter than Owl's 35% |
| Phase 1 retrace | 8% | Standard |
| Phase 1 breaches | 3 | Standard |
| Phase 2 tier 1 | 5% trigger → 20% lock | Capture first leg (Lemon-pattern learning) |
| Phase 2 tier 2 | 10% → 35% lock | |
| Phase 2 tier 3 | 20% → 55% lock | |
| Phase 2 tier 4 | 35% → 70% lock | |
| Phase 2 tier 5 | 50% → 80% lock | |
| Phase 2 tier 6 | 75% → 88% lock | |
| Phase 2 tier 7 | 100% → 92% lock | Infinite trail on massive winners |

---

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 2 concurrent |
| Max entries/day | 4 (dynamic, scales with P&L) |
| Per-asset cooldown | 240 min (4h sniper cadence) |
| Margin per position | 40% of account |
| Leverage range | 3-7x |
| Asset blocklist | BTC, ETH, SOL, all XYZ |
| Daily loss cap | HARD STOP at -25% drawdown |

---

## Differences from Lemon

Both agents are "fleet alpha extractors" but via **opposite** philosophies:

| Dimension | Lemon v1.1 (Degen Fader) | Vulture v2.0 (Long-Tail Rider) |
|---|---|---|
| Asset universe | 12 majors | 25 small caps |
| Direction vs SM | **Fades** (counter-trade) | **Follows** (momentum) |
| Entry condition | SM high + 15m collapsing | SM high + 15m accelerating |
| Avg hold time | 235 min | 24h to 7 days |
| hard_timeout | 480 min | 10,080 min (21x longer) |
| dead_weight_cut | 20 min | 60 min |
| Max leverage | 20x (apex) | 7x (liquidity limit) |
| MIN_SCORE | 8 | 7 |

**Both are bets on asymmetric payoff** — low win rate + big winners / tiny losers — but Lemon bets on mean reversion of exhausted crowds while Vulture bets on continuation of small-cap trends.

---

## Runtime Setup (v3.0 — v2-runtime-native)

**Step 1:** Install package files:
```bash
mkdir -p /data/workspace/skills/vulture-strategy/{config,scripts,state}

curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/runtime.yaml -o /data/workspace/skills/vulture-strategy/runtime.yaml
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/SKILL.md -o /data/workspace/skills/vulture-strategy/SKILL.md
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/config/vulture-config.json -o /data/workspace/skills/vulture-strategy/config/vulture-config.json
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture-producer.py -o /data/workspace/skills/vulture-strategy/scripts/vulture-producer.py
curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/vulture/scripts/vulture_config.py -o /data/workspace/skills/vulture-strategy/scripts/vulture_config.py
```

**Step 2:** Set wallet, strategyId, chatId in `config/vulture-config.json` (this is the canonical source — producer reads from here on every cron tick; runtime reads at startup).

**Step 3:** Set the LLM model env var at runtime-create time only:
- `VULTURE_DECISION_MODEL` — LLM model BARE name (no provider prefix). E.g. `gemini-2.5-pro` or `claude-sonnet-4-20250514`. INVALID: `google/gemini-2.5-pro` (OpenClaw double-prefixes → 500 Unknown model). This env var is resolved ONCE into runtime.yaml's `${VULTURE_DECISION_MODEL}` placeholder when openclaw creates the runtime — it doesn't need to be set in the cron environment.

**Step 4:** Install runtime via `openclaw senpi runtime create --path /data/workspace/skills/vulture-strategy/runtime.yaml`. Verify with `openclaw senpi runtime list` — `vulture-tracker` must appear ACTIVE.

**Step 5:** Add cron (3-min cadence — wallet is read from config.json, no env vars needed):
```cron
*/3 * * * * cd /data/workspace/skills/vulture-strategy && python3 scripts/vulture-producer.py >> state/producer.log 2>&1
```

**Step 6 (legacy cleanup if migrating from v2.x):** Remove old vulture-scanner.py cron entry. Delete `state/trade-counter.json`, `state/cooldowns.json` — runtime owns this state now.



**Step 2:** Set strategy wallet and telegram chat:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/vulture-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/vulture-strategy/runtime.yaml
```

**Step 3:** Install runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/vulture-strategy/runtime.yaml
openclaw senpi runtime list
```

**Step 4:** Create scanner cron (3-minute interval):
```bash
# In openclaw: create a cron scanning every 3 minutes
# Command: python3 /data/workspace/skills/vulture-strategy/scripts/vulture-scanner.py
```

---

## Bootstrap Gate

On EVERY session start, verify:
1. senpi-trading-runtime SKILL is loaded
2. Senpi MCP is connected
3. Runtime is installed (`openclaw senpi runtime list`)
4. Scanner cron is active (3 min interval)

Send: "🦅 VULTURE v2.0 online. Long-Tail Momentum Rider. Riding small-cap trends. Silence = no momentum confluence."

---

## Changelog

### v2.0 (2026-04-15) — **COMPLETE REWRITE**
- Thesis flipped: contrarian fader → momentum rider
- Universe expanded: 4 majors → 25 small caps
- DSL hard_timeout: 360 min → 10,080 min (7 days)
- Leverage: 5-7x (unchanged, appropriate for small caps)
- MIN_4H_MOVE_PCT gate removed (was unreachable)
- New MIN_15M_VELOCITY=0.3 hard gate (actively-building confirmation)
- Max positions: 1 → 2
- Signal-driven direction (not bias)
- Based on Arena winner #1 3-week playbook

### v1.0 (pre-2026-04-15)
- Initial release. Contrarian SM-exhaustion fader on 4 majors. 0 trades produced.

---

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
