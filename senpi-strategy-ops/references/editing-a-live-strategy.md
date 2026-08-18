# Editing a strategy that is already LIVE

The trigger: the user asks to re-score / re-scan / re-tune something that is already deployed and
holding positions. `senpi-strategy-ops/SKILL.md` routes here; this file is the procedure.

**The edit itself — `scoring.py` / `scan.py` / `runtime.yaml`, leverage, sizing, DSL — is authored in
`senpi-strategy-author`** (the only skill that knows the scanner / yaml / DSL schema). Ops APPLIES the
edited package; it does not write it.

**Most edits now apply IN PLACE — `openclaw senpi update`. Do not close a live book to re-tune it.**
No wallet is created, nothing is funded, no position is market-exited, and DSL state, scanner stores
and action history all survive. Re-running `create` still will NOT apply an edit — the deploy verb is
idempotent, so it adopts the wallet that already exists and leaves the deployed scanner as it is.

```bash
# 1. Prove the edit runs, and write the proof `--apply` requires.
openclaw senpi validate <recipe-dir>

# 2. PLAN it — what changes, and what it leaves alone. Changes nothing; show this to the user.
openclaw senpi update <recipe-dir> --id <runtime_id>

# 3. Apply.
openclaw senpi update <recipe-dir> --id <runtime_id> --apply
```

`--id` names which runtime; on a multi-instance package each arm is its own runtime, so this is how
you re-tune one sleeve and leave its siblings untouched. `--address <wallet>` works too. Changed only
`scan.py`/`scoring.py`? Add `--code-only` and it refuses if the recipe moved as well.

**Read the plan to the user before applying — one thing in it will surprise them.** Exit changes are
**forward-only**: a new `dsl_preset` governs NEW entries, while every position already open keeps a
snapshot of the preset it was opened under. A tightened stop does **not** reach a position that is
already running, and the only way to move one onto the new exits is to close and re-open it. The plan
names the affected positions. Do not let "I made it tighter" be heard as "my open trades are now
tighter".

Exit `2` means it refused and the runtime was never touched. Exit `1` means an apply was attempted
and the runtime may not be where you left it — read the message rather than retrying; it says whether
the runtime was restored to its previous recipe or is down with positions unmanaged and needs a human.

## When the edit CANNOT be applied in place

`senpi update` refuses these outright, because each would silently orphan state the runtime keys on:

| Change | Why it needs a new deployment |
|---|---|
| a different `strategy.wallet` | That is a different deployment. The old wallet's positions would be left with nothing managing them. |
| a **renamed** or **reordered** scanner | External scanner state is keyed by name and position together — a rename starts it from empty, a reorder points it at another scanner's directory. **Appending at the end is allowed.** |
| a changed `action_type` under a stable `name` | The new action class would inherit the previous one's execution history. |

Only for those does applying mean **closing the strategy and redeploying it on a fresh wallet** —
which market-exits its open positions and returns the funds to main. That is the procedure below, and
it is a money conversation before it is a command:

1. **Confirm the edited package is on disk** in the durable root (`/data/workspace/strategies/<id>/…`),
   authored via `senpi-strategy-author`, not hand-guessed here.
2. **Prove the edit still RUNS before you close anything** — `openclaw senpi validate <recipe-dir>`
   must return `PASS`. An edit is exactly when a scanner breaks, and you are about to flatten a live
   book to install it. **Point it at the dir holding that instance's `runtime.yaml`** — the package
   root for a flat package (no `instances:` list), the arm's own dir (`<package-dir>/<arm>`) once
   `strategy.yaml` lists instances, which every catalog package does. Validation runs against one
   runtime, so a root that lists instances and holds no recipe of its own refuses
   `[E_VALIDATE_NO_RECIPE]` and lists the instances to pick from. That is also the only way the edited
   arm gets its own proof — without one the `create` below refuses.
3. **Get explicit consent, in these words**: closing market-exits any open position, funds return to
   the main wallet, the strategy redeploys on a NEW wallet, and a custom ratchet/stop ladder on the old
   positions does **not** carry over — re-apply it afterwards if wanted. Never present this as a
   re-tune.
4. **Then `close.py <id>`**, wait for `closed`, and `create` the edited package with a budget the user
   confirmed. Any balance above that budget stays in main rather than following the strategy across.
   **On a multi-instance package, do it one sleeve at a time**: `close.py <id> --instance <arm>` closes
   only that arm, and the following `create` **adopts the siblings that are still live** and creates a
   fresh wallet for the closed one alone — so the others keep running and keep their positions.
   **`--budget` is still the WHOLE package's budget, split by `funding_share` — it is not the arm's
   amount.** Only the instances needing a wallet are funded, but each still gets `--budget × its own
   declared share`, so redeploying a `funding_share: 0.3` arm with `--budget 300` funds it **$90**, not
   $300. Size it as *the amount you want in that arm ÷ that arm's share* (300 ÷ 0.3 → `--budget 1000`),
   and **say the resulting wallet figure to the user, not the `--budget` number**, when you take
   consent for the redeploy. If a budget warn printed its own re-run `--budget`, **that figure is a
   floor, not the size to consent to** — the rule, and why, live with the code that emits it:
   `[W_BUDGET_BELOW_STRATEGY_MIN]` in [`refusal-playbook.md`](refusal-playbook.md).

**NEVER, when applying an edit:**

- close a live strategy to apply a change `senpi update` would have made in place. Flattening a book
  costs the user spread, fees and their open P&L; reach for the close/redeploy path only for the
  three changes in the table above.
- hand-render a `runtime.yaml` or run raw `openclaw senpi runtime create` on a hand-built file — the
  `./scanners` "NO ENTRY SCANNERS" trap, and it skips the funds preflight, the attribution and the
  verified tick.
- raw `strategy_create_custom_strategy` — that is a naked wallet with no runtime.
- claim "upgraded / live" before the deploy report says `overall: live`.
