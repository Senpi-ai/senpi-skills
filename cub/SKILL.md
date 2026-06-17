---
name: cub-strategy
description: >-
  CUB v1.0 — Lion + Pre-IPO Hedge Fund. A variation of Lion that allocates ~90%
  of the budget to the Lion two-speed-market (K-shaped) AI long/short engine and
  ~10% to a pre-IPO ramp satellite. THREE books on three wallets, one producer.
  The LONG "haves" book + SHORT "have-nots" book ARE Lion (long the AI complex +
  HYPE/SOL, short the broad U.S. market via SP500 + laggard alts, trend-confirmed,
  conviction-sized) — the 90%. The PREIPO book auto-discovers pre-IPO perpetuals
  (IPOPs) on Hyperliquid XYZ by their funding signature (Lemur method) and LONGS
  the ones ramping into their listing (the SpaceX / Cerebras pattern) — the 10%
  high-optionality satellite that catches the next AI winner before it converts
  to a standard equity (at which point it graduates into the haves book). The
  90/10 split is the operator's funding allocation across the three wallets, not
  a hardcoded constant. NOT a copy-trader; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. CUB_LEG env selects the
  book (long | short | preipo).
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

# 🐾 CUB v1.0 — Lion + Pre-IPO

Cub is a **variation of [Lion](../lion/)** that pairs the full Lion two-speed AI
long/short engine (the **90%**) with a **pre-IPO ramp satellite** (the **10%**):

- **~90% → the Lion engine.** LONG the "haves" (the AI complex + crypto winners
  HYPE/SOL) and SHORT the "have-nots" (the broad U.S. market via SP500 + laggard
  crypto alts) — a K-shaped dispersion bet, trend-confirmed and conviction-sized.
- **~10% → the pre-IPO satellite.** Auto-discovers pre-IPO perpetuals (IPOPs) by
  their funding signature and LONGS the ones ramping into their listing — the
  SpaceX / Cerebras pattern. High-optionality: catches the next AI winner *before*
  it converts to a standard equity (at which point it graduates into the haves
  book automatically).

One producer script (`cub-producer.py`) serves all three books; the `CUB_LEG` env
var selects which.

| Book | Holds | Alloc | Wallet env | Runtime | Scanner |
|---|---|---|---|---|---|
| `long` | AI complex + HYPE/SOL (LONG) | part of 90% | `CUB_LONG_WALLET` | `runtime-long.yaml` | `cub_long_signals` |
| `short` | SP500 + laggard alts (SHORT) | part of 90% | `CUB_SHORT_WALLET` | `runtime-short.yaml` | `cub_short_signals` |
| `preipo` | Discovered IPOPs (LONG, ramp) | 10% | `CUB_PREIPO_WALLET` | `runtime-preipo.yaml` | `cub_preipo_signals` |

## The 90/10 allocation is YOUR funding split

The three books run on **separate wallets**, so you set the 90/10 by how much
capital you fund into each — **not a hardcoded constant**. Default: fund the
`long` + `short` wallets with **~90%** of the combined pool (split between them
by Lion's net-exposure dial, default modest net-long ~60/40 long:short), and the
`preipo` wallet with **~10%**. To run the AI engine pure, just don't fund the
preipo wallet. To lean more into pre-IPO optionality, raise its share.

## The pre-IPO satellite (Lemur method)

Each tick the `preipo` book pulls the live xyz board and flags **IPOPs** — pre-IPO
perpetuals carry a structural signature: `|funding| ≤ ipopFundingMaxAbs` AND venue
`max_leverage ≤ ipopMaxLeverageCap` (the throttled pre-listing state). It LONGs the
IPOPs in a confirming 4h/1h uptrend (same absolute-trend gate as the haves book),
sized off a **budget-relative** liquidity floor (no hardcoded $). It's **episodic**
— most ticks may find 0–2 IPOPs. When an IPOP IPOs and converts (funding jumps
~100×, leverage cap lifts), it drops out of this universe; if it's an AI name it
becomes eligible for the haves book. SM is a bonus, not a gate (pre-IPO SM data is
sparse).

## Why this is a *cross-asset* fund

Unlike the other long/short funds, Cub's two universes span **both tokenized
U.S. equities (Hyperliquid XYZ) and main-DEX crypto on one cross-margined
wallet**. `_dex_for()` routes each asset to its DEX automatically. The
`get_positions()` helper reads both the `main` and `xyz` clearinghouse sections
(two views of one wallet) and takes account value via `max()`, never the sum.

## The thesis, asset by asset

| Sleeve | Direction | Default names | Rationale |
|---|---|---|---|
| AI chips | LONG | NVDA, AMD, MRVL, ARM, AVGO, INTC, TSM, ASML, CBRS | The compute layer of the AI boom |
| AI memory | LONG | MU, SMSN, SKHX, SNDK | HBM/DRAM/NAND — the AI-capex supply squeeze |
| AI infra / cloud | LONG | CRWV, NBIS, DELL, LITE | GPU cloud, AI servers, datacenter optics |
| AI software / hyperscalers | LONG | GOOGL, MSFT, META, AMZN, ORCL, PLTR, NOW, IBM | The model + cloud layer |
| Frontier compute | LONG | **SPCX** (owns xAI), **QNT** (quantum+ML) | Speculative AI optionality — sized smaller |
| Crypto winners | LONG | **HYPE (1.5× size)**, SOL (0.6× size) | HYPE keeps booming; SOL modest growth |
| Broad U.S. market | SHORT | **SP500 (1.2× size)** | The rest of the economy suffers |
| Laggard crypto | SHORT | ETH, XRP, DOGE, AVAX, LINK, ADA, LTC, NEAR, APT (0.7× size) | The rest of crypto struggles vs HYPE/SOL |

The AI long basket is the full live-board AI complex (27 names), resolved against
the trade.xyz instrument board on 2026-06-17. Speculative frontier names size
smaller (SPCX 0.6×, QNT 0.5×, CBRS/NBIS 0.7×); the core megacap chips and
hyperscalers run full slots. The universe auto-extends as new AI names list.

**The long-AI / short-SP500 overlap is intentional.** SP500 *contains* the AI
names; longing NVDA while shorting the index isolates the pure
**AI-outperforms-the-broad-market** spread — exactly the bet.

## How it picks (producer-side)

1. **Curated thematic universe** — `config.universe` (the haves for the long
   leg, the have-nots for the short leg), intersected with the live instrument
   board + a **relative-to-market liquidity gate** (an instrument's 24h volume
   must be ≥ `volFloorPctOfMedian` × the universe's median volume — no hardcoded
   dollar floor; it adapts to the market and drops anomalously-thin names). New
   listings auto-join once added.
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
`strategy_close*` tool against Cub's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

Cloned from the Cougar helpers-native two-book pattern, generalized from a
single-universe equity cross-section to a **cross-asset thematic** book (xyz
equities + main-DEX crypto), with **absolute-trend gating** (vs Cougar's pure
cross-sectional gate) so a thesis winner is held while it trends, and **per-group
conviction sizing weights** so HYPE is sized larger than SOL without any
hardcoded dollar amount.
