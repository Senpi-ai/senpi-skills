# Thesis Fund — preset vocabulary

The Thesis Fund is **one engine that expresses any of several macro views.** You pick
*what you believe will happen*; the fund trades the long/short **basket** that expresses
it, **pressing** each name only when the market is *confirming* the thesis direction
(4h/1h trend + 24h momentum aligned). One wallet holds the whole basket — a single
coherent bet.

In Runtime 3.0 the active preset is the pair of lists in `main/runtime.yaml` under
`inputs.longBasket` / `inputs.shortBasket` (with `inputs.thesis` as the label). **To
switch views, replace those two lists with one of the presets below — no code change.**
The shipped default is `risk_off`.

Each name's *direction* is FIXED by which list it's in (`longBasket` → LONG,
`shortBasket` → SHORT). The scanner only enters a name while the tape confirms that
direction; it skips a long-basket name in a confirmed downtrend and a short-basket name
in a confirmed uptrend. Opposing presets are just flipped baskets.

| `thesis` | The bet | `longBasket` | `shortBasket` |
|---|---|---|---|
| `risk_off` *(default)* | Bet against the Trump economy / risk-off | `["xyz:GOLD", "xyz:SILVER"]` | `["xyz:SP500", "xyz:XYZ100", "BTC"]` |
| `recovery` | U.S. recovery / risk-on (the mirror) | `["xyz:SP500", "xyz:XYZ100", "BTC"]` | `["xyz:GOLD"]` |
| `war_escalation` | Iran/US/Israel quagmire deepens | `["xyz:BRENTOIL", "xyz:CL", "xyz:GOLD"]` | `["xyz:SP500", "xyz:XYZ100", "BTC"]` |
| `war_recovery` | De-escalation / recovery | `["xyz:SP500", "xyz:XYZ100", "BTC"]` | `["xyz:BRENTOIL", "xyz:CL", "xyz:GOLD"]` |
| `hype_vs_market` | HYPE keeps outrunning the majors | `["HYPE"]` | `["BTC", "ETH", "SOL"]` |
| `gold_over_btc` | Real gold beats digital gold | `["xyz:GOLD"]` | `["BTC"]` |
| `btc_over_gold` | Digital gold beats real gold | `["BTC"]` | `["xyz:GOLD"]` |

## Asset conventions

- Bare ticker = main-DEX crypto: `BTC`, `ETH`, `SOL`, `HYPE`.
- `xyz:` prefix = XYZ DEX (indices/metals/energy): `xyz:GOLD`, `xyz:SILVER`, `xyz:SP500`,
  `xyz:XYZ100`, `xyz:BRENTOIL`, `xyz:CL`. XYZ trades 24/7 (weekends included). The
  scanner routes candle fetches with `dex="xyz"` automatically off the `xyz:` prefix.
- A name not live on the instrument board is skipped automatically (the scanner validates
  every basket name against `market_list_instruments` before scoring).

## What carries across every preset

`minScore` (4 — the confirmation bar), `marginPct` (12 percent of withdrawable),
`maxLeverage` (strict 5x, then each asset's HL venue max), `maxSlots` (6), the
confirmation scoring, the DSL exit ladder, and the per-instance risk gates are all
preset-independent. Only the basket changes.
