# senpi-trader-research

A Senpi skill that answers **"who should I copy?"** and **"is this trader any good?"** — it ranks
Hyperliquid traders by track record and builds a due-diligence dossier on a specific trader before you
mirror them.

Same hidden-engine pattern as `senpi-market-pulse` / `senpi-smart-money` / `senpi-portfolio`: a
deterministic Python engine does the data work; the LLM (SKILL.md) does the judgment + CTAs.

Collapses these read tools off the eager tool list (per the context-reduction plan):
`discovery_get_top_traders`, `discovery_get_top_strategies`, `discovery_get_trader_history`,
`discovery_get_trader_state`, `leaderboard_get_trader`, `leaderboard_get_trader_positions`.

## Two modes

- **Find** (`research.py`) — historical track-record ranking with behavior labels + a reliability
  verdict per trader. Or `--strategies` for the top copy-trading (mirror) leaderboard.
- **Vet** (`research.py --trader 0x…`) — a dossier: track record + labels + current positions (with
  account risk) + 4h momentum + risk flags. Distinguishes *proven* from *a hot streak*.

## Key reads

- **Track record ≠ timing.** Discovery = historical (is the trader good); leaderboard = 4h (are they
  hot now). A copy decision needs both.
- **Reliability floor:** < 5 trades or < 7 active days → `thin_track_record` (Senpi Discovery's own
  trust threshold). The engine flags it; the SKILL surfaces it loudly.
- **Risk flags:** `choppy_consistency`, `high`/`critical_margin_usage` (>80 / >90), `currently_in_drawdown`,
  `concentrated_book`.

## Run

```sh
python3 scripts/research.py                                   # top traders this month
python3 scripts/research.py --time-frame ALL_TIME --sort-by WIN_RATE --limit 15
python3 scripts/research.py --trader 0xABC…                   # vet one trader
python3 scripts/research.py --strategies                      # top copy strategies
python3 scripts/research.py --dry                             # raw schema dump
python3 scripts/research.py --fixture tests/fixtures/research_fixture.json   # offline (tests)
```

## ⚠ Token scope

`discovery_*` needs a **USER-scoped** `SENPI_AUTH_TOKEN` (resolves a user id). App-scoped → empty
rankings + `meta.degraded`.

## Status / review notes

- Input params/enums verified against the live MCP tool schemas (`time_frame`, `sort_by`, label enums,
  `trader_addresses`). Output field names use defensive alias fallbacks — run `--dry` to confirm the
  live response shape and adjust if needed.
- CTA 1 routes to `strategy_create` (copy path); confirm budget + multiplier before creating.
- Offline fixture test included; no network.
