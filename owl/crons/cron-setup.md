## OWL — Hunt Scanner
- Schedule: every 15m
- Session: main
- Payload kind: systemEvent
- Enabled: True

### Payload:
```
OWL HUNT v4: Run `OPENCLAW_WORKSPACE=/data/workspace timeout 55 python3 /data/workspace/skills/owl-strategy/scripts/owl-hunt-v4.py`, parse JSON.

If `signals` array contains entries with `action: "ENTER"`:
1. Execute via create_position:
   - orderType: FEE_OPTIMIZED_LIMIT
   - ensureExecutionAsTaker: true (ALO entry)
   - Direction, leverage, asset from signal. Use ISOLATED margin for xyz: assets.
   - Set stopLoss (orderType MARKET) from signal dsl.absoluteFloor.
   - marginAmount from signal.
2. CRITICAL: After EVERY create_position (even timeouts/errors), check clearinghouse to verify fill. ALO can timeout without filling.
3. Create DSL v5 state file at /data/workspace/dsl/{STRATEGY_ID}/{ASSET}.json:
   - tiers from signal.dsl.tiers (9-tier, triggerPct/lockPct as PERCENTAGES)
   - phase1.retraceThreshold = signal.dsl.floorBase (0.06 or 0.08)
   - phase1.absoluteFloor = signal.dsl.absoluteFloor
   - phase1.hardTimeoutMin = signal.dsl.hardTimeoutMin (45/60/75)
   - phase1.weakPeakCutMin = signal.dsl.weakPeakCutMin (20/25/30)
   - phase1.deadWeightCutMin = signal.dsl.deadWeightCutMin (12/15/20)
   - phase1.greenIn10TightenPct = 50
   - phase2.retraceThreshold = 0.012, consecutiveBreachesRequired = 2
   - Store entryScore in score field, greenIn10: false
   - active: true, wallet, strategyId, size, entryPrice, direction, leverage
   - highWaterPrice = entryPrice, currentTierIndex = -1, phase = 1
4. Update owl-state.json activePositions.
5. Telegram ({CHAT_ID}): OWL v4 ENTRY: asset, dir, lev, margin, score (X pts), reasons, crowding, funding.

If no signals: HEARTBEAT_OK. Silent.
```

### openclaw cron add command:
openclaw cron add --name "OWL — Hunt Scanner" --every 15m --session main --wake now --system-event '...payload above...'

---

## OWL — DSL v5
- Schedule: every 3m
- Session: isolated
- Payload kind: agentTurn
- Model: model configured in OpenClaw
- Enabled: True

### Payload:
```
OWL DSL v5: First read owl-state.json at /data/workspace/skills/owl-strategy/state/{STRATEGY_ID}/owl-state.json — if activePositions is empty, reply HEARTBEAT_OK immediately.

Otherwise run: `DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID={STRATEGY_ID} PYTHONUNBUFFERED=1 python3 /data/workspace/scripts/dsl/dsl-v5.py`, parse ndjson.

CONVICTION-SCALED Phase 1 timing (read score + createdAt from each DSL state file):
Score 8-9: hardTimeout=45min, weakPeak=20min, deadWeight=12min
Score 10-11: hardTimeout=60min, weakPeak=25min, deadWeight=15min
Score 12+: hardTimeout=75min, weakPeak=30min, deadWeight=20min
No score: use 8-9 defaults.

Phase 1 timing rules:
- hardTimeout: close if never hit Tier 1 in X min
- weakPeakCut: close if peak ROE < 5% (was 3%) and declining after X min — 5% gives slow builders room to breathe
- deadWeightCut: close if never positive ROE after X min
- greenIn10: if greenIn10TightenPct > 0 AND position never showed positive ROE in 10min, tighten absoluteFloor to 50% of original distance from entry

EXCHANGE SL DELAY (T1 WARMUP): If DSL state has exchangeSlDelayMin > 0, do NOT set exchange SL via edit_position until that many minutes after entry. This prevents noise from tripping SL on fresh positions. After delay, set exchange SL at absoluteFloor.

STAGNATION TP: If ROE >= 8% AND highWaterPrice unchanged for 60min, auto-close.

For closes: if position is in profit (Phase 2), use close_position with orderType FEE_OPTIMIZED_LIMIT, ensureExecutionAsTaker true. If Phase 1 or emergency, use MARKET.

For each closed position: Telegram ({CHAT_ID}) with OWL emoji, asset, PnL, reason, tier. Update owl-state.json: remove from activePositions. Record loss in owl-hunt-state.json recentLosses for 4hr cooldown.
For tier changes: Telegram.
Otherwise: HEARTBEAT_OK.
```

### openclaw cron add command:
openclaw cron add --name "OWL — DSL v5" --every 3m --session isolated --wake now --message '...payload above...'

---

## OWL — OI Tracker
- Schedule: every 5m
- Session: isolated
- Payload kind: agentTurn
- Model: model configured in OpenClaw
- Enabled: True

### Payload:
```
OWL OI Tracker: Run `OPENCLAW_WORKSPACE=/data/workspace timeout 55 python3 /data/workspace/skills/owl-strategy/scripts/oi-tracker.py`, parse JSON output.

If `success: true` → HEARTBEAT_OK (silent).
If `error` → report to Telegram ({CHAT_ID}).
```

### openclaw cron add command:
openclaw cron add --name "OWL — OI Tracker" --every 5m --session isolated --wake now --message '...payload above...'

---

## OWL — Risk Guardian
- Schedule: every 5m
- Session: isolated
- Payload kind: agentTurn
- Model: model configured in OpenClaw
- Enabled: True

### Payload:
```
OWL Risk Guardian: First read owl-state.json at /data/workspace/owl-state.json — if activePositions is empty, reply HEARTBEAT_OK immediately.

Otherwise run: `OPENCLAW_WORKSPACE=/data/workspace timeout 55 python3 /data/workspace/skills/owl-strategy/scripts/owl-risk.py`, parse JSON.

Process actions by priority:
- reCrowding (CRITICAL): close position immediately via close_position (MARKET order, wallet {WALLET}). Crowd is growing, not unwinding.
- oiRecovery (HIGH): close position. OI rebounded, thesis invalidated.
- fundingFlip (MEDIUM): tighten SL to breakeven via edit_position.
- fundingFloorAdjust (LOW): tighten SL by funding income earned via edit_position.
- dailyLossHalt (CRITICAL): halt new entries, tighten all stops. Update safetyFlags in owl-state.json.

After closing any position: update owl-state.json (remove from activePositions). Record loss in owl-hunt-state.json recentLosses for 4hr cooldown.

Send Telegram ({CHAT_ID}) for any action taken.
If no actions: HEARTBEAT_OK (silent).
```

### openclaw cron add command:
openclaw cron add --name "OWL — Risk Guardian" --every 5m --session isolated --wake now --message '...payload above...'

---

## OWL — Correlation Scanner
- Schedule: every 5m
- Session: main
- Payload kind: systemEvent
- Enabled: True

### Payload:
```
OWL CORRELATION: Run `OPENCLAW_WORKSPACE=/data/workspace timeout 55 python3 /data/workspace/skills/owl-strategy/scripts/owl-correlation.py`, parse JSON.

If `signals` array contains entries with `action: "ENTER"`:
IMPORTANT: Check owl-state.json activePositions first. Max 3 total slots. Same-direction limit: max 2.

For each valid signal:
1. create_position with orderType FEE_OPTIMIZED_LIMIT, ensureExecutionAsTaker true.
2. Do NOT set stopLoss on create_position. DSL cron sets exchange SL after 5min delay (T1 warmup).
3. Check clearinghouse after EVERY create_position.
4. Create DSL v5 state file:
   - 9-tier, triggerPct/lockPct as PERCENTAGES
   - exchangeSlDelayMin = 5, weakPeakRoeThreshold = 5
   - Store score, scanner: "correlation", leader + lagRatio
   - active: true, phase: 1, currentTierIndex: -1
5. Update owl-state.json activePositions.
6. Telegram ({CHAT_ID}): OWL CORRELATION: asset, dir, leader, move, lag, score.

If no signals: HEARTBEAT_OK. Silent.
```

### openclaw cron add command:
openclaw cron add --name "OWL — Correlation Scanner" --every 5m --session main --wake now --system-event '...payload above...'

---

## OWL — Momentum Scanner
- Schedule: every 5m
- Session: main
- Payload kind: systemEvent
- Enabled: True

### Payload:
```
OWL MOMENTUM: Run `OPENCLAW_WORKSPACE=/data/workspace timeout 55 python3 /data/workspace/skills/owl-strategy/scripts/owl-momentum.py`, parse JSON.

If `signals` array contains entries with `action: "ENTER"`:
IMPORTANT: Check owl-state.json activePositions first. Max 3 total slots shared across ALL OWL scanners. If slots full, skip.
Same-direction limit: max 2 positions in the same direction.

For each valid signal:
1. create_position with orderType FEE_OPTIMIZED_LIMIT, ensureExecutionAsTaker true. Use ISOLATED margin for xyz: assets.
2. Do NOT set stopLoss on create_position. The DSL cron will set exchange SL after 5min delay (T1 warmup — prevents noise trips on fresh entries).
3. CRITICAL: Check clearinghouse after EVERY create_position to verify fill.
4. Create DSL v5 state file at /data/workspace/dsl/{STRATEGY_ID}/{ASSET}.json:
   - 9-tier from signal.dsl.tiers (triggerPct/lockPct as PERCENTAGES)
   - phase1 from signal.dsl (hardTimeoutMin=45, weakPeakCutMin=20, deadWeightCutMin=12)
   - weakPeakRoeThreshold = 5 (close only if peak < 5%, not 3%)
   - exchangeSlDelayMin = 5 (T1 warmup)
   - phase2.retraceThreshold = 0.012
   - Store entryScore in score field
   - active: true, phase: 1, currentTierIndex: -1, greenIn10: false
5. Update owl-state.json activePositions.
6. Telegram ({CHAT_ID}): OWL MOMENTUM: asset, dir, lev, margin, score, reasons.

If no signals: HEARTBEAT_OK. Silent.
```

### openclaw cron add command:
openclaw cron add --name "OWL — Momentum Scanner" --every 5m --session main --wake now --system-event '...payload above...'

---

