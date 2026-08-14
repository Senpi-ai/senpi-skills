# Monitoring a running mirror — the read contract

Use this when a user asks about a mirror that's already live: *"how's my mirror doing?"*, *"compare it to
my old mirror"*, *"is my trader still active — no trades today?"*, *"why didn't it pick up 0x…?"*, *"should
I close the positions that drifted from the trader?"*. It **composes existing read surfaces** — it does not
introduce a new engine. Read-only: report and recommend; any close/withdraw/rebalance is a **senpi-strategy-ops**
action the user confirms.

## The three reads
1. **Your mirror** — `strategy_get_clearinghouse_state` on the mirror wallet (or **senpi-portfolio** for the
   full performance read): your open positions, entry vs mark, unrealised PnL, margin.
2. **The trader (OG)** — **senpi-trader-research** `--trader 0x…`: their `current_positions` (with
   `moved_from_entry_pct`), `last_trade_days_ago` / `trades_per_day` (whether they're still active), `momentum`
   (hot/cold — this is 4h **PnL direction**, NOT an activity signal), and `mirrorability`.
3. **The diff** — line the two books up by asset. That diff is the whole answer.

## Answer each recurring question from the diff

- **"How's my mirror doing?"** — your positions + unrealised PnL (read 1), then the one-line context read 2
  gives: are you still tracking the OG, or have your books diverged?
- **"Compare to my other mirror."** — run read 1 on each mirror wallet; compare realised + unrealised PnL,
  utilisation, and how closely each still tracks its OG. Rank by *tracking + PnL*, not PnL alone.
- **"Is my trader still active? No trades today."** — judge idleness by **`last_trade_days_ago` /
  `trades_per_day`** and an unchanged book since your last check (**not** `momentum` — that's 4h PnL direction,
  not activity) → **the trader is idle, not a bug.** A mirror only fires when the OG does;
  say that plainly instead of implying the mirror is broken.
- **"Why didn't it pick up `<coin>` / fire?"** — the OG holds it but you don't. Two honest causes: (a)
  **slippage-skipped** — `moved_from_entry_pct` on that position is beyond your slippage tolerance (it had
  already run when your mirror looked), or (b) **budget-floored** — the copy scaled below the ~$12 minimum.
  Name which. Don't say "it will sync shortly" — it won't; the position is skipped until they re-enter fresh.
- **"Should I close the positions that drifted?"** — a position where **the OG has since exited** but you're
  still holding, or where your entry sits far from theirs (large `moved_from_entry_pct`), is a drift
  candidate. If the OG closed it and your mirror didn't follow, flag it — the mirror should have closed on
  their exit; verify on-chain before acting. Closing is a **senpi-strategy-ops** action; recommend, don't
  auto-execute.

## Don'ts
- Never predict a sync that hasn't happened ("it'll replicate shortly") — verify the OG's live book first.
- Never call an idle OG a broken mirror.
- Never close/withdraw/rebalance from here — that's **senpi-strategy-ops**, on the user's confirmation.
