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
