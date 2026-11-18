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
  archetype: trend_following     # the market BELIEF (1 of 6; see glossary). The LLM ranks belief on this.
  sub_style: alpha_hunter        # refinement within the archetype (glossary; extensible)
  asset_classes: [major_alts]    # which classes you trade (glossary). Tag INCLUSIVELY — a universe
                                 #   scanner that touches BTC/ETH lists btc_eth too. xyz category comes
                                 #   from YOU (the platform exposes no category): big-tech -> xyz_equities,
                                 #   oil/metals -> commodities, SP500 -> indices, SpaceX -> pre_ipo.
                                 #   THIS is the one field the engine hard-FILTERS on — get it right.
  asset_scope: single            # single | basket | universe | follows_traders
  risk_level: moderate           # conservative | moderate | aggressive (be honest)
  tier: starter                  # starter (beginner-friendly; shows a STARTER badge) | advanced
  direction: long_short          # long_only | short_only | long_short
  belief_plain: "Hunts SOL alpha with multi-factor conviction scoring."   # ONE jargon-free sentence;
                                 #   how it trades, in a person's words.

  # NEW — worldview / theme surface (FREE TEXT, no glossary to maintain):
  thesis: "Concentrated SOL conviction with leverage that scales to the signal."   # one sentence:
                                 #   WHEN / WHO it's for. This is how the LLM matches a user's WORLDVIEW
                                 #   ("there'll be a war", "run a hedge fund"). Write it as a purpose.
  tags: [sol, alpha-hunter, single-asset]   # free-text keywords (hedge-fund, tail-risk, macro,
                                 #   all-weather, war, …). No controlled vocab — the LLM reads them.
```

## Page copy (author-set; what a strategy page shows)

senpi-web's strategy pages read these straight from `catalog.json`. Each is optional today (the page
falls back to the name's subtitle, a split `belief_plain` and a generic risk line), but write all
three for every new strategy. `gen_catalog.py` warns, and `strategies/tests/test_catalog_page_fields.py`
fails, when a present one breaks its rule.

```yaml
catalog:
  descriptor: "Rides BTC while it trends"     # ≤45 characters, plain words, starts with a verb,
                                              #   no full stop. The line under the name.
  summary: "Holds BTC through a trend and steps out when it stalls."   # ≤140 characters, one
                                              #   sentence. The hero's one-liner; belief_plain moves
                                              #   under "How it works".
  risks: "A sideways market chops it in and out, paying fees each time."   # one or two sentences
                                              #   on where THIS strategy goes wrong.
  categories: [conservative]                  # optional: the category pages it appears on (slugs)
```

A fork keeps its template's `descriptor`, `summary` and `risks`; rewrite them when the fork changes
what the strategy does.

`shown_on` is not authored: `gen_catalog.py` sets it to the catalog id at the root of the `varies`
chain (the page a version sits on), or null when that root is not in the catalog.

## Derived for you (do NOT duplicate unless params can't express it)

`gen_catalog.py` computes these from `instances[]/params`:

| Field | Source |
|---|---|
| `assets` | `params.allowedAssets` (list) or `params.asset` (scalar) across instances |
| `leverage_max` | `params.*MaxLeverage` / `leverageTiers[].leverage` / `defaultLeverage` |
| `funding_split` | `instances[].funding_share` (single-instance → `[1.0]`) |
| `cadence_seconds`, `time_horizon` | `instances[].tick_seconds` (≤60s→scalp, ≤600s→swing, else position) |
| `instance_count`, `max_slots` | `len(instances)`, `params.maxSlots` |
| `min_budget` | **computed** by `min_budget.py` (baked here + carried as `wallet_count` + `min_budget_breakdown`) — the smallest budget where every wallet funds and its smallest slot clears the $12 bumped notional; NOT authored |

If your params don't carry the asset/leverage (e.g. it's hardcoded in the scanner), declare a fallback
in the `catalog:` block (`assets: [ETH]`, `leverage_max: 10`) and `gen_catalog` will use it.

## Writing `belief_plain` vs `thesis`

- **`belief_plain`** = *what it does*, plain. Good: *"Rides BTC while it's trending and steps out when it
  stalls."* Bad: *"4h SM-gated trend-continuation with conviction-tiered sizing."*
- **`thesis`** = *when / who it's for* — the worldview it expresses. This is what lets a user's view of
  the world find you. Good: *"You want the portfolio hedge of a fund line-up — green on the days
  everything else is red — without holding a view."* (Rhino) · *"Bet the conflict deepens: long oil +
  gold, short equities + BTC."* (a thesis fund). If your strategy is a directional macro/thematic bet
  or a fund-style book, write the `thesis` carefully — it's the only worldview hook.

## Why `thesis`/`tags` are free text (no glossary)

Worldviews are open-ended ("stagflation", "AI bubble pops", "China/Taiwan") — no fixed taxonomy covers
them. So the discovery engine does **not** match `thesis`/`tags` with a controlled vocabulary; the LLM
reads them as plain language and ranks semantically. Write them like a sharp human would describe the
bet. The only field the engine hard-filters on is `asset_classes` (+ `direction`/exclusions) — keep
those accurate; be expressive with `thesis`/`tags`.
