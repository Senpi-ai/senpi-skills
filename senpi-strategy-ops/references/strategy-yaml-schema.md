# `strategy.yaml` — the strategy-package deploy manifest

`strategy.yaml` is **ours** (the strategy-ops layer). It bundles **one or more `runtime.yaml`
instances** into a single deployable package and is the **single source of truth** for deploy +
attribution. It is intentionally **thin**: scanner tunables, exit/risk config, and cadence all live in
each self-contained `runtime.yaml` (the runtime's own concept) — the manifest must not duplicate them.

## Package layout

**The rule: a package always RESOLVES to a non-empty `instances[]` — that is not the same as the
manifest having to declare one.** The two layouts differ only in where that list comes from.

**FLAT** — single-instance, and what `senpi-strategy-author` scaffolds. **No `instances:` list at
all**; every loader synthesizes the canonical `main` instance from the root recipe:

```
strategies/<id>/
  strategy.yaml                 # this manifest — no `instances:`
  runtime.yaml                  # the runtime's self-contained spec; `main` is synthesized from it
  scanners/
    scan.py                     # exports scan(inputs, ctx) -> list[dict]
    scoring.py                  # (optional) pure helpers
```

**NESTED** — required for multi-instance, and what every package in this repo's `strategies/`
catalog uses. One `<instance>/` dir per runtime, **each on its own wallet** (a runtime binds to
exactly one wallet), declared in `instances:`:

```
strategies/<id>/
  strategy.yaml                 # this manifest — with `instances:`
  <instance>/
    runtime.yaml                # this instance's spec
    scanners/{scan,scoring}.py
  <instance2>/ …                # e.g. spider swing + scalp
```

Same rule on both sides: `_pkg.load` (synthesizing via `_flat_instance`) raises only when there is
neither an `instances:` list nor a root `runtime.yaml`; the author lint (`validate_strategy.py`) and
the runtime's own `loadDeployPackage` (via `synthesizeFlatInstance`) do the same.
`deploy.py validate <id>` confirms either form is deploy-ready.

### The flat wallet binding is stricter in the runtime than in the python loaders

**A flat package's `strategy.wallet` must be the WHOLE value and UPPERCASE — `"${WALLET_ENV}"`,
`[A-Z0-9_]` only.** The python loaders accept far more than that, and they do not warn:

| `strategy.wallet` | `deploy.py validate` | the runtime |
|---|---|---|
| `"${MY_WALLET}"` | green, binds `MY_WALLET` | loads |
| `"${my_wallet}"` (lowercase) | **green**, binds `my_wallet` | **refuses the package** |
| `"pre${FOO}"` (embedded) | **green**, binds `FOO` | **refuses the package** |
| `"0xabc…"` (no `${…}`) | reports `wallet_env … not bound` | refuses the package |

`_flat_instance` finds the token with a `.search()` over `\$\{([A-Za-z_][A-Za-z0-9_]*)\}`, so it
matches a lowercase name and one embedded mid-string, and only falls back to `<ID>_WALLET` — the case
`validate` then reports — when there is **no** `${…}` anywhere. `synthesizeFlatInstance` anchors and
uppercases (`/^\$\{([A-Z0-9_]+)\}$/`) and throws otherwise. So rows 2 and 3 pass every python check,
including `deploy.py validate`, and are refused at load by `senpi validate` and `senpi deploy`.
(Verified by running both loaders over all four spellings, not by reading them.)

## Schema

```yaml
schema_version: 1
id: spider                  # REQUIRED. == package dir name; == every instance's runtime.yaml `group`
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
  # min_budget is COMPUTED (min_budget.py) — never author it; use min_budget_floor only to RAISE it
  assets: [ … ]                  # explicit asset list for named-asset matching

requires:
  runtime: ">=2.0.0"        # @senpi-ai/runtime semver range

defaults:                   # env VAR NAMES only — never values
  auth_token_env: SENPI_AUTH_TOKEN
  # decision_model_env: <ENV>   # ONLY if a runtime.yaml has a decision_mode: llm action

instances:                  # Each entry = one runtime.yaml + one wallet. REQUIRED and non-empty
                            #   UNLESS the package is FLAT (a root runtime.yaml), which synthesizes
                            #   `main` — see "Package layout". Multi-instance MUST declare it.
  - name: swing                       # REQUIRED. Instance id.
    runtime: swing/runtime.yaml       # REQUIRED. Path to this instance's runtime.yaml.
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

- every instance's `runtime.yaml` has **`group: <strategy id>`** (e.g. `group: spider`)
- every instance's `runtime.yaml` has **`name: <id>-<instance>`** (e.g. `spider-swing`)

So: **forward** = `instances[].runtime` path → the instance's spec; **reverse** (monitor/close, no state file)
= `openclaw senpi runtime list` rows where `group == <id>`, or MCP `strategy_list` rows where
`skillName == <id>`. The manifest's `wallet_env` must appear as `${WALLET_ENV}` in that runtime.yaml.

## Validation

`deploy.py` preflight-validates the package (and the model in `scripts/_pkg.py` is reusable): id == dir,
id lowercase, version present, the package resolves to at least one instance (declared, or synthesized
from a root `runtime.yaml`), each `runtime.yaml` exists + binds `${wallet_env}` + has the `group`/`name`
linkage + an `external_scanner` whose entrypoint module exists, distinct `wallet_env` per instance,
`funding_share` sums to 1.0, and no bare `@senpi/runtime` anywhere.
