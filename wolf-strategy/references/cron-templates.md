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

**2-tier model approach** — auto-configured by `wolf-setup.py --provider`:

| Provider | Mid Model | Budget Model |
|----------|-----------|--------------|
| `anthropic` | `anthropic/claude-sonnet-4-5` | `anthropic/claude-haiku-4-5` |
| `openai` | `openai/gpt-4o` | `openai/gpt-4o-mini` |
| `google` | `google/gemini-2.0-flash` | `google/gemini-2.0-flash-lite` |

> Model IDs in the `cronTemplates` output from `wolf-setup.py` are already correct for your provider. Use them directly when creating crons.

All 5 crons can also run on a single model if you prefer simplicity over cost savings.

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
SLOT GUARD (MANDATORY): Check `anySlotsAvailable` — if false, output HEARTBEAT_OK immediately. Do NOT open any position when all strategies show 0 available slots. Check `strategySlots` per strategy before routing.
On FIRST_JUMP/CONTRIB_EXPLOSION/IMMEDIATE_MOVER/NEW_ENTRY_DEEP/DEEP_CLIMBER signals:
use `strategySlots` to route to a strategy with available > 0 (skip strategies at capacity).
Enter via: `python3 {SCRIPTS}/open-position.py --strategy {strategyKey} --asset {ASSET} --direction {DIR} --conviction {CONVICTION}`
Conviction comes from scanner output (`conviction` field per alert). This opens the position AND creates the DSL state file atomically. Do NOT manually call create_position or hand-write DSL JSON.
No leverage floor — all assets are tradeable. Leverage auto-calculated from strategy tradingRisk + asset maxLeverage + signal conviction. Apply WOLF entry rules from SKILL.md (rank #25+ entry, no top-10 entries, rotation logic).
Alert Telegram ({TELEGRAM}) for each entry. Else HEARTBEAT_OK.
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
