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

## 1. Emerging Movers — Primary / Main Session

```json
{
  "name": "FOX — Emerging Movers v7 (3min)",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FOX scanner: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-emerging-movers.py`, parse JSON.\n\nSLOT GUARD (MANDATORY): Check `anySlotsAvailable` BEFORE any entry. If false → HEARTBEAT_OK.\n\nv0.1 ENTRY FILTERS (apply to ALL signals before scoring):\n1. RANK JUMP MINIMUM: `rankJumpThisScan ≥ 15` OR `contribVelocity > 15`. If neither → skip signal.\n2. TIME-OF-DAY SCORING: add +1 pt if current hour (UTC) is 04:00-13:59. Subtract 2 pts if 18:00-01:59.\n3. Standard scoring: FIRST_JUMP +3, IMMEDIATE +2, vel>10 +2, CONTRIB_EXPLOSION +2, DEEP_CLIMBER +1, other reasons +1 each, BTC alignment +1.\n4. Score threshold: ≥6 pts (≥8 for NEUTRAL regime).\n5. Max leverage ≥ 7x (check max-leverage.json). Hourly trend alignment.\n\nRead `{WORKSPACE}/fox-trade-counter.json` for tiered margin. Route to best-fit strategy from `{WORKSPACE}/fox-strategies.json`.\nOn entry: create DSL state with conviction-scaled Phase 1 (v7.2). Store `score` in DSL state. Phase 1 tolerance auto-scales: score 6-7 → 0.02/lev/30min, score 8-9 → 0.025/lev/45min, score 10+ → 0.03/lev/60min.\n\nRE-ENTRY CHECK (v7.2): If a recently-exited Phase 1 trade's asset is still in scanner output with same direction AND contribVelocity > 5 AND price moved further in original direction AND original loss ≤ 15% ROE AND within 2h of exit → re-enter at 75% margin, score threshold 5 pts (relaxed). Set isReentry=true in DSL state. isFirstJump NOT required for re-entries.\nAlert {TELEGRAM}. If no actionable signals → HEARTBEAT_OK."
  }
}
```

## 2. DSL v5 — Mid / Isolated (per-strategy, created dynamically)

```json
{
  "name": "FOX — DSL v5 [{STRATEGY_NAME}]",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "model": "{MID_MODEL}",
    "message": "FOX DSL: Run `DSL_STATE_DIR={WORKSPACE}/dsl DSL_STRATEGY_ID={STRATEGY_ID} PYTHONUNBUFFERED=1 python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py`, parse ndjson.\n\nFor each line:\n- if `status==\"strategy_inactive\"` → output: remove this cron.\n- if `closed==true` → alert {TELEGRAM} with asset, direction, close reason, PnL.\n- if `pending_close==true` → alert {TELEGRAM}: close failed, retrying.\n- if `tier_changed==true` → alert {TELEGRAM} with new tier details.\n- if `status==\"error\"` and `consecutive_failures>=3` → alert {TELEGRAM}.\n- else → no action for this line.\n\nPHASE 1 TIMING ENFORCEMENT (v0.1+v7.2 — check AFTER dsl-v5.py output):\nFor each active Phase 1 position (phase==1), read its DSL state file. Check `score` field for conviction-scaled timing:\n- Score 6-7 (or missing): floor=0.02/lev, hardTimeout=30min, weakPeak=15min, deadWeight=10min\n- Score 8-9: floor=0.025/lev, hardTimeout=45min, weakPeak=20min, deadWeight=15min\n- Score 10+: floor=0.03/lev, hardTimeout=60min, weakPeak=30min, deadWeight=20min\nUse the phase1 fields in DSL state (they were set at entry based on score). Check `createdAt` for age:\n1. DEAD WEIGHT: if age > deadWeightCutMin AND current ROE ≤ 0% → close immediately, alert {TELEGRAM}.\n2. WEAK PEAK: if age > weakPeakCutMin AND peak ROE < 3% AND ROE declining → close, alert {TELEGRAM}.\n3. HARD TIMEOUT: if age > hardTimeoutMin AND still Phase 1 → close, alert {TELEGRAM}.\n4. GREEN-IN-10: if age > 10min AND greenIn10==false AND ROE was never positive → set absoluteFloor to 50% of original distance (tighten, don't close). Update state file.\nOn Phase 1 close: log exit details (asset, direction, exitPrice, ROE, exitTime) for potential re-entry.\nIf no lines or all lines are routine and no timing cuts → HEARTBEAT_OK."
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
    "message": "FOX SM: Run `python3 {SCRIPTS}/fox-sm-flip-check.py`, parse JSON.\n\nFor each alert in `alerts`:\n- if `alertLevel==\"FLIP_NOW\"` → close that position on the wallet for `strategyKey`, alert {TELEGRAM} with asset, direction, conviction, strategyKey.\n- if `alertLevel==\"FLIP_WARNING\"` or `alertLevel==\"WATCH\"` → no action needed.\nIf `hasFlipSignal==false` or no FLIP_NOW alerts → HEARTBEAT_OK."
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
    "message": "FOX Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS}/fox-monitor.py`, parse JSON.\n\nFor each strategy in output:\n- if `crypto_liq_buffer_pct<30` → CRITICAL: close position with lowest ROE% in that strategy, alert {TELEGRAM}.\n- if `crypto_liq_buffer_pct<50` → WARNING: alert {TELEGRAM} only.\n- if any position has `liq_distance_pct<15` and wallet_type==\"xyz\" → alert {TELEGRAM}.\nIf no alerts → HEARTBEAT_OK."
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

## 6. Opportunity Scanner — Primary / Main Session

```json
{
  "name": "FOX — Opportunity Scanner v6 (15min)",
  "schedule": { "kind": "cron", "expr": "*/15 * * * *", "tz": "UTC" },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "FOX scanner: Run `PYTHONUNBUFFERED=1 timeout 180 python3 {SCRIPTS}/fox-opportunity-scan-v6.py 2>/dev/null`, parse JSON.\n\nSLOT GUARD (MANDATORY): Check `anySlotsAvailable` BEFORE any entry. If false → HEARTBEAT_OK.\n\nFor each opportunity with finalScore ≥ 175:\n1. Check hourly trend alignment (HARD REQUIREMENT — counter-trend = skip).\n2. Check `max-leverage.json` for actual max leverage (min 7x).\n3. Read `{WORKSPACE}/fox-trade-counter.json` for tiered margin.\n4. Route to best-fit strategy from `{WORKSPACE}/fox-strategies.json`. Open position → create DSL state → ensure DSL cron.\nAlert {TELEGRAM}. If no qualified opportunities → HEARTBEAT_OK."
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
    "message": "FOX Regime: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-market-regime.py`, parse JSON.\n\nSave the full output to `{WORKSPACE}/market-regime-last.json`.\n- if `market_regime==\"BEARISH\"` → log: SHORT only.\n- if `market_regime==\"BULLISH\"` → log: LONG only.\n- if `market_regime==\"NEUTRAL\"` → log: both directions, higher score threshold.\nHEARTBEAT_OK after saving."
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
    "message": "FOX Health: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/fox-health-check.py`, parse JSON.\n\nFor each issue, check the `action` field:\n- `auto_created` → DSL was missing, script created it. Alert {TELEGRAM}.\n- `auto_deactivated` → Orphan DSL deactivated (position closed externally). No alert needed.\n- `auto_replaced` → Direction mismatch fixed. Alert {TELEGRAM}.\n- `updated_state` → Size/entry/leverage reconciled. No alert needed.\n- `skipped_fetch_error` → Orphan check skipped (transient). No alert needed.\n- `alert_only` with type `NO_WALLET` or `DSL_INACTIVE` → CRITICAL. Alert {TELEGRAM}.\nIf no issues → HEARTBEAT_OK."
  }
}
```

## Summary Table

| # | Cron | Interval | Session | Payload Kind | Mandate Key | Model |
|---|------|----------|---------|-------------|-------------|-------|
| 1 | Emerging Movers | */3 * * * * | main | systemEvent | `text` | Primary |
| 2 | DSL v5 | */3 * * * * | isolated | agentTurn | `message` | Mid |
| 3 | SM Flip | */5 * * * * | isolated | agentTurn | `message` | Budget |
| 4 | Watchdog | */5 * * * * | isolated | agentTurn | `message` | Budget |
| 5 | Portfolio | */15 * * * * | isolated | agentTurn | `message` | Mid |
| 6 | Opp Scanner | */15 * * * * | main | systemEvent | `text` | Primary |
| 7 | Market Regime | 0 */4 * * * | isolated | agentTurn | `message` | Mid |
| 8 | Health Check | */10 * * * * | isolated | agentTurn | `message` | Mid |
