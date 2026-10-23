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
- **`armed: true`** → the record's tier is engaged (`currentTierIndex` ≥ 0: the position has crossed
  Tier 1); report **"Tier N, locked at L% of peak, high-water +H% ROE"** from `tier` / `locked` /
  `high_water_roe` (`status` = `ACTIVE`/`PAUSED`/…). `tier` is 1-based; `tier_index` is the record's
  0-based `currentTierIndex` (index 0 = Tier 1) — never say "Tier 0". `locked` is the record's own
  `lockRoe` at that tier (what the backend enforces, also after a `ratchet_stop_edit`), else the
  ladder's `lock_hw_pct`; both are % of the peak.
- **`armed: false`** → the position is **sub-Tier-1**: no ratchet record, or a record at tier -1 with a
  null floor and no SL order of its own (`ratchet_record: true`, `tier_index: -1`). A strategy with
  `phase1.enabled: false` starts in phase 2, so the backend holds its record from open; that switch turns
  off only the phase-1 trailing retrace, and `max_loss_pct` still stops from entry. For a runtime strategy
  the profit-ratchet hasn't armed yet, but the position is **still protected from entry by the max-loss
  (phase1) stop.** Report it that way — `note` already phrases it ("protected from entry by the phase1
  hard stop; profit-ratchet arms at Tier 1 (+X%) — currently +Y%"). Use `arm_at_roe_pct` + the
  position's `roe`: "protected, ratchet arms at +8% — currently at +6%." With a record, `arm_at_roe_pct`
  is the record's own Tier 1 trigger.
- **No runtime ladder** (`profile.dsl` null, or `{preset_name, note}`) → the engine never says
  "protected" off the ratchet alone; quote `note`. A **raw position with a tier -1 record** carries
  **`downside_stop: false`**: MCP `ratchet_stop_add` does not persist a max-loss floor today, so the
  ratchet gives **no downside stop until Tier 1** ("ratchet registered, not yet armed"). A raw position
  with no record is "no ratchet". Either way, read its resting orders before saying it has no stop (the
  raw-position bullet in SKILL.md). A copy-trade's note names the trader's exits + its strategy-level
  SL/TP; a failed runtime read's note says it could not be verified.
- **A DSL exit the engine does not enforce** → `downside_stop: false`, never "protected": `exit: {engine:
  dsl}` with no `dsl_preset` (`protected: true`, `profile.dsl` null — the runtime runs no ladder, so no
  hard stop and no trailing floor), or a `dsl_preset` the engine could not install (a string preset, or
  one its parser refuses). `protected: true` there means only that the author asked for a DSL exit.
- **A registered runtime whose `runtime.yaml` did not render** (`runtime_registered: true`, no
  descriptor) → `downside_stop: null` and "could not be verified" — it is running, so never "no runtime
  DSL exit". The same holds when the ratchet read failed or answered a shape the engine doesn't know
  (`meta.warnings` names it): a ladder strategy keeps its config framing, anything else reads "ratchet
  read failed" and is never "protected".

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
`ratchet_stop_list` shows **only** the live ratchet — before Tier 1 a position has no record, or one at
tier -1 with a null floor and no SL order — and it does **not** carry the strategy's config DSL exit (the
runtime's max-loss stop), so a by-hand read makes every sub-Tier-1 position look "unprotected" when it
isn't. The engine reads BOTH the config ladder (`profile.dsl`) and the live tier
(`positions[].dsl`) and frames every position correctly; run it.

- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Every
  strategy deployed from a Senpi template runs a built-in DSL exit (trailing stop) + risk guard-rails,
  enforced every tick. Never tell a user to "add a 10–15% SL via `strategy_update`" on a deployed
  strategy — it already has one. To *verify* protection, read `profile.dsl` (the ladder) + each
  `positions[].dsl` (the live tier) — see "DSL — how it works per strategy, and which position is in
  which tier"; never infer "no stop" from the absence of a resting stop order (DSL exits are
  runtime-managed, not resting orders) or from a missing or tier -1 ratchet record.

This is the failure this section exists to prevent. **No ratchet record, or a tier -1 one, is
sub-Tier-1, NOT "unprotected"** — before Tier 1 a position at, say, +6% ROE has either no record yet or a
record at tier -1 with a null floor (a live xyz:CL short read -1 / null / null while its max-loss stop order
rested on the venue). It is protected **two ways** on a runtime strategy: the max-loss
(phase1) stop, active from entry, and the phase2 ratchet that will arm at Tier 1. **Never read "no
ratchet record" as "no DSL."** Say **"protected; profit-ratchet arms at Tier 1 (+X%)"** — never
"unprotected / unmonitored / no stop." (The engine frames every `armed: false` position in `dsl.note`;
never override it with an "unprotected" reading.)

- **An ERRORED or empty DSL query is "unknown," never "unprotected."** `ratchet_stop_list` can fail
  (e.g. `SERR031` auth, or the DSL engine lagging behind a just-opened position) or come back empty. That
  is a **data gap**, not evidence of missing protection — treat it exactly like `dsl:null`. The engine
  fails open here (config framing stands alone, plus a `meta.warnings` note); a by-hand call has no such
  fallback, which is why hand-rolling produces false "unprotected" verdicts. Never turn a failed read into
  a risk finding.
