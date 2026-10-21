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

## Why the engine, not a by-hand read

From SKILL.md's "Use this skill FIRST", "Golden rules" and the DSL HARD rule; the rules stay there.

**This includes DSL / "are my positions protected?" questions — do NOT hand-roll them.** Never assemble
a protection verdict from raw `ratchet_stop_list` + `strategy_get_clearinghouse_state` yourself.
`ratchet_stop_list` shows **only** the live ratchet for positions that have already crossed Tier 1 — it
does **not** carry the strategy's config DSL exit, so a by-hand read makes every sub-Tier-1 position look
"unprotected" when it isn't. The engine reads BOTH the config ladder (`profile.dsl`) and the live tier
(`positions[].dsl`) and frames every position correctly; run it.

- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Every
  strategy deployed from a Senpi template runs a built-in DSL exit (trailing stop) + risk guard-rails,
  enforced every tick. Never tell a user to "add a 10–15% SL via `strategy_update`" on a deployed
  strategy — it already has one. To *verify* protection, read `profile.dsl` (the ladder) + each
  `positions[].dsl` (the live tier) — see "DSL — how it works per strategy, and which position is in
  which tier"; never infer "no stop" from the absence of a resting stop order (DSL exits are
  runtime-managed, not resting orders) or from a missing ratchet record (sub-Tier-1 positions have none).

This is the failure this section exists to prevent. An **empty ratchet record on a sub-Tier-1 position is
NOT "unprotected"** — `ratchet_stop_list` only returns a record **once a position crosses Tier 1**, so a
position at, say, +6% ROE correctly has **no ratchet record yet**. It is protected **two ways**: the
phase1 hard stop (active from entry) and the phase2 ratchet that will arm at Tier 1. **Never read "no
ratchet record" as "no DSL."** Say **"protected; profit-ratchet arms at Tier 1 (+X%)"** — never
"unprotected / unmonitored / no stop." (The engine already frames every `armed: false` position this way
in `dsl.note`; do not override it with an "unprotected" reading.)

- **An ERRORED or empty DSL query is "unknown," never "unprotected."** `ratchet_stop_list` can fail
  (e.g. `SERR031` auth, or the DSL engine lagging behind a just-opened position) or come back empty. That
  is a **data gap**, not evidence of missing protection — treat it exactly like `dsl:null`. The engine
  fails open here (config framing stands alone, plus a `meta.warnings` note); a by-hand call has no such
  fallback, which is why hand-rolling produces false "unprotected" verdicts. Never turn a failed read into
  a risk finding.
