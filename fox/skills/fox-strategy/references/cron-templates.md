# Cron Templates — Fox v0.1

> Per Senpi Skill Guide §7: systemEvent uses `text` key, agentTurn uses `message` key.
> Replace placeholders: `{WORKSPACE}`, `{SCRIPTS}`, `{TELEGRAM}`, `{MID_MODEL}`, `{BUDGET_MODEL}`.

## Placeholder Reference

| Placeholder | Scope | Example |
|------------|-------|---------|
| `{WORKSPACE}` | Workspace root | `/data/workspace` |
| `{SCRIPTS}` | Scripts directory | `/data/workspace/scripts` |
| `{TELEGRAM}` | Telegram target | `telegram:<chat_id>` |
| `{MID_MODEL}` | Mid-tier model ID | model configured in OpenClaw |
| `{BUDGET_MODEL}` | Budget-tier model ID | model configured in OpenClaw |

## 1. Emerging Movers — Primary / Isolated Session

```json
{
  "name": "FOX — Emerging Movers v7 (3min)",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "FOX scanner: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-emerging-movers.py`, parse JSON.\n\nSLOT GUARD (MANDATORY): Check `anySlotsAvailable` BEFORE any entry. If false → NO_REPLY.\n\nv1.6 FIVE-LAYER ENTRY GAUNTLET (ALL must pass):\n\nLAYER 1 — FIRST JUMP: Asset must jump 10+ ranks from outside Top 25 (prevRank > 25) in one scan. If currentRank ≤ 10 AND prevRank ≤ 25 → SKIP (buying the top).\n\nLAYER 2 — SCORE THRESHOLDS: minScore ≥ 9, minReasons ≥ 4, minVelocity > 0.10, rankJump ≥ 15 OR velocity > 15.\n\nLAYER 3 — ASSET TREND ALIGNMENT: Pull the specific asset's 4H and 1H trend. If signal direction conflicts with the asset's 4H trend → TRASH. This is a HARD BLOCK, not a score penalty.\n\nLAYER 4 — LEVERAGE FLOOR: Check max-leverage.json. If max_leverage < 7x → SKIP.\n\nLAYER 5 — TIME-OF-DAY MODIFIER: Add +1 pt if 04:00-13:59 UTC. Subtract 2 pts if 18:00-01:59 UTC. Apply BEFORE the minScore check.\n\nScoring: FIRST_JUMP +3, IMMEDIATE +2, vel>10 +2, CONTRIB_EXPLOSION +2, DEEP_CLIMBER +1, other reasons +1 each, BTC alignment +1.\nAfter time-of-day modifier: must be ≥ 9 pts (≥ 11 for NEUTRAL regime).\n\nRead `{WORKSPACE}/fox-trade-counter.json` for tiered margin. Route to best-fit strategy from `{WORKSPACE}/fox-strategies.json`.\nOn entry: create DSL state with conviction-scaled Phase 1. Store `score` in DSL state. Phase 1: ALL time exits DISABLED (hardTimeout=0, weakPeak=0, deadWeight=0). Conviction floors: score 9-11 → -20% ROE, score 12-14 → -25% ROE, score 15+ → unrestricted.\nEntry order: FEE_OPTIMIZED_LIMIT (maker). Never MARKET for entries.\n\nRE-ENTRY CHECK: If a recently-exited trade's asset is still in scanner with same direction AND contribVelocity > 5 AND price moved further AND original loss ≤ 15% ROE AND within 2h → re-enter at 75% margin, score threshold 8.\nAlert {TELEGRAM} ONLY if you OPENED or CLOSED a position. No action = NO_REPLY."
  }
}
```

## 2. DSL v5.3.1 — Mid / Isolated (per-strategy, created dynamically)

```json
{
  "name": "FOX — DSL v5.3.1 [{STRATEGY_NAME}]",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{MID_MODEL}",
    "message": "FOX DSL: Run `DSL_STATE_DIR={WORKSPACE}/dsl DSL_STRATEGY_ID={STRATEGY_ID} PYTHONUNBUFFERED=1 python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py`, parse ndjson.\n\nFor each line:\n- if `status==\"strategy_inactive\"` → output: remove this cron.\n- if `closed==true` → alert {TELEGRAM} with asset, direction, close reason, PnL.\n- if `pending_close==true` → alert {TELEGRAM}: close failed, retrying.\n- if `tier_changed==true` → alert {TELEGRAM} with new tier details.\n- if `status==\"error\"` and `consecutive_failures>=3` → alert {TELEGRAM}.\n- else → no action for this line.\n\nv1.6 PHASE 1: ALL TIME EXITS DISABLED. No hardTimeout, no weakPeak, no deadWeight, no greenIn10.\nPhase 1 exits ONLY on conviction-scaled absolute floor breach (structural invalidation):\n- Score 9-11: floor at -20% ROE\n- Score 12-14: floor at -25% ROE\n- Score 15+: unrestricted\nIf no lines or all lines are routine → NO_REPLY.\n\nNOTIFICATION: Only send Telegram on position CLOSED or tier CHANGED. Do NOT narrate routine DSL checks."
  }
}
```

## 3. SM Flip Detector — Budget / Isolated

```json
{
  "name": "FOX — SM Flip Detector (5min)",
  "schedule": { "kind": "cron", "expr": "*/5 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{BUDGET_MODEL}",
    "message": "FOX SM: Run `python3 {SCRIPTS}/fox-sm-flip-check.py`, parse JSON.\n\nFor each alert in `alerts`:\n- if `alertLevel==\"FLIP_NOW\"` → close that position on the wallet for `strategyKey`, alert {TELEGRAM} with asset, direction, conviction, strategyKey.\n- if `alertLevel==\"FLIP_WARNING\"` or `alertLevel==\"WATCH\"` → no action needed.\nIf `hasFlipSignal==false` or no FLIP_NOW alerts → NO_REPLY.\n\nNOTIFICATION: Only send Telegram if a position was CLOSED due to SM flip. Do NOT narrate flip checks or conviction levels."
  }
}
```

## 4. Watchdog — Budget / Isolated

```json
{
  "name": "FOX — Watchdog (5min)",
  "schedule": { "kind": "cron", "expr": "*/5 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{BUDGET_MODEL}",
    "message": "FOX Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS}/fox-monitor.py`, parse JSON.\n\nFor each strategy in output:\n- if `crypto_liq_buffer_pct<30` → CRITICAL: close position with lowest ROE% in that strategy, alert {TELEGRAM}.\n- if `crypto_liq_buffer_pct<50` → WARNING: alert {TELEGRAM} only.\n- if any position has `liq_distance_pct<15` and wallet_type==\"xyz\" → alert {TELEGRAM}.\nIf no alerts → NO_REPLY.\n\nNOTIFICATION: Only send Telegram on margin WARNING or position FORCE CLOSE. Do NOT send routine margin reports or all-clear messages."
  }
}
```

## 5. Portfolio Update — Mid / Isolated

```json
{
  "name": "FOX — Portfolio (15min)",
  "schedule": { "kind": "cron", "expr": "*/15 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{MID_MODEL}",
    "message": "FOX Portfolio: Read `{WORKSPACE}/fox-strategies.json`. For each enabled strategy, call `strategy_get_clearinghouse_state` with the strategy wallet.\n\nFormat a code-block table with: per-strategy name, account value, positions (asset, direction, ROE%, PnL, DSL tier), entry count / max entries, and global totals.\nSend to {TELEGRAM}."
  }
}
```

## 6. Opportunity Scanner — Primary / Isolated Session

```json
{
  "name": "FOX — Opportunity Scanner v6 (15min)",
  "schedule": { "kind": "cron", "expr": "*/15 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "FOX scanner: Run `PYTHONUNBUFFERED=1 timeout 180 python3 {SCRIPTS}/fox-opportunity-scan-v6.py 2>/dev/null`, parse JSON.\n\nSLOT GUARD (MANDATORY): Check `anySlotsAvailable` BEFORE any entry. If false → NO_REPLY.\n\nFor each opportunity with finalScore ≥ 175:\n1. Check hourly trend alignment (HARD REQUIREMENT — counter-trend = skip).\n2. Check `max-leverage.json` for actual max leverage (min 7x).\n3. Read `{WORKSPACE}/fox-trade-counter.json` for tiered margin.\n4. Route to best-fit strategy from `{WORKSPACE}/fox-strategies.json`. Open position → create DSL state → ensure DSL cron.\nAlert {TELEGRAM}. If no qualified opportunities → NO_REPLY.\n\nNOTIFICATION RULE: Only send Telegram if you OPENED or CLOSED a position. Do NOT narrate skipped signals, scanner results, or reasoning. No action = NO_REPLY, nothing else."
  }
}
```

## 7. Market Regime Refresh — Mid / Isolated

```json
{
  "name": "FOX — Market Regime (4h)",
  "schedule": { "kind": "cron", "expr": "0 */4 * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{MID_MODEL}",
    "message": "FOX Regime: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-market-regime.py`, parse JSON.\n\nSave the full output to `{WORKSPACE}/market-regime-last.json`.\n- if `market_regime==\"BEARISH\"` → log: SHORT only.\n- if `market_regime==\"BULLISH\"` → log: LONG only.\n- if `market_regime==\"NEUTRAL\"` → log: both directions, higher score threshold.\nNO_REPLY after saving.\n\nNOTIFICATION: Only send Telegram if regime FLIPPED. Do NOT narrate routine regime checks."
  }
}
```

## 8. Health Check — Mid / Isolated

```json
{
  "name": "FOX — Health Check (10min)",
  "schedule": { "kind": "cron", "expr": "*/10 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{MID_MODEL}",
    "message": "FOX Health: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-health-check.py`, parse JSON.\n\nFor each issue, check the `action` field:\n- `auto_created` → DSL was missing, script created it. Alert {TELEGRAM}.\n- `auto_deactivated` → Orphan DSL deactivated (position closed externally). No alert needed.\n- `auto_replaced` → Direction mismatch fixed. Alert {TELEGRAM}.\n- `updated_state` → Size/entry/leverage reconciled. No alert needed.\n- `skipped_fetch_error` → Orphan check skipped (transient). No alert needed.\n- `alert_only` with type `NO_WALLET` or `DSL_INACTIVE` → CRITICAL. Alert {TELEGRAM}.\nIf no issues → NO_REPLY.\n\nNOTIFICATION: Only send Telegram if orphan position found, state corruption detected, or DSL reconciliation needed. Do NOT narrate routine health checks."
  }
}
```

## Summary Table

| # | Cron | Interval | Session | Payload Kind | Mandate Key | Model |
|---|------|----------|---------|-------------|-------------|-------|
| 1 | Emerging Movers | */3 * * * * | isolated | agentTurn | `message` | Primary |
| 2 | DSL v5.3.1 | */3 * * * * | isolated | agentTurn | `message` | Mid |
| 3 | SM Flip | */5 * * * * | isolated | agentTurn | `message` | Budget |
| 4 | Watchdog | */5 * * * * | isolated | agentTurn | `message` | Budget |
| 5 | Portfolio | */15 * * * * | isolated | agentTurn | `message` | Mid |
| 6 | Opp Scanner | */15 * * * * | isolated | agentTurn | `message` | Primary |
| 7 | Market Regime | 0 */4 * * * | isolated | agentTurn | `message` | Mid |
| 8 | Health Check | */10 * * * * | isolated | agentTurn | `message` | Mid |
