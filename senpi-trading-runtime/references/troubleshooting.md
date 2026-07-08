# Troubleshooting — "it's deployed but not trading"

The trap this page exists to break: a strategy shows **`status: ACTIVE`** and is funded, but no positions
ever open. `ACTIVE` only means the strategy record is live and its **`position_tracker`** is running — it
says **nothing** about whether the `external_scanner` is producing signals. A strategy is only actually
working when its scanner has **ticked at least once and emitted a valid signal**.

> **Definition of done** (not "ACTIVE"): the `external_scanner` is **registered**, `interval_seconds > 0`,
> it has **≥1 successful tick**, and it has **emitted ≥1 signal that passes `signal_data_schema`**. Confirm
> it — don't assume it.

## One command first

Before inspecting files by hand, run the doctor — it names ONE cause instead of making you infer:

```bash
python3 senpi-strategy-ops/scripts/diagnose.py <id>              # deterministic verdict per instance
python3 senpi-strategy-ops/scripts/diagnose.py <id> --run-scan   # also run scan() live + show its literal return
```

It checks, in order: scanner declared? required fields present? `interval_seconds > 0`? entrypoint on
disk? runtime registered + running? scanner registered on the runtime? erroring? **ticking** (via the
scanner-liveness clock, *not* `runCount`)? **BARREN** (alive + ran + 0 signals)? If you only read one thing,
read its verdict line.

## Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| ACTIVE + funded, **no positions ever**, `position_tracker` runs fine | The `external_scanner` isn't producing — see the specific rows below (this is the umbrella symptom). | `diagnose.py <id>` to get the specific cause; then fix **source** + redeploy. |
| `diagnose` says **only `position_tracker` registered** / no `external_scanner` | The runtime.yaml declares no `external_scanner` (or it failed to load). Nothing feeds `OPEN_POSITION`. | Add/repair the `external_scanner` block; `validate_strategy.py` before deploy. |
| **`interval_seconds: 0`** (or missing/negative) | An `external_scanner` with a non-positive interval is **never scheduled** — it never ticks. | Set a positive integer `interval_seconds` (e.g. `300`). Fix source + redeploy. |
| Scanner **registered but never ran** (`runs = 0`, not alive), just deployed | Not a bug — the first `scan()` fires on its `interval_seconds`, not at deploy time. | Wait one interval, re-run `diagnose.py`. Don't `sleep`-poll right after deploy. |
| **BARREN** — scanner is alive, has run, but **0 signals** | (a) correctly quiet (regime/tail-risk sit idle by design); **or** (b) thresholds too tight / universe empty → `scan()` returns `[]`; **or** (c) `scan()` emits a shape that fails `signal_data_schema` → the runtime **drops it silently**. | `diagnose.py <id> --run-scan`: `[]` ⇒ loosen the thesis/inputs; a **shape violation** ⇒ fix your `data{}` keys/types to match `signal_data_schema`. Fix source + redeploy. |
| Scanner shows **consecutive errors** | `scan()` (or its `import scoring`) throws every tick — a bug in the scan module, a bad MCP arg, or a missing dependency. | `openclaw senpi events -r <id> --level error` for the traceback (or `--run-scan`). Fix the exception; redeploy. |
| Signals emitted but **still no positions** | Not a scanner problem — a **risk gate** (cooldown, daily-loss, drawdown-halt, max-entries) or **no free slot**. | `openclaw senpi risk -r <id> --json` (eligibility + per-gate reasons) and check `slots` vs open positions. |
| Positions open but **unprotected** (no stop) | DSL exit not wired: missing `position_tracker` scanner / `POSITION_TRACKER` action, or no `exit.dsl_preset`. | See [`dsl-protection-check.md`](dsl-protection-check.md). Every strategy must ship a DSL exit. |
| `entrypoint … not found` at deploy/diagnose | `path`/`entrypoint` don't resolve against the runtime.yaml dir, or `scan.py`/sibling `scoring.py` is missing. | Re-fetch/rebuild the package; `validate_strategy.py` checks this statically. |

## `runCount` lies — use the scanner-liveness clock

For a **selective** scanner (one that only emits when its thesis fires), the counter surfaced as
`runCount` in raw state counts **emitted signals, not invocations**. A healthy scanner that has run 200
times but correctly stayed quiet reads as `runCount: 0` — so **never use `runCount` to decide "did it
tick".** Liveness is the scanner's `alive` heartbeat / `lastRunFinishedAt` (or the state file's mtime).
`openclaw senpi scanner -r <id> --json` separates the two — `runs` (invocations) vs `signals` (emits) —
and flags **BARREN** = alive + ran + 0 signals. `diagnose.py` uses that, not `runCount`.

## Read the REAL runtime state (not a swallowed CLI line)

When the CLI output looks empty/truncated, read the source of truth directly:

```bash
openclaw senpi scanner -r <id> --json     # per-scanner health: runs, signals, errors, alive, BARREN
openclaw senpi state   -r <id> --json     # full runtime state (the escape hatch)
openclaw senpi events  -r <id> --level error --limit 50   # the actual scan()/action tracebacks

# the on-disk state, read straight from the filesystem (CLI-independent):
STATE_DIR="$(openclaw senpi config get state-dir 2>/dev/null || echo ~/.openclaw/senpi-state)"
find "$STATE_DIR" -name 'state.json' | xargs -I{} sh -c 'echo "== {} =="; cat "{}"'
```

## Rules

- **Fix source, never deployed state.** The runtime owns its state files and overwrites them every tick —
  hand-editing `state.json` fixes nothing and is erased. The fix is always: correct the **package**
  (`scan.py`/`scoring.py`/`runtime.yaml`) → `close.py <id>` → author → `deploy.py <id>`.
- **Never "fix" a non-firing scanner by falling back to a raw `strategy_create_custom_strategy`.** That
  makes an empty custom-position strategy with **no DSL and no automated exits** — you lose the entire
  point of the runtime. Repair the package instead.
- **Don't declare it fixed without a tick.** "Redeployed, status ACTIVE" is not a fix. Re-run
  `diagnose.py <id>` (and `--run-scan`) and confirm a **signal emitted**.
