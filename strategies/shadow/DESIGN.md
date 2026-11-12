# Shadow — blocked, not deployable (2026-10-12)

Shadow is **tracked and tested in this repo but removed from the catalog**. It cannot work
against the data source it reads. This file records the measurement so nobody re-derives it,
and specifies what would unblock it.

## The premise, and why it can't hold

Shadow mirrors a vetted trader's **newly opened** position, and only while that position is
still young — `maxEntryAgeSeconds: 240` — and still near the trader's fill
(`maxEntrySlippagePct: 1.5`). That premise requires the trader's position to be *visible to us*
within four minutes of them opening it.

It is not. Position state reaches us roughly **two hours late**.

Measured on `hyperliquid.hyperliquid_position_states`, `event_type = 'start'`, as
`updated_at - trade_time`:

| day | position opens | median lag | available inside 240s |
|---|---:|---:|---:|
| 2026-09-26 | 9,963 | 133.0 min | **0.0%** |
| 2026-09-28 | 28,607 | 138.7 min | **0.0%** |
| 2026-09-30 | 21,870 | 144.6 min | **0.0%** |
| 2026-10-02 | 22,936 | 131.9 min | **0.0%** |
| 2026-10-05 | 17,334 | 134.9 min | **0.0%** |
| 2026-10-07 | 23,774 | 130.6 min | **0.0%** |
| 2026-10-08 | 34,877 | 141.1 min | **0.0%** |
| 2026-10-09 | 21,192 | 134.4 min | **0.2%** |

Fourteen complete days, ~280,000 position opens, **essentially none** of them visible inside
shadow's window. This is chronic, not a regression — the lag is flat across the whole period.

A worked case from one live install (2026-10-09): watched trader
`0xcb5c2f378860437fad512c3aec09efce1c470283` opened ZK LONG from flat at **18:20:27.714Z**
(5x, $4,003 margin, taker, no TWAP, position held and grew). That row was not written to
position state until **20:00:04.012Z** — a lag of **5,976 s (1 h 40 m)**. Scan ticks ran at
+3.5 s and +124 s, both inside the 240 s window, and both correctly reported `fresh opens 0`,
because the position did not yet exist in the data we read.

**So the scanner is behaving exactly as written.** `opened_within` rejects positions whose
reported age exceeds 240 s, and by the time one is visible its age is ~2 h. Nothing is
mis-tuned.

## This also explains the pre-2.0.0 behaviour

Shadow 1.3.0 logged **1,153 chase rejections** (2026-09-25 → 10-08) with a median distance of
**14.9%** past the trader's fill, minimum 3.04%, and **nothing at all in the 1.5–3.0% band**.
Those were never fresh opens. They were ~2-hour-old positions becoming visible, and the
±1.5% slippage cap was the only thing preventing users from mirroring a move that had already
happened. The cap was not a nuisance; it was the only functioning safety in the design.

## What it cost

Ten installs, 2026-09-25 → 10-12. Five ever filled — all on the 1.3.0 build, where stale
positions reached the order path. Net realized **−$31.44** (worst: one wallet −$27.97 over
96 fills). As of 2026-10-12 **every one of the ten wallets holds $0.00**; all capital was
withdrawn. Two installs still arrived after the diagnosis was known, which is why this is
delisted rather than left pending a fix.

## What would unblock it

The cohort *selection* is fine and should not change — `top_perps_traders_7d` is current, and
`_resolve_cohort` was verified against it (funnel reproduced to 32 survivors, MM and quality
counts within 1). The problem is only the **per-tick book read**.

1. Point `_fetch_states` at Hyperliquid's public `clearinghouseState`, which is live,
   unauthenticated, and has no indexing lag. It returns coin, `szi` (size + side), `entryPx`
   and leverage — everything the diff and the slippage check need.
2. It does **not** return a position open timestamp, so freshness can no longer come from a
   reported age. Use the transition test (`diff_fresh`: present this tick, absent last tick)
   as the sole freshness signal — at a 120 s cadence that bounds detection to ≤120 s by
   construction, which is tighter than the 240 s the age gate was asking for. Keep
   `opened_within` only where an age is actually available.
3. Read the cohort **sequentially** — the HL info endpoint rate-limits parallel reads.
   Ten wallets per 120 s tick is comfortably inside the limit.
4. The `#664` guard must stay: on a failed read, carry the already-seeded book forward rather
   than emptying the seen-set, or the next tick reads the trader's whole standing book as
   fresh opens.

### Before relisting, prove it fires

The economics are already verified and they are good — the opportunity is real, it was only
ever invisible. Across the live cohort's **71** new positions in the 7 days to 2026-10-12, all
69 that could be priced were **within ±1.5% of the trader's entry one poll later** (median
0.101%, p90 0.44%, worst 1.19%). Every one would have passed the slippage cap, and the cohort
opens roughly **10 new positions a day**.

So the relist gate is a live one: run the fixed scanner on a single funded box and show
`fresh opens` going above zero and at least one `EMIT` against a chain-confirmed open, with the
entry inside the cap. Until that is on the board, this package stays blocked.
