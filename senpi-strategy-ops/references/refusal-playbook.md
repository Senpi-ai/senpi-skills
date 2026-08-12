# Refusal and warn playbook

Read this when a specific code fires and you need more than the message gave you.

**The message is the source of truth, not this file.** Every refusal and warn is rendered by the
runtime against the state it actually read — `buildBudgetEscape` (`src/deploy/orchestrator.ts:810`)
picks a scoped `close.py --instance`, a read-only triage pointer, or **no command at all**, per
report. Where this file and a rendered message differ, the message wins and this file is stale.

## Budget warnings (`W_`)

**The `W_` prefix means WARNING — the deploy went through.** Every `E_` code stops something; a `W_`
code never does. The four budget codes below are the `W_` ones. They ride a
`live` report as `minBudget` / `minWalletCount` / `belowMin` / `minBudgetNote` / `minBudgetUnresolved`
/ `partialFundNote` (printed as `calculated minimum:` and `warn:` lines by `deploy status`), and they
persist on the snapshot — a later `deploy status` re-renders the same warn. The first two judge the
funding **plan**; the last two judge what the backend actually **landed** (`[W_BUDGET_PARTIAL_FUND]`
when the figure read short, `[W_BUDGET_FUNDED_UNREADABLE]` when no figure could be read at all — the
whole point of the second is that it is never conflated with the first). A report can carry both
kinds.
**Never report a deploy as failed, and never close a wallet, because of one:**
- **`[W_BUDGET_BELOW_STRATEGY_MIN]`** — one or more wallets **this deploy funded** got less than their
  own sizing needs (the warn names each one and both numbers: `scalp $12.00 (needs $13.50)`). The
  strategy **deployed and is running**, just **degraded** — fewer slots than the author designed, each
  position a larger share of its wallet. Tell the user plainly, and offer the authored size as a
  *choice*: a smaller book is legitimate, not a mistake to undo.
  **Follow the warn's own escape verbatim — do not improvise one, and do not widen it.** It is
  close-then-redeploy (deploy never adds funds to an existing wallet, so a re-run only adopts it) and
  it is **scoped**: `close.py <id> --instance <name>` when only some sleeves are short. Never widen
  that to `close.py <id>` — the other sleeves may be adopted, live and funded, and this warn is not
  about them. Some reports deliberately carry **no** command: a funded wallet with no runtime cannot
  be closed by instance at all, so the warn points at read-only `status.py <id>` triage instead —
  that is the correct answer there, not a gap for you to fill with a package-wide close. A
  `funding_share: 0` sleeve gets no command either: NO budget can lift it above the $10 floor, so
  closing and re-deploying would only reproduce the identical warn — the warn names the real fix (a
  share in strategy.yaml); relay that, never close the sleeve over it. And where
  `[E_ROLLBACK_INCOMPLETE]` is on the report, it owns the cleanup; do that first.
  `minBudget` in the report is **context** ("the whole package fresh needs $30 across 2 wallets"), not
  the thing that was violated — a partially-adopted deploy splits the budget among fewer wallets.
- **`[W_BUDGET_UNRESOLVED]`** — one or more sleeves publish risk weights rather than slot sizes, so the
  minimum could not be computed and the printed figure is a **lower bound**. The deploy ran. Say the
  number **may be understated** and size conservatively; do not quote it as verified. If a wallet is
  ALSO short the warn names it and `belowMin` is set — the two codes are **not mutually exclusive**, so
  never read "no `[W_BUDGET_BELOW_STRATEGY_MIN]`" as "the budget was enough". Read `belowMin`. That case
  carries the same scoped escape; follow it verbatim too.
- **`[W_BUDGET_PARTIAL_FUND]`** — the backend funded a wallet for materially less than 90% of what the
  create asked for (a partial bridge leg). The warn names each wallet once with both numbers, the
  percentage and the shortfall (`main (0x…) funded $60.00 of requested $500.00 (12%, short $440.00)`).
  The strategy **is live and trading** — at the size that landed. **Quote the `funded` figure, never
  the requested one**; saying "$500 deployed" over a $60 book is the failure this code exists to stop.
  **Do not close the strategy over the shortfall** (that is a rule about this code; it never overrides
  a close another line on the report instructs — see `[E_ROLLBACK_INCOMPLETE]`). Those figures
  are **as at deploy time** and the note re-renders on every later `deploy status`, so **re-read the
  current funding first**: `status.py <id>`, then add whatever difference it still shows via the
  `senpi-deposit-withdraw-transfer` skill (deploy never adds funds to an existing wallet, so
  re-running at the same `--budget` only adopts it). Acting on the stale figure tops up twice.
  Close-and-redeploy is the destructive alternative and this warn carries **no** close command on
  purpose — agree the scope with the user off that same read. On a `failed`/`pending` report the warn
  drops its "nothing is broken" line: read the failed step first, it says what state the deploy left.
  Where `[E_ROLLBACK_INCOMPLETE]` is on the report this warn is **silent for that wallet** — the
  rollback line owns it, and its reclaim is what you do.
- **`[W_BUDGET_FUNDED_UNREADABLE]`** — the backend reported **no readable funded amount** for a wallet
  this deploy created. Not the same as a $0 wallet, and never to be reported as one: the amount that
  landed is **UNKNOWN**. No percentage, no shortfall, **no top-up** — the wallet may already hold the
  full ask, and adding the requested amount would double the deployment. Tell the user the figure is
  unreadable, name what was requested, and ask **them** to verify what actually landed (`status.py
  <id>`, or inspecting the wallet) before any money action.
