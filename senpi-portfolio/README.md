# senpi-portfolio

A Senpi skill that answers **"analyze my portfolio across all wallets"** with a precise, real-time
wallet/balance breakdown and genuine analysis — not a flat data dump.

Modeled on `senpi-strategy-discover` / `senpi-market-pulse` / `senpi-smart-money`: a **hidden
deterministic engine** does the multi-wallet real-time pull + the balance taxonomy; the **LLM
(SKILL.md)** does the analysis and the CTAs.

## The problems it solves

1. **Wallet clarity.** Main embedded wallet vs. per-strategy sub-wallets vs. funds deployed in
   positions vs. sitting idle — computed explicitly and labeled by location.
2. **The idle-cash trap.** Agents conflate `total_withdrawable` (free margin *inside strategy
   wallets*) with "idle cash in the embedded wallet." The engine splits these into two structurally
   separate fields so the agent can never mix them. (This is the exact bug that mislabeled $1,157 of
   strategy margin as embedded idle when the embedded wallet held $0.)
3. **No cached data.** `account_get_portfolio` caches HL data for 12h — the engine **always** passes
   `forceFetch: true`, and reads each strategy's live `strategy_get_clearinghouse_state`.
4. **Analysis, not a dump.** Every position is compared to the market (24h move, with/against the
   tape, leveraged return); the portfolio gets net-exposure, concentration, and idle-drag reads.
5. **Funding-ledger correctness.** Surfaces `total_funded` / `total_withdrawn` so a small balance
   from withdrawn profits isn't misread as a loss.

## The three buckets

```
grand_total = idle_in_embedded      (HL USDC + EVM USDC in the main wallet — truly free)
            + idle_in_strategies     (free margin inside strategy wallets — == total_withdrawable)
            + deployed_in_positions  (margin backing open trades)
```

`total_withdrawable` is **bucket 2, not bucket 1.** That distinction is the whole point of the skill.

## Architecture

```
scripts/portfolio.py   hidden engine — user_get_me (embedded addr) + account_get_portfolio
                       (forceFetch) + strategy_list + per-wallet strategy_get_clearinghouse_state +
                       per-asset market_get_asset_data → three-bucket taxonomy + exposure + signals
scripts/mcp_client.py        self-contained streamable-HTTP MCP client (stdlib only; read-only)
SKILL.md               the analyst — wallet model + bucket contract + position-vs-market + 2 CTAs
references/analysis-framework.md   the money map, position-vs-market, exposure/concentration/idle-drag
tests/                 offline fixture test (no network) — guards the conflation bug
```

## How it works

1. **Embedded wallet** — `user_get_me` for the embedded address; `account_get_portfolio(forceFetch)`
   for `total_in_hyperliquid` (nested under a `portfolio` wrapper) + EVM `token_balances` = idle-in-embedded (the only truly free
   cash). Also grabs the portfolio aggregate for a reconciliation cross-check.
2. **Strategy wallets** — `strategy_list` → per-wallet `strategy_get_clearinghouse_state` (live, both
   DEXes). Each wallet's `withdrawable` = its idle margin (bucket 2); its positions = deployed.
3. **Market context** — capped per-asset `market_get_asset_data` for each holding's 24h move →
   `vs_market` (with/against the tape) on every position.
4. **Compute** — the three buckets, net exposure (long/short, by asset), concentration, idle drag,
   and a `reconciles` flag that flags drift between the per-wallet sum and the portfolio aggregate.

Fails open end-to-end; partial data still returns valid JSON with `meta.warnings`.

## Run

```sh
python3 scripts/portfolio.py             # full real-time pull (all wallets + market context)
python3 scripts/portfolio.py --no-market # skip the per-asset market enrichment
python3 scripts/portfolio.py --dry       # dump raw MCP responses for schema debugging
python3 scripts/portfolio.py --fixture tests/fixtures/portfolio_fixture.json   # offline (tests)
```

## ⚠ Token scope

Every tool here is **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`.
An app-scoped token returns no wallet data and the engine sets `meta.degraded` — the SKILL says so
rather than reporting "$0."

## Status / review notes

- **Field names verified against the Senpi MCP tool schemas + overview guide** (`total_withdrawable`,
  `total_in_hyperliquid`, `total_allocated_in_strategy`, `token_balances` — all nested under `portfolio`; clearinghouse
  `marginSummary.accountValue` / `withdrawable` / `assetPositions[].position`). The engine reads
  defensively with alias fallbacks — run `--dry` to confirm the live shape and adjust if needed.
- Offline fixture test: guards that idle-in-embedded ≠ `total_withdrawable`, plus position/exposure
  computation. No network required.
