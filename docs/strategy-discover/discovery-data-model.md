# Strategy Discovery — Data Model Specification

> **Scope of this document:** the *data model* — the vocabulary discovery uses to describe strategies,
> capture user intent, and read live context. **Companions:** `discovery-architecture.md` (components,
> contracts, sourcing declared-vs-derived, matcher/scoring, the 35 resolved decisions),
> `discovery-conversation.md` (the SKILL.md conversation design), and `glossary.yaml` (the canonical,
> machine-readable enum vocabulary — the authoritative source for archetype/sub_style/asset_class values).
> Where this doc and `glossary.yaml` differ on enum values, the glossary wins.

---

## 1. Why this exists

`senpi-strategy-discover` is the agent's **general decision-tree picker** — it helps a user choose an
installable strategy. It can be entered **in any form**: freeform prose ("something safe for BTC,
~$300"), "what's winning right now", a guided multi-step questionnaire (the UI mock's
style → assets → budget chips is just *one* entry path), or a single targeted ask.

Today the picking logic is **hardcoded prose**: the decision tree in
`senpi-trading-runtime/references/producer-patterns.md` enumerates ~50 named strategies across 6
archetypes and hand-routes users to them. That doesn't scale — strategies are added/removed
continuously, and the skill must keep working without edits when the roster changes.

This data model replaces the hardcoding: a strategy is described by **attributes**, the user expresses
**intent**, live **signals** add context, and matching is a function over those — so adding a strategy
is a data change, not a code change. The model must be **complete** (every decision-tree leaf is
expressible) and **extensible** (new strategies/branches slot in as data).

---

## 2. The model has three entities

1. **Strategy Descriptor** — the attributes a strategy exposes to be discovered & matched.
2. **User Intent** — the datapoints a user can express, in any form, that select/filter strategies.
3. **Live Signals** — runtime context that infers missing intent and justifies/ranks the matches.

> **Matching** = map **User Intent** (+ **Live Signals**) onto **Strategy Descriptor** attributes.
> **Hard boundary:** Live Signals never decide *what is installable* — the Strategy Descriptor set
> always does.

---

## 3. Entity 1 — Strategy Descriptor

Six facets. Enum values are derived from the decision tree's branches so every existing leaf is
expressible.

### Facet A — Identity
| Attribute | Type | Notes |
|---|---|---|
| `id` | slug | package id (e.g. `kodiak`) |
| `name` | string | display name |
| `emoji` | string | brand glyph |
| `tagline` | string | one-line pitch |
| `belief_plain` | string | one jargon-free sentence ("rides BTC while it trends, steps out when it stalls") |
| `version` | semver | |
| `url` | string | predators/marketing link |

### Facet B — Thesis (the market belief)
| Attribute | Type | Allowed values | Maps to |
|---|---|---|---|
| `archetype` | enum | `trend_following`, `contrarian_fade`, `copy_trading`, `single_market`, `breakout_momentum`, `structural_neutral` | decision-tree Layer 1 |
| `sub_style` | enum (archetype-scoped) | trend: `rotation`,`parabolic`,`hodl`,`oi_confirmed`,`adaptive`,`basket` · contrarian: `sm_crowding`,`funding_extreme`,`xyz_overextended` · copy: `arena_winners`,`hot_streak`,`named_whales`,`copy_the_copiers` · single_market: `pre_ipo`,`ipo_moment`,`big_tech`,`broad_index`,`commodity`,`weekend_gap`,`crypto_major` · breakout: `range_break`,`pullback`,`rank_jump`,`momentum_event`,`liquidation_cascade`,`orderbook_pressure` · structural: `cross_asset_lag`,`cross_venue_lag`,`market_making`,`pairs_rv`,`dca` | Layers 2A–2F leaves |

### Facet C — Market / universe
| Attribute | Type | Allowed values | Maps to |
|---|---|---|---|
| `asset_classes` | set | `btc_eth`, `major_alts`, `universe_crypto`, `xyz_equities`, `commodities`, `indices`, `pre_ipo`, `none` | mock Q2; Layer 2D markets |
| `asset_scope` | enum | `single`, `basket`, `universe`, `follows_traders` | "one coin vs basket vs scan-everything vs copy" |
| `assets` | list[symbol] | e.g. `[SOL]`, `[xyz:NVDA, xyz:AMD]` | concrete instruments |
| `direction` | enum | `long_only`, `short_only`, `long_short` | Sheep/Koala = long_only |

### Facet D — Risk / behavior
| Attribute | Type | Allowed values | Maps to |
|---|---|---|---|
| `risk_level` | enum | `conservative`, `moderate`, `aggressive` | mock Q1; Layer 0 risk comfort |
| `tier` | enum | `starter`, `advanced` | 🟢 onboarding vs fleet "level up"; drives a STARTER badge |
| `leverage_max` | number | | sizing |
| `margin_pct_default` | number | | Layer 3 sizing |
| `dsl_preset` | enum | `balanced`, `let_winners_run`, `mean_reversion`, `scalp`, `parabolic_runner` | Layer 3 exit profile |
| `time_horizon` | enum | `scalp`, `swing`, `position`, `hodl` | "small quick wins vs big held wins" |
| `cadence_seconds` | number | | tick frequency |

### Facet E — Capital
| Attribute | Type | Notes |
|---|---|---|
| `min_budget` | number | **soft floor, never a gate** |
| `instance_count` | int | number of wallets |
| `funding_split` | list[float] | multi-instance budget shares (e.g. `[0.6, 0.4]`) |
| `max_slots` | int | concurrent positions |

### Facet F — Signal inputs (what the strategy reacts to)
| Attribute | Type | Allowed values |
|---|---|---|
| `signals` | set | `smart_money`, `funding`, `open_interest`, `momentum`, `trend_structure`, `rsi`, `leaderboard_rank`, `orderbook`, `volume`, `btc_correlation`, `dca_schedule` |

Powers decision-tree branches like "only when OI confirms" (Badger), "extreme funding" (Pangolin),
"order-book pressure" (Marlin), and explainability ("this reacts to smart-money crowding").

---

## 3.1 Enum clarity — `archetype` & `sub_style` need a glossary

Bare slugs are **not** reliably clear to an LLM. Assessment:
- `archetype` (6 values): mostly self-explanatory; only `structural_neutral` is vague.
- `sub_style`: ~1/3 are jargon/ambiguous as slugs and an LLM would guess — `oi_confirmed`,
  `adaptive`, `sm_crowding`, `xyz_overextended`, `copy_the_copiers`, `rank_jump`, `momentum_event`,
  `cross_asset_lag`, `cross_venue_lag`, `pairs_rv`, `dca`.

**Decision: add a glossary (taxonomy dictionary).** Every `archetype` and `sub_style` value gets a
one-line **definition + example user-phrasing + example strategy**. The glossary lives in the discovery
reference and is **inlined into the catalog payload the model reads**, so a weak model never has to know
what a slug like `pairs_rv` means — the gloss is right there. `belief_plain` (Facet A, natural-language
per strategy) stays the model's primary match surface; the enums are deterministic filters / labels.

Glossary format (example rows):

| value | definition (gloss) | user signals (example phrasing) | example |
|---|---|---|---|
| `contrarian_fade` / `sm_crowding` | fade the crowded smart-money side once price stops following | "everyone's long but it's not moving", "bet against the crowd" | Egret |
| `structural_neutral` / `pairs_rv` | trade the ratio between two assets back to its mean (relative value) | "trade the spread between two coins" | Chameleon |
| `trend_following` / `oi_confirmed` | enter a breakout only when open interest is rising (new money confirms) | "only when order flow backs it" | Badger |

## 3.2 Designing for weaker models (Qwen-class) — the quality moves

Target runtime is open models like Qwen, so the matcher must not depend on the model inferring jargon
or doing open-ended semantic matching over a big option set. Beyond the glossary, four principles:

1. **Deterministic pre-filter first.** The clean facets — `risk_level`, `asset_classes`, `budget`,
   `direction`, `asset_scope` — are filtered in **code**, not by the LLM. The model never reasons about
   them. This shrinks the catalog to a few candidates before any LLM judgment.
2. **LLM only does the soft final pick** over those few survivors, comparing user intent to each
   candidate's `belief_plain` + glosses. Small input + small choice → reliable on Qwen-class.
3. **Hierarchical narrowing when the LLM must classify** — pick `archetype` (6-way) first, then
   `sub_style` within that archetype's 4–7 options. Never a 1-of-28 classification in one shot.
4. **Inline the labels + glosses** in the catalog payload the model sees — never rely on the model
   recalling a separate dictionary or knowing jargon. Emit `archetype_label` + gloss beside each slug.

Net: deterministic code does the hard filtering; the weak model is left a tiny, well-labeled,
natural-language judgment. The glossary exists to make the *code filter* and *authoring* unambiguous —
not to make the model smarter.

---

## 4. Entity 2 — User Intent

Datapoints a user can express, in any form. The picker collects whatever is present, infers what it
can, and asks only the genuine gaps. Each maps onto Strategy Descriptor attributes.

| Intent datapoint | Example phrasing | Selects on |
|---|---|---|
| `risk_comfort` | "something safe" / "go aggressive" | `risk_level` |
| `asset_interest` | "BTC & ETH" / "SOL" / "stocks, oil" / "SpaceX" | `asset_classes`, `assets` |
| `market_scope` | "a specific coin" vs "scan everything" | `asset_scope` |
| `behavioral_belief` | "ride trends" / "fade the crowd" / "copy winners" | `archetype` |
| `direction_pref` | "I don't want to short" | `direction` |
| `horizon_pref` | "a few big wins held for days" / "lots of small quick ones" | `time_horizon`, `dsl_preset` |
| `budget` | "about $300" / "$500–$2,000" | `min_budget` feasibility + sizing |
| `autonomy` | "form its own opinion" vs "just copy whoever's winning" | `archetype` (copy_trading or not) |
| `goal` | "just accumulate over time" | `sub_style: dca` |
| `experience` | new vs experienced | `tier` |

**Inference rule (model-level):** any intent datapoint stated up front is inferred and not re-asked;
the picker confirms inferred values in one line and asks only the gaps. Zero stated datapoints → the
picker may walk a guided flow (the mock) or use plain-language prompts (decision-tree Layer 0).

---

## 5. Entity 3 — Live Signals

Runtime context, reasoned per-signal (judged against live tool schemas; `market_get_asset_data`
executed live and works unauthenticated; auth-gated tools judged from schemas). Each signal sorts into
one of three roles, and is kept only where it earns a role.

> **Hard boundary:** Live Signals never decide what is installable. They (1) infer the user's side,
> (2) justify "why now" / rank an already-matched set, or (3) pick a copy target.

### Role 1 — Infer user intent (ask fewer questions)
| Signal | Returns | Verdict |
|---|---|---|
| `account_get_portfolio` | budget, holdings, open positions | ✅ keep — strongest; fills `budget` + `asset_interest` without asking |
| `discovery_get_trader_history` | user's closed trades | ◑ keep, conditional — only if the user has HL history → infers favored assets/direction |

### Role 2 — Justify "why now" + rank the already-matched set
| Signal | Returns | Verdict |
|---|---|---|
| `market_get_asset_data` | per-asset regime (TRENDING/RANGEBOUND/VIOLENT via OI velocity), funding, book | ✅ keep — works unauthed; strongest "why now" |
| `market_get_funding_regime` | market-wide LONG/SHORT_CROWDED | ✅ keep — maps to `contrarian_fade`/funding theses |
| `leaderboard_get_markets` | smart-money concentration per asset (4h) | ✅ keep — confirms trend / flags crowding |
| `leaderboard_get_momentum_events` + `leaderboard_get_top` | fresh momentum / hot traders (4h) | ◑ keep — trader-level; seeds candidate assets, justifies breakout theses |
| `market_get_cross_asset_flows` | laggards vs a leader (BTC) | ◑ keep, niche — justifies structural/lag theses (Mantis/Osprey) |

### Role 3 — Pick a copy target (copy-trading archetype only)
| Signal | Returns | Verdict |
|---|---|---|
| `discovery_get_top_traders` | ranked individual traders (ROI, win rate, risk labels) | ✅ keep — used to **parameterize copy-trading** (which trader to mirror), NOT to rank packages |

**Named gap — live performance ranking of installable *packages*:** there is no signal that cleanly
ranks the installable catalog by recent performance, and so the "what's winning right now" entry path
has no first-class data source today. Resolving this is a **backend decision, deferred** — e.g. build a
per-package deployed-instance performance aggregate — and it does not block the data model.

---

## 6. Matching model (conceptual — not implementation)

- **Tolerant attribute matching:** exact preferred; adjacent acceptable with an honest caveat (no
  `conservative` strategy exists → offer the closest `moderate` framed as "the most conservative
  available", never a dead-end). Asset classes match hierarchically (a "majors+alts" user still sees a
  BTC/ETH-only strategy; "open to XYZ" is the widest bucket).
- **Budget is a soft floor**, never an exclusion; multi-instance strategies surface their `funding_split`.
- **Live signals** infer missing intent (Role 1) and justify/rank the matched set (Role 2); copy-trading
  picks its target via Role 3. None add or remove what's installable.
- **Always land on ≥1 strategy**, plus a "build a custom strategy" fallback (→ `senpi-strategy-author`).

---

## 7. Completeness check (how we validate the model)

Walk **every leaf of the decision tree** and confirm each strategy + branch is a point in this
attribute space. Representative leaves → attribute tuples:

| Strategy (leaf) | archetype / sub_style | asset_classes / scope | direction | risk / tier |
|---|---|---|---|---|
| Koala | trend_following / hodl | btc_eth / single | long_only | conservative / starter |
| Beaver | trend_following / basket→single | btc_eth / single | long_short | moderate / starter |
| Hedgehog | trend_following / basket | btc_eth+major_alts / basket | long_short | moderate / starter |
| Egret | contrarian_fade / sm_crowding | universe_crypto / universe | long_short | aggressive / advanced |
| Pangolin | contrarian_fade / funding_extreme | universe_crypto / universe | long_short | aggressive / advanced |
| Albatross | copy_trading / arena_winners | none / follows_traders | long_short | moderate / starter |
| Lemur | single_market / pre_ipo | pre_ipo / single→basket | long_short | aggressive / starter |
| Bobcat | single_market / big_tech | xyz_equities / basket | long_short | moderate / starter |
| Tortoise | structural_neutral / dca | btc_eth+major_alts / basket | long_only | conservative / starter |
| Chameleon | structural_neutral / pairs_rv | universe_crypto / single (pair) | long_short | aggressive / advanced |

If any leaf needs an attribute/enum not in the model, the model is extended before sign-off.

**Mock cross-check:** the UI mock's 3 questions resolve to a strict subset — `risk_level`,
`asset_classes`, `min_budget` — confirming the mock is one entry path, not the whole model.

---

## 8. Deferred (explicitly NOT decided here)

1. **Sourcing** — for each attribute: author-**declared** (`strategy.yaml` `catalog:`),
   auto-**derived** by `gen_catalog.py` from `instances[]/params`, or fetched **live** from MCP.
2. **The Role-3 gap** — live, per-package performance ranking (build a signal, require enrollment, or
   live without it).
3. **Schema + generator implementation** — extend the `catalog:` block, update `gen_catalog.py`,
   regenerate `catalog.json`, backfill `spider`/`kodiak`/`polar`.
4. **Discovery skill rewrite** — encode the general picker (all entry forms incl. the mock) + matching.
5. **Handoff wiring** — deploy → `senpi-strategy-ops` (`id`); build-new → `senpi-strategy-author`.

---

## 9. Sources of truth referenced

- `senpi-trading-runtime/references/producer-patterns.md` — the decision tree (completeness oracle).
- `catalog.json` + `*/strategy.yaml` (`catalog:` block) — current attributes.
- `senpi-strategy-discover/SKILL.md` + `references/strategy-discovery.md` — current picker.
- Senpi MCP tool schemas — Live Signal definitions.
