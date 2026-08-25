# Running senpi-signals on a schedule

**Why this matters:** the flagship detector, `sm_positioning_build`, compares the proven cohort's
positioning **now** against **~12h ago** ("43% of the top 1,000 now hold HYPE shorts, up from 38%").
It can only fire if a snapshot from ~12h ago exists in the state ring. **Nothing else keeps that
history**, so on a cold state file the best signal the skill has is silent. A schedule is what turns
it on. Everything else here follows from that.

## The one job you actually need

One scheduled sweep, every **~45 minutes**, doing the normal full gather and run:

```bash
python3 scripts/score.py current.json --consumer social --out signals.md
```

That single job does both things: it **produces the content feed** *and* **writes the snapshot** that
becomes tomorrow's 12h baseline. Match the interval to `FRESH_WINDOW_MIN` (~45 min) so a name you
just posted has rotated out by the next run.

State is durable and shared — leave `--state` off and it resolves to
`$SENPI_STATE_DIR/signals/state.json` (`/data/.openclaw/senpi-state/signals/state.json` on the claw,
the Railway persistent volume). Users' ad-hoc runs read the same warm baseline automatically.

## ⚠️ The one rule that will bite you: every snapshot must carry the SAME fields

The ring is one shared history. `_pick_baseline` selects the most recent snapshot **older than the
target age** — it does not care which job wrote it. So if a cheap job writes snapshots holding only
`smart_share`, and one of those becomes the ~1h baseline, then `oi_surge` and `funding_flip` have no
prior to diff against and **go quiet without erroring**.

**So: any job that writes to the ring gathers the full `asset_metrics` shape** — `oi`, `price`,
`price_change_pct`, `smart_dir`/`smart_share`/`smart_long_n`/`smart_short_n`, `crowd_dir`,
`funding_pctile`, `funding_annualized_pct`, `notional_vol`, `dex`. A partial snapshot is worse than
no snapshot, because it silently displaces a complete one.

## `--snapshot-only` (optional second job)

```bash
python3 scripts/score.py current.json --snapshot-only
```

Records the reading into the ring and exits: **no detection, no ranking, no feed, and freshness is
NOT touched** — so it never consumes the anti-repeat budget the content run depends on. It prints
`history_span_hours` and `trend_ready`, so it doubles as the health check for "is the 12h detector
armed yet?"

Use it when you want history denser than your content cadence (say a 15-min warmer beside a 45-min
content run), or to backfill after downtime. It still must gather the full field set — see above.

## What to expect on a cold start

| Time since first run | What fires |
|---|---|
| Run 1 | Only static-state signals (e.g. `funding_extreme`). No diffs exist yet — **expected, not a bug.** |
| ~1h+ | Fast detectors wake: `oi_surge`, `funding_flip`, `sm_conviction`. |
| ~12h+ | **`sm_positioning_build` arms** — the flagship read. `trend_ready: true`. |

Check readiness any time with a `--snapshot-only` run and read `trend_ready`.

## Guardrails for an automated run

- **An empty feed means nothing new happened.** Do not lower the bar, widen the window, or backfill to
  hit a post count. A quiet market correctly produces a quiet feed.
- **Only the social feed is rotated.** The trade lens is deliberately not freshness-gated — a standing
  setup is still a setup for a user asking on demand.
- **Keep consumers separate.** The cron runs `--consumer social`; users default to `adhoc`. They share
  one baseline ring but keep independent anti-repeat memories, so the cron never blanks a user's feed.
- **Golden rules still apply to automated output** — number integrity, observation-not-advice, define
  every HL reference, and never assert a side without the positioned split.
