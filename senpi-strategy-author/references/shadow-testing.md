# Shadow-testing a strategy — without spending AI credits, and without funding it

"Let me test it first" is the right instinct, and the two cheap ways to do it are the ones people
skip. The expensive way — scheduling the agent to *watch* the strategy — is how an AI-credit balance
disappears in days while the strategy itself does nothing.

**There is no paper-trading mode.** Senpi cannot run a strategy forward in simulated time, and nothing you
schedule can stand in for that: a scanner re-run on a timer is a model call per tick, not a simulation, and
it never sees fills, slippage, funding or the DSL. A one-shot offline backtest over historical candles is
fine — no scheduler, no model call per tick, no venue contact — as long as it never wires to a trade tool
and follows [the rules below](#an-offline-backtest--the-rules).
Say that plainly when the user asks to paper trade or to "watch it on a timer", then offer the two real
tests below; "shadow" means exactly one thing here — the `senpi validate` run.

## Two real tests, cheapest first

**1. A tick with no wallet — `openclaw senpi validate`.** One real scan against live, read-only market
data: no wallet, no funding, no model call. It prints what the scanner *would have emitted* and
whether the recipe loads. Re-run it as often as you like; it costs nothing but the tick.

```
openclaw senpi validate /data/workspace/strategies/<id>      # the dir holding that runtime.yaml
```

`PASS` = the code runs and read real data. `UNPROVEN` = it returned before reading anything (an
out-of-session gate that ignores `ctx.dry_run`, say) — not a pass, and not a signal either.

**2. A live run at the floor — the `$10` wallet.** When the user wants to see fills, deploy with the
minimum budget (a little over $10 per wallet; ops has the exact figure) through
`senpi-strategy-ops` — never a raw MCP create. The runtime then supervises it every
`interval_seconds` at **zero model cost**: the scanner, sizing, execution, the DSL exit and the risk
gates all run inside the runtime process. There is nothing left for the agent to poll.

## An offline backtest — the rules

A replay you write yourself is only as honest as its clock. Each rule below is a way a hand-written
backtest has already produced a confident, wrong recommendation for a live strategy.

- **Decide on what was known at that moment.** Walk events in entry-time order. A gate at an entry
  may read only trades that had already *closed* by then; a skipped trade never happened, so its
  outcome cannot feed a later gate, a loss streak or a rolling win rate.
- **A candle counts only once it has closed** before the decision. Never read the final close, high
  or low of a bar still forming at entry, and never enter at the open of the bar whose close fired
  the signal.
- **Exits need the path, in order.** Use bars no coarser than the stop distance, clipped to the
  holding interval. When one bar touches both the stop and a profit tier, take the stop first; with
  no intraday data, report the trade as not covered — never fall back to its recorded exit.
- **Start from the deployed `runtime.yaml`.** Load the baseline exit tiers, stop and guard rails
  from the running instance's file — never retype them from memory or an older note.
- **Every number names its run.** A kept subset cannot exceed its input; a combined variant is a new
  run, never metrics copied from another.
- **A bootstrap interval must pass its own check:** lower bound ≤ upper bound, and one value gives
  that value at both ends. Index the sorted resamples; never subtract from the value.
- **Say what it is.** A filter chosen on the same trades it is scored on is exploratory —
  never "validated", "significant" or "proven" — until it holds on a later window that played no
  part in choosing it. A backtest supports a change; the user decides it.

## Never schedule agent turns to watch it

`openclaw cron add` does **not** run a shell command on a schedule. It schedules an **agent turn**:
every firing is a full model call, and each one re-reads the whole conversation prefix. A "monitor
every 5 minutes" job is **288 model calls a day** — at the cost of the entire context each time —
producing 288 identical "no change" messages. The strategy is already being watched, for free, by
the runtime.

- **No cron for monitoring.** Read on demand instead (below).
- If the user insists on a periodic digest, **once or twice a day is the ceiling**, and say what it
  costs in credits before adding it.
- Anything you did schedule: `openclaw cron list`, then `openclaw cron rm <id>`. Check for
  leftovers from earlier sessions — a forgotten job keeps billing after the strategy is closed.

## Read on demand — every one of these is read-only and free of model cost until you narrate it

```
openclaw senpi scanner -r <runtime_id>           # health / alive / errors / signals — is it alive?
openclaw senpi status -r <runtime_id> --json     # the runtime's own health verdict + risk gates
openclaw senpi dsl positions                     # what it holds and where each stop sits
openclaw senpi action history                    # what it did, and why
python3 /data/.openclaw/skills/senpi-strategy-ops/scripts/status.py     # the fleet view (health, pauses, config drift)
```

One read when the user asks beats a hundred reads nobody asked for.

## If a background process is genuinely unavoidable

Some tests need a helper process (a data producer the user wrote, say). Launch it **once**,
detached, with its own log — never from a cron that relaunches it, and never again on the next turn
"to be safe":

```
nohup env FOO=bar python3 producer.py > /data/workspace/logs/producer.log 2>&1 &
disown
```

Read the log to know what it did. A second launch is a second process, not a restart.

## Paper is not live — label every number

- A `validate` tick **would have** opened; it opened nothing. Say "would have".
- A scanner **signal** is not a **fill**. Only `dsl positions` / `strategy_list` positions are fills.
- Never call a `validate` run, a simulation, or an estimate "live", "running" or "a trade". The
  moment you do, the user manages a position that does not exist.

## Stopping a shadow

`openclaw cron rm <id>` for anything scheduled; `python3 /data/.openclaw/skills/senpi-strategy-ops/scripts/close.py <id>`
for a funded floor wallet (flattens, returns the funds). Say which one you did.
