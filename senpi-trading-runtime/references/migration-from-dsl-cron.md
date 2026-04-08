# Migration Guide: Python DSL Cron → Plugin Runtime

Migrate from the old `dsl-v5.py` cron-based DSL to the plugin-based DSL exit engine.

---

## Who needs this

Skills previously using two crons (scanner + DSL) now use one cron (scanner) + the plugin runtime. If your agent has a `dsl-v5.py` cron running, follow this guide.

---

## Before you start

**Check for open positions**: `openclaw senpi dsl positions`
- Open positions are protected by the old DSL cron
- The migration installs the plugin runtime FIRST, then removes the old cron — no gap in protection
- The `position_tracker` picks up existing positions automatically

---

## Step 1: Get runtime.yaml

Check if `runtime.yaml` exists in your skill directory.

**If it exists** — proceed to Step 2.

**If it doesn't exist**:

1. Check GitHub for the latest: `https://github.com/Senpi-ai/senpi-skills/tree/main/{skill}/runtime.yaml`
2. If not on GitHub, copy the template: `cp /data/workspace/skills/senpi-trading-runtime/references/runtime-template.yaml /data/workspace/skills/{skill}/runtime.yaml`
3. Update `name:` and `description:` to match your skill

**Then extract your skill's actual DSL values from its scanner .py:**

**Do NOT copy values from another skill's runtime.yaml — each skill has unique DSL tuning.**

Run these commands to find the original values:

```bash
# Find retrace threshold (decimal → multiply by 100 for YAML)
grep "retraceThreshold" /data/workspace/skills/{skill}/scripts/*scanner*.py

# Find breaches required
grep "consecutiveBreachesRequired" /data/workspace/skills/{skill}/scripts/*scanner*.py

# Find absolute floor (negative number → use as positive in YAML max_loss_pct)
grep "absoluteFloorRoe" /data/workspace/skills/{skill}/scripts/*scanner*.py

# Find timeouts
grep -E "hardTimeoutMin|phase1MaxMinutes" /data/workspace/skills/{skill}/scripts/*scanner*.py
grep -E "weakPeakCutMin|weakPeakCutMinutes" /data/workspace/skills/{skill}/scripts/*scanner*.py
grep "deadWeightCutMin" /data/workspace/skills/{skill}/scripts/*scanner*.py

# Find tier definitions (triggerPct + lockHwPct pairs)
grep "triggerPct" /data/workspace/skills/{skill}/scripts/*scanner*.py
```

**How to use the grep results:**

| Grep finds | Write in runtime.yaml |
|---|---|
| `"retraceThreshold": 0.03` | `retrace_threshold: 3` (value × 100) |
| `"consecutiveBreachesRequired": 3` | `consecutive_breaches_required: 3` (same) |
| `"absoluteFloorRoe": -20` | `max_loss_pct: 20.0` (drop the minus) |
| `"hardTimeoutMin": 30` or `"phase1MaxMinutes": 30` | `hard_timeout: interval_in_minutes: 30` |
| `"weakPeakCutMin": 15` or `"weakPeakCutMinutes": 15` | `weak_peak_cut: interval_in_minutes: 15` |
| `"deadWeightCutMin": 10` | `dead_weight_cut: interval_in_minutes: 10` |
| `"triggerPct": 7, "lockHwPct": 40` | `- { trigger_pct: 7, lock_hw_pct: 40 }` |

**If the scanner has multiple conviction tiers** (score-based `if/elif` or `CONVICTION_TIERS` array), use the **first/lowest tier** (tightest defaults). Example: if there are tiers for score<9, score>=9, score>=12 — use the score<9 values.

**If the scanner has `DSL_CONFIG` dict** instead of separate constants, read values from that dict's `phase1` sub-object and `tiers` array.

**Copy ALL tier rows** — skills may have 4, 5, or 6 tiers. Do not use a standard 4-tier set if the skill has more.

---

## Step 2: Delete bootstrap marker

```bash
rm config/bootstrap-complete.json
```

Forces the agent to re-bootstrap with the new instructions.

---

## Step 3: Agent re-bootstraps

On next session, the agent reads the updated SKILL.md and runs:

1. Read senpi-trading-runtime skill
2. Set wallet: `sed -i 's/${WALLET_ADDRESS}/<ACTUAL_ADDRESS>/' runtime.yaml`
3. Set telegram: `sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' runtime.yaml`
4. Install: `openclaw senpi runtime create --path runtime.yaml`
5. Verify: `openclaw senpi runtime list`
6. Remove old DSL cron: `openclaw crons list` → `openclaw crons delete <id>` (any cron with `dsl-v5.py`)
7. Verify scanner cron still running

---

## Step 4: Verify

```bash
openclaw senpi runtime list          # runtime installed
openclaw senpi dsl positions         # positions tracked
openclaw crons list                  # only scanner cron, no DSL cron
```

---

## What changed

| Before | After |
|---|---|
| 2 crons (scanner + `dsl-v5.py`) | 1 cron (scanner) + plugin runtime |
| DSL cron polls every 3 min | Plugin monitors every 30s |
| Agent writes DSL state files | `position_tracker` auto-detects on-chain |
| DSL config in Python / JSON | DSL config in `runtime.yaml` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| runtime.yaml missing | GitHub → migrated skill on machine → [template](runtime-template.yaml). Update DSL values from scanner .py |
| Runtime not listed after install | Check `${WALLET_ADDRESS}` was replaced before installing |
| Old DSL cron still running | `openclaw crons list` → `openclaw crons delete <id>` |
| Positions not in `dsl positions` | Wait 10s (position_tracker poll interval). Verify wallet matches. |
| Rollback needed | Reinstall previous skill version — old SKILL.md recreates DSL cron |
