# Senpi agent-facing error-code taxonomy

Stable codes for every refusal/failure an agent can hit on the strategy lifecycle
surfaces. Rules:

1. **One owner per code.** The owning surface renders the message; no other surface
   re-derives it.
2. **Codes are stable once shipped.** Messages may improve; codes never change meaning.
3. **Format:** the code leads the string — `[E_FUNDS_SHORT] …` — so agents (and log
   queries) can match on a fixed prefix. Structured `code` fields arrive with the MCP
   gates (slice B3); until then the prefix IS the contract.
4. **Hints are computed, never templated.** A next-step suggestion may only be rendered
   when its precondition was checked against live state in the same call. A hint that
   can be nonsense for some state is a bug (field case: `--budget ≤ $0`).
5. **Refusals name a non-destructive next step.** Naming only a destructive escape
   (close/recreate/force) teaches the agent to destroy things (field cases: caribou
   raw-recreate, close-to-replace teardown).

## Active codes

| Code | Owner (today) | Condition | Next-step rule |
|---|---|---|---|
| `E_FUNDS_SHORT` | `senpi-trading-runtime` (`senpi deploy`, preflight) → moves to MCP in slice B3 | Accessible balance covers the per-wallet floor for the requested wallet count, but not the requested budget | Offer BOTH: add USDC, or re-run with `--budget ≤ <b*>` (a bare number — the flag is `type=float`, so never `$`/comma-grouped) where b* is the computed maximum feasible budget (Σᵢ max($10, b·shareᵢ) ≤ usable — equal to usable only for even shares). The lower-budget clause may ONLY render when `usable ≥ wallets × $10` |
| `E_FUNDS_BELOW_FLOOR` | same | Accessible balance cannot fund the wallet count at the $10/wallet floor — **no valid budget exists** | Deposit path ONLY (`senpi-deposit-withdraw-transfer` skill + amount still needed). NEVER suggest a lower budget |
| `E_BUDGET_BELOW_STRATEGY_MIN` | `senpi-trading-runtime` (`senpi deploy`, preflight) — **advisory, not a refusal** | One or more wallets THIS DEPLOY FUNDS are allocated less than their own sizing needs (`perWalletMin` from `min_budget.py`, ported to the verb as `src/deploy/min-budget.ts` and parity-tested against it) — those sleeves run degraded, with fewer slots than authored. Deliberately per-wallet, NOT whole-budget-vs-whole-package: on a partially-adopted deploy the budget is split among fewer wallets, and the totals comparison announces shortfalls that do not exist | SOFT warn naming each short wallet and both its numbers; **DEPLOYS anyway** (users size their own budget) — never report it as a refusal or close a wallet over it. The escape is **close-then-redeploy**, never a re-run at a bigger `--budget`: the wallet is already funded, so a re-run only ADOPTS it and reports "the requested amount was NOT added". It is **scoped** — `close.py <id> --instance <name>` for the short sleeves only, since `close.py <id>` tears down the whole package including adopted live wallets. It is emitted ONLY when those sleeves have a live **runtime**, because `--instance` resolves a sleeve through its runtime and hard-exits without one (its error text then reads "omit `--instance` to close all of `<id>`" — the very widening this scoping exists to prevent). A funded wallet with no runtime gets a read-only `status.py <id>` triage pointer instead, and a deploy that created nothing gets no escape at all |
| `E_BUDGET_UNRESOLVED` | same — **advisory, not a refusal** | One or more sleeves expose no resolvable `marginPct` (a vol-parity sleeve publishes risk weights, not slot sizes), so their per-wallet minimum fell back to the bare $10 floor and the computed minimum is a LOWER bound | SOFT warn naming the unresolved sleeves; **DEPLOYS anyway**. Size conservatively — treat the printed minimum as possibly understated, never as verified. When a wallet is ALSO short, the warn names it and `belowMin` is set — the two codes are not mutually exclusive — and it then carries the **same scoped close-then-redeploy escape**, because a note that asserts a concrete shortfall must also name the way out of it |
| `SERR037` (backend) | senpi-hyperliquid-mcp `strategy_create_custom_strategy` | Requested budget is below the platform floor ($10/wallet, env `MINIMUM_STRATEGY_BUDGET`) — the backend rejects the create | The verb's preflight catches this FIRST: the funding plan floors at $10 and halts with `[E_FUNDS_BELOW_FLOOR]` before the create call. If SERR037 still surfaces (a raw MCP path), treat it as `E_FUNDS_BELOW_FLOOR` — deposit path only, never a lower budget |
| `E_STATE_NO_WALLETS` | *(retired — see the Convergence note)* | Deploy state lost AND backend has zero ACTIVE wallets for the instance | `deploy.py create <id> --budget <usd>` (nothing exists; create is safe) |
| `E_STATE_AMBIGUOUS_WALLETS` | `senpi-trading-runtime` (`senpi deploy`, reconcile) | The backend wallet set cannot be safely resolved for an instance: >1 candidate live wallets, or a single candidate whose address is unreadable — either may hide a funded live strategy | Read-only triage (`status.py <id>`), clear ambiguity with the user. NEVER close/recreate to "start clean" |
| `E_RUNTIME_REGISTER_FAILED` | *(retired — see the Convergence note)* | `openclaw senpi runtime create` exited non-zero | Message carries the noise-filtered **tail** of the CLI error (the real cause). No canned hint — the cause steers |
| `E_SCANNER_MOUNT_FAILED` | `senpi-trading-runtime` (`index.ts`, phase `mount`) | `wireRuntime()` threw while mounting external scanners at the boot mount seam | Body carries `cause:` (the exception message). Benign-race vs fatal split lands in later slices; the cause makes them distinguishable NOW |
| `E_SCANNER_LAUNCH_FAILED` | `senpi-trading-runtime` (`index.ts`, phases `launch`, `install_launch`, and `install_wire`) | Supervised scanner launcher (or its hot-install wiring) failed to start (boot or hot-install) | Same as mount |
| `E_SCANNER_TICK_ERROR` | `senpi-trading-runtime` (external-scanner scaffold → runtime `/errors`) | A scanner's `scan()` tick threw; the scaffold caught it, set the tick status to `error`, and posted it to the runtime's `/errors` endpoint | `senpi status` for the failing scanner → read the tick error message → fix the scanner code. NEVER close/recreate the strategy for a scanner error |
| `E_SCANNER_TICK_TIMEOUT` | same | A scanner tick exceeded its wall-clock budget | Same as `E_SCANNER_TICK_ERROR` |
| `E_SCANNER_CRASH_LOOP` | `senpi-trading-runtime` (scanner process supervisor) | The supervisor degraded a scanner after repeated rapid exits; restarts CONTINUE at capped backoff — the scanner is degraded, not stopped (retry-at-cap decision, 2026-07-29) | Same as `E_SCANNER_TICK_ERROR` |
| `E_SCANNER_PATH_UNRESOLVED` | `senpi-trading-runtime` (`senpi.installRuntime`) | A content-install's YAML names a relative scanner `path` and no source dir was provided — resolving against the gateway cwd would mount nothing (M226926/M279357 DOA class). Nothing installed | Install from the file (`-p <runtime.yaml>`) or pass `--runtime-yaml-dir <dir>`. `senpi deploy` always provides the dir — prefer it |
| `E_DEPLOY_IN_PROGRESS` | `senpi-trading-runtime` (`senpi.deploy.start`) | A second deploy was started while one is running — deploys are single-flight because concurrent funding preflights read one shared balance and can jointly overdraw. Nothing was started | Watch the running job: `senpi deploy status`. There is no cancel: a wedged job frees its own slot at the deploy deadline, and undeploying a strategy is closing it (`close.py <id>`) |

2026-08-05 (D1 Convergence): `deploy.py` is now a thin wrapper over `senpi deploy`, so the
funding/state codes are **rendered by the verb** and relayed verbatim — the owner columns above move
with them. `deploy.py` re-derives no message, no number and no code.

Two codes have **no live producer** after Convergence and are marked *retired*: they belonged to the
fat script's lost-state machinery, which went away with the `.deploy-state.json` sidecar
(`E_STATE_NO_WALLETS` — there is no lost state to recover from; the verb reconciles against the
backend and creates a wallet) and with its shell-out to `runtime create` (`E_RUNTIME_REGISTER_FAILED`
— an install failure is now a `failed` install step carrying the runtime's own error). Per rule 2 the
codes are **not reused for anything else**; the rows stay so an old transcript still resolves.

The two `E_BUDGET_*` rows are the only **advisory** codes in this table: they carry the `[E_…]`
prefix so they are greppable, but the deploy **proceeded**. An agent that reads them as a refusal —
and closes a wallet, or reports the deploy failed — has misread them. They ride the report as
`minBudget` / `minWalletCount` / `belowMin` / `minBudgetNote` / `minBudgetUnresolved` (and print as
`calculated minimum:` / `warn:` lines under `senpi deploy status`), never as a `refused` step.

Exactly one of the two codes LEADS the note, and `E_BUDGET_UNRESOLVED` wins when both apply (an
unresolved minimum is a lower bound, so "you are above it" would be the more misleading thing to
say) — but `belowMin` is a fact about the WALLETS, not about which code led, so it is set whenever
one of them is short, under either code. Do not read the absence of
`E_BUDGET_BELOW_STRATEGY_MIN` as "the budget was sufficient"; read `belowMin`.

`minBudget` / `minWalletCount` are **context, not the claim**: they answer "what would deploying
this whole package fresh cost", which is a different question from "did the funding plan give every
wallet enough" the moment any sleeve was adopted. `belowMin` answers the second one — as a claim
about the PLAN, so it stays true on a deploy that then failed before creating anything (the note
says "would have funded" there; the flag keeps the population).

**One report, one teardown instruction.** Where `[E_ROLLBACK_INCOMPLETE]` appears it OWNS the
remediation — the budget warn defers to it by name rather than emitting a second, differently-scoped
close beside it. And `E_ROLLBACK_INCOMPLETE`'s own instruction is a direct `strategy_close` on the
stranded address (that wallet has no runtime, so `--instance` cannot reach it); it offers the
package-wide `close.py <id>` only when nothing else in the package is live, and otherwise names the
live sleeves that command would take down with it.

Note on the `E_SCANNER_*` rows: `E_SCANNER_PATH_UNRESOLVED` is a real refusal string and
leads its message per rule 3. The other five codes are **logical identifiers**, not Body
literals, and are queryable via the `senpi.error.code` **event attribute** (landed in
slice B1, truthful-status instrumentation):

- `E_SCANNER_MOUNT_FAILED` / `E_SCANNER_LAUNCH_FAILED` keep their **frozen** event Body
  prefixes (dashboards match them, so the code cannot lead the string as rule 3
  prescribes); B1 adds the `senpi.error.code` attribute alongside, leaving the body
  prefix and their logical-identifier status unchanged.
- `E_SCANNER_TICK_ERROR` / `E_SCANNER_TICK_TIMEOUT` / `E_SCANNER_CRASH_LOOP` surface
  **purely** as the `senpi.error.code` event attribute — no Body prefix at all (event
  bodies are frozen for dashboard compatibility). Per the 2026-07-29 retry-at-cap
  decision a crash-looping scanner stays degraded-but-restarting, never stopped — so
  the next step is always fix-the-scanner, never close/recreate.

## Reserved families (later slices — do not reuse for anything else)

- `E_VALIDATE_IMPORT`, `E_VALIDATE_SMOKE` — `senpi validate` failures (slice B2).
- `E_NOT_VALIDATED`, `E_PACKAGE_CHANGED` — runtime install-gate refusals (slice B2).
- `E_FUNDS_*` at the MCP layer (slice B3) keeps the same code meanings as above; the
  MCP becomes the owner and `deploy.py` relays verbatim.
