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
| `E_FUNDS_SHORT` | `senpi-strategy-ops/scripts/deploy.py` (`create`) → moves to MCP in slice B3 | Accessible balance covers the per-wallet floor for the requested wallet count, but not the requested budget | Offer BOTH: add USDC, or re-run with `--budget ≤ $<b*>` where b* is the computed maximum feasible budget (Σᵢ max($100, b·shareᵢ) ≤ usable — equal to usable only for even shares). The lower-budget clause may ONLY render when `usable ≥ wallets × $100` |
| `E_FUNDS_BELOW_FLOOR` | same | Accessible balance cannot fund the wallet count at the $100/wallet floor — **no valid budget exists** | Deposit path ONLY (`senpi-deposit-withdraw-transfer` skill + amount still needed). NEVER suggest a lower budget |
| `E_STATE_NO_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND backend has zero ACTIVE wallets for the instance | `deploy.py create <id> --budget <usd>` (nothing exists; create is safe) |
| `E_STATE_AMBIGUOUS_WALLETS` | `deploy.py` (`runtime`) | Deploy state lost AND the backend wallet set cannot be safely resolved: >1 candidate ACTIVE wallets, a candidate whose address is unreadable, or ACTIVE wallets that exist but match no instance name — any of these may hide a funded live strategy | Read-only triage (`status.py <id>`), clear ambiguity with the user. NEVER close/recreate to "start clean" |
| `E_RUNTIME_REGISTER_FAILED` | `deploy.py` (`runtime`) | `openclaw senpi runtime create` exited non-zero | Message carries the noise-filtered **tail** of the CLI error (the real cause). No canned hint — the cause steers |
| `E_SCANNER_MOUNT_FAILED` | `senpi-trading-runtime` (`index.ts`, phases `mount` and `install_wire`) | `wireRuntime()` threw while mounting/wiring external scanners (boot or hot-install) | Body carries `cause:` (the exception message). Benign-race vs fatal split lands in later slices; the cause makes them distinguishable NOW |
| `E_SCANNER_LAUNCH_FAILED` | `senpi-trading-runtime` (`index.ts`, phases `launch` and `install_launch`) | Supervised scanner launcher failed to start (boot or hot-install) | Same as mount |

Note on the `E_SCANNER_*` rows: these two codes are **logical identifiers**, not Body
literals — the event Body prefixes are frozen (dashboards match them), so the code
cannot lead the string as rule 3 prescribes. Decision (2026-07-29): carrying the code
as a queryable event attribute is deferred to slice B1 (truthful-status
instrumentation), where these events gain structured health fields anyway.

## Reserved families (later slices — do not reuse for anything else)

- `E_VALIDATE_IMPORT`, `E_VALIDATE_SMOKE` — `senpi validate` failures (slice B2).
- `E_NOT_VALIDATED`, `E_PACKAGE_CHANGED` — runtime install-gate refusals (slice B2).
- `E_FUNDS_*` at the MCP layer (slice B3) keeps the same code meanings as above; the
  MCP becomes the owner and `deploy.py` relays verbatim.
