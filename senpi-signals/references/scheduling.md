# Running senpi-signals continuously

**Why this matters:** the flagship detector, `sm_positioning_build`, compares the proven cohort's
positioning **now** against **~12h ago** ("43% of the top 1,000 now hold HYPE shorts, up from 38%").
It can only fire if a snapshot from ~12h ago exists in the state ring. **Nothing else keeps that
history**, so on a cold state file the best signal the skill has is silent. A running sweep is what
turns it on. Everything else here follows from that.

## The one job you actually need: deploy `strategies/signals`

```bash
# via senpi-strategy-ops — one wallet at the $10 floor; the package never trades
python3 senpi-strategy-ops/scripts/deploy.py create signals --budget 10
```

`strategies/signals` is a runtime package whose external scanner runs `sweep.run(ctx.senpi_mcp.call_tool)`
every **45 minutes** (`interval_seconds: 2700`, matched to `FRESH_WINDOW_MIN` so a name you just posted
has rotated out by the next run) and **always returns `[]`** — `slots: 0`, no signal is ever emitted.
Each tick does the normal full gather and run under `--consumer social`: it **produces the content
feed** (`signals.md`) *and* **writes the snapshot** that becomes tomorrow's 12h baseline.

**What it costs:** ~8 MCP reads per tick, **no model tokens** — the runtime ticks on its own clock
without a model call. The 1.x design ran the same gather as an agent cron: a full model call per
firing, and on a real box every firing timed out. senpi-smart-money's rule — *"asked to run this on
a schedule? Say the cost first"* — is why the schedule is a scanner, not a cron.

State is durable and shared. On a claw the sweep uses `/data/.openclaw/senpi-state/signals/state.json`,
the runtime's state dir on the persistent volume, even though the agent's exec shell carries no
`SENPI_STATE_DIR`; the host derives the same directory from its launch config. Users' ad-hoc
`sweep.py` runs read the same warm baseline automatically; the content automation reads `signals.md`
there on its own cadence and never gathers.

## ⚠️ The one rule that will bite you: every snapshot must carry the SAME fields

The ring is one shared history. `_pick_baseline` selects the most recent snapshot **older than the
target age** — it does not care which job wrote it. So if a job writes snapshots holding only
`smart_share`, and one of those becomes the ~1h baseline, then `oi_surge` and `funding_flip` have no
prior to diff against and **go quiet without erroring**.

`sweep.py` gathers the full `asset_metrics` shape every run — `oi`, `price`, `price_change_pct`,
`smart_dir`/`smart_share`/`smart_long_n`/`smart_short_n`/`smart_positions`, `crowd_dir`,
`funding_pctile`, `funding_annualized_pct`, `notional_vol`, `dex` — so every writer of the ring is the
same code. **Do not write partial snapshots into the ring by any other route.** A failed source is
recorded in `coverage` and its fields are absent for that run, which is honest; a hand-built snapshot
that silently omits them is not.

## `smart_source` is fixed: `proven_cohort`, on a headcount basis

The trend detector matches its ~12h baseline **per asset and per source**, so switching `smart_source`
restarts that asset's trend history. The sweep always declares `smart_source: proven_cohort` with
`smart_share_kind: cohort_pct` (headcount, immune to mark-to-market drift), and carries the 4h board's
number alongside as `hot_4h_share`. Nothing to decide, nothing to drift.

## `--snapshot-only` (optional, ad hoc)

```bash
python3 scripts/sweep.py --snapshot-only
```

Records the reading into the ring and exits: **no detection, no ranking, no feed, and freshness is
NOT touched** — so it never consumes the anti-repeat budget the content run depends on. It prints
`history_span_hours` and `trend_ready`, so it doubles as the health check for "is the 12h detector
armed yet?" Use it to backfill after downtime; the package is the normal history-warmer.

## What to expect on a cold start

| Time since first tick | What fires |
|---|---|
| Tick 1 | Only static-state signals (e.g. `funding_extreme`, a standing `sm_divergence`). No diffs exist yet — **expected, not a bug.** |
| ~1h+ | Fast detectors wake: `oi_surge`, `funding_flip`, `sm_conviction`. |
| ~12h+ | **`sm_positioning_build` + `sm_flow` arm** — the flagship reads. `trend_ready: true`. |

Check readiness any time: `openclaw senpi scanner` shows the tick, and the tick's state record
(`trend_ready`, `reads`, `coverage`) is what the scanner persists; or run `--snapshot-only` ad hoc.

## Guardrails for an automated run

- **An empty feed means nothing new happened.** Do not lower the bar, widen the window, or backfill to
  hit a post count. A quiet market correctly produces a quiet feed.
- **Only the social feed is rotated.** The trade lens is deliberately not freshness-gated — a standing
  setup is still a setup for a user asking on demand.
- **Keep consumers separate.** The package runs `--consumer social`; ad-hoc runs default to `adhoc`; a
  `senpi validate` tick ranks under `validate`. They share one baseline ring but keep independent
  anti-repeat memories, so the package never blanks a user's feed.
- **Golden rules still apply to automated output** — number integrity, observation-not-advice, define
  every HL reference, and never assert a side without the positioned split.
