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

**The wrapper — `python3 senpi-strategy-ops/scripts/deploy.py update <pkg> --id <runtime_id> [--apply] [--code-only] [--json]`.**
Same verb, three things around it: the structural preflight `create` runs (a package the deployer would
refuse is refused here too, before the verb is called); the instance dir resolved from `--id` on a
multi-instance package; and a clear stop when the box's runtime has no `update` verb yet — exit `1`,
"the edit is on disk and NOT applied" — instead of a Commander parse error that reads like nothing.
It never deletes or re-creates a runtime: there is no path from this command to a fresh wallet. Its
exit codes are the verb's (`0` planned/applied · `1` failed during apply · `2` refused, nothing changed
· `3` bad invocation). `status.py` prints **✎ running recipe ≠ disk** for a runtime whose rendered
descriptor differs from the package on disk — that is the "did my edit ever land?" check, and its fix
is this command.

`--id` names which runtime; on a multi-instance package each arm is its own runtime, so this is how
you re-tune one sleeve and leave its siblings untouched. `--address <wallet>` works too. Changed only
`scan.py`/`scoring.py`? Add `--code-only` and it refuses if the recipe moved as well.

Always point `update` at the **directory**, as above — never hand it the recipe as bare text. The
proof is a claim about a package on disk, so a recipe with no package behind it has nothing to
verify against and `--apply` refuses it rather than swapping in unvalidated bytes. The proof must
also cover the recipe actually being applied, not merely some recipe sitting in that directory.

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
| an external scanner **renamed**, **moved**, **inserted** or **removed** | External scanner state is keyed by name and by position among the external scanners, so both matter. A rename starts it from empty; moving one — or inserting or removing one **above** it, which shifts every external scanner below — points it at another scanner's directory. **Appending at the end moves nobody and is allowed**, as is moving an internal scanner, which no state directory is keyed on. |
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

# DSL changes — the file and the open positions are two different edits

The runtime **snapshots the exit ladder when a position is created**. Editing `exit.dsl_preset` in
`runtime.yaml` and applying it with `update` therefore reaches every position opened **after** the apply
— and none of the ones open now. Those keep the ladder they were born with until they close, unless each
one is edited on its own with `ratchet_stop_edit`, which runs behind the trade-approval gate: one call, one
approve-or-deny in the app, per position. That gate IS the user's yes. Never pre-empt it, never re-issue a
denied call, never say "done" for a position whose call returned empty.

Both paths exist, so the agent's job is to make the choice visible, not to pick one. On any "change the
tiers / the stop / the DSL" request:

1. **Read both.** The file's `exit.dsl_preset` (what future positions get) and `ratchet_stop_list` for the
   strategy's wallet (what each open position actually carries). They drift the moment the file changes
   after a position opened, and a package can carry three ladders at once.
2. **Show the drift, in a table**: position · ladder it carries · matches the file? · current floor.
3. **Price each open position's change in its own numbers** before asking. Semantics: `trigger_pct` is ROE
   (price move × leverage), `lock_hw_pct` is the share of the high-water ROE kept. Floor ROE = high-water
   ROE × lock; floor price = entry × (1 + floor ROE ÷ leverage ÷ 100) for a long, − for a short — a profit lock sits on the
   winning side of entry (entry 100, 3x, floor 7.6% ROE → 102.53 for a long). Example
   from a live book at 3x: high-water 30.5% ROE; lock 25% → floor 7.6% ROE; lock 55% → floor 16.8% ROE —
   "the new ladder keeps twice as much of this position's peak". Say the retrace room too: room to the
   floor in price = (high-water ROE − floor ROE) ÷ leverage.
4. **Ask exactly this:** *"Apply it to (a) future positions only — the file; (b) the N positions open now
   — one approval each; or (c) both?"* Never assume (a). "Change the config" without more is ambiguous:
   ask, don't guess.
5. **(a)/(c):** edit the file, `senpi validate`, `deploy.py update … --apply`. **(b)/(c):** one
   `ratchet_stop_edit` per position with the new ladder, args per `read_senpi_guide
   senpi://guides/ratchet_stop` (never from memory). An edit **re-evaluates the ladder immediately**
   against the current high-water: a position already past a trigger tiers up on the spot and a new stop
   order is placed — say that before the call, and expect the high-water to refresh.
6. **Verify and report**: `ratchet_stop_list` again — each position's ladder now matches the file (or
   doesn't, by the user's choice), the new floor price and stop order per position, and which positions
   were left on the old ladder because the user said so.

The approval gate catches an open position edited by mistake; only the question catches a file edited alone.
