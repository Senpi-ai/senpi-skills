# Liveness Verification

When and how to verify a Senpi runtime is **actually operating**, not merely registered.

Use this whenever you have just deployed a strategy, are diagnosing a "nothing is happening" report, or
are about to tell the user a strategy is live. Do not declare success on `openclaw senpi runtime list:
running` alone — that field reports process status only, not functional liveness.

This is an **agent-side check**. Run the commands yourself; do not ask the user to confirm "is it working?"

> **Supervised model:** the runtime **spawns and supervises** each `external_scanner`, calling
> `scan(inputs, ctx)` every `interval_seconds` itself. There is **no separate producer daemon and no
> `push_signal`** — so there is nothing to reconcile against. Scanner liveness is read entirely from the
> runtime's own state (`openclaw senpi state`). `deploy.py` already runs this check (the `live` vs
> `registered` verdict); use this doc when triaging by hand.

---

## The contract: "running" ≠ "operating"

A runtime can show `status: running` while every component inside it is silent:

- A scanner can be mounted but never scheduled, or it threw on first init.
- The DSL monitor can be wired up while never evaluating a position.
- An action can be wired but never invoked because no signal ever reaches it.

Liveness means walking `openclaw senpi state -r <runtime_id> --json` and confirming each component is
doing real work, with timestamps and counters as proof. `runtime_id` = the runtime.yaml `name`
(`spider-swing`); rediscover a strategy's runtimes via `runtime list` matching `group == <id>`.

## Self-questions checklist

1. **Is the target runtime in `runtime list` with `status: running`?**
2. **Has its `external_scanner` executed at least once and recently** (a positive run count + a fresh
   `lastRunFinishedAt`)?
3. **Are its actions either operating or dormant-by-design** — never a wiring problem or failing?

**On DSL.** The DSL monitor exposes counters (`tickSuccessCount`, `lastTickFinishedAt`) in `state`, but
those only prove the internal tick loop moves — not that the end-to-end protective path (price providers,
MCP, exchange) works. Don't use DSL counters as a runtime-wide liveness gate; use `openclaw senpi dsl
inspect <ASSET>` when triaging a specific position.

---

## Field-level decision tree

Run `openclaw senpi state -r <runtime_id> --json` once, then walk each component. Use field paths against
the JSON structure — they are stable; the human-readable summary is not.

### The supervised external scanner

Path: the scanner entry under the runtime's scanners state (match on the `external_scanner` `name`, e.g.
`spider_swing_signals`).

A scanner is **operating** when **all** hold:

- `enabled === true`
- `runCount > 0` — the runtime has called `scan()` at least once
- `now − lastRunFinishedAt ≤ 2 × interval_seconds` (the runtime calls it on `interval_seconds`)
- `consecutiveErrorCount === 0`
- `lastRunStatus ∈ {"ok", "heartbeat"}`

Failure signatures and what they mean:

| Symptom | Field signature | Likely cause |
|---|---|---|
| Mounted but never ran | `runCount === 0` & `lastRunFinishedAt === null` | Scanner threw on first init, or runtime hasn't scheduled it yet — read `lastError`; re-check after one `interval_seconds` |
| Was running, has stopped | `lastRunFinishedAt` older than `2 × interval_seconds`, `runCount > 0` | `scan()` is throwing — read `lastError`, `lastErrorAt`; a crashed child is restarted with a fresh id, so repeated restarts show as resets |
| Repeatedly failing | `consecutiveErrorCount ≥ 2` | Print `lastError` and remediate (usually an upstream MCP/RPC read in `scan()`) |
| Hung mid-tick | `inFlight === true` & `lastRunStartedAt` older than `timeout_seconds` | `scan()` exceeded its time box — the runtime kills + restarts it; persistent hangs point at a slow upstream read |

`openclaw senpi status -r <id>` may report a scanner `healthy` even at `runCount === 0` (it can't tell
"waiting for first tick" from "broken"). Read the field values from `state` directly for the verdict.

### Actions

Path: the actions state for the runtime.

Actions are reactive — `runCount === 0` is not always a failure. Disambiguate:

| Field signature | Verdict |
|---|---|
| `runCount === 0` AND the feeding scanner has `runCount > 0` and is emitting signals | **Wiring problem** — signals exist but the action never fires |
| `runCount === 0` AND the feeding scanner has not emitted signals yet | **Dormant by design** — not a failure |
| `runCount > 0`, `consecutiveErrorCount === 0`, `lastError === null` | **Operating** |
| `consecutiveErrorCount ≥ 2` or persistent `lastError` | **Failing** — print `lastError` |

For `decision_mode: llm` actions also check `totalDecisionsExecute` vs `totalDecisionsNoExecute`. A
runaway "no execute" rate is usually a prompt/threshold issue, not a runtime fault — surface it but don't
treat it as a liveness failure. (Rule-mode strategies like spider have no LLM decisions.)

---

## What "operating" means

Declare a strategy **live** only when, for **every** instance:

- `runtime list` shows its runtime as `running`;
- its `external_scanner` has `runCount > 0` and a recent `lastRunFinishedAt` (within `2 × interval_seconds`),
  `consecutiveErrorCount === 0`;
- each action is either "operating" or "dormant by design" — never "wiring problem" or "failing".

Anything less: surface the specific failing field and the remediation, not a generic "looks fine." For
deeper engine triage (position_tracker → DSL → actions), see
`senpi-trading-runtime/references/runtime-concepts.md` and `openclaw senpi dsl|action …`.
