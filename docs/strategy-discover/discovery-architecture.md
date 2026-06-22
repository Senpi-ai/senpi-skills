# Strategy Discovery — Architecture

> Companion to `discovery-data-model.md` (the data model) and `discovery-conversation.md` (the
> conversation design). This doc specifies the four components, their contracts, and the resolved
> implementation decisions.

## Principles (locked)

- **Conversation-first; the engine is invisible.** It must feel like a sharp analyst, never a form or
  filter. When intent is unknown the agent **proactively asks plain-English questions** (chips are just
  a UI rendering of those questions).
- **Scripts own data + the concrete filter; the LLM owns all ranking + talking.** Data acquisition and
  the *concrete* filter (asset/direction/exclusion) are deterministic code; the LLM ranks, selects, and
  narrates. Soft/semantic ranking (risk, belief, horizon, worldview) is the LLM's job — no glossary.
- **The matcher only filters the impossible and returns ALL survivors — it does not score or select.**
  A bad rank still contains the right answer; a bad cut doesn't, so the script never cuts (a `--limit`
  is a safety cap only). The LLM ranks the full eligible set.
- **Built for open models (Qwen-class):** records are flat + labels pre-inlined; the LLM ranks a
  human-readable set (its strength), never decodes slugs or emits an exact taxonomy value.
- **Data-driven & extensible:** adding/removing a strategy is a data change (one `strategy.yaml` +
  regenerate), never a skill edit. **Discovery owns the schema + logic; authors own per-strategy values.**

---

## The four components

```
  user ⇄ chat   │ 3. CONVERSATION (LLM, visible): ask · extract CONCRETE flags · RANK the set · narrate
                │        ▲ concrete flags │ MatchResult (ALL eligible) ▲
  2. FILTER (script, hidden): hard-reject only (concrete) · return ALL survivors · neutral order · enrich
                │        ▲ needs data   │
  1. DATA LAYER (scripts, hidden): catalog · user context · market context (via self-contained _mcp.MCPClient)
  4. HANDOFF: deploy → ops (id + version) · build-custom → author (intent brief)
```

### 1. Data Layer — acquire & shape all data (script, hidden)
- **Strategy data** → enriched `catalog.json` (committed; built by `gen_catalog.py`).
- **User context** → one batched MCP pass via the skill's own `_mcp.MCPClient` (`account_get_portfolio`).
- **Market context** → one batched pass (`market_get_funding_regime`, `market_get_asset_data`,
  `leaderboard_get_markets`).
- **Does NOT** decide, rank, or converse. Tolerates missing auth/data (degrades, never throws).
- Outputs: `CatalogRecord[]`, `UserContext`, `MarketContext` (schemas below).

### 2. Filter — reject the impossible, return ALL survivors (script, hidden)
- **Hard-reject** only on explicit **concrete** constraints (cross-domain asset, named-asset
  unavailable, strict-opposite direction, explicit exclusions). Nothing soft ever filters.
- **Return every survivor** — no relevance score, no top-N. Neutral order: `asset-match desc, name`
  (lossless ordering, never a cut). A `--limit` caps the returned list as a safety valve only.
- **Enrich** survivors with `market_facts` (one batched read per unique asset, bounded by asset count).
- **Does NOT** score, select, write prose, or rank on risk/belief/horizon/worldview. The LLM does all of that.

### 3. Conversation Layer — talk, RANK, narrate (LLM, the only visible layer)
- Proactively asks plain-English questions; extracts only **concrete** constraints → flags (keeps risk/
  belief/horizon/worldview in its head); re-runs the engine with the full concrete set; **RANKS the
  returned eligible set itself** (risk_level, belief_plain, `thesis`/`tags` for worldview, market_facts)
  and narrates **2–3 cards**. Never fetches, filters, or names a strategy outside `candidates[]`. Details
  in `discovery-conversation.md`.

### 4. Handoff Layer
- **Deploy** → `senpi-strategy-ops` with **`id` + `version`** (ops re-reads `strategy.yaml`, makes wallets).
- **Build-custom** → `senpi-strategy-author` with a **structured intent brief** (`intent_echo` + summary).

---

## Contracts

**Intent (CLI flags → discover.py): CONCRETE only.** All optional · hybrid values (exact enum or loose
NL, normalizer canonicalizes) · unknown → unstated. There is deliberately **no** `--risk`/`--belief`/
`--horizon`/`--market-scope`/`--goal`/`--experience` — those are the LLM's ranking job.
```
--assets <csv: class-tags (btc_eth,major_alts,universe_crypto,xyz_equities,commodities,indices,pre_ipo)
          and/or named tickers (BTC,SOL,NVDA)>
--direction long_only|short_only|any
--exclude <csv: copy_trading,stocks,crypto,commodities,pre_ipo,dca,shorting>
--budget <number>     --limit <int>   (safety cap only; default returns ALL eligible)
```

**MatchResult (JSON stdout):**
```
{ candidates: [{ id, version, name, emoji, tagline,
                 risk_level, archetype_label, belief_plain, thesis, tags[], tag_labels?,   // SOFT-RANK surface (LLM ranks on these)
                 time_horizon, asset_scope, direction, asset_classes[], assets[], tier,
                 suggested_budget, funding_split?, caveats[ <fixed script string> ],
                 market_facts[ {asset,price_change_24h_pct,funding,trend,oi_trend?,funding_regime?} ] }],  // ALL eligible, neutral-ordered
  build_custom: { label, route: "senpi-strategy-author" },
  meta: { widened?: [], unmet?: [], eligible_count, returned_n, intent_echo, warnings[] } }
```
- `thesis` + `tags` are the LLM's **worldview/theme** ranking surface; `thesis` is narration-grade prose,
  never a matched field. `caveats` are **fixed script strings** (honesty wording, not softened).
- No `relevance`/`match_reasons` — the script does not score. The LLM ranks the full set.

**CatalogRecord** (Data Layer / `gen_catalog`): identity (`id,name,emoji,tagline,belief_plain,version`)
+ thesis/theme (`group, archetype, sub_style, thesis, tags[]`) + market (`asset_classes, asset_scope,
assets, direction`) + risk (`risk_level, tier, leverage_max, time_horizon, cadence_seconds`) + capital
(`min_budget, instance_count, funding_split, max_slots`) + inlined `archetype_label`/`sub_style_label`/
`tag_labels` glosses. `thesis` (one sentence) + `tags` (free-text keywords) are **author-declared, no
controlled vocabulary** — the LLM matches them semantically.

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

**No scoring.** Survivors are returned in full, neutral-ordered by `(asset-match desc, name)` — a
lossless ordering hint (asset-matched first), never a cut. All soft ranking (risk, belief, horizon,
worldview via `thesis`/`tags`) happens LLM-side over the returned set.

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

**v1:** Matcher + Data Layer ship as `senpi-strategy-discover/scripts/discover.py` with a **self-contained
MCP client** (`scripts/_mcp.py`, stdlib only — ported from `SenpiClient` so the skill has **no
`senpi_runtime_helpers` dependency**; works wherever the skill is installed). The matcher is pure stdlib;
only market enrichment touches the network (auth via `SENPI_AUTH_TOKEN`). Invoked by the OpenClaw **`exec`**
tool with discrete flags.

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
