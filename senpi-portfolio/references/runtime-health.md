# Runtime health — ACTIVE ≠ running, `runtime_health`, and the broken-strategy ladder

Read before any liveness verdict ("is it running", a health check, `degraded` / `not_running` /
`unknown` / `unverified`). The always-on rules are in SKILL.md; this is the detail.

## "ACTIVE" ≠ running — a strategy with no runtime registered is NOT alive, and NOT protected

> **First, is it a copy-trade?** If `strategy_kind: "mirror"` (a.k.a. `runtime_health: "mirror"`), everything in
> this section does **NOT** apply — a mirror / copy-trade strategy has **no runtime by design**. Its
> `runtime_registered` / `not_running` / `running_blind` / `protected` are **`null` (N/A), never `false`** — do
> NOT report it as "not running / unprotected," do NOT tell the user to add a DSL, set a stop via
> `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." See
> `mirror-strategies.md`. Everything here is about **CUSTOM** strategies (`strategy_kind: "custom"`).

`status: ACTIVE` only means the strategy *record* exists and is funded — it does **not** mean a runtime is
actually running it. The engine checks the runtime registry and flags any strategy that is **ACTIVE +
funded but has NO runtime registered** via `strategy_groups[].not_running` (and per-instance `not_running`
/ `runtime_registered`), plus a `meta.warnings` line. Such a strategy is **not running at all** — its
scanner has never ticked, so it has **no DSL and no guardrails** — even though it shows ACTIVE and holds
capital. Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no runtime; no scanner, no DSL, no
guardrails**, and tell the user to redeploy it via `senpi-strategy-ops`. **Never** call a `not_running`
strategy "alive and waiting," "scanner is live," or "DSL-protected" — that is a false all-clear (a funded
strategy sat exactly like this while the user believed it was protected and running). This is DISTINCT from
the flat-but-running case (SKILL.md, "A flat/empty instance"): a flat sleeve with a *registered, ticking* runtime is waiting for a signal
(fine); a `not_running` strategy has **no runtime behind it** (broken). `running_blind: true` is a third,
previously-invisible state: the runtime IS registered and ticking, but its entry scanners never wired
(`running — NO ENTRY SCANNERS`), so it **cannot produce entry signals** — report **⚠ RUNNING — NO ENTRY
SCANNERS**, not a clean "running." When `runtime_registered` (or `not_running` / `running_blind`) is
`null`, the registry read did not answer — say **"could not verify on this host,"** never "running" and
never "not running."

**Runtime-verified liveness — `runtime_health`.** ("Telemetry" here is `openclaw senpi status` on the
user's own box — the runtime's own view of itself, not the internal telemetry stack. Don't say
"telemetry reports…" to a user; it sounds like we are reading something they cannot see.) Beyond "is a
runtime registered," the engine asks the
runtime itself (`openclaw senpi status`) whether it's actually *working*, and sets `runtime_health` per
strategy and per group. Narrate it honestly — a registered runtime is not automatically a healthy one:
- **`live`** — registered, and the runtime reports itself healthy. Only this earns "running / protected."
- **`recovering`** — the engine's own `degraded`: **the last scan errored, once.** `consecutiveErrorCount`
  is 1, and the next successful tick zeroes it — this is a blip clearing itself, not a fault. **Do not
  warn on it.** Do not put a ⚠ on the strategy, and do not send the user to a diagnostic. If they ask,
  say the last scan hit an error and the next one retries. If the strategy is opening or closing
  positions, that is the answer to "is it working" — a stale `lastRunStatus` is not.
- **`degraded`** — the engine reports **unhealthy**: **two or more consecutive** scan errors, or
  `running_blind` (up, no entry scanners). This is the one that always deserved the warning. Say
  **"⚠ runtime degraded — running but not healthy,"** not a clean all-clear. Flagged in `meta.warnings`.
- **`not_running`** — no runtime at all (above). ⛔ NOT RUNNING / UNPROTECTED.
- **`last_close_utc` / `hours_since_last_close`** (per strategy) + **`meta.quiet_strategies`** — the
  newest close and its age, past 48h. **Say "has not CLOSED a trade in Xh", never "has not traded"**: a
  position carries no open time, so an entry taken since that close is invisible here. Quiet while
  **holding** means fully allocated, not stuck — check slots and entry gates first, and note every guard
  rail halts ENTRIES only, so a gate counter is moot while every slot is full. No closed record is never
  flagged: "never closed" cannot be told apart from "deployed ten minutes ago".
- **`risk_pause`** (beside `runtime_health`, never instead of it) — the runtime's OWN entry gate says
  `CLOSED` or `COOLDOWN`: the strategy is **healthy and paused by its own rule** (daily entry cap, daily
  loss halt, drawdown halt, a cooldown). Name the gate and quote its `reason` verbatim ("Max Entries/Day —
  Max entries: 4/4 entries today"), say when it lets go (`reset`: daily gates at 00:00 UTC, cooldowns on
  their own; a latched drawdown halt with `drawdown_reset_on_day_rollover: false` never lets go on its own —
  it waits for the user's decision, and you never clear it or resume entries without their explicit approval), and that nothing is broken. **Never send the user to redeploy over it** — a fresh wallet
  market-exits the book and starts the same gate again from zero. `meta.paused_by_risk_gate` lists them.

### Corroborate the verdict before you assert it — in either direction

**The two sources measure different windows, which is why they disagree without either being wrong.**
`runtime_health` describes the **most recent tick**. `positions` / `trade_count` / `recent` describe the
**last several hours**. A strategy can hold positions opened by earlier scans *and* have had its latest
scan error — both facts true at once. So read them as a grid, not a single verdict:

|                          | **strategy IS trading** (open positions / recent closes) | **strategy is NOT trading** (no fills, no recent closes) |
|--------------------------|---|---|
| **field says `degraded`** | **They disagree.** The last scans errored; the book is live and managed. Say *"running — some scans errored"*, never *"your strategy is broken."* Confirm with `status.py <id>` before going further. | **They agree.** It really is broken. Report it and work the ladder below. |
| **field says `live`**     | **They agree.** Clean. Say so. | **They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy` and still be blind. Say it is running but has not found a trade, and use "No signals at all" in `why-not-trading.md`. |


**When the two disagree, say which one you are trusting and why.** If the money is moving, that is the
headline and the field is the footnote — do not report the field and bury the evidence.

**Fees are read, never estimated.** A P&L claim about a strategy — "it is up", "it is not losing" —
quotes the wallet's **signed** fees from its fills (`totalFees` / the closed-trade history), net of
them, with the fee figure beside the PnL. "About $7 on 41 trades" is a guess, and it once turned a −$3
wallet into a +$1.69 one in the user's ear. If the fee read fails, say the number is gross and why.

**Paper is not live.** A `senpi validate` tick, a scanner signal, a simulation or an estimate is not a
trade; only a fill on the strategy wallet (`positions`, `closed`) is. Never narrate a shadow run as
"live", "running" or "a trade" — the user then manages a position that does not exist.

### When it IS genuinely broken, act — don't hand the user a to-do

For `degraded` corroborated by no activity, or `not_running`, work the ladder yourself and report what you
found. Only the last step touches their money, and only that one needs their consent:

**Before the ladder: if the row carries `runtime_reason`, say it.** It is the runtime's own words, read off
`senpi status --json`'s scanner rows (`degradedReason`): a crash-loop cause, e.g.
`jackal_main_signals: 5 crashes in a row, each within 10s of starting`, or the scanner's last tick error,
e.g. `jackal_main_signals: error: boom` (older runtimes give only the crash-loop kind, and `senpi status`
prints it as `crash loop: …`). Quote it in those words: it is why the scanner has not traded. Call it a
crash loop only when it says crashes. No `runtime_reason` means the runtime gave none — never invent one;
the ladder finds it.

1. **`python3 senpi-strategy-ops/scripts/status.py <id>`** — re-ask the runtime. Verdict + position count.
   **Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with this
   skill's. Its **`degraded`** is this skill's **`recovering`** (one errored tick); its **`unhealthy`** is
   this skill's **`degraded`** (the real fault). Reading its "degraded" as ours is how a confirmation step
   confirms the wrong thing.
2. **`openclaw senpi scanner -r <rt>`** — is it alive at all? An external scanner is live when `alive=` is
   recent (older runtimes print `alive=` as epoch ms). Then read the row's `health` (the engine's words):
   `degraded`/`unhealthy` with `last=error` and a fresh `alive` is a past delivery error (read `reason`),
   not a dead scanner; `unhealthy` with `inFlight=true`, or a crash-loop reason, is a real fault;
   `health=unknown` with `alive=—` is a child not heard from yet. Its run count (`runs=` on older runtimes)
   moves only on an accepted signal or an error, so 0 is normal.
3. **Re-run the deploy** — a deploy interrupted by a gateway restart does not resume on its own, and
   re-running reconciles rather than duplicating. This is the fix for `not_running` after a broken deploy.
4. **`close.py` → redeploy** — **last resort, and ask first.** It is a market exit of every open position
   and a fresh wallet. Never reach for it to clear an error you have not diagnosed.

**There is no restart verb.** If the runtime is up, ticking, erroring every read and steps 1–3 show nothing
wrong with the recipe, the remedy is on our side, not theirs — say so plainly and point them at Senpi
Support rather than inventing a step they can take.
- **`unknown`** — registered, but health **not yet proven**: a scanner it has never heard from, a runtime
  just restarted, or a `senpi status` document that carried no health verdict this engine recognises. Say
  **"runtime liveness unverified — not confirmed running"** — never upgrade to "healthy/protected" or
  downgrade to "broken." The runtime is deliberately fail-closed about `unknown` (it refuses to call an
  unproven scanner healthy); repeating it back is the whole point. A runtime *process* that exists
  (`status: running`) is not a health verdict and never reaches `live` on its own.
- **`unverified`** — the registry READ ITSELF failed (no `openclaw` on this host, a build without the
  RPC, or the CLI call errored) — nothing below was ever asked. Say **"could not verify on this
  host"** — never "protected," "not protected," "running," or "not running." **`null` is not `false`.**
  `meta.warnings` names the failed command; quote it, never invent a cause. This is the honest bar:
  **only `live` means "confirmed working."**
- **`mirror`** — a **copy-trade** strategy: no runtime BY DESIGN (see `mirror-strategies.md`).
  Never `live` / `recovering` / `degraded` / `not_running` / `unverified` — those don't apply to it. Judge it on `mirror_of` +
  `mirror_multiplier` + `stop_loss_pct` / `take_profit_pct`, never on a runtime it was never meant to have.

**Minimum runtime — `openclaw senpi runtime list --json`.** The engine asks the runtime for its own
inventory through that command (it never reads the runtime's private state files). It is a **newer
runtime build than some hosts carry**; a box whose `@senpi-ai/runtime` predates it exits non-zero on the
`--json` flag, and you will see **every** runtime-sourced field `null` on **every** strategy —
`runtime_registered` / `not_running` / `running_blind` / `protected` null, `runtime_health:
"unverified"`, `meta.registry_source: null` — plus a `meta.warnings` line naming that exact command.
**That whole-fleet pattern means the runtime on this box is too old for this skill's registry read, not
that the strategies are broken.** Say so in those words, quote the warning, and do not diagnose the
strategies from it: no strategy may be called running, not-running, protected or unprotected off that
run. (A *single* strategy reading null while others read fine is a different thing — that one is
genuinely unattributed.) The fix is a runtime upgrade on the box, not a redeploy of the strategies.

This health check owns **liveness triage** (registered + running + healthy) via telemetry, and **references
`status.py` as the confirmation step** — it does not re-derive the deep checks. A thorough health check
does not stop at the verdict: for a strategy that is `not_running`, `degraded` or `unknown` — **never for
`recovering`** — **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the runtime directly and
returns its verdict **beside a position count**, which is the corroboration below. Run it yourself and give
the answer. **It is your tool, not a step you hand the user** — never write "worth running …" into a
portfolio answer; either run it, or say what you know without it. For
**"where am I leaking / did a stop fail / any halts / exit quality"**, hand to `senpi-improve-trades` (it
reads the runtime event log for protection gaps, risk halts, failed orders, and exit quality). Reference the
right tool to *confirm* — never re-derive its analysis here.
