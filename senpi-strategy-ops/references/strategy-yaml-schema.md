# `strategy.yaml` — the strategy-package deploy manifest

`strategy.yaml` is **ours** (the strategy-ops layer). It bundles **one or more `runtime.yaml`
instances** into a single deployable package and is the **single source of truth** for deploy +
attribution. It is intentionally **thin**: scanner tunables, exit/risk config, and cadence all live in
each self-contained `runtime.yaml` (the runtime's own concept) — the manifest must not duplicate them.

## Package layout

```
<id>/
  strategy.yaml                 # this manifest
  <instance>/
    runtime.yaml                # the runtime's self-contained spec for this instance
    scanners/                   # the supervised scanner module(s)
      scan.py                   # exports scan(inputs, ctx) -> list[dict]
      scoring.py                # (optional) pure helpers
  <instance2>/ …                # one subdir per instance (multi-runtime, e.g. spider swing + scalp)
```

A single-instance strategy has one `<instance>/` dir; a multi-instance one (spider) has several, **each
on its own wallet** (a runtime binds to exactly one wallet).

## Schema

```yaml
schema_version: 1
id: spider                  # REQUIRED. == package dir name; == every leg's runtime.yaml `group`
version: "6.0.0"            # REQUIRED. Single source for catalog + MCP attribution (skillName/skillVersion)

catalog:                    # discovery surface (read by senpi-strategy-discover via catalog.json)
  name: "Spider — AI/Tech Hedge Fund"
  emoji: "🕷️"
  tagline: "…"
  belief_plain: "…"
  group: multi-asset-whitelist   # discovery TAXONOMY bucket (distinct from runtime.yaml `group: <id>`)
  archetype: trend_following     # declared discovery facets (see senpi-strategy-discover glossary.yaml)
  sub_style: basket
  asset_classes: [xyz_equities, major_alts, btc_eth, commodities]
  asset_scope: basket
  direction: long_short
  risk_level: moderate
  tier: advanced
  time_horizon: swing
  leverage_max: 10               # explicit (gen_catalog reads these — no longer derived from params)
  max_slots: 7
  min_budget: 200
  assets: [ … ]                  # explicit asset list for named-asset matching

requires:
  runtime: ">=2.0.0"        # @senpi-ai/runtime semver range

defaults:                   # env VAR NAMES only — never values
  auth_token_env: SENPI_AUTH_TOKEN
  # decision_model_env: <ENV>   # ONLY if a runtime.yaml has a decision_mode: llm action

instances:                  # REQUIRED, non-empty. Each entry = one runtime.yaml + one wallet.
  - name: swing                       # REQUIRED. Instance id.
    runtime: swing/runtime.yaml       # REQUIRED. Path to this leg's runtime.yaml.
    wallet_env: SPIDER_SWING_WALLET   # REQUIRED. Bound as ${SPIDER_SWING_WALLET} in that runtime.yaml.
    funding_share: 0.60               # REQUIRED. Budget split; must sum to 1.0 across instances.
  - name: scalp
    runtime: scalp/runtime.yaml
    wallet_env: SPIDER_SCALP_WALLET
    funding_share: 0.40
```

An instance carries **only** `name`, `runtime`, `wallet_env`, `funding_share`. (Removed vs the legacy
schema: the per-instance `scanner:` block, `params:`, `tick_seconds`, and any telegram field — those are
in the runtime.yaml or gone.)

## Linkage convention (validator-enforced)

Forward and reverse mapping between the manifest and the running runtimes is **ledger-free**, guaranteed
by two rules `deploy.py` validates:

- every leg's `runtime.yaml` has **`group: <strategy id>`** (e.g. `group: spider`)
- every leg's `runtime.yaml` has **`name: <id>-<instance>`** (e.g. `spider-swing`)

So: **forward** = `instances[].runtime` path → the leg's spec; **reverse** (monitor/close, no state file)
= `openclaw senpi runtime list` rows where `group == <id>`, or MCP `strategy_list` rows where
`skillName == <id>`. The manifest's `wallet_env` must appear as `${WALLET_ENV}` in that runtime.yaml.

## Validation

`deploy.py` preflight-validates the package (and the model in `scripts/_pkg.py` is reusable): id == dir,
version present, instances non-empty, each `runtime.yaml` exists + binds `${wallet_env}` + has the
`group`/`name` linkage + an `external_scanner` whose entrypoint module exists, distinct `wallet_env` per
instance, `funding_share` sums to 1.0, and no bare `@senpi/runtime` anywhere.
