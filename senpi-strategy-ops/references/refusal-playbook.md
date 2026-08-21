# Refusal and warn playbook

Read this when a specific code fires and you need more than the message gave you.

**The message is the source of truth, not this file.** Every refusal and warn is rendered by the
runtime against the state it actually read — `buildBudgetEscape` (`src/deploy/orchestrator.ts:810`)
picks a scoped `close.py --instance`, a read-only triage pointer, or **no command at all**, per
report. Where this file and a rendered message differ, the message wins and this file is stale.

**And it is version-scoped.** The depth here is the **converged `openclaw senpi deploy` verb's** —
its codes, its branches, its escapes. A box on an older `@senpi-ai/runtime` renders fewer of them and
some of them differently, which does not make this file a second opinion; it makes it the wrong one.
Read what the box printed.

## Refusals (`E_`)

Each of these **stopped something**. The refusal names its own next step; the depth below is for when
the message left you unsure what it meant, not for composing an answer without it.

### `[E_FUNDS_SHORT]`

The balance covers the $10/wallet floor but not the requested budget. Fund/free
USDC **or** confirm a lower amount with the user (the refusal names the exact `--budget <X>` it can
fund), then re-run. **Never lower the budget without asking.**

### `[E_FUNDS_BELOW_FLOOR]`

**No budget is valid.** Depositing is the only way forward, so **show the funding card** —
`show_widget` with `widget_type: "fund_user_wallet"` — in the same reply that reports the refusal.
Don't name a skill at the user or make them ask for it; the card is the action. Say what is short,
show the card, and re-run once they have funded. **Never** suggest a lower budget here.
(Rails and edge cases: `senpi-deposit-withdraw-transfer`.)

### `[E_STATE_AMBIGUOUS_WALLETS]`

\>1 live wallet matches an instance and one may be a funded **live**
strategy. Triage **read-only** (`python3 status.py <id>` maps each wallet to its runtime/strategy),
resolve WITH THE USER which wallet is live, then re-run. **Never `close.py`/recreate to "start clean"** —
that can tear down a funded live strategy.

### `[E_INSTANCE_BINDING_UNKNOWN]`

Live wallets carry this package's `skillName` stamp and **none of
them answers for the instance being deployed**. Refused **pre-money — nothing was created, no money
moved.** The refusal lists every such wallet (address, funded amount, status, creation date, the name
the record carries). **Read them before deciding anything** — `python3 status.py <id>` — and note what
the refusal says it did **not** read: whether those wallets hold open positions, and whether any runtime
still watches them. **Which wallet is which, and whether it is still wanted, is the USER's call** —
relay the list and ask; deploy will not pick one for them. **Re-running changes nothing** (it binds by
the same two routes that just failed), and this is **not** a funding problem — **never re-run with a
bigger `--budget`**. Only once the user confirms the wallets are unwanted, `close.py <id>` returns
their funds — and it tears down the **WHOLE** package, every sleeve and runtime in it — then re-run.

### `[E_WALLET_OWNED_BY_OTHER_PACKAGE]`

Every live strategy answering to the name this instance
derives carries **another package's** `skillName` stamp. Refused **pre-money — nothing was created, no
money moved**; deploy binds by name, so adopting one would have put this package's runtime on another
package's funded wallet and open positions (and a second exit engine on one margin account). **Not a
retry, ever** — the name is derived from the package id (+ instance name), so it comes out identical
every time. Two routes, neither a money move, both named by the refusal: **rename** — give this package
a name of its own by editing `id:` (or the instance's `name:`) in `strategy.yaml`, then re-run, and
deploy creates its own wallet; or, if one of those wallets **is** this package under an id it no longer
has — and here the refusal is explicit that the obvious edit does **not** work: **setting `id:` back to
the stamp does NOT reach that wallet**, because the name deploy looks for is derived FROM the id, so the
edit moves the name too and the next run refuses again having bound nothing. The name came from the id
**and** the sleeve layout together; only restoring **both** as they were would bind it, and whether this
package should go back to that shape is the **USER's** call — a metadata read cannot settle it. Read
them first — `python3 status.py
'<stamp>'`, per stamp the refusal names. The refusal read metadata only: what those wallets hold, and
whether anything is watching them, was **not** read. **Never `close.py` a wallet stamped for another
package** to clear the collision — that is a different strategy's funds. Whose they are is the USER's call.

### `[INVALID_REQUEST]` — an instance declares no DSL exit block

The instance ships no `exit.dsl_preset` and no `exit.engine: dsl`, so every position it opened would
run with **no stop loss and no trailing floor**. Refused **before any wallet exists**, on every route
into a deploy: the verb's reconcile gate, the gateway's synchronous start gate, and — earlier and
cheaper — the two skills-side validators (`deploy.py validate`, and the author's
`validate_strategy.py`). Nothing was created; nothing was started.

The refusal **names the offending instances and computes the re-check block for them** — one
`openclaw senpi validate '<that instance's dir>'` per offender, then the package-wide
`python3 senpi-strategy-ops/scripts/deploy.py validate '<package dir>'`. **Run the block it printed,
in that order, and do not substitute the python line for the runtime one**: the refusal says so
itself — only `openclaw senpi validate` records the proof the deploy requires, and the python route
writes none. Add the exit block to the instance `runtime.yaml`, re-check, then re-run.

`[INVALID_REQUEST]` is a **family**, not one condition — see the taxonomy row. Fix what the message
names and nothing else; a shape defect no `--budget` can clear sits beside members that a valid
`--budget` is precisely the fix for.

### `[E_VALIDATE_NO_RECIPE]`

`openclaw senpi validate` was pointed at a directory holding no recipe of its own — normally a
package root whose `strategy.yaml` lists instance directories. Validation runs against **one**
runtime, so the root is not a target. Not a package defect and not a money path: the refusal lists
the instances, so point it at the dir holding that instance's `runtime.yaml` and run it once per
instance — which is also how each instance gets its own proof.

### `[E_DEPLOY_IN_PROGRESS]`

Another deploy is running. Watch it (`deploy status`). There is nothing
to cancel; a wedged job times out and frees the slot on its own.

### `[E_ROLLBACK_INCOMPLETE]`

A wallet this deploy created and funded had its install fail, and the
automatic close did not complete. **The wallet is live, funded and unwatched.** The refusal names the
wallet, the amount and the command to reclaim it — **follow that command, do not substitute one**.
It is a direct MCP `strategy_close` on that address, because a wallet with no runtime cannot be
reached by `close.py --instance`; the refusal offers the package-wide `close.py <id>` only when
nothing else in the package is live, and otherwise names the live sleeves that command would take
down with it. Tell the user either way. Never leave this one unreported.

### `[E_INSTALL_INDETERMINATE]`

Rides a **`failed` report (exit 3)**, and it is the one failure that does not tell you what happened.
The deploy stopped *waiting* on an install it cannot cancel, so the install may have completed after
the report was written.

**The report forks on two facts, and it states both. Read which arm you are on before anything
else** — they are not interchangeable, and one of them permits no action at all:

- **The install is STILL RUNNING** (the report says so in those words, and that the wallet is fenced
  until it settles). **Change nothing and close nothing.** The install can still write its registry
  row and start a runtime seconds from now; a close here lands on a strategy the install is binding
  and leaves a live runtime trading a closed strategy. Re-read shortly — `openclaw senpi deploy
  status`, then `openclaw senpi runtime list` — and act on the settled state. A re-run that reaches
  this wallet is refused (`[E_WALLET_INSTALL_IN_FLIGHT]`) rather than installing over it, so it
  cannot settle this for you. **No close appears in this arm of the report, and there is none to
  improvise.**
- **The install is over** (the report says the fence has cleared, so what exists now is what it
  left). Only here does what you read decide anything — and only a wallet **this deploy created**
  can carry a close at all. A wallet this deploy **adopted** predates it entirely: the report names
  **no** close command for it on any arm, on purpose, because those funds are not this deploy's to
  reclaim.

**A registry ROW is not proof the install landed.** The row is written *before* the runtime starts,
so the wedge this code reports for produces exactly a row with nothing running behind it —
`openclaw senpi runtime list` renders it `stopped`. Read the list for the **state**, not for the
presence of a line:

- **The runtime is RUNNING on that wallet** → the install landed. Re-run the same deploy command; it
  adopts the wallet, skips the install and observes a tick.
- **A row exists but is NOT running** → neither landed nor abandoned. **Do not close that wallet**:
  the next gateway restart will try to start that runtime, so a close now buys the same live-runtime-
  on-a-closed-strategy outcome later. Read it first (`openclaw senpi status -r <runtime-id>`) and
  decide with the user.
- **NO runtime names that wallet at all**, on a **created** wallet, with the report saying the fence
  has cleared → nothing is watching the funds: reclaim them with the MCP `strategy_close` the report
  names, on the address it names, and mind the package-wide caveat it computed — other sleeves of
  this package may be live.

**Never close on the strength of the exit code alone, never close while the report says the install
is still running, and never substitute a close the report did not name.** That is the one action
this code exists to stop you taking blind.

### `[E_WALLET_INSTALL_IN_FLIGHT]`

An install on that wallet is **running right now** inside the gateway — usually a previous deploy
whose wait expired while its install kept going. Nothing was installed, nothing was deleted, and the
condition **normally** clears itself when that install finishes.

**Past the deploy's own 120s install bound, treat it as possibly wedged rather than merely slow.**
If it is wedged, the fence on this wallet will not clear until the gateway process restarts — that is
the condition that would clear it, reported here so you know what you are waiting on, not an action
to take yourself.

**Delete nothing.** `openclaw senpi runtime delete` removes the row the in-flight install is about to
bind, and `close.py` would tear down a wallet that is seconds from being watched. Read instead —
`openclaw senpi deploy status`, then `openclaw senpi runtime list` — and once a row names the wallet,
re-run the same deploy command. A re-run adopts the wallet; it does not fund a second one.

### A live `<id>` strategy that is PAUSED (or mid-teardown)

The verb refuses immediately with the
real status quoted; it does **not** wait, because a paused strategy never becomes ACTIVE on its own.
Resume it and re-run, or `close.py <id>` first if you meant to start over. **Never fund a second
wallet beside it.**

### `[E_VALIDATE_UNRESOLVABLE_SCANNER_PATH]`

An install could not resolve a relative scanner path.
The verb always passes the instance directory — and so does a hand-run `runtime create -p <file>`,
which derives it from the file. Only a **content** install (`runtime create -c <yaml>` with no
`--runtime-yaml-dir`) has nothing to resolve against. Install from the file, or use the verb.

### `[E_UNIVERSE_NOT_LIVE]`

The package hardcodes instrument(s) it intends to TRADE that are not live on Hyperliquid. (An
exclusion list — `excludeAssets`, `deny*`/`skip*` — never triggers this: those names are what the
strategy refuses to trade, and are not required to exist on the venue.)
**Nothing was created BY THAT RUN** — the gate read the instrument list and stopped before reading
this package's own live state, so whatever the package already had is untouched and this gate
cannot see it. Relay it that scoped way (never "there is no wallet"): on a resume the package may
already own a funded, live wallet, and the unscoped sentence is what funds a second one beside it.
The one refusal names every dead instrument, both forms it checked
(`T` and `xyz:T`), and the exact file + key path each appears in. Fix each named instrument in the
package (the **senpi-strategy-author** edit path), re-check read-only with
`python3 senpi-strategy-ops/scripts/validate_universe.py <dir>`, then re-run. A dead name never
errors at runtime — the scan skips it and the strategy silently trades nothing — so **never
"deploy anyway"**. If the step instead reports that the live instrument list **could not be read**,
nothing is claimed dead, nothing was created by that run and nothing the package already had was
touched. That one still lands as **`refused` (exit `2`)** like any other gate saying no — the
step is recorded `failed`, but the halt flag decides `overall`. What separates it is the message:
the detail carries **no code at all**, and that absence is the tell — nothing was named dead
because nothing could be read. It is an MCP outage, not a package bug: retry once the server is
reachable, and never go hunting an instrument the refusal never named.

### `[E_VALIDATE_NO_PROOF]` / `[E_VALIDATE_CONTENT_CHANGED]` / `[E_VALIDATE_RUNTIME_VERSION_CHANGED]`

Deploy will not fund a package it cannot prove ever ran. **Refused pre-money — nothing was created
BY THAT RUN.** Say it exactly that scoped way: this gate reads the package's own files and stops
before any live read, so on a resume the package may already own a funded wallet the gate cannot
see. "Nothing exists" is the sentence that gets a second wallet funded beside a live one.
The proof is the `.senpi-proof.json` a passing `openclaw senpi validate <recipe-dir>` writes
(see `SKILL.md`'s preflight step). **Which of the three it is decides the answer — read the code, not the
prose:**
`NO_PROOF` = nothing here has been observed to run; validate the directory the refusal names.
`CONTENT_CHANGED` = the files differ from the ones that passed, and it names them; **look at the
edit before re-proving it** — either restore the proven content or validate what is there now.
`RUNTIME_VERSION_CHANGED` = the package is untouched and only the engine under it moved (the
runtime self-updates in place, so this hits packages nobody has touched). **`deploy.py
create|runtime` repairs that one for you**: it re-runs `senpi validate` once, re-runs the deploy,
and does this for this reason only. If that re-validation does not PASS it prints validate's own
findings and does **not** re-run the deploy — fix those, then re-run.
That repair is the one **automatic** path that can outrun the ~180s tool timeout (re-validate +
a second poll); an explicit `--max-wait` above 150 is the other, and you asked for that one.
**If the call is killed mid-repair, nothing was created** — read `openclaw senpi deploy status`
and do NOT re-run `create`; the repair note is printed to stderr before validation starts, so a
killed call still says what it was doing. Deploy needs **every**
instance proven and stops at the first that is not, so on a multi-instance package expect to be
sent back for the next sleeve; validating every instance dir up front avoids the round trip.

## Budget warnings (`W_`)

**The `W_` prefix means WARNING — it blocked nothing.** Every `E_` code stops something; a `W_`
code never does. A warn rides `failed` and `pending` reports too, so `overall` — not the presence of
a `W_` — is what says whether the deploy went through. The four budget codes below are the `W_` ones. They ride a
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
  **The `--budget` figure inside that escape is a FLOOR, not the size to take consent for.** The verb
  computes it as the smallest budget at which every wallet the re-run will fund clears its own
  *minimum* — `max(perWalletMin ÷ share)` across that set, or the whole-package `minBudget` when
  every sleeve is being closed. Consent to that number and the sleeve redeploys **at the floor**,
  not at the size the user agreed to. Size from **want ÷ that arm's share** instead, and if the
  warn's figure is larger, use the larger one. It is the same inversion the verb just did: a
  `funding_share` is a **divisor**, never a multiplier.
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
  current funding first**: `status.py <id>`, then add whatever difference it still shows (deploy never
  adds funds to an existing wallet, so re-running at the same `--budget` only adopts it). Acting on the
  stale figure tops up twice.
  **Adding that difference is two steps, in this order**, and saying so is the point — money reaches a
  strategy through the embedded wallet, never directly:
  1. If the embedded wallet cannot cover the difference, **show the funding card** (`show_widget`,
     `widget_type: "fund_user_wallet"`) so the user can fill it. If it already covers the difference,
     skip this step entirely.
  2. Move the difference into the strategy with **`strategy_top_up`**.
  **The card alone does not fund the strategy.** Never show it and imply the strategy is topped up —
  that is the same mistake as pointing a deposit at a strategy address.
  (Rails: `senpi-deposit-withdraw-transfer`.)
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
