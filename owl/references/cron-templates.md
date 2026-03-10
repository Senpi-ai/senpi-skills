# OWL Cron Templates

Replace `{STRATEGY_ID}`, `{WALLET}`, `{CHAT_ID}`, and `{WORKSPACE}` with actual values.

---

## Cron 1: OI Tracker (every 5 min, isolated, budget model)

```json
{
  "name": "OWL — OI Tracker",
  "schedule": { "kind": "cron", "expr": "*/5 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL OI Tracker: Run `OPENCLAW_WORKSPACE={WORKSPACE} timeout 55 python3 {WORKSPACE}/skills/owl-strategy/scripts/oi-tracker.py`, parse JSON output.\n\nIf `success: true` → HEARTBEAT_OK (silent). NOTIFICATION: Never send Telegram — data collection only.\nIf `error` → report to Telegram ({CHAT_ID})."
  },
  "delivery": { "mode": "none" }
}
```

## Cron 2: Crowding Scanner (every 5 min, isolated, budget model)

```json
{
  "name": "OWL — Crowding Scanner",
  "schedule": { "kind": "cron", "expr": "1-59/5 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL Crowding Scanner: Run `OPENCLAW_WORKSPACE={WORKSPACE} timeout 55 python3 {WORKSPACE}/skills/owl-strategy/scripts/crowding-scanner.py`, parse JSON.\n\nIf `newCrowded` array is non-empty: notify Telegram ({CHAT_ID}) with asset, score, direction, persistence count.\nIf `promoted` array is non-empty (asset reached READY): notify Telegram.\nOtherwise → HEARTBEAT_OK (silent).\n\nNOTIFICATION: Never send Telegram. Crowding data is consumed by the entry trigger, not the user."
  },
  "delivery": { "mode": "announce" }
}
```

## Cron 3: Exhaustion Detector (every 3 min, isolated, budget model)

```json
{
  "name": "OWL — Exhaustion Detector",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL Exhaustion Detector: Run `OPENCLAW_WORKSPACE={WORKSPACE} timeout 55 python3 {WORKSPACE}/skills/owl-strategy/scripts/exhaustion-detector.py`, parse JSON.\n\nIf `newReady` array is non-empty: notify Telegram ({CHAT_ID}) — asset is READY for contrarian entry.\nIf `expired` array is non-empty: note assets that dropped back to CROWDED.\nOtherwise → HEARTBEAT_OK (silent).\n\nNOTIFICATION: Never send Telegram. Exhaustion state is consumed by the entry trigger."
  },
  "delivery": { "mode": "announce" }
}
```

## Cron 4: Entry Trigger (every 3 min, isolated session)

```json
{
  "name": "OWL — Entry Trigger",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL ENTRY TRIGGER: Run `OPENCLAW_WORKSPACE={WORKSPACE} timeout 55 python3 {WORKSPACE}/skills/owl-strategy/scripts/owl-entry.py`, parse JSON.\n\nIf `signals` array contains entries with `action: \"ENTER\"`:\n1. Verify slot available (max 3), BTC correlation guard, same-direction guard.\n2. Execute via create_position: LIMIT order, contrarian direction, leverage from config.\n3. Pass stopLoss and takeProfit from signal's `dsl` field.\n4. Create DSL v5 state file at `{WORKSPACE}/dsl/{STRATEGY_ID}/{ASSET}.json`.\n   - Use SKILL.md DSL schema (phase1 retrace 0.03/leverage, hardTimeout 75min, etc.)\n   - Include OWL-specific fields: crowdingScoreAtEntry, exhaustionScoreAtEntry, crowdedDirection, entryTriggers, fundingRateAtEntry.\n5. Update owl-state.json: add to activePositions, increment dailyStats.\n6. Send ONE Telegram notification ({CHAT_ID}).\n\nIf no READY assets or no triggers → HEARTBEAT_OK.\n\nNOTIFICATION: Only send Telegram if a position was OPENED. Do NOT narrate scanned assets, skipped signals, or reasoning. No entry = silence."
  }
}
```

## Cron 5: Risk Guardian (every 5 min, isolated, mid model)

```json
{
  "name": "OWL — Risk Guardian",
  "schedule": { "kind": "cron", "expr": "2-59/5 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL Risk Guardian: Run `OPENCLAW_WORKSPACE={WORKSPACE} timeout 55 python3 {WORKSPACE}/skills/owl-strategy/scripts/owl-risk.py`, parse JSON.\n\nProcess actions by priority:\n- `reCrowding: true` → CRITICAL: close position immediately via close_position. Crowd is growing, not unwinding.\n- `oiRecovery: true` → HIGH: close position. OI rebounded, thesis invalidated.\n- `fundingFlip: true` → MEDIUM: tighten SL to breakeven via edit_position.\n- `fundingFloorAdjust` → LOW: tighten SL by funding income earned via edit_position.\n- `dailyLossHalt: true` → CRITICAL: halt new entries, tighten all stops.\n\nSend Telegram ({CHAT_ID}) for any action taken.\nIf no actions → HEARTBEAT_OK (silent).\n\nNOTIFICATION: Only send Telegram if gate CLOSED, position FORCE CLOSED, or re-crowding exit triggered. Do NOT narrate routine checks."
  },
  "delivery": { "mode": "announce" }
}
```

## Cron 6: DSL v5 (every 3 min, isolated, mid model)

```json
{
  "name": "OWL — DSL v5",
  "schedule": { "kind": "cron", "expr": "*/3 * * * *" },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "OWL DSL v5: First check owl-state.json at {WORKSPACE}/skills/owl-strategy/state/{STRATEGY_ID}/owl-state.json — if activePositions is empty, reply HEARTBEAT_OK immediately.\n\nOtherwise run: `DSL_STATE_DIR={WORKSPACE}/dsl DSL_STRATEGY_ID={STRATEGY_ID} PYTHONUNBUFFERED=1 python3 {WORKSPACE}/scripts/dsl/dsl-v5.py`, parse ndjson.\n\nAlso apply OWL Phase 1 timing rules by reading createdAt from each DSL state file:\n- hardTimeoutMin: 75 → close if position never hit Tier 1 in 75min\n- weakPeakCutMin: 40 → close if peak ROE < 3% and declining after 40min\n- deadWeightCutMin: 25 → close if never positive ROE after 25min\n\nFor each closed position: alert Telegram ({CHAT_ID}) with 🦉 emoji, PnL, reason.\nFor tier changes: alert Telegram.\nOtherwise → HEARTBEAT_OK.\n\nNOTIFICATION: Only send Telegram on position CLOSED or tier CHANGED. Do NOT narrate routine DSL ticks."
  },
  "delivery": { "mode": "announce" }
}
```

---

## Stagger Notes

- OI Tracker: :00, :05, :10, :15...
- Crowding Scanner: :01, :06, :11, :16... (1min offset)
- Exhaustion Detector: :00, :03, :06, :09... (overlaps with OI Tracker at :00 marks — different session, no conflict)
- Entry Trigger: :00, :03, :06, :09... (main session, no overlap with isolated crons)
- Risk Guardian: :02, :07, :12, :17... (2min offset from OI Tracker)
- DSL v5: :00, :03, :06, :09... (isolated, separate from Entry)

All scanners use `timeout 55` to prevent overlap within their own cadence.
