# DSL — the tier ladder and each position's live tier

Read before answering "tell me about my strategies and their DSL" or "are my open positions
protected?". The HARD rule (never "no DSL" for a live position) is in SKILL.md.

**(1) How its DSL works — the tier ladder.** Read the strategy's `profile.dsl` (also on each
`strategy_groups[]` entry as `dsl` — surface it once per strategy). Parsed from the strategy's deployed
`runtime.yaml` `exit.dsl_preset`, it has:
- `hard_stop_roe_pct` — the **phase1 hard stop floor, active FROM ENTRY** (e.g. `-14` = the position is
  cut if it hits −14% ROE). This protects every position **immediately, before any profit**.
- `arm_at_roe_pct` — where the **phase2 profit-ratchet ARMS** (Tier 1, e.g. `+8%`). Below this the
  ratchet hasn't engaged yet; the hard stop is still on.
- `tiers[]` — the **profit-lock ladder**: `{trigger_pct, lock_hw_pct}` pairs. Read it as "arms at +8%,
  locks 40% of the peak by +18%, 60% by +35%, 78% by +60%, 88% by +100%." `lock_hw_pct: 0` at Tier 1 =
  priming only (arms the trail, no lock yet).

**(2) Which OPEN position is in which tier — live.** Each open position carries a **`dsl`** object (live
per-position ratchet state, from `ratchet_stop_list`):
- **`armed: true`** → the position has crossed Tier 1; report **"Tier N, locked at L% of peak, high-water
  +H% ROE"** from `tier_index` / `locked` / `high_water_roe` (`status` = `ACTIVE`/`PAUSED`/…).
- **`armed: false`** → the position is **sub-Tier-1**: the profit-ratchet hasn't armed yet, but it is
  **still protected from entry by the phase1 hard stop.** Report it that way — `note` already phrases it
  ("protected from entry by the phase1 hard stop; profit-ratchet arms at Tier 1 (+X%) — currently +Y%").
  Use `arm_at_roe_pct` + the position's `roe`: "protected, ratchet arms at +8% — currently at +6%."

## Config posture vs live tier, and resting orders

- **Config-level `protected` ≠ live per-position tier.** `strategy.protected` / `group.protected`
  (`true`/`false`/`null`) is the **config posture** — `true` only when the deployed `runtime.yaml`'s
  `exit:` block was actually READ by the engine; `null` means the read didn't happen, never assume `true`
  from `skill_name` alone. It says "this strategy has a DSL exit," not which tier a given position sits in.
  The per-position tier is the `dsl` object above. Report both: "cougar runs a DSL exit (hard stop −14%,
  ratchet from +8%); its NVDA short is sub-Tier-1 at +6% — hard-stop protected, ratchet arms at +8%."
- **For a runtime-managed position, never infer "no stop" from the absence of a resting stop order.** DSL
  exits are **runtime-managed**, not resting venue orders — you won't see them as open orders. Absence of a
  resting SL is expected and says nothing about protection. Use the `dsl` objects, not the order book. A
  raw position is the reverse case — the raw-position bullet in SKILL.md.
