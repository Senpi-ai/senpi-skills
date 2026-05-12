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
3. **For every external scanner in a running runtime, is the paired producer daemon registered on this host and healthy? And conversely, for every producer daemon, is there a running runtime declaring the matching external scanner?**

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
- `now − lastRunFinishedAt ≤ producer cadence + buffer` — derive cadence from the daemon's `interval_seconds`; default buffer is 1 × cadence
- `lastRunStatus !== "error"`

**Important:** `openclaw senpi status` reports external scanners as `healthy` even with `runCount === 0` — push-driven scanners cannot distinguish "waiting for first ingest" from "broken pipe" at the health layer. Do not rely on `status` here. Use the field values from `state` directly.

Failure signatures:

| Symptom | Field signature | Likely cause |
|---|---|---|
| Declared but never received | `runCount === 0`, `lastRunFinishedAt === null` | No producer daemon running for this (wallet, scanner) pair, or producer crashed before its first tick — see Daemon ↔ runtime reconciliation below; check `senpi-helpers list` and `senpi-helpers health <name>` |
| Was receiving, has stopped | `lastRunFinishedAt` older than producer cadence + buffer | Producer daemon stopped firing — `senpi-helpers health <name>` will report `down` or `stale_ticks`; check the daemon's log path from `senpi-helpers list --json` |
| Receiving but every payload errors | `lastRunStatus === "error"`, `errorCount` rising | Payload shape mismatch against `external_scanner` `config.fields` — read `senpi-helpers stats <name>` for the error-code histogram |

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

## Daemon ↔ runtime reconciliation

Every running runtime that declares an external scanner MUST have a matching producer daemon registered on this host, and every producer daemon MUST target a running runtime that declares the matching external scanner. This is a wiring contract and the agent should enforce it bidirectionally.

Reconciliation works against the daemon's self-recorded `(wallet, scanner)` pair in its state files — not against any human-assigned name. The daemon name is informational; the wiring contract is the wallet + scanner tuple.

### Where the producer-side data lives

Each daemon writes a `pid.json` and `heartbeat.json` under `${SENPI_HELPERS_STATE_DIR}/<name>/`. `senpi-helpers list --json` aggregates them into a stable envelope:

```json
{
  "daemons": [
    {
      "name": "my-producer-a919c1e2",
      "wallet": "0xa919c1e2…",
      "scanner": "my_signals",
      "running": true,
      "interval_seconds": 300,
      "last_tick_iso": "2026-05-12T15:31:00Z",
      "last_tick_status": "ok",
      "log_path": "/tmp/my-producer.log"
    },
    …
  ]
}
```

`wallet` and `scanner` are mandatory fields that the daemon recorded at boot — they are the join key for reconciliation. No name parsing required.

### Reconciliation algorithm

1. **List daemons.** Run `senpi-helpers list --json`. The `(wallet, scanner)` pairs from each daemon entry form the **producer-side set**. Keep the daemon name alongside each pair for reporting.

2. **List runtime external scanners.** Run `openclaw senpi state --json`. For each runtime entry where `runtime.status === "running"`, walk `components.scanners.state.scanners[]` and collect every entry where `scheduleMode === "external"` and `enabled === true`. The keys `(runtime.wallet.toLowerCase(), scannerId)` form the **runtime-side set**.

3. **Compare the two sets:**

   | Set membership | Verdict | Remediation |
   |---|---|---|
   | In producer set, not in runtime set | **Orphan producer** — daemon is pushing into a wallet that has no running runtime declaring that scanner | Stop the daemon (`senpi-helpers stop <name>`) or install the missing runtime |
   | In runtime set, not in producer set | **Orphan consumer** — runtime is waiting for data nobody is pushing | Launch the producer daemon (Deploy Checklist Step 5) or delete the runtime |
   | In both sets | Confirm liveness via the external-scanner field rules above (`runCount > 0`, recent `lastRunFinishedAt`) **and** `senpi-helpers health <name>` returns `healthy` | — |

### Daemon health states

`senpi-helpers health <name>` returns one of:

| state | meaning |
|---|---|
| `healthy` | Daemon running, recent tick, last status `ok` |
| `down` | `pid.json` missing OR the recorded pid is not alive — daemon crashed or never started |
| `no_ticks_yet` | Running but no heartbeat yet — daemon just started |
| `stale_ticks` | Running but `last_tick_age > 2 × interval_seconds` — overrun cycle |
| `last_tick_failed` | Running, recent tick, but `last_tick_status !== ok` (error / timeout / skipped_locked) |

Exit code is 0 for `healthy`, 1 for any unhealthy state, 2 if no state files exist for `<name>`. Use these for scripted reconciliation. For per-call error histograms over the last N hours, use `senpi-helpers stats <name>`.

### Unclassified daemons

If `senpi-helpers list` shows a daemon whose `wallet` field doesn't match any installed runtime, the daemon is **orphan**. Surface it; do not auto-stop or auto-create runtimes to "fix" it — the operator may be running a manual smoke test or staging a new strategy.

---

## What "operating" means

Declare the runtime operating only when **all** hold:

- `runtime list` shows it as `running`.
- Every `enabled` interval scanner has a recent `lastRunFinishedAt` and `consecutiveErrorCount === 0`.
- Every `enabled` external scanner has `runCount > 0`, recent `lastRunFinishedAt`, AND `senpi-helpers health <daemon-name>` returns `healthy`.
- Daemon ↔ runtime reconciliation produces zero orphans on either side.
- For each action, either the "operating" or "dormant by design" verdict applies — never "wiring problem" or "failing."

Anything less: surface the specific failing field and the remediation to the user, not a generic "looks fine." The agent's job here is to be precise about what is and isn't working — assume nothing.
