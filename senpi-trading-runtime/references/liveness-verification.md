# Liveness Verification

When and how to verify a Senpi runtime is **actually operating**, not merely registered.

Use this whenever you have just installed a runtime, are diagnosing a "nothing is happening" report, or are about to tell the user the system is live. Do not declare success on `openclaw senpi runtime list: running` alone — that field reports process status only, not functional liveness.

This is an **agent-side check**. Run the commands yourself; do not ask the user to confirm "is it working?"

---

## The contract: "running" ≠ "operating"

A runtime can show `status: running` while every component inside it is silent:

- A scanner can be registered but never scheduled.
- The DSL monitor can be wired up while never evaluating a single position.
- An external scanner can be declared in YAML while no producer ever pushes data.
- An action can be wired but never invoked because no signal ever reaches it.

Liveness verification means walking `openclaw senpi state --json` and confirming each component is doing real work, with timestamps and counters as proof.

---

## Self-questions checklist

Run all of these before declaring the runtime live. If any answer is "no" or "unclear," investigate.

1. **Is the target runtime in `runtime list` with `status: running`?**
2. **For every scanner declared in the YAML, has it executed at least once and recently?**
3. **For every external scanner declared in a running runtime, does a paired producer cron exist with the expected name? And conversely, for every producer cron, is there a running runtime declaring the matching external scanner?**

The rest of this document gives the field-level rules to answer (2)–(3) deterministically.

**On DSL.** The DSL monitor exposes counters (`tickSuccessCount`, `lastTickFinishedAt`, etc.) in `state`, but those counters only prove the runtime's *internal* tick loop is moving — they do not prove the end-to-end protective path is working. The path depends on price providers, MCP, and exchange connectivity that live outside the runtime's visibility, so a "healthy" tick can still fail to close a breaching position. Don't use DSL counters as a runtime-wide liveness gate; use `openclaw senpi dsl inspect <ASSET>` when triaging a specific position.

---

## Field-level decision tree

Run `openclaw senpi state --json` once, then walk each component with the rules below. Use field paths against the JSON structure — they are stable, the human-readable summary is not.

### Interval-driven scanners

Path: `state.components.scanners.state.scanners[]` where `scheduleMode === "interval"`.

A scanner is **operating** when **all** hold:

- `enabled === true`
- `lastRunFinishedAt !== null`
- `now − lastRunFinishedAt ≤ 2 × intervalSeconds × 1000` (millisecond comparison)
- `consecutiveErrorCount === 0`
- `lastRunStatus ∈ {"ok", "heartbeat"}`

Failure signatures and what they mean:

| Symptom | Field signature | Likely cause |
|---|---|---|
| Registered but never executed | `runCount === 0` & `lastRunFinishedAt === null` | Scheduler did not pick it up, or scanner threw on first init — read `lastError` |
| Was running, has stopped | `lastRunFinishedAt` older than `2 × intervalSeconds`, `runCount > 0` | Scanner threw and the schedule paused — read `lastError`, `lastErrorAt` |
| Repeatedly failing | `consecutiveErrorCount ≥ 2` | Print `lastError` and remediate |
| Hung mid-run | `inFlight === true` & `lastRunStartedAt` older than `2 × intervalSeconds` | Scanner is stuck — usually upstream provider (MCP, RPC) |

### External scanners (push-driven)

Path: `state.components.scanners.state.scanners[]` where `scheduleMode === "external"`.

A scanner is **operating** when **all** hold:

- `enabled === true`
- `runCount > 0` — the producer has ingested at least once since runtime startup
- `now − lastRunFinishedAt ≤ producer cadence + buffer` — derive cadence from the cron expression of the matching producer; default buffer is 1 × cadence
- `lastRunStatus !== "error"`

**Important:** `openclaw senpi status` reports external scanners as `healthy` even with `runCount === 0` — push-driven scanners cannot distinguish "waiting for first ingest" from "broken pipe" at the health layer. Do not rely on `status` here. Use the field values from `state` directly.

Failure signatures:

| Symptom | Field signature | Likely cause |
|---|---|---|
| Declared but never received | `runCount === 0`, `lastRunFinishedAt === null` | No producer scheduled, or producer cron has wrong name/payload — see Cron ↔ runtime reconciliation below |
| Was receiving, has stopped | `lastRunFinishedAt` older than producer cadence + buffer | Producer cron stopped firing — check `cron list`, tail the producer log |
| Receiving but every payload errors | `lastRunStatus === "error"`, `errorCount` rising | Payload shape mismatch against `external_scanner` `config.fields` |

### Actions

Path: `state.components.actions.state.actions[]`.

Actions are reactive — `runCount === 0` is not always a failure. Disambiguate:

| Field signature | Verdict |
|---|---|
| `runCount === 0` AND every scanner listed in this action's `scanners:` wiring has `runCount > 0` and is emitting signals | **Wiring problem** — signals exist but the action never fires |
| `runCount === 0` AND feeding scanners have not emitted signals yet | **Dormant by design** — not a failure |
| `runCount > 0`, `consecutiveErrorCount === 0`, `lastError === null` | **Operating** |
| `consecutiveErrorCount ≥ 2` or persistent `lastError` | **Failing** — print `lastError` |

For LLM-decision actions also check `totalDecisionsExecute` vs `totalDecisionsNoExecute`. A runaway "no execute" rate is usually a prompt or threshold issue, not a runtime fault — surface it but don't treat it as a liveness failure.

---

## Cron ↔ runtime reconciliation

Every running runtime that declares an external scanner MUST have a matching producer cron, and every producer cron MUST target a running runtime that declares the matching external scanner. This is a wiring contract and the agent should enforce it bidirectionally.

Reconciliation works against the cron job **name**, not the freeform `message` text. The `message` field in `cron list --json` is a natural-language instruction and cannot be parsed reliably for `--address` / `--scanner`.

### Required cron job naming convention

When scheduling a producer with `openclaw cron add`, the `--name` MUST be:

```
senpi-producer-<scanner-name>-<wallet-suffix>
```

Where:
- `<scanner-name>` is the `name:` of the external scanner block in the strategy YAML (e.g. `external_momentum`).
- `<wallet-suffix>` is the **last 4 hex characters** of the strategy wallet, lowercased (no `0x` prefix on the suffix). For wallet `0xAbC123dEf4567890aBc123def4567890ABc12345` the suffix is `2345`.

Examples:

```
senpi-producer-external_momentum-2345
senpi-producer-custom_regime-2345
```

This keeps cron names short and human-readable while still giving a deterministic join key for reconciliation.

**Collision caveat.** A 4-hex suffix has ~65k uniqueness; collisions between two strategy wallets on the same host are unlikely below ~30 wallets but not impossible. The reconciliation algorithm below treats any cron name whose suffix matches **more than one** running runtime's wallet as **ambiguous** — surface it to the user rather than guessing which runtime it targets. If the host outgrows this scheme, lengthen the suffix; the algorithm changes only by widening the regex.

### Reconciliation algorithm

1. **List crons.** Run `openclaw cron list --json`. Filter to entries whose `name` matches:

   ```
   ^senpi-producer-(?<scanner>[A-Za-z0-9_]+)-(?<wallet_suffix>[a-f0-9]{4})$
   ```

   The captured `(wallet_suffix, scanner)` pairs form the **producer-side set**.

2. **List runtime external scanners.** Run `openclaw senpi state --json`. For each runtime entry where `runtime.status === "running"`, walk `components.scanners.state.scanners[]` and collect every entry where `scheduleMode === "external"` and `enabled === true`. For each, derive `wallet_suffix = runtime.wallet.toLowerCase().slice(-4)`. The keys `(wallet_suffix, scannerId)` form the **runtime-side set**, and keep the full wallet alongside each entry for reporting.

3. **Compare the two sets:**

   | Set membership | Verdict | Remediation |
   |---|---|---|
   | In producer set, not in runtime set | **Orphan producer** — cron is firing data into a wallet that has no running runtime declaring that scanner | Disable or delete the cron, or install the missing runtime |
   | In runtime set, not in producer set | **Orphan consumer** — runtime is waiting for data nobody is pushing | Schedule a producer (Deploy Checklist Step 5) or delete the runtime |
   | In both sets | Confirm liveness via the external-scanner field rules above (`runCount > 0`, recent `lastRunFinishedAt`) | — |
   | One producer-side key matches **two or more** runtime-side wallets (same suffix, same scanner name) | **Ambiguous match** — suffix collision | Surface both candidate wallets to the user; do not auto-resolve. Recommend lengthening the suffix in the cron name and runtime-side derivation. |

### Unclassified cron entries

A cron job whose name does not match the convention regex is **unclassified**. The agent should:

- List unclassified entries and surface them to the user.
- Not guess whether they are senpi producers or unrelated jobs.
- Recommend renaming any senpi producer that doesn't comply, so future reconciliation stays deterministic.

Do not parse the `message` text as a fallback — it is fragile and will silently misclassify jobs.

---

## What "operating" means

Declare the runtime operating only when **all** hold:

- `runtime list` shows it as `running`.
- Every `enabled` interval scanner has a recent `lastRunFinishedAt` and `consecutiveErrorCount === 0`.
- Every `enabled` external scanner has `runCount > 0`, recent `lastRunFinishedAt`, and a paired producer cron with the canonical name.
- Cron ↔ runtime reconciliation produces zero orphans on either side.
- For each action, either the "operating" or "dormant by design" verdict applies — never "wiring problem" or "failing."

Anything less: surface the specific failing field and the remediation to the user, not a generic "looks fine." The agent's job here is to be precise about what is and isn't working — assume nothing.
