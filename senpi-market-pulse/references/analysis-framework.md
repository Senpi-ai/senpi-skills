# Analysis framework — turning prices into a non-obvious read

The engine hands you prices, per-group averages, and a few computed `signals`. This is how you turn
that into the analysis a human couldn't assemble alone. **The insight is always in the
*relationships between* assets, never in any single price.** Read these in order.

## 1. Dispersion vs. capitulation (the first question)

Is the headline index calm while its components break, or is everything moving together?

- **Index calm, components broken** (e.g. SP500 −1% but individual semis −10%) → **dispersion / sector
  rotation.** Money is moving *between* sectors, not *out* of the market. This is the common,
  high-signal case — name it explicitly.
- **Everything down together, index and components alike** → **broad / macro move.** A liquidity or
  macro-regime event, not a rotation.

The engine pre-computes this in `signals.dispersion` (SP500 move vs. the worst group's average).
Cite the gap: "SP500 −1.1% while memory names are −10% — that's dispersion, not capitulation."

## 2. The gradient within the epicenter (the texture)

Once you've found the sector taking the damage, read the *gradient* across its sub-groups — it tells
you the *kind* of move:

- A **steep gradient** (`semis_memory` −10%, `semis_equipment` −6%, `semis_logic` −3%) → a
  fundamental, supply/demand-specific story (here: a memory glut, not an AI-capex scare). The
  further from the epicenter, the less damage.
- A **flat gradient** (everything in the sector down ~5% uniformly) → a liquidity/de-risking move,
  not a fundamental one.

The `semis_memory` / `semis_equipment` / `semis_logic` split exists precisely so you can read this.
The same logic generalizes to any sector that's leading.

## 3. The confirmation checklist (does the structure hold up?)

These cross-asset tells separate an orderly rotation from a genuine stress event. The engine computes
each with a plain `read` string — cite them:

| Signal | Holding | Breaking |
|---|---|---|
| **Gold** (`signals.gold`) | Flat/down-small while equities dump → **no forced-liquidation cascade** (orderly) | Gold dumping too → margin-call / liquidity event |
| **DXY** (`signals.dxy`) | Flat → no flight-to-USD, **no funding stress** | Spiking → macro funding crisis |
| **VIX** (`signals.vix`) | ≤ ~22 and not spiking → **fear contained, rotation** | 25+ / spiking → selloff **broadening** |

The classic orderly-rotation signature: *semis −10%, gold only −1%, DXY flat, VIX elevated but not
spiking.* If gold and DXY were both moving hard too, you'd be looking at something much more
dangerous — say so.

## 4. K-shaped / divergence (the most insightful section)

Look for what's moving *opposite* the crowd on the same tape. The textbook case: **asset-light
winners green while asset-heavy losers bleed** — software mega-caps (`software_megacap`) holding up
while the physical-chip complex gets repriced. That divergence tells you the move is *specific*
(hardware/memory), not a broad tech liquidation. This is usually the line the user remembers.

## 5. Volume as conviction; funding as exhaustion

- **Volume** (`volume_usd` on the movers) — flag institutional-scale notional ("MU $417M daily") so
  the user knows what's real repositioning vs. noise. The biggest-volume name is often the real story.
- **Crypto funding** (`funding` on movers + `signals.funding_regime`) — funding flipping negative
  into a flush (shorts paying) on huge volume = a **leverage washout**, often near-term exhaustion
  rather than the start of a deeper leg. An orderly drift with funding unchanged is different — don't
  call a washout that isn't there.

## 6. Compose the thesis

Put it together into one sentence the rest of the read defends, then give the user the triggers that
would change it. Example:

> "Semiconductor-led risk repricing, not a macro crisis — memory −10%, logic −3%, software green,
> gold −1%, DXY flat, no VIX spike; crypto selling in sympathy with washout characteristics.
> **What to watch:** BTC $62k (holds → flush done), memory names finding a floor, VIX 25+ (→
> broadening)."

That's the difference between "the market is down" and a read worth paying for.
