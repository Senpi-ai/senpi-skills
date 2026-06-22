---
name: hydra-strategy
description: >-
  HYDRA v2.0 — Single-Coin Conviction Fund with a CROSS-ASSET hedge. A long-term
  conviction book on ONE major (the thesis), cushioned by a diversified short of
  OTHER assets. ONE producer, parameterized by HYDRA_COIN + HYDRA_LEG (core | dip |
  hedge). CORE rides the coin's trend as a LONG-TERM HOLD on a catastrophic-only
  DSL (you don't get shaken out on normal volatility). DIP buys pullbacks within a
  confirmed uptrend (tactical add). HEDGE (v2.0) shorts a diversified BLEND of
  OTHER assets that are actually breaking down — vol-parity sized, scaled up when
  the thesis coin is itself breaking — so it cushions market risk WITHOUT betting
  against the thesis. Never shorts the thesis coin. Deploy for ETH, SOL, HYPE (or
  any major) = three wallets each. NOT a copy-trader; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "2.1.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐉 HYDRA v2.0 — Single-Coin Conviction Fund (cross-asset hedge)

Hydra is a **long-term conviction book on one coin**, cushioned by a **diversified
short of *other* assets** — so the hedge protects against the market risk that
would also hurt your coin, *without ever betting against your own thesis*. One
producer; `HYDRA_COIN` sets the thesis asset, `HYDRA_LEG` sets the head.

| Head | What it does | Direction | DSL |
|---|---|---|---|
| **core** | the **long-term thesis** — trend-confirmed, conviction-tiered | LONG up / SHORT down | **catastrophic-only** (ride it) |
| **dip** | the **tactical add** — buys pullbacks within a confirmed uptrend | LONG only | moderate (bank the bounce) |
| **hedge** | the **cross-asset cushion** — shorts a diversified blend of *other* breaking assets | SHORT only (never the thesis coin) | tight-ish (bank the cushion) |

**The two v2.0 ideas:**
1. **The thesis rides.** The core is a multi-month conviction position on a loose,
   catastrophic-only DSL — you're not shaken out by normal volatility. The hedge,
   not a tight stop, handles the interim drawdown.
2. **The hedge is cross-asset.** Shorting your own thesis coin fights your conviction
   and whipsaws. Instead the hedge shorts a diversified blend of *other* assets
   (BTC, other majors, indices, high-beta names) — only the ones actually in a
   confirmed downtrend, vol-parity sized, and **sized up when the thesis coin itself
   is breaking down.** It auto-scales: ~nothing to short in a calm uptrend (light),
   many names breaking in a broad selloff (heavy). Long the conviction, short the
   carnage elsewhere.

**Net:** a long-term conviction long that you *hold through volatility*, cushioned
by a diversified short that grows as the market breaks — without ever shorting the
coin you believe in.

**v1.0 → v2.0:** v1.0's hedge was a *same-asset* stress short on the thesis coin
(zero basis risk, but it lagged, whipsawed on V-recoveries, and fought the thesis),
and the core ran a tighter ratcheting DSL. v2.0 makes the core a long-term hold and
the hedge cross-asset.

## Shipped as named variants over ONE shared engine

Hydra is published as three **named variants** — **Hydra-ETH**, **Hydra-SOL**,
**Hydra-HYPE** — each a self-contained, deployable fund the picker recommends
directly (no "which coin?" prompt needed). They share ONE producer + runtimes;
only the per-coin config sets differ. `HYDRA_COIN` selects the variant,
`HYDRA_LEG` the head — three wallets per variant:

| Variant | core | dip | hedge |
|---|---|---|---|
| **Hydra-ETH** | `HYDRA_COIN=ETH HYDRA_LEG=core` | `…=ETH …=dip` | `…=ETH …=hedge` |
| **Hydra-SOL** | `HYDRA_COIN=SOL HYDRA_LEG=core` | `…=SOL …=dip` | `…=SOL …=hedge` |
| **Hydra-HYPE** | `HYDRA_COIN=HYPE HYDRA_LEG=core` | `…=HYPE …=dip` | `…=HYPE …=hedge` |

Each variant loads `config/hydra-<coin>-<leg>-config.json`. In v2.0 the variants
differ only by the **thesis coin** (`HYDRA_COIN`) — the hedge params are uniform,
because the hedge shorts a blend of *other* assets, not the thesis coin (the
producer auto-excludes the thesis coin from `hedgeUniverse` at runtime). Run all
three = nine wallets, one codebase. Extensible to any major.

## How each head scores (producer-side)

- **core** — single-coin. 4h trend is the hard gate (sit out a neutral tape); LONG
  if bullish / SHORT if bearish; 1h confirmation, RSI blow-off (long) / capitulation
  (short) guard, funding tailwind; conviction → leverage tier (`stdLeverage` →
  `maxLeverage` at `apexScore`). Ridden on a catastrophic-only DSL (long-term hold).
- **dip** — single-coin. Requires a confirmed 4h **uptrend** AND a pullback (1h not
  bullish, or 1h RSI ≤ `dipRsiMax`); LONG only; tactical add inside the uptrend.
- **hedge (v2.0 — cross-asset)** — scans `hedgeUniverse` (a blend of OTHER assets,
  thesis coin excluded) and **shorts only the names in a confirmed 4h downtrend**
  (per-asset drawdown over `stressLookback` ≥ `stressDropPct`, or a 1h breakdown),
  capitulation-guarded. **Vol-parity sized** (margin inversely ∝ each asset's ATR%,
  clamped) and multiplied UP by a **thesis-stress** factor (`thesisStressFullPct`,
  `stressMultMax`) when the thesis coin itself is breaking down. A basket (up to
  `maxSlots`), total margin capped at `hedgeMaxTotalPct`. Auto-scales: ~nothing to
  short in a calm uptrend, heavy in a broad selloff. Never shorts the thesis coin.

## Sizing — the funding split is YOUR dial

The split across a coin's three head wallets is the operator's funding decision.
**Default ~50 core / 25 dip / 25 hedge** of the coin's combined budget — net-long
the thesis with a real cushion. Fund only core+dip to drop the hedge; raise hedge
for more protection.

## Fleet-standard rules (enforced)

- **Max leverage 5x core / 4x dip / 3x hedge** (strict clamp then venue max; hedge
  lowest — short squeezes are violent). core/dip = one position on the thesis coin;
  hedge = a basket (up to `maxSlots`), total margin capped at `hedgeMaxTotalPct`.
- **Sizing** — core/dip `marginPct` of equity; hedge **vol-parity** (no hardcoded $).
- **Drawdown halt** 22/20/20% from rolling peak.
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **DSL profiles** — core = **catastrophic-only** (wide disaster stop, weak_peak OFF,
  late Phase 2; ride the long-term thesis); dip = moderate; hedge = tight-ish (bank
  the cushion, let it run in a sustained selloff). Time-cuts OFF on all.
- **Budget-scaling notional floor** `max(account_value × minNotionalPctOfEquity,
  venueMinNotionalUsd)`; sizes off `max(main, xyz)` account value.
- **Signature-adaptive daemon launch** (passes `wallet=`/`scanner=` only if the
  installed helpers signature accepts them).

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Hydra's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## Versions

- **v2.1 (current)** — two fixes from the first live run (HYPE chop):
  (1) **1d-alignment chop filter** on core + dip — they only enter when the daily
  trend confirms the 4h, so they stop buying the top of a range (in chop the 1d is
  NEUTRAL → stand down); dip also requires a deeper RSI pullback (`dipRsiMax` 42).
  (2) **Opt-in hybrid hedge** (`hedgeIncludesThesis`) — for an idiosyncratic coin
  (HYPE), the hedge will ALSO short the thesis coin when IT is the one breaking down,
  cushioning a coin-specific move the cross-asset blend misses. ETH/SOL stay pure
  cross-asset (false). **NOTE:** the loose core DSL is a *runtime* file — recreate
  the core runtime on upgrade or it keeps the tighter prior DSL (the v2.0→host gap).
- **v2.0** — single-coin **conviction** fund: core/dip = long-term hold on a
  catastrophic-only DSL; the hedge became a **cross-asset blend short** (vol-parity,
  thesis-stress-scaled, never the thesis coin).
- **v1.0** — single-coin portfolio fund: three regime-gated heads with a *same-asset*
  stress short hedge and a tighter ratcheting core DSL.

Planned v2.2: Smart-Money confirmation; the "follow them out" exit isn't applicable
here (it's a single-coin thesis, not a copy book).
