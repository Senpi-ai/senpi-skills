# `strategy.yaml` — the deploy declaration (schema reference)

`strategy.yaml` is the single source of truth for a strategy package. It is **data, not a skill**.
A strategy *definition* deploys into N **instances** (`main` for single-instance; e.g. `swing`/`scalp`
for a two-book strategy). The installer reads this file to deploy deterministically; the scanner reads
its `params` via `senpi_runtime_helpers.load_params()`.

## Top-level fields

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | manifest format version (currently `1`). |
| `id` | yes | strategy identity; **must equal the package directory name**. |
| `version` | yes | the ONE version; feeds the catalog entry and `strategy_id`/`strategy_version` attribution. |
| `catalog` | yes | discovery metadata: `name`, `emoji`, `tagline`, `group` (archetype slug), `risk_level`, `min_budget`. |
| `requires.runtime` | yes | `@senpi-ai/runtime` semver range (e.g. `">=1.1.0"`). NOT the runtime.yaml schema major. |
| `defaults` | yes | env VAR NAMES only (never values): `decision_model_env`, `telegram_chat_id_env`, `auth_token_env`. |
| `instances[]` | yes | one entry per deployable unit. |

## `instances[]` fields (per instance)

| Field | Notes |
|---|---|
| `name` | instance id (`main`, or e.g. `swing`/`scalp`). |
| `runtime` | path to this instance's `runtime.yaml`. |
| `scanner.entrypoint` | the scanner script (`scanner.py`). |
| `scanner.name` | **must match** an `external_scanner` name in `runtime`. |
| `scanner.signal_type` | the `signal_type` the scanner emits. |
| `wallet_env` | env var name the runtime render + scanner daemon both bind to; **must appear as `${…}` in `runtime`**. |
| `env` | instance-selecting env injected into the daemon (e.g. `{SPIDER_LEG: swing}`). Empty `{}` for single-instance. |
| `tick_seconds` | scanner cadence. |
| `funding_share` | share of the budget for this instance's wallet when `wallet="new"` (must sum to ~1.0 across instances). |
| `params` | **the single source of scanner tunables** (thresholds, asset sets, leverage tiers). Declarative data only — algorithm logic stays in `scanner.py`. |

## Single-instance example (`polar`)

```yaml
schema_version: 1
id: polar
version: "5.0.0"
catalog:
  name: "Polar — ETH Alpha Hunter"
  emoji: "🐻‍❄️"
  tagline: "Single-asset alpha hunter for ETH …"
  group: single-asset-alpha-hunter
  risk_level: moderate
  min_budget: 100
requires: { runtime: ">=1.1.0" }
defaults:
  decision_model_env: POLAR_DECISION_MODEL
  telegram_chat_id_env: TELEGRAM_CHAT_ID
  auth_token_env: SENPI_AUTH_TOKEN
instances:
  - name: main
    runtime: runtime.yaml
    scanner: { entrypoint: scanner.py, name: polar_signals, signal_type: POLAR_ETH_HYBRID }
    wallet_env: WALLET_ADDRESS
    env: {}
    tick_seconds: 300
    funding_share: 1.0
    params: { minScore: 14, quietHours: { startUtc: 0, endUtc: 4, apexBypassScore: 17 } }
```

## Multi-instance example (`spider`)

Two instances, two wallets, one `scanner.py` multiplexed by `SPIDER_LEG`. See `spider/strategy.yaml`
in the repo for the full two-book (`swing`/`scalp`) declaration with per-instance `params` and
`funding_share` 0.60 / 0.40.

## How the scanner reads params

```python
import senpi_runtime_helpers as h
params = h.load_params(__file__)      # resolves this package's strategy.yaml,
                                      # selects this instance (by env or sole), returns params
min_score = params.get("minScore", 5)
```

`load_params()` selects the instance whose declared `env` matches the process environment (the
installer sets it), or the sole instance for single-instance strategies. The wallet address is read
from `wallet_env`, never hardcoded.

## Validate

```
python3 senpi-strategy-author/scripts/validate_strategy.py <package-dir>
```
