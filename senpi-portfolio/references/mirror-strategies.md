# Copy-trade / mirror strategies (`strategy_kind: "mirror"`)

Read before judging a copy-trade. The NEVER-for-a-mirror rule is also in SKILL.md.

A **mirror** (copy-trade) strategy — created via **`senpi-trade`** (`strategy_create`) — **copies a specific
trader** instead of running a scanner. It has **no runtime, no `runtime.yaml`, and no DSL by design** — that is
NOT a defect, and it is NOT "unprotected." Recognise it by `strategy_kind: "mirror"` (equivalently
`runtime_health: "mirror"`); it carries `mirror_of` (the copied trader, masked), `mirror_multiplier` (how hard
it sizes vs the OG), and `stop_loss_pct` / `take_profit_pct` (its **strategy-level** risk caps). Its `name` is
**"copy of `mirror_of`"** — never call it "unnamed."

**How a mirror is protected — two ways, neither a DSL:**
1. It **follows the copied trader's exits** — when the OG closes or trims, the mirror does too. Its positions,
   direction, and leverage are the OG's, scaled by `mirror_multiplier` (so a `20x` position is the *trader's*
   20x, mirrored — inherited, not a config you tune per-position).
2. Optional **strategy-level `stop_loss_pct` / `take_profit_pct`** — a hard cap the user placed on the copy.

**Judging one, and the ONLY correct remedies.** A mirror's risk = the copied trader's risk × `mirror_multiplier`.
High leverage or a lopsided book is worth *surfacing* ("this copies `mirror_of` at 20x — a sharp adverse move
liquidates fast; it has [no] strategy-level stop"), but the fix is **never** a DSL or a per-position stop. To
add/tighten a downside cap, take profit, or size down → set `stopLossPercentage` / `takeProfitPercentage` or
lower `mirrorMultiplier` **via `senpi-trade`** (it wraps `strategy_update`). To stop copying → unsubscribe /
close the mirror **via `senpi-trade`**.

> **NEVER, for a mirror:** add a DSL / ratchet; set a stop via `edit_position` on its positions; "redeploy it
> via `senpi-strategy-ops`"; call it "running unprotected / not running"; or call it "redundant with strategy X,
> close it." Those are **custom-strategy** remedies — applied to a mirror they break the copy-trade the user
> deliberately set up. A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its
> strategy-level SL/TP.
