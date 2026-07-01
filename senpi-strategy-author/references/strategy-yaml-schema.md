# `strategy.yaml` — the deploy manifest (schema reference)

`strategy.yaml` is the single source of truth for a strategy **package**. It is **data, not a skill**.
A strategy *definition* deploys into N **instances** (`main` for single-instance; e.g. `swing`/`scalp`
for a two-book strategy). `senpi-strategy-ops` `deploy.py` reads this file to deploy deterministically
(create wallets → render each runtime → verify). **Scanner tunables do NOT live here** — they live in
each instance's `runtime.yaml` `inputs:` block, read inside `scan(inputs, ctx)` via `inputs.get(...)`.
The manifest carries no scanner-tunable block and no param-loading helper; those were the retired v2
producer model.

## Top-level fields

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | manifest format version (currently `1`). This is the **manifest** version, not the runtime version. |
| `id` | yes | strategy identity; **must equal the package directory name**. |
| `version` | yes | the ONE version; feeds the catalog entry and `skillName`/`skillVersion` deploy attribution. |
| `catalog` | yes | discovery metadata: `name`, `emoji`, `tagline`, `group` (archetype slug), `risk_level`, `min_budget`, plus the facet fields the discover catalog reads. |
| `requires.runtime` | yes | `@senpi-ai/runtime` semver range — **`">=3.0.0"`**. NOT the `runtime.yaml` schema major. |
| `defaults` | no | env VAR NAMES only (never values), e.g. `auth_token_env: SENPI_AUTH_TOKEN`. |
| `instances[]` | yes | one entry per deployable unit (one wallet each). |

## `instances[]` fields (per instance)

| Field | Notes |
|---|---|
| `name` | instance id (`main`, or e.g. `swing`/`scalp`). |
| `runtime` | path to this instance's `runtime.yaml` (e.g. `main/runtime.yaml`). |
| `wallet_env` | env var NAME the runtime render binds the fresh wallet to; **must appear as `${…}` in that `runtime.yaml`'s `strategy.wallet`**. |
| `funding_share` | share of the budget for this instance's wallet (must sum to ~1.0 across instances). |

> There is no `scanner.entrypoint` / `scanner.name` / `scanner.signal_type` / `env` / `tick_seconds` /
> `params` in the manifest. The scanner path (`scanners/scan.py`), its `inputs:` tunables, its
> `interval_seconds` cadence, and its `signal_data_schema` all live in the instance's `runtime.yaml`
> under the `external_scanner`. The manifest only points at the runtime file and names the wallet env.

## Single-instance example (`kodiak`)

Copied from `strategies/kodiak/strategy.yaml` (the gold single-instance template):

```yaml
schema_version: 1

id: kodiak
version: "1.0.0"

catalog:
  name: "Kodiak — SOL Alpha Hunter"
  emoji: "🐻"
  tagline: "A single-asset SOL specialist: enters only when 4h and 1h trend structure agree, 15m momentum confirms, and smart-money lean, funding, and BTC all line up — then sizes leverage to conviction (5/6/7x) and lets the DSL ride the winner."
  group: single-asset
  archetype: single_market
  sub_style: alpha_hunter
  asset_classes: [major_alts]
  asset_scope: single
  direction: long_short
  risk_level: aggressive
  tier: advanced
  leverage_max: 7
  max_slots: 1
  min_budget: 200
  assets: ["SOL"]
  tags: [sol, single-asset, momentum, smart-money, conviction-leverage, alpha-hunter]

requires:
  runtime: ">=3.0.0"

defaults:
  auth_token_env: SENPI_AUTH_TOKEN

instances:
  - name: main
    runtime: main/runtime.yaml
    wallet_env: KODIAK_WALLET
    funding_share: 1.0
```

The tunables that a v2 manifest would have listed under `params` (`minScore`, `marginPct`,
`leverageTiers`, thresholds …) live in `main/runtime.yaml` under the `external_scanner`'s `inputs:`
map — the scan reads them with `inputs.get("minScore", 10)`. See
[`../../senpi-trading-runtime/references/runtime-yaml.md`](../../senpi-trading-runtime/references/runtime-yaml.md).

## Multi-instance example (`spider`)

Two instances, two wallets, two `runtime.yaml` files under one manifest. Each instance points `runtime`
at its own file (`swing/runtime.yaml`, `scalp/runtime.yaml`), binds its own `wallet_env`, and carries a
`funding_share` (e.g. `0.60` / `0.40`) — the per-leg universe and sizing live in each runtime's
`inputs:`, not in the manifest:

```yaml
instances:
  - name: swing
    runtime: swing/runtime.yaml
    wallet_env: SPIDER_SWING_WALLET
    funding_share: 0.60
  - name: scalp
    runtime: scalp/runtime.yaml
    wallet_env: SPIDER_SCALP_WALLET
    funding_share: 0.40
```

See `strategies/spider/strategy.yaml` in the repo for the full two-book declaration.

## How the scan reads tunables

Not from `strategy.yaml`. The runtime passes the instance's `runtime.yaml` `inputs:` map as the first
arg to `scan(inputs, ctx)`:

```python
def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", 10))
    margin_pct = float(inputs.get("marginPct", 20))
    ...
```

The wallet is resolved by the runtime from `strategy.wallet: "${KODIAK_WALLET}"` and exposed to the scan
as `ctx.wallet` — never hardcoded, never read from the manifest.

## Validate

```
python3 senpi-strategy-author/scripts/validate_strategy.py <package-dir>
```
