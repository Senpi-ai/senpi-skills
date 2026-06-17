# Discovery catalog fields — what to declare in `strategy.yaml`

When you author a strategy, the **discovery picker** (`senpi-strategy-discover`) matches it to users by
the fields you declare in the `catalog:` block. `gen_catalog.py` reads these, derives the mechanical
ones from your `instances/params`, and inlines glosses from
`senpi-strategy-discover/references/glossary.yaml`. **Validation only warns** — a missing field won't
fail the build, but your strategy won't match well until it's set.

> The canonical value vocabulary (allowed `archetype`, `sub_style`, `asset_classes`, …) lives in
> **`senpi-strategy-discover/references/glossary.yaml`**. Pick values from there. `sub_style` is
> extensible — if no value fits a genuinely new pattern, add it to the glossary with a one-line gloss.

## Declare these (author-set — discovery can't infer them)

```yaml
catalog:
  name: "..."           # already required
  emoji: "🐻"
  tagline: "..."
  group: single-asset-alpha-hunter   # structural grouping (existing)

  # NEW — discovery matching fields:
  archetype: trend_following     # the market BELIEF (1 of 6; see glossary). What --belief matches.
  sub_style: alpha_hunter        # refinement within the archetype (glossary; extensible)
  asset_classes: [major_alts]    # which classes you trade (glossary). Tag INCLUSIVELY — a universe
                                 #   scanner that touches BTC/ETH lists btc_eth too. xyz category comes
                                 #   from YOU (the platform exposes no category): big-tech -> xyz_equities,
                                 #   oil/metals -> commodities, SP500 -> indices, SpaceX -> pre_ipo.
  asset_scope: single            # single | basket | universe | follows_traders
  risk_level: moderate           # conservative | moderate | aggressive (be honest)
  tier: starter                  # starter (beginner-friendly; shows a STARTER badge) | advanced
  direction: long_short          # long_only | short_only | long_short
  belief_plain: "Hunts SOL alpha with multi-factor conviction scoring."   # ONE jargon-free sentence;
                                 #   this is the LLM's primary match + narration surface — write it well.
```

## Derived for you (do NOT duplicate unless params can't express it)

`gen_catalog.py` computes these from `instances[]/params`:

| Field | Source |
|---|---|
| `assets` | `params.allowedAssets` (list) or `params.asset` (scalar) across instances |
| `leverage_max` | `params.*MaxLeverage` / `leverageTiers[].leverage` / `defaultLeverage` |
| `funding_split` | `instances[].funding_share` (single-instance → `[1.0]`) |
| `cadence_seconds`, `time_horizon` | `instances[].tick_seconds` (≤60s→scalp, ≤600s→swing, else position) |
| `instance_count`, `max_slots` | `len(instances)`, `params.maxSlots` |
| `min_budget` | `max(declared, 100 × instance_count)` — the per-wallet floor |

If your params don't carry the asset/leverage (e.g. it's hardcoded in the scanner), declare a fallback
in the `catalog:` block (`assets: [ETH]`, `leverage_max: 10`) and `gen_catalog` will use it.

## Writing `belief_plain`

One plain sentence, no jargon, describing *what it does* as a person would say it. It's what the
analyst reads to the user. Good: *"Rides BTC while it's trending and steps out when it stalls."*
Bad: *"4h SM-gated trend-continuation with conviction-tiered sizing."*
