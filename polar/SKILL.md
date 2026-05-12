---
name: polar-strategy
description: >-
  POLAR v5.0.0 — ETH alpha hunter, senpi_runtime_helpers migration.
  Plumbing-only flip from openclaw-CLI subprocess + mcporter subprocess
  to in-process SenpiClient (direct HTTPS for MCP, direct HTTP POST to
  runtime /signals, long-lived producer_daemon). Thesis preserved
  verbatim from v4.2.0: ETH single-asset hybrid, hyperfeed SM gates
  (pct≥5%, traders≥30, cc_15m≥0.3), structural gates (4h trend,
  1h-4h alignment, 15m momentum, RSI), multi-factor scoring (~17 max),
  conviction-tiered leverage (5x standard / 7x conviction / 10x apex),
  MIN_SCORE 12, FP-001 quiet hours (00-04 UTC unless apex score 17+).
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐻‍❄️ POLAR v5.0.0 — ETH Alpha Hunter (senpi_runtime_helpers)

Best gross trader in the fleet. Single asset. Maximum conviction.

**v3 → v4 architectural rewrite.** v3.x was a full-agency scanner that called create_position directly and tracked state in Python. v4.0 flips to the standard senpi-trading-runtime pattern: producer emits signals, runtime owns execution + state.

**What changed structurally:**
- `polar-producer.py` (NEW) replaces `polar-scanner.py` (DELETED). Pure producer — no execution, no counters, no cooldowns, no resting-order guards.
- `runtime.yaml` is now v2-runtime-native: external_scanner + LLM-pass-through gate + native risk.guard_rails + DSL preset with FEE_OPTIMIZED_LIMIT.
- Trade chain DB emits `LIFECYCLE_RUNTIME_STARTED → DECISION_EXECUTED → ACTION_RESULT → DSL_CREATED → DSL_CLOSED` for every trade. **Per-trade telemetry is restored.**
- The `set_cooldown` / `load_tc` Python state crashes that bit Vulture v2.x are structurally impossible in v4.0 (cooldowns are runtime-managed).

**What's preserved from v3.0.6:**
- ETH single-asset thesis (no multi-asset rotation)
- Hyperfeed SM gates: pct≥5%, traders≥30, **cc_15m≥0.3** (15m velocity acceleration)
- Structural gates: 4h trend != NEUTRAL, 4h matches SM direction, 1h matches 4h, 15m momentum aligned, RSI 26-74 band
- Multi-factor scoring (~17 max points): base-tech (3+2+1+1) + SM concentration (1-3) + SM velocity (1-2) + SM accelerating (1) + trader depth (1) + funding alignment (-1/0/+2) + OI velocity (-1/0/+1/+2) + BTC correlation (1) + RSI room (1) + 4h momentum bonus (1) + move-exhaustion penalty (-1/-2)
- MIN_SCORE = 14 (config-overridable)
- Conviction-tiered leverage: 5x standard / 7x conviction / 10x apex
- DSL preset preserved EXACTLY: time-cuts all disabled, Phase 1 max_loss 25% / retrace 8 / 3 breaches, Phase 2 leverage-aware ladder (8/25, 15/50, 25/65, 35/80, 50/85)

**v3.0.4/3.0.5/3.0.6 v1-DSL fixes preserved:**
- `dead_weight_cut`: DISABLED (single-asset has no rotation cost)
- `hard_timeout`: DISABLED (v1 DSL fired this in Phase 2 incorrectly; fixes the 2026-04-23 wrongful-close pattern)
- `weak_peak_cut`: DISABLED (completes the no-time-based-cuts sweep)
- All exits now 100% price-action

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

These tools are reserved for the **producer cron** (polar-producer.py) and the **DSL ratchet engine**. The cron is the only entry path. The DSL is the only exit path. User-conversation sessions are read-only.

If a user asks "should I take this trade?" or "anything close to triggering?", respond by reading current state — DO NOT execute. The producer will fire on its next tick if a real signal is there.

### RULE FP-001: Quiet hours for low-liquidity windows

Producer skips emission during 00:00-04:00 UTC by default. Apex setups (score >= `quietHours.apexBypassScore`, default 17) bypass — high-conviction ETH setups can fire any hour; routine score-14/15 entries wait until 04:00 UTC.

Configurable via `quietHours.{startUtc,endUtc,apexBypassScore}` in `polar-config.json`. Set `startUtc == endUtc` to disable.

---

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/polar-strategy/`
### RULE 2: THE SCANNER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION
### RULE 4: Verify runtime on every session start
### RULE 5: Never modify parameters
### RULE 6: MAX 4 ENTRIES PER DAY
### RULE 7: 240-minute cooldown between entries (v2.4 — sniper cadence)
### RULE 8: 120-minute same-direction cooldown after wins
### RULE 9: MIN_SCORE = 10 (v2.4 — was 8 in v2.3)
### RULE 10: 15m velocity must exceed MIN_SM_ACCEL_PCT=0.3% (v2.4 hard gate)

---

## How It Works

Polar scans ETH every 3 minutes. It scores using SM consensus, multi-timeframe
contribution velocity (15m, 1h, 4h), price alignment, funding, and trader depth.

**v2.3 key behaviors:**
- Move-exhaustion: if ETH 4h price change ≥4% in entry direction, -2 points.
  If ≥2.5%, -1 point. Creates tension with 4H confirmation scoring.
- Same-direction lock: after a winning exit, won't re-enter the same direction
  for 60 minutes. Prevents chasing the same move at worse prices.
- Midnight-safe cooldowns: timestamps persist across UTC date rollover.

## Scoring (max ~18 points)

| Signal | Points | Notes |
|---|---|---|
| SM consensus | 1-3 | ≥15% dominant = +3 |
| Trader depth | 0-1 | ≥100 traders = +1 |
| 4H alignment | -1 to +2 | Confirms direction |
| Move exhaustion | -2 to 0 | **v2.3** penalizes ≥2.5% existing moves |
| 1H momentum | 0-1 | Confirms direction |
| 15m velocity | -1 to +4 | Extreme spike = +4 |
| 1h acceleration | 0-2 | Strong accel = +2 |
| 4H major shift | 0-1 | ≥5% contribution change |
| Acceleration pattern | 0-1 | 15m > 1h = accelerating |
| Funding alignment | 0-1 | Funding pays your direction |
| US session | 0-1 | 13-21 UTC |

**Min score: 8.** Leverage: 8→7x, 9→10x, 10→15x, 11+→20x.

---

## Runtime Setup

```bash
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/polar-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime create --path /data/workspace/skills/polar-strategy/runtime.yaml
openclaw senpi runtime list
openclaw senpi status
```

---

## Bootstrap Gate

On EVERY session start, check `config/bootstrap-complete.json`. If missing:
1. Read senpi-trading-runtime skill
2. Verify Senpi MCP
3. Set wallet and telegram in runtime.yaml
4. Install runtime
5. Verify: `openclaw senpi runtime list` and `openclaw senpi status`
6. Create scanner cron (3 min, main)
7. Write `config/bootstrap-complete.json`
8. Send: "🐻‍❄️ POLAR v2.3 online. Hunting ETH. Silence = no conviction."

---

## Risk

| Rule | Value |
|---|---|
| Max positions | 1 |
| Max entries/day | 4 |
| Leverage | 7-20x (conviction-scaled, capped at 20x for ETH) |
| General cooldown | 120 min |
| Same-direction cooldown | 60 min after wins |
| Min score | 8 |
| Margin | 50% |

---

## Files

| File | Purpose |
|---|---|
| `scripts/polar-scanner.py` | ETH scoring + entry execution |
| `scripts/polar_config.py` | Config helper (MCP, state, positions) |
| `config/polar-config.json` | Wallet, strategy ID |
| `runtime.yaml` | Runtime YAML for DSL plugin |

---

## License

MIT — Built by Senpi (https://senpi.ai).


---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
