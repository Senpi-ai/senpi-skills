# One book — partial subtotals, the comparison gate, who is in the question

The rules are under "One book — every wallet first-class" in `SKILL.md` (one list in the engine's order,
`book.total.line` verbatim, origin-aware protection, the comparison and the deep-dive question asked once,
at the end). This is the detail behind three of them.

## Which wallets make one row

One package is one strategy: its instances are
its wallets, grouped by the package they were deployed under (`skill_name`); a wallet with no package
stamp is its own row.

## A row or subtotal over only some of its wallets

- `value_read: "partial"` → a strategy whose value covers only some of its wallets: say "(1 of 2 wallets)" from `wallets_loaded` /
  `wallet_count`.
- `book.total.line` already words its subtotals this way. A subtotal over only some of
  its wallets says so ("1 of 2 wallets"); one whose wallets all failed is "unknown", never $0; with no
  saved wallets the line has no read-only clause.

## `book.comparison` — what the engine compares, and when

The engine computes
it — of the closes measurable against today's price, the share that got out ahead of the later move
(hindsight context, never a grade: guardrail 2) — only when 2+ wallets have `min_closes` (8)
measurable closes, and it names any wallet whose trades it couldn't read or that hit the fill ceiling.
`not_compared[]` lists each wallet left out and why.

## `book.deep_dive` — who is in the question

`order` holds only wallets with a value, an open position, trades
to review or a value that couldn't load — an empty $0 wallet stays in the table, never in the
question. The Senpi main wallet is never in it.
