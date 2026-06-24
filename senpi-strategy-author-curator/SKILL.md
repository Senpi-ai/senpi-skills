---
name: senpi-strategy-author-curator
description: >-
  PRIVILEGED (curator / fleet-operator only) — build a Senpi strategy AND PUBLISH it
  to the discovery catalog so every user can find and install it. Use when the
  operator wants a strategy to become a discoverable fleet PRODUCT, not just a
  private package: "build and publish a strategy", "add this to the catalog", "make
  a new fleet strategy", "publish <id> to discover", "list <id> for users". Inherits
  the full senpi-strategy-author build flow and ADDS a strict discovery-facet gate +
  the catalog publish step. This skill COMMITS to the repo — always confirm before
  publishing. NOT for general users (use senpi-strategy-author) and NOT for
  installing (use senpi-strategy-ops).
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
    - senpi-strategy-author
---

# Senpi Strategy Author — Curator (build + publish to discovery)

This is the **privileged** author skill. It builds a strategy exactly like `senpi-strategy-author`,
then **publishes it to the discovery catalog** so every user can find and install it. A normal user's
strategy is a private, local package deployed to their own wallet; a **published** strategy is a fleet
**product** — committed to the repo and indexed in `strategies/catalog.json` that `senpi-strategy-discover`
reads. That publish power is why this skill lives on a **curator-only branch and never merges to main.**
(The hard security gate is repo push access; this skill is the workflow + the strict facet check that
push access alone doesn't give you.)

## Step 1 — Build (unchanged — drive it from the author skill)

Build the package exactly as **`senpi-strategy-author`** does: the 7-decision interview (one question
at a time), assemble `scoring.py` / `scan.py` / `runtime.yaml` / `strategy.yaml`, unit-test the math,
`validate_strategy.py`, then **smoke-test** (dry-run → `scan()` once on live MCP → tiny deploy →
confirm the runtime *accepted* a signal). Full method + worked example:
[`../senpi-strategy-author/references/creating-a-strategy.md`](../senpi-strategy-author/references/creating-a-strategy.md).
**Do not duplicate the build logic here.** Proceed to publish only once the smoke test is green.

## Step 2 — The discovery surface IS the product. Gate it strictly.

For a *private* strategy the `catalog:` facets are cosmetic; for a *published* one they are the entire
way users find it — and `gen_catalog.py` only **warns**, so a weak surface ships silently un-matchable.
Before publishing, run the **hard** facet gate (it fails, it doesn't warn):

```
python3 senpi-strategy-author-curator/scripts/validate_catalog_facets.py strategies/<id>
```

It blocks on: any of the 8 declared facets missing/empty; an `archetype` outside the **closed set of 6**;
an `asset_classes` that is empty or carries a value not in the glossary (this is the field the engine
**hard-filters** on — get it right); an invalid `asset_scope` / `risk_level` / `tier` / `direction`; or a
missing/too-thin `belief_plain`. It **warns** (doesn't block) when `sub_style` is a new value (extensible —
add it to `glossary.yaml` with a gloss) or when there's no real **`thesis`** (the only worldview hook —
how "run me a hedge fund" / "bet on a war" finds the strategy; add one unless the strategy is purely
mechanical). **Fix every error.** A published strategy with a bad surface is worse than no strategy.

## Step 3 — Confirm, then publish

Publishing is **outward-facing** — the strategy becomes a user-visible product. So:

1. **Replay the catalog record the user will see** — `name`, `belief_plain`, `thesis`, `archetype`,
   `asset_classes`, `risk_level`, `tier`, `direction` — and get an explicit **"yes."**
2. **Commit the package** `strategies/<id>/` to the **published branch** (default `strategy-v2`; switch
   to `main` once v2 ships — confirm the target each time).
3. **Regenerate the catalog (never hand-edit `catalog.json`):**
   ```
   python3 senpi-trading-runtime/scripts/gen_catalog.py --updated <YYYY-MM-DD> --branch <published>
   ```
   This re-reads **every** `strategies/*/strategy.yaml` and rewrites `strategies/catalog.json` — so the
   package must be committed first.
4. **Commit `strategies/catalog.json`.** Discover reads it off the published branch → the strategy is
   now discoverable and installable by any user.

## Un-publish (deprecate)

The catalog is exactly the set of packages on the branch. To pull a strategy from discovery: remove
`strategies/<id>/` from the published branch, re-run `gen_catalog.py`, commit `catalog.json`. Confirm
first — it's outward-facing, and it stops users from finding a strategy they may already run (their
deployed instances keep running; only discovery changes).

## Guardrails

- **Publishing commits to the repo and makes a product.** Get an explicit confirmation; never auto-publish.
- **Only the published branch you have push access to.** Push access is the real gate; this skill is the
  curator workflow on top of it.
- **`catalog.json` is GENERATED — never hand-edit it.** Always regenerate via `gen_catalog.py`.
- **`gen_catalog` rebuilds the WHOLE catalog** from the packages present on the branch — commit the new
  package *before* regenerating, and review the diff (you should see exactly one record added).
- **Everything in `senpi-strategy-author` still holds** — read-only `scan()`, `marginPct` intent, pure
  `scoring.py`, named DSL presets, facets from the glossary. This skill only adds the publish layer.
