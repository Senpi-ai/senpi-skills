# WOLF v6 Cron Templates — Multi-Strategy

All crons use OpenClaw's systemEvent format:
```json
{
  "name": "...",
  "schedule": { "kind": "every", "everyMs": ... },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": { "kind": "systemEvent", "text": "..." }
}
```

**These are OpenClaw crons, NOT Senpi crons.** They wake the agent with a mandate text that the agent executes.

**v6 change: One set of crons for ALL strategies.** Each script iterates all enabled strategies from `wolf-strategies.json` internally. You do NOT need separate crons per strategy.

Replace these placeholders in all templates:
- `{TELEGRAM}` — telegram:CHAT_ID (e.g. telegram:5183731261)
- `{SCRIPTS}` — path to scripts dir (e.g. /data/workspace/skills/wolf-strategy/scripts)

**Wallet/strategy-specific placeholders are gone in v6.** Scripts read wallets from `wolf-strategies.json`.

---

## 1. Emerging Movers (every 90s)

```
WOLF v6 Scanner: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/emerging-movers.py`, parse JSON.

MANDATE: Enter EARLY on first jumps — before the peak, not at it. Speed is edge.

SIGNAL PRIORITY (act on the FIRST one that fires):

1. **FIRST_JUMP** (highest priority): `isFirstJump: true`. Asset jumped 10+ ranks from #25+ AND was not in previous top 50 (or was >= #30). ENTER IMMEDIATELY. 2+ reasons is enough. vel > 0 is enough. Do NOT wait for confirmation.

2. **CONTRIB_EXPLOSION**: `isContribExplosion: true`. 3x+ contrib spike from rank #20+. ENTER. NEVER downgrade for erratic history.

3. **IMMEDIATE_MOVER**: `isImmediate: true` (but not FIRST_JUMP). 10+ rank jump from #25+ in ONE scan. ENTER if not downgraded.

4. **NEW_ENTRY_DEEP**: Asset appears in top 20 from nowhere. ENTER.

5. **DEEP_CLIMBER**: `isDeepClimber: true`, steady climb, vel >= 0.03, 3+ reasons. Enter when it crosses top 20.

MULTI-STRATEGY SIGNAL ROUTING:
For each actionable signal, read wolf-strategies.json and route:
1. Which strategies have empty slots?
2. Does any strategy already hold this asset? (skip within same strategy, allow cross-strategy)
3. Which strategy's risk profile matches? (aggressive strategies get FIRST_JUMPs, conservative get DEEP_CLIMBERs)
4. Route to best-fit strategy -> open position on THAT strategy's wallet -> create DSL state in state/{strategyKey}/dsl-{ASSET}.json
5. Use the routed strategy's marginPerSlot and defaultLeverage.

RULES:
- Min 7x leverage (check max-leverage.json). Alert user on Telegram ({TELEGRAM}).
- **ANTI-PATTERN: NEVER enter assets already at rank #1-10 for 2+ scans.**
- **DEAD WEIGHT RULE**: Negative ROE + SM conviction against it for 30+ min -> CUT immediately.
- **ROTATION RULE**: If target strategy slots FULL and FIRST_JUMP fires -> compare against weakest position in THAT strategy.
- If no actionable signals -> HEARTBEAT_OK.
- **AUTO-DELEVER**: Per-strategy check — if account below strategy's autoDeleverThreshold -> reduce max slots by 1.
```

---

## 2. DSL Combined Runner (every 3min)

```
WOLF DSL: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/dsl-combined.py`, parse JSON.

This checks ALL active positions across ALL strategies in one pass. Parse the `results` array.

FOR EACH position in results:
- `strategyKey` tells you which strategy owns this position.
- If `closed: true` -> alert user on Telegram ({TELEGRAM}) with asset, direction, strategyKey, close_reason, upnl. Evaluate: empty slot in that strategy for next signal?
- If `tier_changed: true` -> note the tier upgrade (useful for portfolio context).
- If `phase1_autocut: true` and `closed: true` -> position was cut for Phase 1 timeout (90min) or weak peak (45min). Alert user.
- If `status: "pending_close"` -> close failed, will retry next run. Alert user if first occurrence.

If `any_closed: true` -> at least one position was closed this run. Check for new signals.
If all positions active with no alerts -> HEARTBEAT_OK.
```

---

## 3. SM Flip Detector (every 5min)

```
WOLF SM Check: Run `python3 {SCRIPTS}/sm-flip-check.py`, parse JSON.

Multi-strategy: each alert includes `strategyKey` identifying which strategy's position is affected.

If any alert has conviction 4+ in the OPPOSITE direction of our position with 100+ traders -> CUT the position (set DSL state active: false in state/{strategyKey}/dsl-{ASSET}.json). Don't flip, just close and free the slot.
Conviction 2-3 = note but don't act unless position is also in negative ROE.
Alert user on Telegram ({TELEGRAM}) for any cuts.
If hasFlipSignal=false -> HEARTBEAT_OK.
```

---

## 4. Watchdog (every 5min)

```
WOLF Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS}/wolf-monitor.py`. Parse JSON output.

Multi-strategy: output has `strategies` dict keyed by strategy key. Check EACH strategy:

1. **Cross-margin buffer** (`crypto_liq_buffer_pct`): If <50% -> WARNING to user. If <30% -> CRITICAL, consider closing weakest position in THAT strategy.
2. **Position alerts**: Any alert with level=CRITICAL -> immediate Telegram alert ({TELEGRAM}). WARNING -> alert if new (don't repeat same warning within 15min).
3. **Rotation check**: Compare each position's ROE within its strategy. If any position is -15%+ ROE AND emerging movers show a strong climber (top 10, 3+ reasons) we DON'T hold -> suggest rotation to user, noting which strategy has the slot.
4. **XYZ isolated liq**: If liq_distance_pct < 15% -> alert user.
5. Per-strategy watchdog state saved in state/{strategyKey}/watchdog-last.json for dedup.

If no alerts needed -> HEARTBEAT_OK.
```

---

## 5. Portfolio Update (every 15min)

```
WOLF portfolio update: Read wolf-strategies.json for all enabled strategies. For each strategy, get clearinghouse state for its wallet. Send user a concise Telegram update ({TELEGRAM}). Code block table format. Include:
- Per-strategy: name, account value, positions (asset, direction, ROE, PnL, DSL tier), slot usage
- Global: total account value across all strategies, total PnL
```

---

## 6. Health Check (every 10min)

```
WOLF Health Check: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/job-health-check.py`, parse JSON.

Multi-strategy: validates per-strategy state dirs vs actual wallet positions.

If any CRITICAL issues -> fix immediately:
- Orphan DSL state files (active: true but no matching position) -> set active: false in state/{strategyKey}/dsl-{ASSET}.json
- Positions without DSL state files -> create dsl state in the correct strategy's state dir
- Direction mismatches -> fix state file direction

Alert user on Telegram ({TELEGRAM}) for critical issues.
If only WARNINGs -> fix silently.
If no issues -> HEARTBEAT_OK.

NOTE: The combined DSL runner handles all positions across all strategies. Health check validates state files per strategy.
```

---

## 7. Opportunity Scanner (every 15min)

```
WOLF scanner: Run `PYTHONUNBUFFERED=1 timeout 180 python3 {SCRIPTS}/opportunity-scan-v6.py 2>/dev/null`. Parse JSON.

v6 scanner with BTC macro context, hourly trend filter, hard disqualifiers, and cross-scan momentum. Multi-strategy position awareness built in.

For each scored opportunity (threshold 175+, disqualified=false):
1. Check `conflict` field — if true, asset already held in at least one strategy. Consider whether another strategy should also hold it (different direction allowed cross-strategy).
2. Which strategies have empty slots? Route to best-fit.
3. Open position on THAT strategy's wallet, using THAT strategy's marginPerSlot and defaultLeverage.
4. Create DSL state file in state/{strategyKey}/dsl-{ASSET}.json with strategyKey field.
5. Alert user ({TELEGRAM}).

`btcMacro.trend` tells you macro context. If "strong_down", be cautious with LONG signals.
`disqualified` array shows assets that were scored but hard-skipped (with reason and wouldHaveScored for transparency).
`momentum.scanStreak` > 3 with positive scoreDelta = strengthening signal.

Otherwise HEARTBEAT_OK.
```

---

## v6 Changes from v5

| Change | v5 | v6 |
|--------|----|----|
| Strategy support | Single wallet | **Multi-strategy registry** |
| State file location | `dsl-state-WOLF-{ASSET}.json` in workspace root | **`state/{strategyKey}/dsl-{ASSET}.json`** |
| Cron architecture | Some per-strategy values in mandate | **One set of crons, scripts iterate all strategies** |
| Script wallets | Hardcoded or env var | **Read from `wolf-strategies.json`** |
| Opportunity Scanner | Broken/optional | **v6: BTC macro, hourly trend, disqualifiers, parallel fetches** |
| Signal routing | One wallet | **Route to best-fit strategy by available slots + risk profile** |
| Scanner interval | 90s (unchanged) | 90s |
| DSL architecture | Combined runner (unchanged) | Combined runner iterating all strategies |
