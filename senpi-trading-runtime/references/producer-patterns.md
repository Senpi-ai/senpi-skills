# Producer Patterns — Scanner Archetypes Catalog

The active fleet of trading agents on Senpi implements roughly a dozen distinct producer/scanner archetypes. This doc catalogs them so you can pick a starting pattern when building your own strategy, and points at a working living example for each.

Every active fleet agent's producer is built on the `senpi_runtime_helpers` SDK (`SenpiClient`, `producer_daemon`, `push_signal`). What differs between agents is **which MCP tools they call**, **how they score signals**, and **what scoring archetype they implement**. Pick the archetype that matches the kind of market regime you want to hunt, then copy the structure from the named example agent.

---

## How to use this catalog

### Picking a starting pattern

1. **Identify what kind of signal you want to detect.** Are you scanning a universe of assets, hunting one asset deeply, following specific traders, fading crowded positions, or detecting laggards across asset relationships? Match your goal to the archetype below.
2. **Open the example agent's producer file** at `<example-agent>/scripts/<agent>-producer.py`. That's a working, audit-verified implementation of the pattern.
3. **Copy the structure**, then swap the parts that are strategy-specific:
   - The MCP calls that pull market data (keep the same archetype-defining ones)
   - The scoring logic (your thesis)
   - The thresholds (your conviction tiers)
   - The asset universe (one asset / whitelist / top-N / XYZ)
   - The tick interval (`producer_daemon(interval_seconds=N, ...)`)
4. **Match the example's `runtime.yaml` structure** for the LLM decision gate, DSL preset, and risk.guard_rails. Tune values, don't rewrite the structure.
5. **Verify on-chain** after launch by audit-querying the producer-signature MCP call(s) listed in this doc — that's the easiest way to confirm your producer is firing.

### What you don't need to write yourself

Every pattern below assumes `senpi-trading-runtime` handles:
- Maker-first execution (`FEE_OPTIMIZED_LIMIT` entries + exits)
- DSL exits (Phase 1 max-loss/retrace + Phase 2 ratcheting trailing)
- Risk gates (daily loss caps, drawdown halts, consecutive-loss cooldowns, per-asset cooldowns)
- Position tracking (`position_tracker` scanner runs every 10s)
- Long-lived daemon scheduling (`producer_daemon`)
- Reentrancy locks (handled by `producer_daemon`'s `scanner_lock`)
- Telemetry (trade-chain DB records every action/inaction)

Your producer only has to score the signal and call `push_signal(...)`. The runtime takes care of the rest.

---

## The archetypes

### 1. Universe trend-follower

**Thesis:** Scan top-N HL assets each tick, score on SM consensus + multi-TF alignment, fire entries when conviction tier is hit. Hunts coordinated risk-on / risk-off moves across crypto majors.

| | |
|---|---|
| Primary MCP tools | `leaderboard_get_markets` (universe pull), `market_get_asset_data` (per-candidate scoring) |
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 180s (3 min) |
| Typical risk envelope | top 50 HL assets, `max_entries_per_day` 1–3, conviction-tier leverage |
| Example agent | Condor — see `condor/scripts/condor-producer.py` |

**When to use this pattern:** You want broad market coverage and entries when multiple confirmations align across timeframes. Best for trend-continuation theses.

---

### 2. Single-asset alpha hunter (Kodiak family)

**Thesis:** One asset, six-gate entry validation, tight scoring, conviction-tiered leverage. Hunts the specific behavior of a single asset (BTC, ETH, SOL, HYPE) with thresholds tuned to that asset's volatility and liquidity.

| | |
|---|---|
| Primary MCP tools | `market_get_asset_data` for the target asset (called once per tick with multi-timeframe candles) |
| Producer-signature for fleet audit | `market_get_asset_data` every tick |
| Typical tick interval | 180s (3 min) |
| Typical risk envelope | single asset, `slots: 1`, `max_entries_per_day` 1–3, leverage 7x–10x |
| Example agent | Wolverine (HYPE) — see `wolverine/scripts/wolverine-producer.py`. Also Polar (ETH), Grizzly (BTC), Kodiak (SOL) — all share the same family pattern. |

**When to use this pattern:** You have a thesis specific to one asset and want to tune scoring + DSL preset for that asset's behavior.

---

### 3. Single-asset XYZ specialist

**Thesis:** Same as Kodiak family but on a non-crypto asset (oil, metals, indices) on Hyperliquid's XYZ DEX. Slower cadence + wider DSL preset because XYZ assets move differently from crypto.

| | |
|---|---|
| Primary MCP tools | `market_get_asset_data` with `asset="xyz:BRENTOIL"` (or similar XYZ-prefixed asset) |
| Producer-signature for fleet audit | `market_get_asset_data` every tick |
| Typical tick interval | 180s |
| Typical risk envelope | single XYZ asset, tighter `drawdown_halt_pct` (tail risk on commodities), wider DSL phase1 |
| Example agent | Dire (BRENTOIL) — see `dire/scripts/dire-producer.py` |

**When to use this pattern:** You want to trade oil, gold, silver, equities indices, etc. via Hyperliquid XYZ. Inherits Kodiak family structure but with XYZ-tuned DSL and risk.

---

### 4. Multi-asset whitelist

**Thesis:** Iterate over a strict whitelist of crypto majors (e.g. BTC/ETH/SOL), score each asset, fire on the best-scoring one. Tighter universe than universe trend-followers — more discipline, less noise.

| | |
|---|---|
| Primary MCP tools | `market_get_asset_data` looped over each whitelisted asset |
| Producer-signature for fleet audit | `market_get_asset_data` (multiple calls per tick — one per whitelisted asset) |
| Typical tick interval | 300s (5 min) |
| Typical risk envelope | 3–6 whitelisted assets, conviction-tier leverage, `max_entries_per_day` 1–3 |
| Example agent | Bison (BTC/ETH/SOL) — see `bison/scripts/bison-producer.py` |

**When to use this pattern:** You believe most crypto noise comes from low-cap alts and want to restrict to majors only. Or your thesis is specific to a small known set of assets.

---

### 5. Trader-follower / hot-streak

**Thesis:** Pull ELITE/RELIABLE traders winning recently, identify their strongest current position, follow it. Hunts coat-tail alpha from quality traders.

| | |
|---|---|
| Primary MCP tools | `discovery_get_top_traders` (cached for 24h), `discovery_get_trader_state` (every tick), `leaderboard_get_markets` (SM confirmation) |
| Producer-signature for fleet audit | `discovery_get_trader_state` every tick (the cached `discovery_get_top_traders` fires only on cache miss) |
| Typical tick interval | 60–180s |
| Typical risk envelope | conviction-tier leverage based on trader quality + position size, whale entry-discipline gate, per-trader event dedupe |
| Example agent | Raptor — see `raptor/scripts/raptor-producer.py` |

**When to use this pattern:** You believe selecting alpha-generating traders and copying them produces better risk-adjusted returns than pure technical scanning.

---

### 6. Striker / rank-jump detector

**Thesis:** Detect when an asset jumps the SM-leaderboard ranks aggressively (10+ positions in one tick from #25+). Catches first-jump events before they become crowded top-3 plays. "One amazing trade per day" cadence.

| | |
|---|---|
| Primary MCP tools | `leaderboard_get_markets` + delta tracking (rank-history in producer state) |
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 180s |
| Typical risk envelope | top 50 HL assets with $3M+ day notional, `max_entries_per_day` 1, conviction-tier leverage |
| Example agent | Jaguar — see `jaguar/scripts/jaguar-producer.py` |

**When to use this pattern:** You want to catch the inflection point when SM interest starts spiking on a previously-quiet asset. Fewer trades per day, higher conviction per trade.

---

### 7. Funding-regime fade

**Thesis:** Detect when funding has been persistently extreme for hours (crowded one direction), then fade the crowd at exhaustion. Combines funding extremity + SM positioning + cooldowns.

| | |
|---|---|
| Primary MCP tools | `market_get_funding_regime`, `market_get_funding_history` (per asset), `leaderboard_get_markets` (SM context), `market_list_instruments` (universe) |
| Producer-signature for fleet audit | `market_get_funding_regime` every tick |
| Typical tick interval | 300s (5 min — funding doesn't change that fast) |
| Typical risk envelope | crypto perps with OI > $3M, FP-001 quiet hours, post-loss asset cooldowns |
| Example agent | Pangolin — see `pangolin/scripts/pangolin-producer.py` |

**When to use this pattern:** You believe persistent funding extremity is a leading indicator of forced unwinds, and you want to position opposite the crowd at exhaustion.

---

### 8. Contrarian crowding-unwind hunter

**Thesis:** Wait for the crowd to overcommit (high funding + lopsided SM + concentrated OI) AND exhaustion signals to fire (volume decline + price stall + RSI divergence). Enter opposite to the crowd.

| | |
|---|---|
| Primary MCP tools | `leaderboard_get_markets` (SM map, BTC macro), `market_get_asset_data` (per-asset exhaustion detection), `market_list_instruments` (universe) |
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 900s (15 min — contrarian setups develop slowly) |
| Typical risk envelope | all crypto perps with OI > $3M, 6h post-loss per-asset cooldown, MACRO_TREND_GATE blocks fades during trending macro |
| Example agent | Owl — see `owl/scripts/owl-producer.py` |

**When to use this pattern:** You believe crowded trades reliably unwind and you have a way to time the unwind (not just detect the crowding). The hard part is exhaustion timing, not crowding detection.

---

### 9. Cross-asset lag detector

**Thesis:** When BTC moves > 2% in 4h, certain alts lag behind and catch up shortly after. Detect the lag and position for the catch-up.

| | |
|---|---|
| Primary MCP tools | `market_get_cross_asset_flows` (BTC-anchored lag detection, returns laggards with `follow_rate ≥ 0.8`) |
| Producer-signature for fleet audit | `market_get_cross_asset_flows` every tick |
| Typical tick interval | 60s (lag detection wants fresh BTC moves) |
| Typical risk envelope | filtered laggards only, fires only when BTC's 4h move exceeds threshold (often silent) |
| Example agent | Mantis — see `mantis/scripts/mantis-producer.py` |

**When to use this pattern:** You believe BTC leads the alt market and want to systematically capture the lag. Most ticks are silent (BTC hasn't moved enough); the producer fires only when the macro condition is met.

---

### 10. Multi-asset XYZ contrarian fader

**Thesis:** Multiple XYZ macro assets (CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100), contrarian direction flip when SM has overconcentrated, spread + freshness gates. Slower XYZ-tuned DSL.

| | |
|---|---|
| Primary MCP tools | `leaderboard_get_markets`, `market_get_asset_data` (per XYZ asset), `strategy_get_open_orders` (resting-order stale-cancel guard) |
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 300s (5 min) |
| Typical risk envelope | 6 XYZ macro assets, conviction-tier leverage, has_resting_orders 600s stale-cancel auto-purge |
| Example agent | Bald Eagle — see `bald-eagle/scripts/eagle-producer.py` |

**When to use this pattern:** You want to trade XYZ commodities/indices with a contrarian thesis (faders, not trend-followers). The XYZ-specific stale-order guard is important because XYZ ALOs can rest for days if not actively managed.

---

### 11. Volume engine / market-making

**Thesis:** Specialized — runs a two-wallet pair (one volume + one runner) that recycles builder fees and accumulates volume credits. Not a directional trading thesis.

| | |
|---|---|
| Primary MCP tools | Specialized (continuous order placement / cancellation) |
| Producer-signature for fleet audit | High-frequency `cancel_order` + `create_position` patterns |
| Typical tick interval | Continuous |
| Typical risk envelope | Two-wallet pair with daily top-ups; net wallet bleed = builder-fee-recycling cost rate |
| Example agent | Turbine — see `turbine/` (specialized; not a fit for standard archetype templates) |

**When to use this pattern:** You're not trading direction — you're recycling builder fees against a known volume target. This is a niche use case; most builders should pick one of patterns 1–10.

---

## Decision tree — which pattern fits your goal?

```
Are you trading a single asset, a small whitelist, or a universe?
├─ Single crypto asset (BTC, ETH, SOL, HYPE, etc.)
│  └─ Pattern 2 — Single-asset alpha hunter (Kodiak family)
│
├─ Single XYZ asset (oil, metals, indices)
│  └─ Pattern 3 — Single-asset XYZ specialist
│
├─ Small whitelist of crypto majors (3–6 assets)
│  └─ Pattern 4 — Multi-asset whitelist
│
├─ Top-N HL universe (scan everything liquid)
│  ├─ Want trend-continuation? → Pattern 1 — Universe trend-follower
│  ├─ Want first-jump detection? → Pattern 6 — Striker / rank-jump
│  ├─ Want funding-regime fades? → Pattern 7 — Funding-regime fade
│  └─ Want contrarian crowding-unwinds? → Pattern 8 — Contrarian unwind hunter
│
├─ Multiple XYZ assets with contrarian thesis
│  └─ Pattern 10 — Multi-asset XYZ contrarian fader
│
├─ Follow specific traders (copy alpha)
│  └─ Pattern 5 — Trader-follower / hot-streak
│
└─ BTC-anchored lag (alt catches up to BTC)
   └─ Pattern 9 — Cross-asset lag detector
```

If your thesis doesn't fit any of these patterns: it's probably either (a) a hybrid of two patterns (most active agents are hybrids of 1–2 archetypes), or (b) something genuinely new. Hybrid: copy the closest archetype and layer in the second one. Genuinely new: write it from scratch using `senpi_runtime_helpers`; the framework supports any signal flow that can call MCP tools and emit `push_signal`.

---

## Common ingredients (regardless of pattern)

Every pattern above shares these common producer ingredients:

- **SDK probe** at the top of `<agent>_config.py` — locates `senpi_runtime_helpers` in the standard install paths.
- **Lazy `SenpiClient` wrapper** — instantiates on first MCP call, validates `SENPI_AUTH_TOKEN`.
- **Wallet resolver** — reads from `<AGENT>_WALLET` env var first, then `config.json`.
- **Wallet-hashed daemon name** — `f"<agent>-producer-{sha256(wallet.lower())[:12]}"`.
- **`producer_daemon(fn=main, interval_seconds=N, name=..., wallet=..., scanner=...)`** — long-lived scheduler with built-in reentrancy guard.
- **Final stdout heartbeat per tick** — `{"status": "ok", "scanned": N, "candidates": M, "signals_pushed": K, "_<agent>_producer_version": "X.Y.Z"}` for telemetry + audit.

When you're copying a pattern as a starting template, keep all of these — they're the helpers-native conventions every fleet agent shares. Change only the archetype-specific scoring + thresholds.

---

## Fleet auditor reference

When verifying that a producer is firing on-chain (not silent), audit_query the producer-signature MCP call from the table above for the agent's `senpiUserId`. If the call appears at the configured tick interval, the producer is alive. If runtime-side calls (`strategy_get_clearinghouse_state` every 10s + `market_get_prices` every 30s) appear but the producer-signature calls don't, the daemon is dead or the runtime registration is broken — see `senpi-trading-runtime/references/liveness-verification.md` for the full diagnostic flow.

---

## Where this catalog lives in the broader docs

- `senpi-trading-runtime/SKILL.md` — start here for the runtime + Producer SDK overview
- `senpi-trading-runtime/references/yaml-schema.md` — full `runtime.yaml` field reference
- `senpi-trading-runtime/references/python-producer-sdk.md` — full Producer SDK reference
- `senpi-trading-runtime/references/strategy-examples.md` — runtime.yaml templates by strategy type
- **This file** — scanner archetype catalog (you are here)
- `senpi-trading-runtime/references/liveness-verification.md` — how to confirm your producer is firing on-chain

Pick a pattern from this catalog, copy the example agent's producer + runtime.yaml as your starting template, tune the scoring and thresholds for your thesis, deploy with `openclaw senpi runtime create` + `nohup python3 ... &`. That's the canonical build path.
