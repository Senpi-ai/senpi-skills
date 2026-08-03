# Senpi agent-facing error-code taxonomy

Stable codes for every refusal/failure an agent can hit on the strategy lifecycle
surfaces. Rules:

1. **One owner per code.** The owning surface renders the message; no other surface
   re-derives it.
2. **Codes are stable once shipped.** Messages may improve; codes never change meaning.
3. **Format:** the code leads the string — `[E_FUNDS_SHORT] …` — so agents (and log
   queries) can match on a fixed prefix. Structured `code` fields arrive with the B3
   funding gates (enforced in the platform backend, relayed through the MCP); until
   then the prefix IS the contract.
4. **Hints are computed, never templated.** A next-step suggestion may only be rendered
   when its precondition was checked against live state in the same call. A hint that
   can be nonsense for some state is a bug (field case: `--budget ≤ $0`).
5. **Refusals name a non-destructive next step.** Naming only a destructive escape
   (close/recreate/force) teaches the agent to destroy things (field cases: caribou
   raw-recreate, close-to-replace teardown).

## Active codes

| Code | Owner (today) | Condition | Next-step rule |
|---|---|---|---|
| `E_FUNDS_SHORT` | `senpi-strategy-ops/scripts/deploy.py` (`create`) — B3 hard-gate enforcement delegated to the platform backend (2026-08-03); deploy.py stays as the stricter advisory preflight in front (fee buffer, multi-wallet split) and relays backend refusals verbatim | Accessible balance covers the per-wallet floor for the requested wallet count, but not the requested budget | Offer BOTH: add USDC, or re-run with `--budget ≤ <b*>` (a bare number — the flag is `type=float`, so never `$`/comma-grouped) where b* is the computed maximum feasible budget (Σᵢ max($100, b·shareᵢ) ≤ usable — equal to usable only for even shares). The lower-budget clause may ONLY render when `usable ≥ wallets × $100`. At the backend/MCP layer (single amount per call) the lower-amount hint is `≤ accessible` and may only render when `accessible ≥ floor` |
| `E_FUNDS_BELOW_FLOOR` | same | Accessible balance cannot fund the wallet count at the $100/wallet floor — **no valid budget exists** | Deposit path ONLY (`senpi-deposit-withdraw-transfer` skill + amount still needed). NEVER suggest a lower budget |
| `E_STATE_NO_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND backend has zero ACTIVE wallets for the instance | `deploy.py create <id> --budget <usd>` (nothing exists; create is safe) |
| `E_STATE_AMBIGUOUS_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND the backend wallet set cannot be safely resolved: >1 candidate ACTIVE wallets, a candidate whose address is unreadable, or ACTIVE wallets that exist but match no instance name — any of these may hide a funded live strategy | Read-only triage (`status.py <id>`), clear ambiguity with the user. NEVER close/recreate to "start clean" |
| `E_RUNTIME_REGISTER_FAILED` | `deploy.py` (`runtime`) | `openclaw senpi runtime create` exited non-zero | Message carries the noise-filtered **tail** of the CLI error (the real cause). No canned hint — the cause steers |
| `E_SCANNER_MOUNT_FAILED` | `senpi-trading-runtime` (`index.ts`, phase `mount`) | `wireRuntime()` threw while mounting external scanners at the boot mount seam | Body carries `cause:` (the exception message). Benign-race vs fatal split lands in later slices; the cause makes them distinguishable NOW |
| `E_SCANNER_LAUNCH_FAILED` | `senpi-trading-runtime` (`index.ts`, phases `launch`, `install_launch`, and `install_wire`) | Supervised scanner launcher (or its hot-install wiring) failed to start (boot or hot-install) | Same as mount |
| `E_SCANNER_TICK_ERROR` | `senpi-trading-runtime` (external-scanner scaffold → runtime `/errors`) | A scanner's `scan()` tick threw; the scaffold caught it, set the tick status to `error`, and posted it to the runtime's `/errors` endpoint | `senpi status` for the failing scanner → read the tick error message → fix the scanner code. NEVER close/recreate the strategy for a scanner error |
| `E_SCANNER_TICK_TIMEOUT` | same | A scanner tick exceeded its wall-clock budget | Same as `E_SCANNER_TICK_ERROR` |
| `E_SCANNER_CRASH_LOOP` | `senpi-trading-runtime` (scanner process supervisor) | The supervisor degraded a scanner after repeated rapid exits; restarts CONTINUE at capped backoff — the scanner is degraded, not stopped (retry-at-cap decision, 2026-07-29) | Same as `E_SCANNER_TICK_ERROR` |

Note on the `E_SCANNER_*` rows: all five codes are **logical identifiers**, not Body
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
- `E_FUNDS_*` hard-gate enforcement (slice B3 — delegated 2026-08-03 to the platform
  backend service that owns strategy/wallet creation, instead of the MCP) keeps the
  same code meanings as above. The backend refuses **pre-mutation** with a structured
  payload (`code`, `requested`, `accessible`, `floor`, `short_by` — `short_by` rounded
  UP to cents so a deposit hint always clears the gap); the MCP renders the teaching
  text (deposit path, computed hints) and `deploy.py` relays verbatim. "Accessible" =
  the create waterfall's full fundable balance (HL perps + HL spot USDC + bridgeable
  EVM USDC), matching `deploy.py available_usd` since ops v2.11.0 — never a single
  bucket (the Starling false-refusal), never `total_withdrawable`.
- `E_FUNDS_BELOW_MIN` — reserved for the same B3 backend gate: requested budget/amount
  below the per-strategy minimum ($100 production / $10 dev). Balance-independent
  (fires even when no balance was read), so it precedes the other two — precedence:
  `BELOW_MIN` → `BELOW_FLOOR` → `SHORT`. `deploy.py` never emits it (it floors
  per-wallet amounts at the minimum); direct callers can. Note: the copy-create path's
  below-minimum refusal currently surfaces as `SERR037` through the MCP — a misuse of
  that code (its registry meaning is backend insufficient balance); it becomes
  `E_FUNDS_BELOW_MIN` when the gate ships. These rows move to the Active table when
  the backend gate lands.
