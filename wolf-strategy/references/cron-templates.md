# WOLF v6 Cron Templates — Multi-Strategy

## Session & Model Tier Configuration

WOLF uses isolated sessions and a 2-tier model approach. Configure per-cron in OpenClaw.

| Cron | Frequency | Session | Payload | Model Tier |
|------|-----------|---------|---------|------------|
| Emerging Movers | 3min (20x/hr) | isolated | agentTurn | Mid |
| DSL Combined | 3min (20x/hr) | isolated | agentTurn | Mid |
| Health Check | 10min (6x/hr) | isolated | agentTurn | Mid |
| SM Flip Detector | 5min (12x/hr) | isolated | agentTurn | Budget (cheapest available) |
| Watchdog | 5min (12x/hr) | isolated | agentTurn | Budget (cheapest available) |
| Risk Guardian | 5min (12x/hr) | isolated | agentTurn | Budget (cheapest available) |

**2-tier model approach** — auto-configured by `wolf-setup.py --provider`:

| Provider | Mid Model | Budget Model |
|----------|-----------|--------------|
| `anthropic` | `anthropic/claude-sonnet-4-5` | `anthropic/claude-haiku-4-5` |
| `openai` | `openai/gpt-4o` | `openai/gpt-4o-mini` |
| `google` | `google/gemini-2.0-flash` | `google/gemini-2.0-flash-lite` |

> Model IDs in the `cronTemplates` output from `wolf-setup.py` are already correct for your provider. Use them directly when creating crons.

All 6 crons can also run on a single model if you prefer simplicity over cost savings.

---

## Notification Policy

**Only notify when a trade action was taken.** Isolated sessions have no memory, so informational warnings fire every cycle and create noise. Follow these rules:

**NOTIFY (Telegram):**
- Position opened (Emerging Movers entry)
- Position closed (DSL breach, phase1 autocut, stagnation, SM FLIP_NOW, Watchdog emergency close)
- Position auto-fixed (Health Check: `auto_created`, `auto_replaced`)
- Critical config error requiring manual fix (Health Check: `NO_WALLET`, `DSL_INACTIVE`)

**NEVER NOTIFY:**
- Buffer/margin warnings with no close action taken
- Liquidation distance warnings with no close action taken
- ROE warnings, rotation candidates, or other informational output
- Pending retries (`pending_close`) — auto-retries next cycle
- Internal bookkeeping (freed slots, state reconciliation)
- Transient errors (fetch failures, stale data)

**Fallback rule:** If you did not open, close, or fix a position, output `HEARTBEAT_OK` — no Telegram message.

---

All crons use the **isolated session** (agentTurn) format:

```json
{
  "name": "...",
  "schedule": { "kind": "every", "everyMs": ... },
  "sessionTarget": "isolated",
  "payload": { "kind": "agentTurn", "message": "...", "model": "<Mid or Budget tier model ID>" }
}
```

**Critical:** agentTurn uses `"message"`, NOT `"text"` — using `"text"` will silently fail. Model is set inside `payload`, not at the job root level.

**These are OpenClaw crons, NOT Senpi crons.** They wake the agent with a mandate text that the agent executes.

**v6 change: One set of crons for ALL strategies.** Each script iterates all enabled strategies from `wolf-strategies.json` internally. You do NOT need separate crons per strategy.

**All crons are isolated.** Each cron runs in its own session — no context pollution between runs, enabling cheaper model tiers. Every cron is self-contained: it runs a script, parses JSON, and acts on rules.

Replace these placeholders in all templates:
- `{TELEGRAM}` — telegram:CHAT_ID (e.g. telegram:5183731261)
- `{SCRIPTS}` — path to scripts dir (e.g. /data/workspace/skills/wolf-strategy/scripts)
- `{WORKSPACE}` — path to workspace root (e.g. /data/workspace)

**Wallet/strategy-specific placeholders are gone in v6.** Scripts read wallets from `wolf-strategies.json`.

---

## 1. Emerging Movers (every 3min) — isolated / agentTurn

```
WOLF Emerging Movers: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/emerging-movers.py`, parse JSON.
SLOT GUARD (MANDATORY): Check `anySlotsAvailable` — if false AND no FIRST_JUMP signals, output HEARTBEAT_OK immediately. Do NOT open any position when all strategies show 0 available slots. Check `strategySlots` per strategy before routing.
SIGNAL SELECTION (MANDATORY — strict priority order):
Act ONLY on the `topPicks` array — it contains pre-selected signals sorted by priority. Process topPicks[0] first, then topPicks[1], etc. Do NOT browse `alerts` to pick signals yourself.
Each signal has a `signalType` field (FIRST_JUMP, CONTRIB_EXPLOSION, IMMEDIATE_MOVER, NEW_ENTRY_DEEP, DEEP_CLIMBER) and `signalPriority` (1=highest). NEVER skip a higher-priority signal to act on a lower-priority one.
If `hasFirstJump` is true, FIRST_JUMP signals MUST be acted on before any other type.
Use `strategySlots` to route each signal to a strategy with available > 0 (skip strategies at capacity).
Enter via: `python3 {SCRIPTS}/open-position.py --strategy {strategyKey} --asset {qualifiedAsset} --signal-index {signalIndex}`
The `qualifiedAsset` field includes the `xyz:` prefix for XYZ equities (e.g., `xyz:SILVER`). Use it directly — do NOT strip the prefix.
The `signalIndex` field is in each alert — it tells open-position.py which signal to use for direction and conviction. Do NOT pass --direction or --conviction manually. This opens the position AND creates the DSL state file atomically. Do NOT manually call create_position or hand-write DSL JSON.
No leverage floor — all assets are tradeable. Leverage auto-calculated from strategy tradingRisk + asset maxLeverage + signal conviction.
ENTRY CHECKS (do NOT enter if any fail):
- currentRank must be >= 25 at time of first signal (the scanner already filters this — trust `topPicks`)
- NEVER enter assets currently at rank #1-10 — that's the top, not the entry
- >= 10 SM traders for crypto (ignore trader count for XYZ equities)
- Negative velocity + no jump = skip
ANTI-PATTERNS — do NOT override these with judgment:
- 4+ reasons at rank #5 = SKIP (asset already peaked, you'd be buying the top)
- 2 reasons at rank #25 with big jump = ENTER (the move is just starting)
- NEVER wait for a signal to "clean up" — by the time history is smooth and velocity high, the move is priced in
- LATE ENTRY: If an asset has been in top 10 for 2+ scans, it's too late — skip it. Enter on the FIRST signal or not at all.
ROTATION COOLDOWN (MANDATORY): When slots are full and rotation is needed, only rotate a coin listed in `strategySlots[strategy].rotationEligible pCoins`. Do NOT rotate coins absent from that list — they are under cooldown. If `hasRotationCandidate` is false for all strategies, output HEARTBEAT_OK — no rotation is safe this cycle.
SAME-RUN ANTI-ROTATION: NEVER rotate a position you opened earlier in THIS same cron run. After opening position(s), remaining signals with no available slots must be SKIPPED. Positions need 45 minutes of price action before rotation eligibility.
For each successful entry, send each message in the `notifications` array from the open-position.py output to Telegram ({TELEGRAM}). Else HEARTBEAT_OK.
```

---

## 2. DSL Combined Runner (every 3min) — isolated / agentTurn

```
WOLF DSL: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/dsl-combined.py`, parse JSON.
Send each message in `notifications` to Telegram ({TELEGRAM}).
If `notifications` is empty → HEARTBEAT_OK.
```

---

## 3. SM Flip Detector (every 5min) — isolated / agentTurn

```
WOLF SM Check: Run `python3 {SCRIPTS}/sm-flip-check.py`, parse JSON.
For each alert in `alerts`: if `alertLevel == "FLIP_NOW"` → close that position on the wallet for `strategyKey` (set `active: false` in `{WORKSPACE}/state/{strategyKey}/dsl-{ASSET}.json`), alert Telegram ({TELEGRAM}) with asset, direction, conviction, strategyKey.
Ignore alerts with `alertLevel` of WATCH or FLIP_WARNING (no action needed).
If `hasFlipSignal == false` or no FLIP_NOW alerts → HEARTBEAT_OK.
```

---

## 4. Watchdog (every 5min) — isolated / agentTurn

```
WOLF Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS}/wolf-monitor.py`, parse JSON.
For each item in `action_required`: close the specified position (coin + strategyKey), then alert Telegram ({TELEGRAM}) with what was closed and why.
Ignore all other alerts in the output — they are informational only.
If `action_required` is empty → HEARTBEAT_OK.
```

---

## 5. Health Check (every 10min) — isolated / agentTurn

```
WOLF Health Check: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/job-health-check.py`, parse JSON.
Send each message in `notifications` to Telegram ({TELEGRAM}).
If `notifications` is empty → HEARTBEAT_OK.
```

---

## 6. Risk Guardian (every 5min) — isolated / agentTurn

```
WOLF Risk Guardian: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/risk-guardian.py`, parse JSON.
Send each message in `notifications` to Telegram ({TELEGRAM}).
If `notifications` is empty → HEARTBEAT_OK.
```

---

## v6 Changes from v5

| Change | v5 | v6 |
|--------|----|----|
| Strategy support | Single wallet | **Multi-strategy registry** |
| State file location | `dsl-state-WOLF-{ASSET}.json` in workspace root | **`state/{strategyKey}/dsl-{ASSET}.json`** |
| Cron architecture | Some per-strategy values in mandate | **One set of crons, scripts iterate all strategies** |
| Script wallets | Hardcoded or env var | **Read from `wolf-strategies.json`** |
| Signal routing | One wallet | **Route to best-fit strategy by available slots + risk profile** |
| Scanner interval | 90s | 3min |
| DSL architecture | Combined runner (unchanged) | Combined runner iterating all strategies |
