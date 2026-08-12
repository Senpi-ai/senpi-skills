# Editing a strategy that is already LIVE

The trigger: the user asks to re-score / re-scan / re-tune something that is already deployed and
holding positions. `senpi-strategy-ops/SKILL.md` routes here; this file is the procedure.

**The edit itself — `scoring.py` / `scan.py` / `runtime.yaml`, leverage, sizing, DSL — is authored in
`senpi-strategy-author`** (the only skill that knows the scanner / yaml / DSL schema). Ops APPLIES the
edited package; it does not write it.

**There is no in-place scanner reload, and no single verb for this today.** Re-running `create` will
NOT apply the edit: the deploy verb is idempotent, so it adopts the wallet that already exists and
leaves the deployed scanner as it is. Applying an edit means **closing the strategy and redeploying it
on a fresh wallet** — which market-exits its open positions and returns the funds to main.

**So this is a money conversation before it is a command:**

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

- hand-render a `runtime.yaml` or run raw `openclaw senpi runtime create` on a hand-built file — the
  `./scanners` "NO ENTRY SCANNERS" trap, and it skips the funds preflight, the attribution and the
  verified tick.
- raw `strategy_create_custom_strategy` — that is a naked wallet with no runtime.
- claim "upgraded / live" before the deploy report says `overall: live`.
