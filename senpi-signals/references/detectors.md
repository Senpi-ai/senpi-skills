# Detector library — senpi-signals

Each detector is one of the scanner families the 100+ templates already use, run in **observe mode**.
For every detector: the MCP source, the fields, the threshold, the normalized signal it emits, and
the human framing. **Confirmed** = response shape verified; **VERIFY-LIVE** = call the tool once and
read the real response before writing extraction (KeyError > silent zero).

## The normalized signal schema
Everything downstream (scoring, framing) consumes this one shape:
```json
{
  "asset": "OIL",                     // exact HL instrument (case-sensitive; xyz: prefix for stocks)
  "dex": "" ,                          // "" = main HL, "xyz" = xyz DEX
  "detector": "oi_surge",             // see families below
  "direction": "long",                // "long" | "short" | null
  "numbers": ["OI +10% (1h)", "price flat"],   // the cited facts, each from a real read
  "notional_vol": 4200000,            // liquidity, for the credibility filter
  "concrete_entity": null,            // a public 0x… wallet when the signal is about one trader
  "magnitude": 0.10                   // normalized size the scorer reads (see each detector)
}
```
Diff-based detectors (OI, smart-money, funding) are fired *inside* `score.py` from `asset_metrics` +
the prior snapshot — you supply the metrics, not the signal. Event-based detectors (whale, momentum,
cross-asset) you assemble as pre-formed `events[]`.

---

## Diff-based detectors (fire in score.py from asset_metrics vs prior snapshot)

### 1. `oi_surge` — open-interest build / OI-price divergence  *(oi-tracker family)*
- **Source:** open interest per asset. **VERIFY-LIVE:** HL Info API `metaAndAssetCtxs` →
  `openInterest` per asset (the template scanners in `strategies/*/main/scanners/scan.py` read this;
  copy their extraction). Price from `market_get_prices` / `market_get_asset_data` (candles keyed
  `o/h/l/c/v` as **strings**).
- **Metric fields:** `oi`, `price`.
- **Fires when:** `(oi − prior_oi)/prior_oi ≥ 0.10`. If `|price change| < 1%` at the same time →
  tag it OI-price **divergence** (positioning building with no price move — a conflict bonus).
- **Why non-obvious:** OI is invisible on a price chart. Magnitude = the OI %.
- **Framing:** `OI +10% on <ASSET> <longs|—> while price sat flat.`

### 2. `sm_divergence` — smart money vs the crowd  *(smart-money + divergence families)*
- **Source (Confirmed shape):** `leaderboard_get_markets` — per token+dex+direction:
  `pct_of_top_traders_gain` (0–100, top-trader concentration), `is_dominant_direction`,
  `trader_count`, `token_price_change_pct_4h`, `day_notional_volume`, `window` (~"4h"). The
  **dominant direction** = top-trader (smart-money) bias. Crowd bias: the aggregate side — use HL OI
  long/short split (VERIFY-LIVE via `metaAndAssetCtxs`) or `senpi-smart-money`'s crowd read.
- **Metric fields:** `smart_dir`, `crowd_dir`, `smart_share` (= `pct_of_top_traders_gain`).
- **Fires when:** `smart_dir != crowd_dir` AND `smart_share ≥ 25`. Flip bonus if `prior.smart_dir`
  existed and differs (smart money *just* shifted). Conflict bonus (it's a divergence).
- **Framing:** `Smart money is <SHORT> on <ASSET> (<share>% of top-trader PnL) while the crowd is <LONG>.`

### 3. `sm_conviction` — fresh smart-money conviction  *(smart-money + momentum)*
- **Source:** `leaderboard_get_markets` `pct_of_top_traders_gain` / `trader_count`, diffed vs prior.
- **Metric fields:** `smart_share`, `trader_count`.
- **Fires when:** `smart_share − prior.smart_share ≥ 12` points (top traders piling in this window).
- **Framing:** `Top traders are crowding into <ASSET> <long|short> — concentration jumped to <share>%.`

### 4. `funding_dislocation` — funding at an extreme / flipped  *(funding family — the biggest)*
- **Source (partly Confirmed):** `market_get_funding_history` (asset) → 8h rate, annualized %,
  funding_direction (who collects), persistence, trend; `market_get_funding_regime`. (Needs a
  market-scoped token — it 401s on an under-scoped one; then skip this detector, don't fake it.)
- **Metric fields:** `funding_pctile` (0–100), `funding_annualized_pct`, optional prior sign.
- **Fires when:** `funding_pctile ≥ 95` OR the sign flipped vs prior.
- **Framing:** `<Shorts|Longs> are paying <annualized%>/yr to hold <ASSET> — <pctile>th-percentile funding.`

## Event-based detectors (assemble as pre-formed `events[]`)

### 5. `whale_move` — a proven wallet moves real size  *(whale / position_tracker)*
- **Source (VERIFY-LIVE):** the whale set = the top-trader cohort from
  `discovery_get_top_traders` (auto-derived each run; a curated seed list optional). Their live book +
  4h deltas from `leaderboard_get_trader_positions` and `discovery_get_trader_state`. Diff each
  wallet's per-asset notional vs its prior snapshot (store wallet books in the state file too).
- **Fires when:** a wallet opens/adds/flips ≥ ~$1M (or ≥ ~25% of its book) on one asset.
- **`concrete_entity`** = the public `0x…`. **Framing:**
  `A top trader (0x12…) grew their <ASSET> short by $10M to $50M.`

### 6. `cross_asset_laggard` — rotation not yet priced in  *(cross-asset family)*
- **Source:** `market_get_cross_asset_flows` (meaningful only when BTC moved >2% in 4h; `follow_rate`).
- **Framing:** `BTC ran <x%> but <ASSET> hasn't followed (follow-rate <r>) — the laggard.`

### 7. `momentum_event` — the platform's own tiered events  *(momentum family)*
- **Source:** `leaderboard_get_momentum_events` (tiers + behavioral tags + notification decisions —
  it already scores "is this worth notifying"). Pass through the high-tier ones as events.

### 8. `regime_shift` — sector/asset regime flip  *(regime family)*  *(v1: optional / focus mode)*
- **Source:** the regime read (bull/bear/range/event-driven) from weekly-daily structure; flag flips.

---

## Noteworthiness scoring (in score.py — keep in sync)
`score = 100 × ( 0.35·non_obvious + 0.25·magnitude + 0.20·conflict + 0.10·concrete + 0.10·credibility )`
- **non_obvious** (per detector): `oi_surge, funding_dislocation, sm_divergence, whale_move` = 1.0;
  `sm_conviction, cross_asset_laggard` = 0.8; `momentum_event, regime_shift` = 0.6.
- **magnitude:** normalized 0–1 (OI%/0.25; funding_pctile/100; smart_share/100; whale_$/ $10M; capped 1).
- **conflict:** divergence detectors (`sm_divergence`, OI-price-divergence flavor of `oi_surge`) = 1, else 0.
- **concrete:** `concrete_entity` set (a named wallet) = 1, else 0.3.
- **credibility:** `notional_vol ≥ VOL_FLOOR` → 1, scaled below; **below HARD_VOL_FLOOR → dropped entirely.**

## Anti-noise (drop before ranking)
- Below `HARD_VOL_FLOOR` notional volume (illiquid micro-cap / wash).
- Score below `MIN_SCORE`.
- Duplicate per asset — keep the single highest-scoring signal (a 2nd only if a different detector
  *and* both high).
- The obvious — a plain price move with no OI/funding/smart-money/whale angle is not a signal here.

## Defaults (mirrored in score.py — change in both)
`OI_SURGE_PCT 0.10 · PRICE_FLAT 0.01 · SMART_SHARE_MIN 25 · SMART_JUMP_PP 12 · FUNDING_PCTILE 95 ·
WHALE_MIN_USD 1_000_000 · VOL_FLOOR 1_000_000 · HARD_VOL_FLOOR 250_000 · MIN_SCORE 45 · TOP_N 6`
