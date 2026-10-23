# The mandate — where it comes from and the `profile` fields

Read before a per-strategy verdict. The judging rules are in SKILL.md ("Judge each strategy against
its OWN mandate").

> **The mandate comes from the strategy's own deployed `runtime.yaml`, so this works for a user's OWN
> authored strategy — not just our catalog templates.** The engine attaches `strategies[].profile`,
> whose **`profile.description` is read from the deployed `runtime.yaml` that the runtime registers**
> (every deployed strategy has one). Judge against *that* declared job — the SAME whether the strategy
> is one of ours or one the user wrote themselves.

**Get the mandate first, then judge.** Before you call any strategy good or bad, know what it was *for* —
and get that from the **source of truth, not memory.** The engine already does the lookup for you, and
it works **universally** — for a user's own authored strategy, not just our catalog templates:

- **`strategies[].profile`** — a single merged block for each deployed strategy. Its load-bearing field:
  - **`profile.description`** — the strategy's **"what it does / how it works," read from its DEPLOYED
    `runtime.yaml`** (the folded top-level `description:` block that the runtime itself registers). This
    is the **universal, authoritative** mandate: every deployed strategy has a `runtime.yaml`, so this is
    populated even for a strategy the *user wrote themselves*. It is versioned with the deploy and can't
    go stale. **Lead the per-strategy read with `profile.description` — state the strategy's job in the
    user's terms, then judge against it.**
  - `profile.runtime_name` / `profile.group` / `profile.dsl_preset` — also from the deployed
    `runtime.yaml` (`dsl_preset` is the named exit preset if one shipped, else `true` for a bespoke
    inline preset).
  - **Catalog enrichment (templates only, may be absent):** `belief_plain`, `thesis`, `archetype`,
    `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline` — extra facets the engine adds
    for a strategy deployed from one of our packages (keyed by `skill_name`). Use them **when present**;
    they are `null` for a user-authored/custom strategy, which is normal — `profile.description` still
    carries the mandate.
  - `profile.source` — `"registry"` (authored/custom, description only), `"registry+catalog"` (one of
    ours, description + facets), or `"catalog"` (facets only, registry unreadable).

**Anti-patterns — these exact misreads happened live; never repeat them:**

- **A risk-parity / all-weather core is NOT "misaligned" or "dead weight."** Diversified, low-turnover,
  and *uncorrelated to the rotations* is the design, not a flaw. It is supposed to sit calm while
  faster books churn. Judge it on drawdown control and steadiness, not on whether it caught this week's
  move.
- **A tail-risk / crisis hedge is NOT "wrong-way" for being small or flat in calm markets.** Its job is
  "lose a little in calm, win big in a crisis." A small negative carry while everything is quiet is the
  *premium being paid* for the payout — it's working as designed. Only a hedge that fails to pay off in
  an actual crisis is broken.
- **A selective strategy with NO open position is NOT a "ghost" or "dead."** Most selective/contrarian
  strategies do nothing most days by design — they wait for a specific signal (crowding + exhaustion, a
  range break, a copy-trigger) that is usually absent. `deployed == 0` and `positions == []` means
  **waiting for its signal**, not broken. Say "flat, waiting for its setup," never "idle dead weight."

## The verdict rules, as SKILL.md states them

SKILL.md's strategy-first rule, whole:

> **Strategy-first, judged against each strategy's OWN mandate.** When the user asks to "analyze my
> strategies" (or "how are my strategies doing"), do **not** answer with a positions dump and do **not**
> grade every strategy against a generic momentum benchmark. Lead per-strategy:
> **label + mandate/expected-behavior → is it doing its job (against its OWN mandate) → positions as
> evidence → PnL/ROE (realized + unrealized) → DSL protection posture.** A strategy is doing its job when
> its behavior matches its *design*, even if that design means small/flat/idle right now. See
> "Judge each strategy against its OWN mandate" in SKILL.md.

This is the SAME read whether the strategy is one of ours or user-authored — every deployed strategy has a
`runtime.yaml`.

SKILL.md "A strategy is ALL its wallets", word for word (the unit every verdict is about):

**This is the most important rule in this skill.** A single strategy can deploy as **MULTIPLE instances
on SEPARATE wallets** — **ox** = `core`+`ballast` (risk-parity), **cougar** = `long`+`short`
(market-neutral), **cub** = `long`+`short`+`preipo` (multi-sleeve dispersion). `strategy_list` returns
**each instance/wallet as its own row**, so the raw list looks like several separate strategies. **It is
not.** A multi-wallet strategy (long+short, core+ballast, multi-sleeve) is **ONE strategy across N
wallets/instances** — the wallets are the *legs of one design*, not independent bets.

**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.** The engine re-unites the
per-wallet rows into **`strategy_groups[]`** — one entry per real strategy, with `is_multi_wallet`,
`instances[]` (the per-wallet detail), and `totals` summed across every wallet. **Present each group as
one strategy**; never present its wallets/instances as separate strategies. (`strategies[]` is still
there for per-wallet detail and the bucket math — but the *unit of analysis and recommendation* is the
group.) When `meta.has_multi_wallet_strategy` is true, at least one strategy spans multiple wallets —
be especially careful.

For a **hedge, market-neutral, all-weather, or contrarian** strategy, **being counter is the DESIGN.** A
market-neutral book is *supposed* to be short the names the crowd is long; a hedge is *supposed* to lean
against the prevailing move; a contrarian book is *supposed* to fade the consensus. **Judge it against
its own `mandate` / `profile.description`, NOT against alignment with the 4h leaderboard / Predators
view.** Do **not** recommend closing a hedge/neutral/all-weather strategy because it's "fighting the
whales" or "on the wrong side of smart money" — that IS its job. (For a *directional momentum* strategy,
fighting the tape is a real red flag — but only for a strategy whose mandate is to ride the move.)

The **leaderboard / Predators view is a ~4h momentum window, not a portfolio mandate.** A strategy can be
"behind the current 4h rotation" and still be doing exactly its multi-week job. **Never recommend a
wholesale close+redeploy of a deliberate book to chase what's hot on a 4h screen.** Before proposing any
close+redeploy, weigh **turnover cost** (fees compound on churn) and **regime durability** (is this a
lasting shift or a 4h blip?). A deliberate, on-mandate strategy is not "underperforming" because it
didn't catch this afternoon's move.
