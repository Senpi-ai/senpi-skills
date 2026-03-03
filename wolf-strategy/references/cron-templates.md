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

**2-tier model approach** (configure per-cron in OpenClaw):
- **Mid** — Structured tasks, script output parsing, multi-strategy routing, entry decisions. Examples: `anthropic/claude-sonnet-4-5`, `openai/gpt-4o`, `google/gemini-2.0-flash`.
- **Budget** — Simple threshold checks, binary decisions. Examples: `anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini`, `google/gemini-2.0-flash-lite`.

All 5 crons can also run on a single model if you prefer simplicity over cost savings.

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
For each entry in `results`: if `status=="closed"` → alert Telegram ({TELEGRAM}) with asset, direction, strategyKey, close_reason, upnl. If `phase1_autocut: true` → note timeout cut. If `status=="pending_close"` → alert user (retry next run).
If `any_closed: true` → note freed slot(s) for next Emerging Movers run. Else HEARTBEAT_OK.
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
Check each strategy: crypto_liq_buffer_pct<50% → WARNING (alert Telegram only); <30% → CRITICAL (close the position with lowest ROE% in that strategy, then alert Telegram ({TELEGRAM})). XYZ liq_distance_pct<15% → alert Telegram.
If no alerts → HEARTBEAT_OK.
```

---

## 5. Health Check (every 10min) — isolated / agentTurn

```
WOLF Health Check: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/job-health-check.py`, parse JSON.
The script auto-fixes most issues (check the `action` field per issue):
- auto_created → DSL was missing, script created it. Alert Telegram ({TELEGRAM}).
- auto_deactivated → Orphan DSL deactivated (position closed externally). No alert needed.
- auto_replaced → Direction mismatch fixed with fresh DSL. Alert Telegram ({TELEGRAM}).
- updated_state → Size/entry/leverage reconciled to match on-chain. No alert needed.
- skipped_fetch_error → Orphan check skipped due to API error. No alert needed (transient).
- alert_only → Script could not auto-fix. Handle manually:
  - NO_WALLET → CRITICAL, needs manual config. Alert Telegram ({TELEGRAM}).
  - DSL_INACTIVE → CRITICAL, set `active: true` in the DSL state file. Alert Telegram ({TELEGRAM}).
If no issues → HEARTBEAT_OK.
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
