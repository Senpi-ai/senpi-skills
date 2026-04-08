# DSL to Plugin Migration Playbook

**Applies to**: Any skill that has Python-based DSL exit logic and needs to migrate to the `senpi-trading-runtime` plugin.

**Already migrated**: ORCA, FOX, MANTIS, ROACH

---

## Step 1: Extract DSL config into runtime.yaml

Read the skill's scanner `.py` file and find: `DSL_TIERS`, `CONVICTION_TIERS`, `STAGNATION_TP`, `build_dsl_state_template()`.

Create `{skill}/runtime.yaml` with the runtime config:

```yaml
name: {skill}-tracker
version: 1.0.0
description: >
  {SKILL} position tracker with DSL trailing stop-loss.

strategy:
  wallet: "${WALLET_ADDRESS}"
  enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 360
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 120
      min_value: 5
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 60
    phase1:
      enabled: true
      max_loss_pct: 4.0
      retrace_threshold: 7
      consecutive_breaches_required: 1
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 7,  lock_hw_pct: 40 }
        - { trigger_pct: 12, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 75 }
        - { trigger_pct: 20, lock_hw_pct: 85 }

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

**Reference**: `senpi-trading-runtime/references/yaml-schema.md` for full field definitions, `senpi-trading-runtime/references/strategy-examples.md` for alternative profiles (conservative, aggressive, profit-focused).

---

## Step 2: Remove DSL code from Python scanner

**From scanner .py** — delete:
- `DSL_TIERS`, `CONVICTION_TIERS`, `STAGNATION_TP` constants
- `build_dsl_state_template()` function
- `dslState` loop in `run()`
- DSL keys from `constraints` output (`stagnationTp`, `dslTiers`, `convictionTiers`, `_dslNote`)

**From config .json** — delete:
- Entire `"dsl": { ... }` section

**Keep**: All scanner logic, non-DSL constants, non-DSL output.

---

## Step 3: Update SKILL.md

**Remove**:
- DSL High Water Mode section (JSON tiers, Phase 1 table, Stagnation TP)
- DSL State Template section
- DSL cron from Cron/Bootstrap
- Stagnation TP from Risk Management table
- Conviction tier rows from any changelog tables
- "create DSL state" from agent rules
- Any `dsl-implementation.md` references

**Add/Update**:

Frontmatter:
```yaml
metadata:
  requires:
    - senpi-trading-runtime
```

Exit Management section:
```markdown
## Exit Management
DSL exit is handled by the plugin runtime via `runtime.yaml`. The `position_tracker` scanner auto-detects position opens/closes on-chain.

**Monitor positions:**
- `openclaw senpi dsl positions` — list all DSL-tracked positions
- `openclaw senpi dsl inspect <ASSET>` — full position details
```

Runtime Setup section:
```markdown
## Runtime Setup

**Step 1:** Set your strategy wallet address in runtime.yaml:
```bash
sed -i 's/${WALLET_ADDRESS}/<STRATEGY_WALLET_ADDRESS>/' /data/workspace/skills/{skill}-strategy/runtime.yaml
```

**Step 2:** Install the runtime:
```bash
openclaw senpi runtime create --path /data/workspace/skills/{skill}-strategy/runtime.yaml
```

**Step 3:** Verify:
```bash
openclaw senpi runtime list
```
```

Bootstrap Gate (insert as first step):
```
1. Read the senpi-trading-runtime skill: `cat /data/workspace/skills/senpi-trading-runtime/SKILL.md`
```

Agent rule update:
```
Verify runtime is installed on every session start.
Run `openclaw senpi runtime list`. Runtime must be listed.
```

Files table — add:
```
| runtime.yaml | Runtime config for plugin (DSL exit + position tracker) |
```

---

## Step 4: Verify

```bash
# Python syntax
python3 -c "import py_compile; py_compile.compile('{skill}/scripts/{skill}-scanner.py', doraise=True)"

# JSON syntax
python3 -c "import json; json.load(open('{skill}/config/{skill}-config.json'))"

# YAML syntax + required keys
python3 -c "
import yaml
with open('{skill}/runtime.yaml') as f:
    data = yaml.safe_load(f)
for k in ['name','strategies','scanners','actions','exit','notifications']:
    assert k in data, f'missing {k}'
print('OK')
"

# No stale DSL references
grep -rn 'DSL_TIERS\|CONVICTION_TIERS\|STAGNATION_TP\|build_dsl_state_template\|dslState\|dsl-profile\|dsl-v5\|dsl\.yaml\|dsl-implementation' {skill}/
```

---

## Key behavioral changes (old → new)

| Aspect | Old (Python cron) | New (Plugin) |
|---|---|---|
| DSL execution | dsl-v5.py every 3 min | Plugin monitor every 30s |
| Position detection | Agent writes state file | position_tracker auto-detects on-chain |
| Phase 1 timeouts | Conviction-scaled per score | Fixed values from runtime.yaml |
| Phase 2 | Cron breach counting | Exchange-SL driven |
| Install | 2 crons (scanner + DSL) | 1 runtime install + 1 scanner cron |

---

## Files changed per skill

| File | Action |
|---|---|
| `runtime.yaml` | Created |
| `scripts/{skill}-scanner.py` | DSL code removed |
| `config/{skill}-config.json` | `"dsl"` section removed |
| `SKILL.md` | Updated for plugin workflow |
| `dsl.yaml` | Deleted (replaced by runtime.yaml) |
| `dsl-implementation.md` | Deleted |
