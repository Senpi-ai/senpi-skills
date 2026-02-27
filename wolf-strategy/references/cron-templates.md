# WOLF v6 Cron Templates — Multi-Strategy

## Model Tier Configuration

Set per-cron in OpenClaw. **Tier 1** = fast/cheap (e.g. claude-haiku-4-5, gpt-4o-mini, gemini-flash). **Tier 2** = capable (e.g. claude-sonnet-4-6, gpt-4o, gemini-pro).

| Cron | Frequency | Model Tier |
|------|-----------|-----------|
| Emerging Movers | 90s (40x/hr) | **Tier 2 (capable)** |
| Opportunity Scanner | 15min (4x/hr) | **Tier 2 (capable)** |
| DSL Combined | 3min (20x/hr) | Tier 1 (fast/cheap) |
| SM Flip Detector | 5min (12x/hr) | Tier 1 (fast/cheap) |
| Watchdog | 5min (12x/hr) | Tier 1 (fast/cheap) |
| Portfolio Update | 15min (4x/hr) | Tier 1 (fast/cheap) |
| Health Check | 10min (6x/hr) | Tier 1 (fast/cheap) |

---

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
WOLF Emerging Movers: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/emerging-movers.py`, parse JSON.
On FIRST_JUMP/CONTRIB_EXPLOSION/IMMEDIATE_MOVER/NEW_ENTRY_DEEP/DEEP_CLIMBER signals: read wolf-strategies.json ONCE, route to best-fit strategy (available slot + risk profile match), open position on that wallet, create DSL state in state/{strategyKey}/dsl-{ASSET}.json.
Apply WOLF entry rules from SKILL.md (min 7x leverage, rank #25+ entry, no top-10 entries, rotation logic).
Alert Telegram ({TELEGRAM}) for each entry. Else HEARTBEAT_OK.
```

---

## 2. DSL Combined Runner (every 3min)

```
WOLF DSL: Run `PYTHONUNBUFFERED=1 python3 {SCRIPTS}/dsl-combined.py`, parse JSON.
For each entry in `results`: if `status=="closed"` → alert Telegram ({TELEGRAM}) with asset, direction, strategyKey, close_reason, upnl. If `phase1_autocut: true` → note timeout cut. If `status=="pending_close"` → alert user (retry next run).
If `any_closed: true` → note freed slot(s) for next Emerging Movers run. Else HEARTBEAT_OK.
```

---

## 3. SM Flip Detector (every 5min)

```
WOLF SM Check: Run `python3 {SCRIPTS}/sm-flip-check.py`, parse JSON.
For each alert in `alerts`: if `alertLevel == "FLIP_NOW"` → close that position on the wallet for `strategyKey` (set `active: false` in `state/{strategyKey}/dsl-{ASSET}.json`), alert Telegram ({TELEGRAM}) with asset, direction, conviction, strategyKey.
Ignore alerts with `alertLevel` of WATCH or FLIP_WARNING (no action needed).
If `hasFlipSignal == false` or no FLIP_NOW alerts → HEARTBEAT_OK.
```

---

## 4. Watchdog (every 5min)

```
WOLF Watchdog: Run `PYTHONUNBUFFERED=1 timeout 45 python3 {SCRIPTS}/wolf-monitor.py`, parse JSON.
Check each strategy: crypto_liq_buffer_pct<50% → WARNING (alert Telegram only); <30% → CRITICAL (close the position with lowest ROE% in that strategy, then alert Telegram ({TELEGRAM})). XYZ liq_distance_pct<15% → alert Telegram.
If no alerts → HEARTBEAT_OK.
```

---

## 5. Portfolio Update (every 15min)

```
WOLF Portfolio: Read wolf-strategies.json, get clearinghouse state per wallet, send Telegram ({TELEGRAM}).
Format: code-block table with per-strategy name/account value/positions (asset, direction, ROE, PnL, DSL tier)/slot usage + global totals.
```

---

## 6. Health Check (every 10min)

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

## 7. Opportunity Scanner (every 15min)

```
WOLF Scanner: Run `PYTHONUNBUFFERED=1 timeout 180 python3 {SCRIPTS}/opportunity-scan-v6.py 2>/dev/null`, parse JSON.
Act on opportunities with finalScore≥175. Use btcMacro.trend for macro context; be cautious with LONGs if "strong_down".

PROCESSING ORDER (prevents context growth):
1. Read wolf-strategies.json ONCE. Map available slots per strategy.
2. Build complete action plan: [(asset, direction, strategyKey, margin, leverage), ...]
3. Execute entries sequentially. No re-reads of wolf-strategies.json.
4. Send ONE consolidated Telegram ({TELEGRAM}) after all entries: "Wolf entered N positions: ASSET1 LONG (Strategy A), ASSET2 SHORT (Strategy B)"

Apply WOLF scanner rules from SKILL.md for routing/conflict judgment. If no opportunities≥175 → HEARTBEAT_OK.
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
