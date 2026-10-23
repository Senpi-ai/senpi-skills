# Engine output — every field

Read before quoting an engine field you have not used yet. Rules for narrating these fields are in
SKILL.md; this is the glossary.

Returns `{book, totals, embedded_wallet, external_wallets, strategies, strategy_groups, exposure, signals, meta}`:
- `book` — **every wallet in ONE list by value, and the one book total. PRESENT THE LIST FROM HERE.**
  `wallets[]` (sorted by the engine — keep its order): `label`, `kind` (`managed` / `read-only`),
  `origin` (`embedded` / `strategy` / `saved`), `value_usd` (null = couldn't load), `loaded`,
  `open_positions`, `unrealized_pnl_usd`; a strategy row adds `strategy_group` (its
  `strategy_groups[].label`), `wallet_count`, `strategy_wallets[]`, `wallets_couldnt_load`, `protected`,
  `runtime_health`, `realized_pnl_usd` (or `not_read_this_step` on the `money` step); a saved row adds
  `address`, `protection` counts, `excludes_coins`, `no_hyperliquid_activity`, `positions_scope`, `access`,
  `not_applicable`. `totals`: `total_usd`, `managed_usd` (= `grand_total_usd`), `managed_breakdown` (the
  three buckets + `reconciles`), `managed_complete`, `read_only_usd`, `read_only_wallets`
  (`total` / `loaded`), `read_only_note`, `excludes` (+ `strategies_unreadable`), `excludes_note`, `line`
  (the rendered total — quote it). `deep_dive`: `offer`, `question`, `order` (labels, largest first —
  only wallets with something to go deeper on).
- `external_wallets` — the saved wallets' raw reads: `status` (`ok` / `unavailable`) and `wallets[]`,
  each `state` verbatim from `account_get_external_wallets` (see "One wallet list — every wallet first-class").
- `totals` — the MANAGED money map: the three buckets + `grand_total_usd`, `unrealized_pnl`, and a `reconciles` flag (cross-
  checks the per-wallet sum against the portfolio aggregate; if `false`, say the numbers don't tie out
  and lead with the per-wallet figures).
- `embedded_wallet` — `address`, `idle_hl_usdc`, `evm_usdc[]` (per chain), `spot_usd`, `idle_total`.
- **`strategy_groups[]` — ONE entry per real strategy (a strategy is ALL its wallets). LEAD HERE.** The
  engine re-unites the per-wallet `strategies[]` rows into one group per strategy, keyed by
  `profile.group` (→ fallback `skill_name` → fallback the wallet). **This is the unit of analysis and
  recommendation** — present and reason at this level, never at individual wallets. Each group:
  - `label` — the group id (e.g. `ox`, `cougar`, `cub`); `skill_name`; `archetype` / `archetype_label`
    / `direction` (catalog facets when present).
  - `mandate` — the strategy's declared job (its `profile.description`, else `belief_plain`); shared by
    all instances. Judge the whole strategy against this.
  - `dsl` — the strategy's **DSL protection ladder** (how its DSL works), shared by all instances:
    `hard_stop_roe_pct` / `arm_at_roe_pct` / `tiers[]` / `has_phase2`, or `{preset_name, note}` for a
    named preset, or `null`. Surface it **once per strategy**; each open position's *live* tier is on
    `positions[].dsl`. See `dsl.md`.
  - `is_multi_wallet` (bool) — `true` when the strategy spans >1 wallet (long+short, core+ballast,
    multi-sleeve). When true, the wallets are legs of ONE design — see "A strategy is ALL its wallets."
  - `instances[]` — the per-wallet detail: `name` (= `runtime_name`, e.g. `ox-core`), `wallet`,
    `wallet_short`, `account_value`, `idle_withdrawable`, `deployed`, `upnl`, `positions[]` (each with a
    live `dsl` tier object), `closed`.
  - `totals` — summed across every instance: `account_value`, `idle_withdrawable`, `deployed`, `upnl`,
    and `realized_pnl` (when available). **Report the strategy's figures from here, not per-wallet.**
  - `protected` (`true` / `false` / `null`) — `true` **only if ALL instances are protected**; `null` if
    ANY instance is `null` — an unread instance is never laundered into a `false`.
  - `flat_instances` — names of instances with **no open positions**. For a multi-wallet strategy these
    are the OTHER sleeve(s) **waiting for a signal** — NOT redeployable idle, never "dead money."
  - `profile_source` — where this strategy's profile came from (`registry` / `registry+catalog` / …).
- `strategies[]` — the per-wallet detail (kept for the bucket math + `exposure`; `strategy_groups[]` is
  the level you *present* from). Per wallet: `name`, `wallet`, `account_value`, `idle_withdrawable` (bucket 2 for
  *this* strategy), `deployed` (equity tied up in positions = account_value − withdrawable),
  `position_margin` (initial margin detail), `total_funded`/`total_withdrawn`, and:
  - `name` / `name_source` — the display name, and WHICH FIELD produced it. Chain:
    `strategyName` (its own name, `<id>-<instance>` for a package deploy) → `tradingStrategyName` (the
    package id) → `name` → the `"strategy"` placeholder (`name_source: null`). This is the instance
    label; `skill_name` is the package. **Trust `name` as a name only when `name_source` is
    `"strategyName"`.**
  - `skill_name` / `skill_version` — the strategy's package attribution (e.g. `ox`, `cougar`, `lion`),
    from its `strategy_list` record. `null` for a hand-rolled/custom strategy with no package.
  - `profile` — **the strategy's declared job, universal across ours + user-authored strategies.** Its
    load-bearing field is **`profile.description` — read from the strategy's DEPLOYED `runtime.yaml`**
    (the top-level folded `description:` the runtime registers), collapsed to a single line. Also from
    the runtime.yaml: `runtime_name`, `group`, `dsl_preset` (named preset string, or `true` for a
    bespoke inline preset), and **`dsl`** — the parsed **DSL protection ladder** (how DSL works for this
    strategy): `hard_stop_roe_pct` (phase1 floor, active from entry), `arm_at_roe_pct` (where the
    profit-ratchet arms = Tier 1), `tiers[]` (`{trigger_pct, lock_hw_pct}` profit-lock ladder), and
    `has_phase2`. For a **named-string preset** it's `{preset_name, note}` instead; `null` when the
    `exit:` block has no `dsl_preset`. This is the CONFIG side — pair it with each position's live `dsl`
    tier (see `positions[].dsl`). Optional **catalog enrichment** (templates only, keyed by `skill_name`;
    `null` for authored strategies): `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`,
    `risk_level`, `time_horizon`, `tagline`. `profile.source` = `"registry"` / `"registry+catalog"` /
    `"catalog"`. **This is the yardstick — judge the strategy against `profile.description`, not memory
    and not a momentum benchmark.** `profile` is `null` only when the strategy is in neither the runtime
    registry nor the catalog (`meta.profile_source` records `registry`/`catalog`/`mixed`/`null`).
  - `protected` (`true` / `false` / `null`) — `true` only when the deployed `runtime.yaml`'s `exit:`
    block was actually READ by the engine (`dsl_preset` or `engine: dsl`); a `skill_name` attribution
    stamp alone no longer counts. `null` = the runtime read did not answer — say "could not verify on
    this host," never "protected" or "not protected." Config-level posture, not a live per-position
    check — see the tri-state rule (SKILL.md, Golden rules).
  - `closed` — `{realized_pnl, trade_count, winners, losers, win_rate_pct, longs, shorts, unknown_side, closed_record_newest_utc, recent[]}`
    from a read-guarded `discovery_get_trader_history` on the strategy wallet: `realized_pnl` (total
    booked PnL over the recent pull), the record over that pull (a flat close is neither a winner nor
    a loser; `win_rate_pct` = winners / trade_count), `closed_record_newest_utc` (the newest close the
    record holds, UTC), and `recent[]` (last few closed trades: `asset`, `direction`, `realized_pnl`,
    `entry_px`, `exit_px`, `closed_at_utc` — the close's UTC date and time, the one to quote — and
    `closed_time`, the raw epoch as the feed sent it). A close time that cannot be read is
    `closed_at_utc: null` plus a `meta.warnings` line: give that close no date. `strategy_groups[].totals`
    carries the same counts summed across the strategy's wallets and the newest of their
    `closed_record_newest_utc`. On a read failure `realized_pnl` and the counts are `null` and a
    `meta.warnings` entry is added — treat as "unavailable," never as zero.
  - `positions[]` (asset, dex, direction, leverage, notional, margin, `upnl`, `return_on_equity_pct`,
    `liq_px`, `market_24h_pct`, `vs_market`, and **`dsl`** — the live per-position ratchet tier).
    - **`dsl`** — this position's live DSL/ratchet state. **`armed: true`** (a ratchet record at an
      engaged tier, `currentTierIndex` ≥ 0) → `tier` (1-based "Tier N"), `tier_index` (0-based),
      `high_water_roe`, `status`, `locked` (% of peak; record first). **`armed: false`**
      (the position is sub-Tier-1: no ratchet record, or a record at tier -1 / null / missing with a null
      floor — then `ratchet_record: true`, `tier_index`, `status`) → `hard_stop_roe_pct`,
      `arm_at_roe_pct` (record first), `roe`,
      and a `note` that reads "protected from entry by the phase1 hard stop; profit-ratchet arms at Tier
      1 (+X%) — currently +Y%." **`armed: false` means the profit-ratchet hasn't ARMED yet, NOT that the
      position is unprotected** — for a runtime strategy the max-loss (phase1) stop protects it from
      entry. Never present it as "no DSL." **No runtime ladder** (`profile.dsl: null`): `note` never says
      "protected" off the ratchet — a raw position's tier -1 record gives `downside_stop: false` (no
      downside stop until Tier 1: `ratchet_stop_add` persists no max-loss floor); no record gives `null`
      ("no ratchet"; resting orders are not read here). Unenforced DSL exits, unrendered runtimes, mirror and
      failed reads: `dsl.md`. If the ratchet read failed or its shape is unknown, every position still
      gets an `armed: false` object (+ a `meta.warnings` note).
- `exposure` — `net_notional_usd` + `net_bias`, gross long/short, `by_asset_net_usd`,
  `largest_position`.
- `signals` — `idle_drag_pct` (how much capital isn't working), `deployed_pct`,
  `largest_position_pct_of_deployed` (concentration).
- `meta` — `profile_source` (`registry` / `catalog` / `mixed` / `null` — where the strategies' mandates
  came from, in aggregate), `registry_source` (`"runtime-cli"` / `null` — `null` when the
  `openclaw senpi runtime list` read failed), **`runtime_read_ok`** (bool — `true` when that read
  answered, `false` when it failed; `false` is the whole-fleet "this box's runtime could not be asked"
  signal that pairs with the `meta.warnings` line naming the command), `catalog_source`
  (`local` / `remote` / `null`), `strategy_count`, **`has_multi_wallet_strategy`** (bool — `true` when at
  least one strategy spans multiple wallets/instances; a cue to reason at `strategy_groups[]` and apply
  the "a strategy is all its wallets" rules), and `warnings[]`.
- The engine **fails open** — partial data still returns valid JSON with `meta.warnings`. If the runtime
  registry is unreadable, mandates fall back to the catalog (templates only); if that's also gone,
  `profile` is `null` and you judge on behavior.

## Cross-DEX: main and xyz are ONE wallet, not two

A strategy wallet's clearinghouse state has a `main` (crypto) view and an `xyz` (equities/metals)
view. **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is
**shared** and reported *identically* in both views — so it is counted **once**, never summed. Each
view's `accountValue` = that shared idle + only *that* DEX's position equity, so
`wallet_value = main.av + xyz.av − shared_idle`. The engine already de-duplicates this; you just read
`account_value` / `idle_withdrawable` / `deployed` per strategy. **Never add the two views' account
values or withdrawables yourself** — that double-counts the shared collateral (the bug that inflated a
$3.1K account to $5.6K).

## Steps — the fail-open contract

**Narrate each slice as it returns — never wait for all steps.** The steps share a state file
(`<tempdir>/senpi-portfolio/state.json`, overridable with `--state`), so a later step reuses what an earlier
one fetched instead of re-pulling.

**Saved wallets.** Their raw reads are `external_wallets: {status, wallets}` (each `state` verbatim), returned from
every step (`money`, `strategies`, `positions`) and from `all`, with `meta.no_strategy_path` on each;
their rows in `book.wallets` carry the columns.

`--no-market` applies to every step (skips the `positions` market fan-out). Same fail-open contract as
`all`: each step returns valid JSON with `meta.warnings` on partial data and **never crashes on a
missing/corrupt state file, nor on a runtime CLI that is absent or cannot even be spawned** (it
self-heals by recomputing its prerequisites — every step also works STANDALONE, just slower; an
unreadable runtime costs you the runtime fields, which come back `null` + a warning, not the read). Keep `all` as the one-shot fallback when a single blocking call is fine; every
existing guardrail (the three-bucket taxonomy, `protected`, the mandate reads, multi-wallet grouping, the
DSL ladder + live tiers) holds identically across the steps and `all`. Every step and `all` print the
same `book` (the one wallet list and the book total), so the list never depends on which step answered.

## Engine notes moved from SKILL.md

The rules in these lines stay in SKILL.md; this is what the engine does behind them.

A strategy row's wallets are grouped by
the same key in every step — the package they were deployed under (`skill_name`; a wallet with none
is its own row, `strategy_group` names the key) — so the `money` step and the `strategies` step list
the same rows. The engine sorts it (`book.order_rule`:

`book.totals.managed_usd` is
  `grand_total_usd` (the same number; `reconciles` and the three buckets are about it alone).

- **Run the engine; never hand-pull balances.** `python3 scripts/portfolio.py` enumerates the
  embedded wallet + every strategy sub-wallet, pulls live clearinghouse state per wallet, and
  classifies the buckets. Read its JSON.
- **Real-time, always.** The engine forces a fresh fetch (no 12h cache) and reads each strategy's
  live clearinghouse state. Never report balances from earlier in the conversation — re-run.
- **Report realized PnL + closed trades, not only open ones.** Each strategy carries a `closed` block —
  `realized_pnl` (total booked PnL over the recent history pull), the record over that pull
  (`trade_count`, `winners`, `losers`, `win_rate_pct`, `longs`, `shorts`, `unknown_side`) and `recent[]` (last few
  closed trades: asset, direction, realized pnl, `closed_at_utc`).
- **"Current / my strategies" = ACTIVE only — never CLOSED.** The engine filters
  `strategy_list(status=["ACTIVE"])`, starts each analysis turn from a clean state, and expires the shared
  cache after a short window — so a strategy CLOSED since a prior run can't linger as a ghost.
- **A strategy with a null name is still a real strategy.** `strategyName` is optional on
  `strategy_create_custom_strategy` and absent entirely from `strategy_create`, so it comes back null for
  many strategies — identify and analyze them by `strategyId` + wallet, never skip, mislabel ("Unnamed"),
  or double-count them for lacking a display name. (If you *created* it this session, you already know its
  name — don't re-derive it as "unknown.")
- **Only `name_source: "strategyName"` means `name` is really its name.** Anything else is a stand-in the
  engine substituted: `"tradingStrategyName"` is the **package id** (identical on every sleeve of a
  package — all three cub sleeves read `cub`), `"name"` is a defensive flat-payload alias, and `null`
  means it has no name at all.

`all` is the default when no step is given — it composes every slice into one dict (the same output the
engine always produced). Prefer the **steps** above for a full read (they stream and don't trip the
