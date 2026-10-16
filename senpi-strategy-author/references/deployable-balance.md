# The deployable balance — and the balance-bucket trap

`account_get_portfolio` returns several totals. **Only three of them are money a new strategy can be
funded from.** Quoting any of the others offers the user capital that is already working.

| field | what it is | deployable? |
|---|---|---|
| `total_in_hyperliquid` | free USDC in the **funding** wallet's perps account | **yes** |
| `total_spot_usd_in_hyperliquid` | its Spot balance | **yes** |
| `token_balances[]` | EVM stables (USDC / USDC.e / USDT), bridgeable | **yes** |
| `total_withdrawable` | free margin sitting **inside strategy wallets** | no — committed |
| `total_allocated_in_strategy` | margin backing open strategy positions | no — at risk |
| `total_balance_usd` | every bucket above, summed | no — meaningless as a budget |

**Deployable = the first three, added.** Nothing else.

## Why this has its own page

Two real failures, both in the same direction — reporting committed capital as spendable:

- On this repo's own portfolio fixture (`senpi-portfolio/tests/fixtures/portfolio_fixture.json`),
  `total_balance_usd` reads **$3,102.94** and `total_withdrawable` **$2,461.98**, while the truly free
  balance is **$1.51**. That account had $3,101.43 live inside strategies.
- On a real account, 2026-10-02: a catalog offer said *"You've got ~$6,470 free"* and proposed funding
  a new strategy. **$21.06** was free; the rest was running in seven strategies. The figure came from
  `senpi-strategy-discover`, which read `total_balance_usd` as the budget.

## A balance of 0 is a fact, not a failed read

The mechanism in both cases was an `or` chain:

```python
budget = portfolio.get("total_balance_usd") or portfolio.get("total_in_hyperliquid")   # WRONG
```

`total_in_hyperliquid` is **0** for a fully deployed user. Zero is falsy, so the chain decides the
field is missing and reaches for the next one — which is the committed-capital figure. The fallback
therefore fires at exactly the worst moment: when the user genuinely has nothing free.

Never write a fallback chain across these fields. Sum the three deployable ones and return the sum,
even when it is 0.

## How to report it

Free capital and committed capital are both worth saying. What is never acceptable is one number that
silently merges them.

| Say | Never say |
| --- | --- |
| "You've got **$21 free**. There's about $6,400 more inside your running strategies — freeing it means withdrawing from a strategy, which is your call." | "You've got ~$6,470 free" |
| "Nothing free right now. I can show you which strategy to withdraw from if you want to fund this." | A budget taken from `total_balance_usd` or `total_withdrawable` |

Moving money out of a running strategy is the user's decision, not a step to take on their behalf —
name the strategy and let them choose.

## The same rule elsewhere

Three other surfaces already state it; keep them aligned rather than inventing a fourth version.

- `senpi-portfolio/scripts/portfolio.py` — the canonical three-pool model, and the file that names
  this "the balance-bucket trap": `idle_in_embedded` / `idle_in_strategies` / `deployed`.
- `senpi-strategy-ops/references/lifecycle.md` — deploy preflight uses the accessible-USDC waterfall,
  "never `total_withdrawable`".
- `senpi-deposit-withdraw-transfer/SKILL.md` — a top-up must be gated on free perps
  (`withdrawable` from `strategy_get_clearinghouse_state`), never on the account value.

One further caution from that last one: `total_in_hyperliquid` is the perps **account value** — free
USDC *plus* margin locked in any position held in the funding wallet itself. It is the right field for
a deployable estimate, but for a hard gate on an exact amount, read `withdrawable` from the
clearinghouse.
