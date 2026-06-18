---
name: hydra-strategy
description: >-
  HYDRA v1.0 — Single-Coin Portfolio Fund. A complete book on ONE major: a
  directional thesis bet + a complementary dip-buyer + a stress-gated short
  hedge, each on its own wallet ("head"). ONE producer, parameterized by
  HYDRA_COIN (the asset) + HYDRA_LEG (core | dip | hedge). The CORE head rides
  the coin's 4h trend either way (the thesis); the DIP head buys pullbacks within
  a confirmed uptrend (LONG only, the complement); the HEDGE head shorts only on
  a confirmed downtrend + a fast-drawdown signal (the cushion). The heads are
  gated to different regimes so they never take opposing positions at once — the
  fund is NET-LONG the coin, pressed on dips and cushioned on breaks. Deploy it
  for ETH, SOL, HYPE (or any major) = three wallets each. NOT a copy-trader; the
  runtime owns the LLM gate (pass-through), DSL exits, and all risk.guard_rails.
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

# 🐉 HYDRA v1.0 — Single-Coin Portfolio Fund

Hydra is a **complete book on one coin** — the thesis, a complement, and a hedge,
each on its own wallet ("head"). It's the productized version of *pairing
strategies as a hedge* (see producer-patterns → *Running a portfolio*), packaged
into one fund. One producer; `HYDRA_COIN` sets the asset, `HYDRA_LEG` sets the head.

| Head | What it does | Direction | Regime lane |
|---|---|---|---|
| **core** | the **thesis bet** — trend-momentum, conviction-tiered | LONG up / SHORT down | always-on |
| **dip** | the **complement** — buys pullbacks within a confirmed uptrend | LONG only | uptrends only |
| **hedge** | the **hedge** — stress-gated short (confirmed downtrend + fast drawdown) | SHORT only | downtrends/stress only |

**Why the heads don't conflict** (the key design point) — they're gated to
*different regimes*, so across the three wallets they never hold opposing
positions at once:
- **Uptrend** → core long + dip adding on pullbacks (cushioned aggression); hedge idle.
- **Downtrend** → core short + hedge short (defensive); dip stands down.
- **Chop** → core mostly flat, dip catches range-lows, hedge mostly idle.

**Net:** a net-long bet on the coin that **presses harder on pullbacks** and
**flips defensive with a fast hedge** when it breaks.

## Deploy it per coin (ETH / SOL / HYPE / any major)

One engine, three wallets per coin — set `HYDRA_COIN` and `HYDRA_LEG` per wallet:

| Coin | core | dip | hedge |
|---|---|---|---|
| ETH | `HYDRA_COIN=ETH HYDRA_LEG=core` | `…=ETH …=dip` | `…=ETH …=hedge` |
| SOL | `HYDRA_COIN=SOL HYDRA_LEG=core` | `…=SOL …=dip` | `…=SOL …=hedge` |
| HYPE | `HYDRA_COIN=HYPE HYDRA_LEG=core` | `…=HYPE …=dip` | `…=HYPE …=hedge` |

Run all three coins = nine wallets, one codebase. (`HYDRA_COIN` accepts any
main-DEX perp, or an `xyz:` name.)

## How each head scores (producer-side, single asset)

- **core** — 4h trend is the hard gate (sit out a neutral tape); LONG if bullish /
  SHORT if bearish; 1h confirmation, RSI blow-off (long) / capitulation (short)
  guard, funding tailwind; conviction → leverage tier (`stdLeverage` → `maxLeverage` at `apexScore`).
- **dip** — requires a confirmed 4h **uptrend** AND a pullback (1h not bullish, or
  1h RSI ≤ `dipRsiMax`); LONG only; stands down outside uptrends so it never
  knife-catches the hedge.
- **hedge** — requires a confirmed 4h **downtrend** AND a fast drawdown
  (peak-to-current over `stressLookback` ≥ `stressDropPct`, or a 1h breakdown);
  SHORT only; **capitulation guard** (won't short below `rsiOversold`). Episodic —
  most uptrend ticks it does nothing; that tiny idle is the cost of the insurance.

## Sizing — the funding split is YOUR dial

The split across a coin's three head wallets is the operator's funding decision.
**Default ~50 core / 25 dip / 25 hedge** of the coin's combined budget — net-long
the thesis with a real cushion. Fund only core+dip to drop the hedge; raise hedge
for more protection.

## Fleet-standard rules (enforced)

- **Max leverage 5x core / 4x dip / 3x hedge** (strict clamp; hedge lowest — short
  squeezes are violent). Per-position margin 20/18/15% (≤ 25% cap), one position per head.
- **Drawdown halt** 22/20/18% from rolling peak.
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Single-asset DSL standard** — time-cuts OFF (hard_timeout + dead_weight),
  weak_peak self-limiting ON; Phase 1 + Phase 2 own exits (per the fleet
  single-asset rule — Kodiak/Polar/Grizzly family).
- **Budget-scaling notional floor** `max(account_value × minNotionalPctOfEquity,
  venueMinNotionalUsd)`; sizes off `max(main, xyz)` account value.
- **Signature-adaptive daemon launch** (passes `wallet=`/`scanner=` only if the
  installed helpers signature accepts them).

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Hydra's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

The first **single-coin portfolio** fund — the portfolio/hedge-pairing pattern
packaged into one multi-wallet product. Heads gated to disjoint regimes so they
complement rather than fight; net-long the coin, pressed on dips, cushioned on
breaks. v1.0 gates on price-action (trend / RSI / drawdown / funding); Smart-Money
confirmation is a planned v1.1 enhancement.
