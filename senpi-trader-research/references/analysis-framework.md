# Analysis framework — vetting a trader before you copy

The engine hands you a track record, labels, current positions, and 4h momentum. This is how you turn
that into a copy decision a user can trust. **The job is separating a proven edge from a lucky streak.**

## 1. Is the sample even trustworthy? (gate first)

Before anything else, check the reliability gate. A glittering ROI on **< 5 trades or < 7 active days**
is noise — the engine flags it `thin_track_record`. Senpi Discovery itself won't rank a trader as
reliable below that floor. Lead with this when it applies: *"+340% sounds great, but it's 3 trades over
4 days — that's not a track record yet."* Never recommend a copy off a thin sample.

## 2. Track record vs. timing — two different clocks

- **Discovery (historical)** answers *is this trader good* — ROI, win rate, max drawdown, consistency
  over weeks/months. This is the due-diligence layer.
- **Leaderboard (4h)** answers *are they hot right now* — current rank, recent delta PnL. This is the
  timing layer.

"Should I copy?" needs both. A trader with a great history who's stone cold in the 4h window is a
different decision than one who's also surging — and a hot 4h with no history is the classic trap.
Always say which clock each number is on.

## 3. Read the behavior labels, don't just cite them

- **Consistency** (ELITE / RELIABLE / STREAKY / CHOPPY) — the single most useful label for copy. ELITE/
  RELIABLE = the returns are repeatable; STREAKY/CHOPPY = high variance, you might copy in at the wrong
  point of the cycle. `choppy_consistency` is a real flag, not a nit.
- **Risk** (CONSERVATIVE / BALANCED / AGGRESSIVE / SNIPER) — tells the user what *they're* signing up
  for. Copying a SNIPER means inheriting SNIPER drawdowns. Match it to the user's stomach.
- **Activity** (DEGEN / ACTIVE / TACTICAL / PATIENT) — sets expectations on turnover (and fees).

## 4. What they hold *now* is part of the diligence

A great history doesn't matter if they're currently over-extended. Read `current_positions` +
`net_exposure`:
- **`margin_pct`** is the risk tell — > 80 is high, > 90 is critical (near liquidation). Copying a
  trader who's already at 90% margin usage means inheriting that liquidation risk on day one.
- **Concentration** — `concentrated_book` means one position dominates; their result will swing on a
  single name.
- **Net bias + drawdown** — are they long or short, and are they currently green or bleeding
  (`currently_in_drawdown`)? You're copying into their *current* book, not their historical average.

## 5. Compose the verdict

One honest sentence first, then the support. Examples:

> "Proven copy candidate: RELIABLE/BALANCED, +62% over 90 days across 140 trades, 58% win rate, −18%
> max drawdown — and currently hot (rank 12 in the 4h window). Risk to note: 84% margin usage right
> now, so they're running hot into this — size the copy accordingly."

> "I'd pass for now: the +210% is real but it's STREAKY over just 6 trades — that's a hot streak, not a
> track record. Worth watching, not copying yet."

That's the difference between "this trader is up a lot" and a copy call worth standing behind.
