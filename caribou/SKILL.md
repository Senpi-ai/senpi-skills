---
name: caribou-strategy
description: >-
  CARIBOU v1.0 — Cross-Asset Trend Fund (managed futures / CTA). Trend-follows a
  maximally diversified universe spanning EVERY asset class on Hyperliquid —
  crypto, xyz stocks, indices, metals, energy — long the uptrends and short the
  downtrends. Each asset is judged by its OWN trend (time-series, 4h hard gate +
  daily align + momentum + RSI guard), positions are sized to EQUAL RISK
  (volatility parity — margin scales inversely with the asset's ATR%), and the
  book is capped per asset CLASS so it can never collapse into one class. Two
  INDEPENDENT sleeves on SEPARATE wallets (long / short), so the fund may hold
  the same asset long in one and short in the other. Long uptrends + short
  downtrends across uncorrelated classes = net beta ~0 and crisis-positive (it
  shorts the fallers when markets break). The managed-futures hedge. NOT a
  copy-trader; runtime owns the LLM gate (pass-through), the asymmetric trend DSL,
  and all risk.guard_rails.
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

# 🦌 CARIBOU v1.0 — Cross-Asset Trend Fund (Managed Futures / CTA)

The great migration: **follow the trend across all terrain.** Caribou trend-follows
a maximally diversified universe spanning **every asset class on Hyperliquid** —
crypto, xyz stocks, indices, metals, energy — long the uptrends and short the
downtrends, each position sized to **equal risk**.

| Sleeve | What it does | Direction | Wallet |
|---|---|---|---|
| **long** | opens LONGS on assets in a confirmed uptrend | LONG only | one |
| **short** | opens SHORTS on assets in a confirmed downtrend (the crisis-alpha engine) | SHORT only | one |

> **Two sleeves, two wallets, fully independent** — so the fund is *not* restricted
> from holding the **same asset in opposite directions** (e.g. the long sleeve
> trails out a stale ETH long while the short sleeve opens a fresh ETH short on a
> trend flip). Each wallet nets per-asset on its own.

## Why a cross-asset trend fund

Managed futures (CTA) is the most-proven hedge-fund category, and its **entire edge
is cross-asset diversification**: trends appear in different markets at different
times. When crypto chops, gold may be trending; when stocks sell off, oil or the
dollar may be trending. **The more uncorrelated markets you trend-follow, the
smoother the curve** — which is exactly why "use every asset class" is the thesis,
not a bolt-on. Long uptrends + short downtrends across uncorrelated classes →
**net beta near zero** and **crisis-positive** (short the fallers when everything
bleeds). That is the hedge.

## How it works (producer-side, per sleeve)

- **Universe = every class.** From the live instrument board, bucket all liquid
  names into **crypto / equity / index / metal / energy**, top-N + relative-median
  liquidity gate *within each class* (so a thin stock isn't compared to BTC volume).
- **Time-series trend, per asset.** Each asset judged vs its *own* history:
  **4h structure is the hard gate** (bullish→long candidate / bearish→short),
  the **daily** aligns (+/−), **24h momentum** confirms, and an **RSI guard**
  blocks blow-off longs / capitulation shorts.
- **Volatility-parity sizing.** `margin = equity × baseRiskPct × (referenceVol /
  asset_ATR%)`, clamped to [3%, 15%] of equity. A calm index (~1.5% ATR) gets ~2×
  base margin; a wild memecoin (~6%) gets ~0.5× — **each position contributes ~equal
  risk.** This is the managed-futures formula and what keeps the curve from being
  crypto-dominated.
- **Per-class caps.** No asset class may exceed **40% of equity** in margin — the
  book stays diversified across classes (the entire CTA edge). Up to 8 slots.
- **Conviction leverage.** `baseLeverage` (3x), bumped to `maxLeverage` (5x) on an
  apex-score trend, then clamped to each asset's HL venue max.

## Asymmetric trend DSL — cut losers fast, let winners run

Trend-following lives and dies on **"cut losers short, let winners run."** So the
DSL is asymmetric: a **tight Phase 1** (cut a losing trend quickly) + a **wide
Phase 2 ladder** that lets a winner run to +150% before locking 84%, and
**time-cuts OFF** (a real trend can run for weeks). Taker-fallback entries — a
trend-follower must *catch* the move (the opposite of a fader).

## Deploy — two wallets

```
CARIBOU_LEG=long   CARIBOU_LONG_WALLET=<wallet A>   # the long sleeve
CARIBOU_LEG=short  CARIBOU_SHORT_WALLET=<wallet B>  # the short sleeve
```
Fund both for the full hedge (net-beta-neutral). Funding split is your dial —
**default ~50/50** long/short; tilt toward long in a broad bull, toward short if you
want more crisis protection.

## Fleet-standard rules (enforced)

- **Max leverage 5x** (strict clamp, then venue max). Vol-parity margin (no
  hardcoded $); per-class cap 40%; up to 8 slots.
- **Drawdown halt 22%**, daily loss limit 12%, `max_consecutive_losses 6` (trend
  takes many small losses before the big trend — don't halt on normal chop).
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Sizes off `max(main, xyz)` account value** — never the sum (cross-margin).
- **Signature-adaptive daemon launch**; per-(leg,wallet) fcntl lock.

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Caribou's wallets. Entries are emitted only by the
producer daemons; exits are owned only by the runtime DSL.

## v1.0 — initial build

The first **cross-asset trend fund** — managed futures across every Hyperliquid
asset class, vol-parity sized, class-capped, two independent sleeves. v1.0 gates on
price-action trend (4h/daily structure + momentum + RSI + ATR vol). Cross-asset
correlation-aware netting and a vol-target overlay are planned v1.1 enhancements.
