---
name: eel-strategy
description: >-
  EEL v1.0 — Electrons vs Hydrocarbons Energy-Pair Hedge Fund. Two books on two
  wallets, one producer. The LONG "power" book longs the AI-power complex on
  Hyperliquid XYZ — uranium (URNM), gas-fired power (NATGAS), the grid metal
  (COPPER), on-site fuel cells (BE), rare-earth (USAR) — the electrons AI
  datacenters need. The SHORT "oil" book shorts crude (Brent + WTI), the legacy
  transport fuel losing relative ground to electrification. Both legs are
  ENERGY, so an energy-wide move washes out and the P&L is the
  electrons-vs-hydrocarbons spread — an energy-sector-neutral pair, not a
  directional energy bet. Trend-confirmed on ABSOLUTE structure, per-name
  conviction sizing (uranium/copper big, fuel-cells small). NOT a copy-trader:
  each book scores its own universe; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. EEL_LEG env selects the book.
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

# ⚡ EEL v1.0 — Electrons vs Hydrocarbons (the AI-power pair)

Eel bets that **AI datacenters are a structural new source of electricity
demand** — so the **power complex (uranium, gas, grid, fuel cells) outperforms
crude oil**, the legacy transport fuel barely levered to AI. It expresses that as
an **energy-sector-neutral long/short**:

- **LONG the power complex** — `URNM` (uranium), `NATGAS` (gas-fired power),
  `COPPER` (grid/electrification), `BE` (Bloom Energy fuel cells), `USAR` (rare
  earth).
- **SHORT crude oil** — `BRENTOIL` + `CL` (WTI).

**Why it's hedged:** both legs are *energy*, so a broad energy crash hurts both
and cancels — the P&L is the **electrons-vs-barrels spread**, not energy
direction. One producer (`eel-producer.py`) serves both books; `EEL_LEG` selects.

| Book | Holds | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | AI-power complex (LONG) | `EEL_LONG_WALLET` | `runtime-long.yaml` | `eel_long_signals` |
| `short` | Brent + WTI crude (SHORT) | `EEL_SHORT_WALLET` | `runtime-short.yaml` | `eel_short_signals` |

## The thesis, asset by asset

| Sleeve | Direction | Names | Rationale |
|---|---|---|---|
| Nuclear power | LONG | **URNM** (1.2×) | uranium — the purest AI-power play |
| Grid / electrification | LONG | **COPPER** (1.1×) | the metal of the electrification buildout |
| Gas-fired power | LONG | NATGAS | the bridge fuel for datacenter load |
| Distributed power | LONG | USAR (0.7×), BE (0.6×) | rare-earth magnets, on-site fuel cells (smaller/speculative) |
| Crude oil | SHORT | BRENTOIL, CL | legacy transport fuel — the hedge leg |

## How it picks (producer-side)

1. **Curated universe** — `config.universe`, intersected with the live board + a
   **relative-to-market liquidity gate** (24h vol ≥ `volFloorPctOfMedian` × the
   universe median — no hardcoded $ floor).
2. **ABSOLUTE trend is the gate** — long power only while its 4h is not bearish;
   short oil only while its 4h is not bullish (+ capitulation guard so it never
   shorts an exhausted oil bottom).
3. **Relative strength is a tiebreaker** — tilts ranking/size, never benches a
   genuinely-trending name.
4. **Conviction sizing** — `margin = account_value × marginPct ×
   sizingWeights[name]`. Uranium/copper big, fuel-cells small. No hardcoded $.

## The long/short balance is YOUR dial

The split is the operator's **funding** across the two wallets (see config
`_hedge_note`) — not hardcoded. Default a **slight long-power tilt (~55/45)** since
the thesis is directionally pro-power (long book: 4 slots / 18% / 5x; oil book:
2 slots / 15% / 4x). Fund 50/50 for a cleaner market-neutral spread, or tilt to
the oil book if you expect a crude shock.

## Fleet-standard rules (enforced)

- **Max leverage 5x power / 4x oil** (strict clamp + runtime gate; venue caps below).
- **Per-position margin ≤ 18% / 15%** (× conviction weight, ≤ 25% cap).
- **Drawdown halt** 20% / 18%, `reset_on_day_rollover`.
- **Mandatory DSL**; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- **Verbose per-tick JSON**; sizes off `max(main, xyz)` account value.
- **Oil book runs tighter** (lower leverage, tighter max-loss, faster stall-cuts)
  — a crude supply-shock spike is violent.

## Caveat — say it honestly

The pair can invert short-term on an **oil-supply shock** (Mideast escalation, an
OPEC cut) or a cold-winter **natgas spike** — events that move hydrocarbons
independent of the electrification thesis. The DSL owns those drawdowns; the edge
is the structural multi-month electrons-beat-barrels drift, not any single week.

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against Eel's wallets. Entries are emitted only by the
producer daemon; exits are owned only by the runtime DSL.

## v1.0 — initial build

A Lion-family two-book long/short (the proven helpers-native pattern: one
leg-parameterized producer, two wallets, absolute-trend gate, RS tiebreaker,
per-group conviction sizing, relative-to-market liquidity gate) pointed at an
**energy dispersion** — the cleanest hedge of the thesis-fund family, since both
legs sit in one sector.
