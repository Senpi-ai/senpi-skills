# Liveness Verification

When and how to verify a Senpi runtime is **actually operating**, not merely registered.

Use this whenever you have just deployed a strategy, are diagnosing a "nothing is happening" report, or
are about to tell the user a strategy is live. Do not declare success on `openclaw senpi runtime list:
running` alone — that field reports process status only, not functional liveness.

This is an **agent-side check**. Run the commands yourself; do not ask the user to confirm "is it working?"

> **Supervised model:** the runtime **spawns and supervises** each `external_scanner`, calling
> `scan(inputs, ctx)` every `interval_seconds` itself. There is **no separate producer daemon and no
> `push_signal`** — so there is nothing to reconcile against. Scanner liveness is read from the runtime's
> own **CLI commands** (`openclaw senpi status` / `state`) — **never from the on-disk state files**
> (`/data/.openclaw/senpi-state/…`); those are internal, partially-written, and not a contract.
> `deploy.py verify` already runs this check (the `live` / `not-live` / `unverified` verdict); use this
> doc when triaging by hand.
>
> **Two commands, and which to trust when they disagree:**
> - **`openclaw senpi status -r <id> --json`** (getHealthStatus) — the runtime's own per-scanner
>   **health verdict** (`components.scanners.scanners[].health`). Light and **reliable immediately after
>   deploy.** This is the primary liveness source.
> - **`openclaw senpi state -r <id> --json`** (getSystemState) — the rich per-scanner row (runCount,
>   `lastAliveAt`, `lastError`, `enabled`). Richer, but **`getSystemState` transiently THROWS for the
>   first several minutes after a runtime starts** (scanner subprocesses still launching) — the CLI then
>   prints nothing usable and exits non-zero. **A failed/empty `state` read is NOT evidence the scanner is
>   down** — fall back to `status` and re-check. (Confirmed live: a fully-healthy runtime whose `state`
>   threw for ~9 min while `status` reported `2/2 enabled and healthy` the whole time.)

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

A scanner is **operating** when the runtime says so — and for a **supervised external scanner** the
authoritative signals are `health` and the heartbeat, **not** the run counters:

- **`health ∈ {"healthy", "degraded"}`** — the runtime's own verdict (in **both** `status` and `state`).
  This is the primary signal; trust it.
- **`lastAliveAt` is fresh** (`now − lastAliveAt ≤ 2 × interval_seconds`) — the scanner POSTed to intake
  this cycle. **A healthy scanner that finds no setup still POSTs an empty heartbeat every tick**, so a
  live barren scanner has a fresh `lastAliveAt` even with **`runCount === 0` / no signals**.
- `enabled === true`, `consecutiveErrorCount === 0`, `lastError === null`.

> **Do NOT require `runCount > 0`.** For an external scanner, `runCount` and `lastRunFinishedAt` lag or
> stay `0`/`null` until the runtime has processed a POST, and a barren scanner legitimately emits no
> signals for long stretches. `runCount === 0` on its own is **never** breakage — it means "no trade this
> cycle," which is normal. Judge liveness by `health` + `lastAliveAt`; `runCount > 0` is a bonus, not a
> requirement.

Failure signatures (**positive** evidence of breakage only — anything else is "not yet confirmed," retry):

| Symptom | Field signature | Likely cause |
|---|---|---|
| Runtime says it's broken | `health === "unhealthy"` (in `status` or `state`) | The runtime's own verdict — trust it; read `lastError` |
| Repeatedly failing | `consecutiveErrorCount ≥ 1` or a persistent `lastError` | `scan()` is throwing — print `lastError`, `lastErrorAt` (usually an upstream MCP/RPC read in `scan()`) |
| Disabled | `enabled === false` | Scanner is turned off — not wired to run |
| Hung mid-tick | `inFlight === true` & `lastRunStartedAt` older than `timeout_seconds` | `scan()` exceeded its time box — the runtime kills + restarts it; persistent hangs point at a slow upstream read |
| Can't read either command | `state` throws AND `status` unreadable | **Not a scanner fault** — the gateway read is transiently unavailable (common right after deploy). Re-check; do not declare the scanner down. |

**When `status` and `state` disagree, `status` wins for the health verdict.** `status` (getHealthStatus)
keeps answering while `state` (getSystemState) is still throwing post-deploy — so a scanner that `status`
calls `healthy` **is** healthy even if `state` won't load yet. Use `state`'s raw fields (`lastAliveAt`,
`runCount`, `lastError`) only to *enrich* the verdict when the read succeeds, never to override a clean
`status` health with "state unreadable."

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
- its `external_scanner` is `health ∈ {healthy, degraded}` (per `status`, and per `state` when it loads),
  `enabled`, `consecutiveErrorCount === 0`, `lastError === null` — with a fresh `lastAliveAt` when `state`
  is readable. **A barren scanner (`runCount === 0`, healthy, heartbeating) counts as live** — it is
  scanning, just not trading this cycle;
- each action is either "operating" or "dormant by design" — never "wiring problem" or "failing".

If you **cannot read `status` or `state`** for an instance, the correct verdict is **"unverified," not
"down"** — the strategy is registered, funded and DSL-protected; re-run `deploy.py verify <id>` shortly
(that command reports this case distinctly). **Never report a strategy as live until `verify` returns
`live`** — an unreadable state is not a green light.

Anything less: surface the specific failing field and the remediation, not a generic "looks fine." For
deeper engine triage (position_tracker → DSL → actions), see
`senpi-trading-runtime/references/runtime-concepts.md` and `openclaw senpi dsl|action …`.
