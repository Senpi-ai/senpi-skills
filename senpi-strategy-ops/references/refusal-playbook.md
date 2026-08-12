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

**No budget is valid.** Help the user deposit
(`senpi-deposit-withdraw-transfer`), then re-run. **Never** suggest a lower budget here.

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

The package hardcodes instrument(s) that are not live on Hyperliquid.
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
