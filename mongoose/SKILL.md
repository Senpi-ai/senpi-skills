---
name: mongoose-strategy
description: >-
  MONGOOSE v1.0 — On-Chain Finance vs Legacy Hedge Fund. Two books on two
  wallets, one producer. The LONG "on-chain" book longs the on-chain financial
  rails on Hyperliquid — HYPE (the venue), CRCL (Circle/stablecoins), COIN
  (Coinbase), HOOD (Robinhood), MSTR + PURRDAT (crypto treasuries) — the
  disruptors eating legacy finance. The SHORT "legacy" book shorts the incumbents
  + broad financial-beta (BX/Blackstone + SP500). Trend-confirmed on ABSOLUTE
  structure, per-name conviction sizing (HYPE/CRCL big; levered treasury proxies
  small). The edge is the disruptor-vs-incumbent spread. NOTE: the hedge is
  cross-sector and the short universe is thin, so it leans on the index — a looser
  hedge than a same-sector pair. NOT a copy-trader; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. MONGOOSE_LEG env selects the book.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🪙 MONGOOSE v1.0 — On-Chain Finance vs Legacy

Mongoose bets that **money is migrating on-chain** — stablecoins, crypto
exchanges, and BTC treasuries are eating legacy financial rails. It expresses
that as a long/short:

- **LONG the on-chain rails** — `HYPE` (the venue), `CRCL` (Circle/stablecoins),
  `COIN` (Coinbase), `HOOD` (Robinhood), `MSTR` + `PURRDAT` (crypto treasuries).
- **SHORT legacy finance** — `BX` (Blackstone) + `SP500` (broad financial-beta).

The P&L is the **disruptor-vs-incumbent spread**. One producer
(`mongoose-producer.py`) serves both books; `MONGOOSE_LEG` selects.

| Book | Holds | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | on-chain rails (LONG) | `MONGOOSE_LONG_WALLET` | `runtime-long.yaml` | `mongoose_long_signals` |
| `short` | BX + SP500 (SHORT) | `MONGOOSE_SHORT_WALLET` | `runtime-short.yaml` | `mongoose_short_signals` |

## The thesis, asset by asset

| Sleeve | Direction | Names | Rationale |
|---|---|---|---|
| The venue | LONG | **HYPE** (1.3×) | the on-chain exchange itself — core of the thesis |
| Stablecoin rails | LONG | **CRCL** (1.2×) | Circle/USDC — the clearest on-chain-finance winner |
| Crypto brokers/exchanges | LONG | COIN, HOOD | Coinbase, Robinhood |
| Crypto treasuries | LONG | MSTR (0.7×), PURRDAT (0.6×) | BTC/HYPE treasury proxies — sized down (levered, double-count) |
| Legacy finance | SHORT | BX (0.8×), SP500 (1.2×) | the incumbents + broad financial-beta — the hedge |

## The honest caveat — the hedge is loose

The short leg is **cross-sector and thin**: few pure legacy-finance names (banks,
payment incumbents) list on the venue yet, so the hedge leans on `SP500` + the one
liquid legacy name (`BX`). It **nets down market beta** rather than perfectly
isolating disruptor-vs-incumbent. As more legacy-finance names list, add them to
`config.universe` on the short book to sharpen the pair. The long book is the
strong, high-conviction side.

## How it picks (producer-side)

1. **Curated universe** intersected with the live board + a **relative-to-market
   liquidity gate** (24h vol ≥ `volFloorPctOfMedian` × the universe median — no $ floor).
2. **ABSOLUTE trend is the gate** — long a rail only while its 4h is not bearish;
   short an incumbent only while its 4h is not bullish (+ capitulation guard).
3. **Relative strength is a tiebreaker** — tilts ranking/size, never benches a trender.
4. **Conviction sizing** — `margin = account_value × marginPct × sizingWeights[name]`. No $.

## The long/short balance is YOUR dial

The split is the operator's **funding** across the two wallets (config
`_hedge_note`) — not hardcoded. Default a **net-long tilt** (the thesis is bullish
the disruptors; long book 5 slots / 18% / 5x, legacy book 2 slots / 15% / 4x).
Fund 50/50 for a tighter (still cross-sector) hedge.

## Fleet-standard rules (enforced)

- **Max leverage 5x on-chain / 4x legacy**; per-position margin ≤ 18% / 15% (× weight).
- **Drawdown halt** 20% / 18%, `reset_on_day_rollover`.
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Verbose per-tick JSON**; sizes off `max(main, xyz)` account value.
- **Legacy book runs tighter** (lower leverage, tighter max-loss, faster stall-cuts).

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Mongoose's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

A Lion-family two-book long/short pointed at the **on-chain-finance disruption**.
Strong long book (all hot, live names); the short is the deliberately-thin,
index-led hedge — documented honestly — to be sharpened as more legacy-finance
names list on the venue.
