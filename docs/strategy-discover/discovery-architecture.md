# Strategy Discovery — Architecture

> Companion to `discovery-data-model.md` (the data model) and `discovery-conversation.md` (the
> conversation design). This doc specifies the four components, their contracts, and the resolved
> implementation decisions.

## Principles (locked)

- **Conversation-first; the engine is invisible.** It must feel like a sharp analyst, never a form or
  filter. When intent is unknown the agent **proactively asks plain-English questions** (chips are just
  a UI rendering of those questions).
- **Scripts own data + matching; the LLM owns talking.** All data acquisition and all matching is
  deterministic code; the LLM converses, selects, and narrates.
- **The matcher rejects the impossible and coarse-narrows — it does not select.** The LLM does the
  final selection over a small, enriched top-N.
- **Built for open models (Qwen-class):** the LLM only ever sees small, fact-rich, bounded payloads.
- **Data-driven & extensible:** adding/removing a strategy is a data change (one `strategy.yaml` +
  regenerate), never a skill edit. **Discovery owns the schema + logic; authors own per-strategy values.**

---

## The four components

```
  user ⇄ chat   │ 3. CONVERSATION (LLM, visible): ask · extract intent · select · narrate
                │        ▲ intent flags │ MatchResult ▲
  2. MATCHER (script, hidden): hard-reject · coarse-narrow (relevance count) · enrich top-N
                │        ▲ needs data   │
  1. DATA LAYER (scripts, hidden): catalog · user context · market context (via SenpiClient)
  4. HANDOFF: deploy → ops (id + version) · build-custom → author (intent brief)
```

### 1. Data Layer — acquire & shape all data (script, hidden)
- **Strategy data** → enriched `catalog.json` (committed; built by `gen_catalog.py`).
- **User context** → one batched `SenpiClient` pass (`account_get_portfolio`, `discovery_get_trader_history`).
- **Market context** → one batched pass (`market_get_funding_regime`, `market_get_asset_data`,
  `leaderboard_get_markets`).
- **Does NOT** decide, rank, or converse. Tolerates missing auth/data (degrades, never throws).
- Outputs: `CatalogRecord[]`, `UserContext`, `MarketContext` (schemas below).

### 2. Matcher — reject, enrich & coarse-narrow (script, hidden)
- **Hard-reject** only on explicit constraints (cross-domain asset, named-asset unavailable,
  strict-opposite direction, explicit exclusions).
- **Coarse relevance** = `#stated facets matched − (1 if opposite belief)`. Flat +1 per facet, no tuned
  weights. Surface the **top-N (=8)**.
- **Enrich** the top-N with `market_facts`.
- **Does NOT** select the lead, write prose, or score with weights. The LLM selects.

### 3. Conversation Layer — talk, select, narrate (LLM, the only visible layer)
- Proactively asks plain-English questions; extracts intent → flags; re-calls the engine with the full
  accumulated flag set; **selects from the top-N** and narrates **2–3 cards** in the analyst voice using
  `market_facts`. Never fetches, filters, or names a strategy outside `candidates[]`. Details in
  `discovery-conversation.md`.

### 4. Handoff Layer
- **Deploy** → `senpi-strategy-ops` with **`id` + `version`** (ops re-reads `strategy.yaml`, makes wallets).
- **Build-custom** → `senpi-strategy-author` with a **structured intent brief** (`intent_echo` + summary).

---

## Contracts

**Intent (CLI flags → discover.py):** all optional · hybrid values (exact enum or loose NL, normalizer
canonicalizes) · unknown → unstated.
```
--risk conservative|moderate|aggressive
--assets <csv: class-tags (btc_eth,major_alts,universe_crypto,xyz_equities,commodities,indices,pre_ipo)
          and/or named tickers (BTC,SOL,NVDA)>
--belief trend|contrarian|copy|breakout|structural|single_market
--horizon scalp|swing|position|hodl   --direction long_only|short_only|any
--budget <number>   --exclude <csv>   --experience new|experienced
--limit <int=8>   --offset <int=0>
```

**MatchResult (JSON stdout):**
```
{ candidates: [{ id, name, emoji, tagline, archetype_label, suggested_budget, funding_split?,
                 relevance, match_reasons[ {dim,value,tolerant} ], market_facts[ {asset,price_change_24h_pct,funding,trend,oi_trend?,funding_regime?} ],
                 caveats[ <fixed script string> ] }],   // coarse-ranked top-N; LLM selects
  build_custom: { label, route: "senpi-strategy-author" },
  meta: { widened?: [], unmet?: [], eligible_count, returned_n, offset, intent_echo, warnings[] } }
```
- `market_facts` + `match_reasons` are **structured** (LLM phrases); `caveats` are **fixed script strings**
  (honesty wording, not softened).
- `relevance` is a coarse sort key, never quoted to the user.

**CatalogRecord** (Data Layer / `gen_catalog`): identity (`id,name,emoji,tagline,belief_plain,version`)
+ thesis (`group, archetype, sub_style`) + market (`asset_classes, asset_scope, assets, direction`) +
risk (`risk_level, tier, leverage_max, time_horizon, cadence_seconds`) + capital (`min_budget,
instance_count, funding_split, max_slots`) + inlined `archetype_label`/`sub_style_label` glosses.

**UserContext** `{budget, holdings[], favored_assets[], favored_direction}` (any null) — **data only**;
the LLM decides whether to turn it into intent (confirms with the user first; never silently inferred).

**MarketContext** per asset `{price_change_24h_pct, funding (sign), trend}` from `asset_context`+candles
(work unauthenticated) + `oi_trend` & market-wide `funding_regime` when authenticated (top-N assets).

---

## Matcher detail

**Hard rejects** (only on an explicitly expressed constraint; unstated never rejects; risk & budget
never reject): cross-domain asset (crypto-stated user vs *pure* non-crypto strategy; `xyz:` prefix gives
domain), named-asset unavailable (membership check on `assets`), strict-opposite direction
(`short_only` for a long-only user; `long_short` never rejected), explicit exclusions.

**Coarse relevance:** +1 per matched stated facet (risk exact-or-adjacent, asset fit, belief, direction
`long_only`, horizon, scope, goal, starter-when-`--experience new`); `−1` opposite belief; `long_short`
for a longs-only user = +0. Sort desc → top-N. Tie-breaks: lower `min_budget` → `sort_order` → name.

**Degrade (empty set):** only **named-asset auto-broadens** to its class (flag `meta.widened`).
Cross-domain / direction / exclusions are **not** silently relaxed → build-custom + `meta.unmet`; the
conversation may *offer* widening explicitly.

**Multi-instance:** match on the asset **union**; when the user's assets are covered by only a subset of
legs, attach a **caveat** (narration surfaces "the swing leg ~60% trades alts/tech, not BTC/ETH").

---

## Intent robustness (Qwen-class)

Precision is code's job. Layers: (1) hybrid flags + forgiving **normalizer** (synonym maps;
medium→moderate, yolo→aggressive, oil→commodities; unmapped → unstated; canonicalizations logged in
`meta.warnings`); (2) **fail open** — every unknown value drops to unstated (widens, never dead-ends);
(3) **total matcher** — empty intent → full ranked top-N; (4) **always valid JSON** + exit 0 for handled
cases (non-zero only on a true crash → SKILL.md generic fallback).

---

## Data Layer detail

**Sourcing:** *declared* by author (`archetype, sub_style, asset_classes, asset_scope, risk_level, tier,
belief_plain, direction`); *derived* by `gen_catalog` (`assets, leverage_max, funding_split,
instance_count, cadence_seconds, time_horizon, max_slots`).

**No hardcoded ticker table.** `market_list_instruments` gives the live universe (no category field);
crypto split is trivial (`BTC`/`ETH`→`btc_eth`, else→`major_alts`); xyz category = the author's declared
`sub_style`; user-named tickers resolve by `xyz:` prefix (domain) + membership check (no per-ticker class).

**Derivation:** `assets`=union(allowedAssets) · `leverage_max`=max of `params.*MaxLeverage`/conviction
tiers · `funding_split`=`[instances.funding_share]` (single→`[1.0]`) · `cadence_seconds`=min(tick_seconds)
· `time_horizon`=≤60s→scalp/≤600s→swing/>600s→position (override) · **`min_budget`=`100×instance_count`**
(effective floor; spider→$200). `gen_catalog` **warns** (never fails) on unknown/missing declared values.

**Two-pass:** Pass 1 matches on static catalog + user context (no market dep). Pass 2 fetches market data
for the top-N's ≤3 most-relevant assets each → `market_facts`. One `exec` invocation does both.

---

## Code location & iteration

**v1:** Data Layer + Matcher ship as `senpi-strategy-discover/scripts/discover.py`, reusing
`senpi_runtime_helpers.SenpiClient` via the `sys.path` shim. Invoked by the OpenClaw **`exec`** tool with
discrete flags. **v2:** lift into `senpi-trading-runtime` (`senpi-helpers discover` CLI), MCP-tool-wrappable.

---

## Resolved implementation decisions (35)

See the plan's decision log for full text. Highlights: LLM-side inference (script returns context, LLM
confirms before using) · flat +1 coarse relevance (no weights) · named ticker via live-list + membership
· ops handoff = id+version · hybrid LLM-facing fields (structured facts/reasons, fixed-string caveats) ·
glossary rides along in `MatchResult` · committed `catalog.json` · `min_budget=100×instances` · multi-leg
union+caveat · graceful degrade · keep `group` AND `archetype` · `asset_scope` declared · named-strategy
→ route to ops · below-floor → surface, don't block · **discovery owns schema+logic, authors own values**
· glossary = `references/glossary.yaml` · `sub_style` extensible (warn) · author guide in
`senpi-strategy-author` · 2–3 cards · belief-first cold open · forgiving+logged synonyms.
