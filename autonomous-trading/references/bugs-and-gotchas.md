## Known Bugs & Gotchas

> ⚠️ Read this section FIRST. These are hard-won lessons that cost real money.

### 🐛 BUG: `create_position` with `dryRun: true` ACTUALLY EXECUTES

**Senpi's `create_position` ignores the `dryRun` flag and opens a real position.** There is no dry-run mode. Do NOT use `dryRun: true` thinking it's safe — it will place a real trade with real money.

**Workaround:** There is none. Don't use `dryRun: true`. If you need to validate parameters, check them manually before calling.

### 🐛 Race Condition: Phantom Closes

Multiple cron jobs (scanner, SM flip detector, DSL) can all try to close the same position. This was the "phantom close" mystery — a position would "disappear" because the scanner closed it, then DSL fired 2 minutes later and found nothing.

**Fix:** When ANY job closes a position:
1. Immediately deactivate the DSL state file for that position
2. Immediately disable the DSL cron for that position
3. Log which job performed the close and why
4. All other jobs should check if the position still exists before attempting close

```python
def close_position_safe(asset, reason, job_name):
    """Close position and immediately clean up all related automation."""
    # 1. Close the position
    result = close_position(asset)
    
    # 2. Immediately deactivate DSL state file
    dsl_state_path = f"dsl-state-{asset.lower()}.json"
    if os.path.exists(dsl_state_path):
        state = json.load(open(dsl_state_path))
        state["active"] = False
        state["deactivated_by"] = job_name
        state["deactivated_at"] = datetime.utcnow().isoformat()
        state["deactivated_reason"] = reason
        json.dump(state, open(dsl_state_path, "w"), indent=2)
    
    # 3. Disable DSL cron for this asset
    disable_dsl_cron(asset)
    
    # 4. Log it
    log(f"[{job_name}] Closed {asset}: {reason}")
    return result
```

### 🐛 XYZ DEX Positions Require `leverageType: "ISOLATED"`

All XYZ DEX positions (equities, metals, indices like `xyz:GOLD`, `xyz:SILVER`, `xyz:NVDA`) **must** be opened with `leverageType: "ISOLATED"`. Any wallet can hold them — you do NOT need a separate wallet. Just set the leverage type correctly.

### ⚠️ Tier 1 Lock Does NOT Guarantee Profit

A common misconception: "I locked Tier 1, so I'm safe." **Wrong.**

The tier lock protects your profit measured from the **high water mark**, not from your entry price. If price dumps below your entry, you still lose — the lock only prevents giving back gains from the peak.

Example: Enter at $100, price goes to $110 (Tier 1 locks), then dumps to $95. Your Tier 1 lock preserves gains relative to $110, but you're still -$5 from entry.

### ⚠️ Scanner Leverage ≠ Actual Max Leverage

The scanner's `leverage` recommendation is conservative. Always check `max-leverage.json` for actual maximums from the Hyperliquid API `meta` endpoint. See `/data/workspace/recipes/max-leverage.md`.

Examples of what the scanner underestimates:
- HYPE: scanner says ~4x, actual max is 10x
- XRP: scanner says ~6x, actual max is 20x
- BTC: scanner says ~3x, actual max is 40x

---
