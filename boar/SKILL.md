---
name: boar-strategy
description: >-
  BOAR v1.0 — Hard Money vs Paper (Debasement) Hedge Fund. Two books on two
  wallets, one producer. The LONG "hard-money" book longs scarce real assets —
  GOLD, BTC (digital gold), SILVER, PLATINUM, PALLADIUM. The SHORT "paper" book
  shorts the broad market (SP500) + rate-sensitive long-duration growth
  (RIVN/DKNG/HIMS) — the fiat-denominated financial claims that lose most as the
  term premium rises under fiscal dominance. The edge is the
  real-assets-beat-paper-claims spread. HONEST CAVEAT: the loosest hedge of the
  thesis-fund family — in a pure liquidity melt-up gold AND stocks can rise
  together; the short tilts to rate-sensitive names to tighten it. Trend-confirmed,
  conviction-sized. NOT a copy-trader; the runtime owns the LLM gate (pass-through),
  DSL exits, and all risk.guard_rails. BOAR_LEG env selects the book.
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

# 🥇 BOAR v1.0 — Hard Money vs Paper (the debasement bet)

Boar bets on **fiscal dominance / currency debasement**: as deficits compound and
the term premium rises, **scarce real assets (gold, BTC, silver) outperform
fiat-denominated financial claims**. It expresses that as a long/short:

- **LONG hard money** — `GOLD`, `BTC` (digital gold), `SILVER`, `PLATINUM`, `PALLADIUM`.
- **SHORT paper** — `SP500` (broad financial-beta) + rate-sensitive long-duration
  growth `RIVN` / `DKNG` / `HIMS` (the claims discounted hardest as rates rise).

The P&L is the **real-assets-beat-paper-claims spread**. One producer
(`boar-producer.py`) serves both books; `BOAR_LEG` selects.

| Book | Holds | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | hard money (LONG) | `BOAR_LONG_WALLET` | `runtime-long.yaml` | `boar_long_signals` |
| `short` | SP500 + rate-sensitive growth (SHORT) | `BOAR_SHORT_WALLET` | `runtime-short.yaml` | `boar_short_signals` |

## ⚠️ The honest caveat — this is the loosest hedge of the family

In a **pure liquidity melt-up**, *everything priced in debasing fiat rises* — gold
AND stocks — so the long and short legs can correlate **positively** and the hedge
underperforms. To tighten it, the short tilts toward **rate-sensitive long-duration
growth** (RIVN/DKNG/HIMS), which underperforms even in a melt-up because a rising
term premium compresses far-future cash-flow multiples. But the hedge is imperfect:
Boar works best in a *"real assets outperform"* regime, not a blow-off. The DSL owns
the drawdowns. Size this fund accordingly.

## The thesis, asset by asset

| Sleeve | Direction | Names | Rationale |
|---|---|---|---|
| Monetary metals | LONG | **GOLD** (1.2×), SILVER (1.0×), PLATINUM (0.7×), PALLADIUM (0.6×) | scarce real stores of value |
| Digital gold | LONG | **BTC** (1.2×) | the hardest money — co-core with gold |
| Broad paper | SHORT | **SP500** (1.2×) | the broad financial-claims anchor |
| Rate-sensitive paper | SHORT | RIVN, DKNG, HIMS (0.6×) | long-duration growth — hit hardest by a rising term premium (squeeze-prone, small) |

## How it picks (producer-side)

1. **Curated universe** + a **relative-to-market liquidity gate** (24h vol ≥
   `volFloorPctOfMedian` × the universe median — no $ floor).
2. **ABSOLUTE trend is the gate** — long hard money only while its 4h is not
   bearish; short paper only while its 4h is not bullish (+ capitulation guard).
3. **Relative strength is a tiebreaker**; **conviction sizing** via `sizingWeights`. No $.

## The long/short balance is YOUR dial

The split is the operator's **funding** across the two wallets (config
`_hedge_note`) — not hardcoded. Default a **net-long-hard-money tilt** (hard-money
4 slots / 18% / 5x; paper 4 slots / 15% / 4x). Fund 50/50 for a tighter (still
imperfect) hedge.

## Fleet-standard rules (enforced)

- **Max leverage 5x hard-money / 4x paper**; per-position margin ≤ 18% / 15% (× weight).
- **Drawdown halt** 20% / 18%, `reset_on_day_rollover`.
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Verbose per-tick JSON**; sizes off `max(main, xyz)` account value.
- **Paper book runs tighter** (lower leverage, tighter max-loss, faster stall-cuts);
  the rate-sensitive growth shorts are small + squeeze-prone, gated hard.

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Boar's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

A Lion-family two-book long/short pointed at the **debasement** macro divergence.
The loosest-hedged member of the family (documented honestly) — the short's
rate-sensitive tilt is the lever that tightens an inherently melt-up-correlated
pair. Best deployed as a tilt, not a market-neutral bet.
