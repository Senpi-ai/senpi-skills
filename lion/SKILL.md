---
name: lion-strategy
description: >-
  LION v1.0 — Two-Speed-Market (K-Shaped) Cross-Asset Long/Short Hedge Fund. Two
  thematic books on two wallets, one producer. The LONG "haves" book longs the
  structural winners of a K-shaped world — the AI complex on Hyperliquid XYZ
  (trade.xyz: NVDA, AMD, MRVL, TSM, ASML, ARM, AVGO, CRWV, PLTR, ORCL, …) plus
  the crypto winners (HYPE large, SOL modest). The SHORT "have-nots" book shorts
  the laggards — the broad U.S. market via the SP500 index product (the "economy
  suffers" core) plus a curated, gated basket of laggard crypto alts. Both
  trend-confirmed on ABSOLUTE structure, with relative strength as a tiebreaker
  and per-name conviction sizing (HYPE big, SOL small, SP500 core). The edge is
  the DISPERSION between the two speeds, not market direction. Net exposure is an
  explicit operator decision (default modest net-long). NOT a copy-trader: each
  book ranks and scores its own curated, cross-asset universe; the runtime owns
  the LLM gate (pass-through), DSL exits, and all risk.guard_rails. LION_LEG env
  selects the book.
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

# 🦁 LION v1.0 — Two-Speed-Market (K-Shaped) Cross-Asset Long/Short

Lion bets on a **K-shaped divergence**: the **AI complex and HYPE/SOL keep
booming** while the **broad U.S. economy and the rest of crypto struggle**. It
expresses that as a single thematic long/short book:

- **LONG the "haves"** — the AI complex (semis / AI hardware / AI infra / AI
  software) plus crypto's winners (HYPE large, SOL modest).
- **SHORT the "have-nots"** — the broad U.S. market (the SP500 index product)
  plus a curated basket of laggard crypto alts.

The P&L is the **dispersion** between the two speeds, not the market's direction.
One producer script (`lion-producer.py`) serves both books; the `LION_LEG` env
var selects which.

| Book | Holds | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | AI complex + HYPE/SOL (LONG) | `LION_LONG_WALLET` | `runtime-long.yaml` | `lion_long_signals` |
| `short` | SP500 + laggard alts (SHORT) | `LION_SHORT_WALLET` | `runtime-short.yaml` | `lion_short_signals` |

## Why this is a *cross-asset* fund

Unlike the other long/short funds, Lion's two universes span **both tokenized
U.S. equities (Hyperliquid XYZ) and main-DEX crypto on one cross-margined
wallet**. `_dex_for()` routes each asset to its DEX automatically. The
`get_positions()` helper reads both the `main` and `xyz` clearinghouse sections
(two views of one wallet) and takes account value via `max()`, never the sum.

## The thesis, asset by asset

| Sleeve | Direction | Default names | Rationale |
|---|---|---|---|
| AI complex | LONG | NVDA, AMD, MRVL, MU, TSM, ASML, ARM, AVGO, CRWV, PLTR, ORCL, SMCI, DELL | The AI capex/compute cycle keeps compounding |
| Crypto winners | LONG | **HYPE (1.5× size)**, SOL (0.6× size) | HYPE keeps booming; SOL modest growth |
| Broad U.S. market | SHORT | **SP500 (1.2× size)** | The rest of the economy suffers |
| Laggard crypto | SHORT | ETH, XRP, DOGE, AVAX, LINK, ADA, LTC, NEAR, APT (0.7× size) | The rest of crypto struggles vs HYPE/SOL |

**The long-AI / short-SP500 overlap is intentional.** SP500 *contains* the AI
names; longing NVDA while shorting the index isolates the pure
**AI-outperforms-the-broad-market** spread — exactly the bet.

## How it picks (producer-side)

1. **Curated thematic universe** — `config.universe` (the haves for the long
   leg, the have-nots for the short leg), intersected with the live instrument
   board + a **budget-relative liquidity floor** (an instrument's 24h volume must
   be ≥ `liqVolMultiple` × your position notional — no hardcoded dollar floor, so
   a bigger book demands a deeper market). New listings auto-join once added.
2. **ABSOLUTE trend is the gate** — long a have only while its 4h structure is
   not bearish; short a have-not only while its 4h is not bullish. This is the
   hard gate: *never long a downtrend, never short an uptrend*, even for a
   thesis name.
3. **Relative strength is a tiebreaker** — cross-sectional excess vs the
   leg-universe mean tilts ranking and score, but does **not** bench a
   genuinely-trending winner on a day its peers ran harder.
4. **Conviction sizing** — `margin = account_value × marginPct ×
   sizingWeights[name]`. HYPE big, SOL small, SP500 core. No hardcoded dollars;
   every size scales with the budget.
5. **Guards** — RSI blow-off guard (long) / capitulation guard (short); held-asset
   dedup; per-candidate affordability cap (never emit an order the wallet can't
   fund).

## Net exposure is YOUR dial

Net exposure (the long/short balance) is an **operator decision**, not a
hardcoded constant — see each config's `_net_exposure_note`. The two books run
on **separate wallets**, so you set the posture by:

- **how much capital each wallet holds** (the primary lever), and
- the per-leg knobs: `maxSlots`, `marginPct`, `sizingWeights`.

The shipped default is a **modest net-long tilt (~60/40)**: the long book carries
more slots (5 vs 4), higher margin (18% vs 15%), and a higher leverage cap (5x vs
4x) than the short book. To run **market-neutral**, fund the wallets equally and
equalize `slots × marginPct`. To run **net-short**, tilt capital toward the short
book.

## Fleet-standard rules (enforced)

- **Max leverage 5x long / 4x short** (strict clamp + runtime gate; venue caps below).
- **Per-position margin ≤ 18% long / 15% short** (× conviction weight, ≤ 25% cap).
- **Drawdown halt** 20% long / 18% short from rolling peak, `reset_on_day_rollover`.
- **Mandatory DSL**; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Verbose per-tick JSON** (never silent): `scanned / ranked_pool / candidates /
  signals_pushed / emitted / mean_rs_24h`.
- **Sizes off `max(main, xyz)` account value** — never the sum (cross-margin
  two-views double-count fix).
- **Short squeezes respected**: short book runs tighter (lower leverage, tighter
  max-loss, faster stall-cuts, smaller alt weights, BTC omitted from the default
  short basket).

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Lion's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

Cloned from the Cougar helpers-native two-book pattern, generalized from a
single-universe equity cross-section to a **cross-asset thematic** book (xyz
equities + main-DEX crypto), with **absolute-trend gating** (vs Cougar's pure
cross-sectional gate) so a thesis winner is held while it trends, and **per-group
conviction sizing weights** so HYPE is sized larger than SOL without any
hardcoded dollar amount.
