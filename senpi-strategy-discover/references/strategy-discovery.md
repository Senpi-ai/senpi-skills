# Strategy discovery — goal → archetype mapping + budget guidance

Use this to filter the registry (`catalog.json`) down to 2–3 recommendations.

## Goal → archetype

| User signal | Archetype (`group`) | Why |
|---|---|---|
| "follow a specific asset" (BTC/ETH/SOL) | `single-asset-alpha-hunter` | one asset, multi-factor conviction scoring |
| "spread across majors", "a whitelist of coins" | `multi-asset-whitelist` | scores a fixed basket, balanced exposure |
| "trade what's trending / momentum" | `universe-trend-follower` | rank-climbs the universe |
| "fade extremes / mean reversion" | `contrarian-unwind` | fades crowded moves once exhaustion confirms |
| "copy good traders" | `trader-follower` | mirrors proven Arena/SM leaders |
| "earn funding / market-neutral-ish" | `funding-fade` | harvests funding dislocations |
| "stocks/indices exposure on Hyperliquid" | `xyz-specialist` | XYZ (HIP-3) equity/index instruments |

(Archetype slugs come straight from each strategy's `catalog.group`. Humanize for display, e.g.
`single-asset-alpha-hunter` → "Single-Asset Alpha Hunters".)

## Risk appetite

- `conservative` → slower cadence, wider stops, lower leverage caps.
- `moderate` → balanced (most strategies).
- `aggressive` → tighter, higher-turnover or higher-leverage theses.

Match `catalog.risk_level` to the user's stated comfort.

## Budget guidance

- `min_budget` (~$100) is the realistic platform floor AND a comfortable starting size — **not a hard
  gate**. Position size scales with budget via each instance's `marginPct`, so any amount at/above the
  floor works (smaller budget = smaller positions).
- **Multi-instance** strategies (e.g. `spider`) split the budget across instance wallets by
  `funding_share` — surface that so the user understands a $1000 budget becomes e.g. $600 + $400.
- Offer to start small or watch first; never refuse a willing user who is at/above the floor.

## Presenting

For each of the 2–3 picks: `emoji name` — tagline (one line) — archetype — suggested budget. Lead
with the top fit and one sentence on *why it matches this user*. End with: "…or, if none fit, we can
build a new one." Then route the choice to `senpi-strategy-ops` → `install_strategy`.
