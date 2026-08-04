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
| `E_FUNDS_SHORT` | `senpi-strategy-ops/scripts/deploy.py` (`create`) → moves to MCP in slice B3 | Accessible balance covers the per-wallet floor for the requested wallet count, but not the requested budget | Offer BOTH: add USDC, or re-run with `--budget ≤ <b*>` (a bare number — the flag is `type=float`, so never `$`/comma-grouped) where b* is the computed maximum feasible budget (Σᵢ max($10, b·shareᵢ) ≤ usable — equal to usable only for even shares). The lower-budget clause may ONLY render when `usable ≥ wallets × $10` |
| `E_FUNDS_BELOW_FLOOR` | same | Accessible balance cannot fund the wallet count at the $10/wallet floor — **no valid budget exists** | Deposit path ONLY (`senpi-deposit-withdraw-transfer` skill + amount still needed). NEVER suggest a lower budget |
| `E_BUDGET_BELOW_STRATEGY_MIN` | `senpi-strategy-ops/scripts/deploy.py` (`create`) — advisory | Budget is at/above the $10×wallets floor but BELOW the template's COMPUTED minimum (`min_budget.py`) — the design runs degraded (fewer slots than authored) | SOFT warn naming the binding sleeve; DEPLOYS anyway (users size their own budget). Fund ≥ the computed minimum for the full design |
| `SERR037` (backend) | senpi-hyperliquid-mcp `strategy_create_custom_strategy` | Requested budget is below the platform floor ($10/wallet, env `MINIMUM_STRATEGY_BUDGET`) — the backend rejects the create | deploy.py catches this FIRST: `plan_funding` floors at $10 and halts with `[E_FUNDS_BELOW_FLOOR]` before the create call. If SERR037 still surfaces (a raw MCP path), treat it as `E_FUNDS_BELOW_FLOOR` — deposit path only, never a lower budget |
| `E_STATE_NO_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND backend has zero ACTIVE wallets for the instance | `deploy.py create <id> --budget <usd>` (nothing exists; create is safe) |
| `E_STATE_AMBIGUOUS_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND the backend wallet set cannot be safely resolved: >1 candidate ACTIVE wallets, a candidate whose address is unreadable, or ACTIVE wallets that exist but match no instance name — any of these may hide a funded live strategy | Read-only triage (`status.py <id>`), clear ambiguity with the user. NEVER close/recreate to "start clean" |
| `E_RUNTIME_REGISTER_FAILED` | `deploy.py` (`runtime`) | `openclaw senpi runtime create` exited non-zero | Message carries the noise-filtered **tail** of the CLI error (the real cause). No canned hint — the cause steers |
| `E_SCANNER_MOUNT_FAILED` | `senpi-trading-runtime` (`index.ts`, phase `mount`) | `wireRuntime()` threw while mounting external scanners at the boot mount seam | Body carries `cause:` (the exception message). Benign-race vs fatal split lands in later slices; the cause makes them distinguishable NOW |
| `E_SCANNER_LAUNCH_FAILED` | `senpi-trading-runtime` (`index.ts`, phases `launch`, `install_launch`, and `install_wire`) | Supervised scanner launcher (or its hot-install wiring) failed to start (boot or hot-install) | Same as mount |
| `E_SCANNER_TICK_ERROR` | `senpi-trading-runtime` (external-scanner scaffold → runtime `/errors`) | A scanner's `scan()` tick threw; the scaffold caught it, set the tick status to `error`, and posted it to the runtime's `/errors` endpoint | `senpi status` for the failing scanner → read the tick error message → fix the scanner code. NEVER close/recreate the strategy for a scanner error |
| `E_SCANNER_TICK_TIMEOUT` | same | A scanner tick exceeded its wall-clock budget | Same as `E_SCANNER_TICK_ERROR` |
| `E_SCANNER_CRASH_LOOP` | `senpi-trading-runtime` (scanner process supervisor) | The supervisor degraded a scanner after repeated rapid exits; restarts CONTINUE at capped backoff — the scanner is degraded, not stopped (retry-at-cap decision, 2026-07-29) | Same as `E_SCANNER_TICK_ERROR` |
| `E_SCANNER_PATH_UNRESOLVED` | `senpi-trading-runtime` (`senpi.installRuntime`) | A content-install's YAML names a relative scanner `path` and no source dir was provided — resolving against the gateway cwd would mount nothing (M226926/M279357 DOA class). Nothing installed | Install from the file (`-p <runtime.yaml>`) or pass `--runtime-yaml-dir <dir>`. `senpi deploy` always provides the dir — prefer it |
| `E_DEPLOY_IN_PROGRESS` | `senpi-trading-runtime` (`senpi.deploy.start`) | A second deploy was started while one is running — deploys are single-flight because concurrent funding preflights read one shared balance and can jointly overdraw. Nothing was started | Watch the running job: `senpi deploy status`. To stop it at the next safe point: `senpi deploy cancel`, then re-run |

2026-08-04 (D1): `senpi deploy` (trading runtime) now also renders `E_FUNDS_SHORT` /
`E_FUNDS_BELOW_FLOOR` / `E_STATE_AMBIGUOUS_WALLETS` under the same computed rules
(surfaced via `senpi deploy status` — the verb runs detached). Owner columns move from
`deploy.py` to the verb at Convergence, when `deploy.py` becomes a wrapper.

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
