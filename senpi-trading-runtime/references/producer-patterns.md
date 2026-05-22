# Producer Patterns — Scanner Archetypes Catalog

The active fleet of trading agents on Senpi implements roughly a dozen distinct producer/scanner archetypes. This doc catalogs them so you can pick a starting pattern when building your own strategy.

Every active fleet agent's producer is built on the `senpi_runtime_helpers` SDK (`SenpiClient`, `producer_daemon`, `push_signal`). What differs between agents is **which MCP tools they call**, **how they score signals**, and **what scoring archetype they implement**. Pick the archetype that matches the kind of market regime you want to hunt, then copy the structure from the named example agent.

---

## How to use this catalog

You typically won't have the example agent's repo cloned locally when you're building your own strategy. Each pattern below includes:

1. **An inline code snippet** showing the producer-signature MCP call(s) and the `push_signal` shape. **These are skeletons, not runnable scripts** — they illustrate the archetype's distinguishing API surface. Helper functions, scoring loops, wallet resolution, and state tracking are elided for clarity. Fetch the example agent's full producer for runnable code.
2. **Direct GitHub URLs** to the example agent's three working files — fetch each with `curl` or `WebFetch`. Every active fleet agent on `main` has at minimum this three-file core layout (a few — Mantis is one — include a fourth `<agent>_state.py` for diff tracking; fetch it too if the example uses it):

```bash
# 1. The producer script (long-lived daemon, scoring loop, push_signal calls)
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<example-agent>/scripts/<agent>-producer.py

# 2. The config/wrapper module (SDK probe + lazy SenpiClient + mcporter_call shim — required by every producer)
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<example-agent>/scripts/<agent>_config.py

# 3. The runtime YAML (LLM decision gate, DSL preset, risk.guard_rails)
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<example-agent>/runtime.yaml
```

Every active fleet agent is on the `main` branch — no other branch matters.

### About `cfg.mcp_call(...)` / `cfg.mcporter_call(...)` and `cfg._wrapper_client.push_signal(...)` in the snippets below

The snippets show calls like `cfg.mcp_call("market_get_asset_data", ...)` and `cfg._wrapper_client.push_signal(...)`. Here's what they mean:

- `cfg` is the shared config module imported at the top of every producer: `import <agent>_config as cfg`. The file is `scripts/<agent>_config.py` (fetch URL #2 above).
- `cfg.mcp_call(tool, **kwargs)` is a direct call to `senpi_runtime_helpers.SenpiClient.mcp_call()` — direct HTTPS to MCP, no per-call retry/timeout/unwrap layer. Newer agents (Condor, Raptor, Mantis, Bald Eagle) use this name directly.
- `cfg.mcporter_call(...)` is a **backward-compat alias** for `cfg.mcp_call` kept in the `_config.py` files for older call sites (`mcporter_call = mcp_call`). It does the same thing — the name is left over from the v3.x subprocess implementation that's been retired. You'll see it in older agents (Wolverine, Owl); newer agents have moved to `mcp_call(...)` directly.
- `cfg._wrapper_client` is the lazy-initialized `SenpiClient` instance exposed as a proxy — call `cfg._wrapper_client.push_signal(...)` to emit signals to the runtime, and `cfg._wrapper_client.mcp_call(tool, ...)` for any MCP call you want from outside the `cfg` shim.
- The leading underscore on `_wrapper_client` is a convention, not "private — don't touch." Every active fleet producer uses it.

If you copy any fleet agent's `_config.py` verbatim into your new strategy directory, all of this works without changes — you just rename the agent string. For new code, prefer `cfg.mcp_call(...)`; `cfg.mcporter_call(...)` is kept only for compat with older producers.

### `push_signal(...)` payload — what's required vs free-form

The `data={}` dict passed to `push_signal(address=..., scanner=..., asset=..., direction=..., score=..., signal_type=..., data={...})` is split into two parts:

- **Required runtime contract fields** (must be present so the runtime LLM gate, DSL preset, and execution engine work): `leverage`, `marginUsd`. The exact list per scanner is declared in the agent's `runtime.yaml` under `scanners[].config.fields` (`required: true` items). See `senpi-trading-runtime/references/signal-schema.md` for the canonical contract.
- **Free-form telemetry** (everything else — `reasons`, `traderId`, `tcs`, `rankJump`, `followRate`, `crowdDir`, etc.): for audit-trail context and producer-side debugging. The runtime ignores keys it doesn't recognize. Add whatever helps you reconstruct the decision later.

Don't cargo-cult the telemetry keys you see in the snippets below — they're examples specific to each archetype's scoring inputs. Use whatever helps you debug your own strategy.

### Building your own strategy from a pattern

1. **Pick an archetype below** that matches your market thesis.
2. **Fetch the example agent's producer + `_config.py` + runtime.yaml** via the URLs in that pattern's section.
3. **Copy the structure**, then swap the parts that are strategy-specific:
   - Keep the archetype-defining MCP calls (those are what make the pattern work)
   - Replace the scoring logic with your thesis
   - Tune the thresholds for your conviction tiers
   - Adjust the asset universe (one asset / whitelist / top-N / XYZ)
   - Tune the tick interval (`producer_daemon(interval_seconds=N, ...)`)
4. **Adjust the `runtime.yaml`** to match: LLM decision_prompt, DSL preset, risk.guard_rails.
5. **Verify on-chain** after launch by audit-querying the producer-signature MCP call from this doc.

The three files above are the **scoring + execution core**. Every shipped fleet agent also has two deployment-plumbing files in the same directory — you'll need them to ship your strategy, but they don't change between archetypes:

- `<agent>/SKILL.md` — operator-facing skill manifest (description, install instructions, version)
- `<agent>/config/<agent>-config.json` — wallet-specific values (`<AGENT>_WALLET`, starting budget, rebase schedule, env-var defaults). **Never hardcode wallet-specific values in the producer or runtime.yaml** — `senpi-skills` is public; those values belong in `config.json`.

Copy both from the example agent and rename the wallet/agent strings.

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

**Thesis:** Scan top-N HL assets each tick, score on SM consensus + multi-timeframe alignment, fire entries when conviction tier is hit. Hunts coordinated risk-on / risk-off moves across crypto majors.

**Distinctive MCP signature:**

```python
# Pull the SM-ranked universe once per tick
markets = cfg.mcp_call("leaderboard_get_markets", limit=50)

# For each candidate, pull multi-TF candles
for asset in candidates:
    ad = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["15m", "1h", "4h"],
        include_funding=True,
    )
    # ... score ...

# Push the strongest signal
cfg._wrapper_client.push_signal(
    address=STRATEGY_ADDRESS,
    scanner="<agent>_signals",
    asset=best_asset,
    direction=best_direction,
    score=normalized_score,
    signal_type="<AGENT>_UNIVERSE_TREND",
    data={"leverage": leverage, "marginUsd": margin, "score": raw_score, "reasons": reasons},
)
```

| | |
|---|---|
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 180s (3 min) |
| Typical risk envelope | top 50 HL assets, `max_entries_per_day` 1–3, conviction-tier leverage |
| Example agent | **Condor** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/scripts/condor-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/scripts/condor_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/condor/runtime.yaml |

**When to use this pattern:** You want broad market coverage and entries when multiple confirmations align across timeframes. Best for trend-continuation theses.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Condor** | v3.0 | Top-50 HL liquid | Scans top-50 liquid assets every 180s. Normalizes SM consensus + multi-TF alignment into conviction tiers. Fires the strongest signal per tick. | Top-50, Multi-TF, Tick 180s |
| **Cheetah** | v5.2 | Top-100 SM | Multi-signal confluence scoring across SM consensus, velocity, dual-price, volume, and quality-trader alignment. 15-point integer score with hard SM + velocity gates. | Top-100, Multi-signal, Quality-trader |
| **Python** | v1.0 | Multi-tier universe | Mixed-signature scan (`market_list_instruments` + `leaderboard_get_markets` + per-asset deep dive). Multi-day-hold thesis with funding, volume, RSI extremes, and move-exhaustion penalty. | Multi-day, Mixed-sig, MIN_SCORE 8 |
| **Scorpion** | v3.0 | Universe + funding | Universe scan with funding-regime backstop and post-close per-asset cooldown. | Funding-backstop, Cooldown, Universe |

Each agent's producer / `_config.py` / runtime.yaml live at `https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<agent>/` (lowercase agent name).

---

### 2. Single-asset alpha hunter (Kodiak family)

**Thesis:** One asset, six-gate entry validation, tight scoring, conviction-tiered leverage. Hunts the specific behavior of a single asset (BTC, ETH, SOL, HYPE) with thresholds tuned to that asset's volatility and liquidity.

**Distinctive MCP signature:**

```python
ASSET = "HYPE"  # or "BTC" / "ETH" / "SOL" — pick one and only one

# One call per tick — pulls all candles + funding for this asset
ad = cfg.mcp_call(
    "market_get_asset_data",
    asset=ASSET,
    candle_intervals=["5m", "15m", "1h", "4h"],
    include_funding=True,
    include_order_book=False,
)

# Six-gate validation (preserve verbatim from example agent)
# Score, then emit if all gates pass
cfg._wrapper_client.push_signal(
    address=STRATEGY_ADDRESS,
    scanner="<agent>_signals",
    asset=ASSET,
    direction=direction,
    score=normalized_score,
    signal_type="<AGENT>_ALPHA_HUNT",
    data={"leverage": leverage_tier, "marginUsd": margin, "score": raw, "reasons": gates_passed},
)
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_asset_data` every tick (single asset) |
| Typical tick interval | 180s (3 min) |
| Typical risk envelope | single asset, `slots: 1`, `max_entries_per_day` 1–3, leverage 7x–10x |
| Six-gate validation | The Kodiak-family scoring core is a six-gate filter that EVERY producer in this family preserves verbatim: (1) 4h trend != NEUTRAL, (2) 4h structural strength ≥ threshold, (3) 1h matches 4h, (4) 15m momentum aligned, (5) base-tech floor, (6) macro V-recovery gate. The exact thresholds vary per asset. **Fetch the example producer for your target asset** — those gates are the archetype, not optional. |

**Example agents — one per asset.** Pick the one whose asset matches yours and fetch all three URLs:

| Asset | Agent | producer.py | _config.py | runtime.yaml |
|---|---|---|---|---|
| **HYPE** | Wolverine | [wolverine-producer.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine-producer.py) | [wolverine_config.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/scripts/wolverine_config.py) | [runtime.yaml](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolverine/runtime.yaml) |
| **BTC** | Grizzly | [grizzly-producer.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly-producer.py) | [grizzly_config.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/scripts/grizzly_config.py) | [runtime.yaml](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/grizzly/runtime.yaml) |
| **ETH** | Polar | [polar-producer.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/scripts/polar-producer.py) | [polar_config.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/scripts/polar_config.py) | [runtime.yaml](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/polar/runtime.yaml) |
| **SOL** | Kodiak | [kodiak-producer.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/scripts/kodiak-producer.py) | [kodiak_config.py](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/scripts/kodiak_config.py) | [runtime.yaml](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/kodiak/runtime.yaml) |

If your target asset is one of these four, use that example directly. If your target asset is something else (e.g. a different crypto major not in this family), pick the closest behavioral match — typically BTC (Grizzly) for slow assets or HYPE (Wolverine) for fast volatile ones.

**When to use this pattern:** You have a thesis specific to one asset and want to tune scoring + DSL preset for that asset's behavior.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Kodiak** | v5.1 | SOL | The original template. SOL alpha hunter running the six-gate framework with SOL-tuned thresholds. One slot, conviction-tiered leverage. | SOL, 6-gate, Single-slot |
| **Grizzly** | v5.3 | BTC | Kodiak template tuned for BTC's lower-volatility, higher-liquidity regime. Same six-gate confluence, calmer thresholds, tighter sizing. | BTC, Low-vol, Blue-chip |
| **Polar** | v3.0 | ETH | Kodiak template tuned for ETH. Same six-gate framework with ETH-specific volatility and liquidity calibration. | ETH, 6-gate, Single-slot |
| **Wolverine** | v3.0 | HYPE | Kodiak ported to HYPE — Hyperliquid's native token. Six-gate validation with HYPE-tuned thresholds for its high-vol profile. | HYPE, High-vol, Sharp moves |
| **Beaver** | v1.0 | BTC | **Onboarding tier.** Simplified 5-component scoring (max ~9), single SM-direction gate, wide Bison-pattern DSL. The "first strategy" for new users. | Onboarding, BTC, SM-gate, Tick 300s |
| **Heron** | v1.0 | ETH | Beaver fork — ETH instead of BTC. Same scaffold + scoring. | Onboarding, ETH, SM-gate, Tick 300s |
| **Hummingbird** | v1.0 | HYPE | Beaver fork — HYPE instead of BTC. Same scaffold + scoring. | Onboarding, HYPE, SM-gate, Tick 300s |

---

### 3. Single-asset XYZ specialist

**Thesis:** Same as Kodiak family but on a non-crypto asset (oil, metals, indices) on Hyperliquid's XYZ DEX. Slower cadence + wider DSL preset because XYZ assets move differently from crypto.

**Distinctive MCP signature:**

```python
# Use XYZ-prefixed asset string
ASSET = "xyz:BRENTOIL"  # or xyz:GOLD / xyz:SILVER / xyz:SP500 / xyz:XYZ100 / xyz:CL

ad = cfg.mcp_call(
    "market_get_asset_data",
    asset=ASSET,
    candle_intervals=["15m", "1h", "4h"],
    include_funding=True,
)

cfg._wrapper_client.push_signal(
    address=STRATEGY_ADDRESS,
    scanner="<agent>_signals",
    asset=ASSET,  # asset string must keep the xyz: prefix
    direction=direction,
    score=normalized_score,
    signal_type="<AGENT>_XYZ_SPECIALIST",
    data={"leverage": leverage, "marginUsd": margin},
)
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_asset_data` every tick (single XYZ asset) |
| Typical tick interval | 180s |
| Typical risk envelope | single XYZ asset, tighter `drawdown_halt_pct` (tail risk on commodities), wider DSL phase1 |
| Example agent | **Dire** (BRENTOIL) |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dire/scripts/dire-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dire/scripts/dire_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dire/runtime.yaml |

**When to use this pattern:** You want to trade oil, gold, silver, equities indices, etc. via Hyperliquid XYZ. Inherits Kodiak family structure but with XYZ-tuned DSL and risk.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Dire** | v1.0 | xyz:BRENTOIL | BRENTOIL specialist. Adapted Kodiak framework with XYZ-tuned thresholds and wider phase-1 loss tolerances. Commodity-specific drawdown guardrails. | BRENTOIL, Wide DSL, XYZ |
| **Lemur** | v1.0 | xyz:IPOPs (auto-discovered) | **Onboarding tier.** Auto-detects Pre-IPO Perpetuals (IPOPs) via the trade.xyz funding signature: `\|funding\| ≤ 1e-7 AND max_leverage ≤ 5` (1% funding multiplier vs 0.5 standard). Today: `xyz:SPCX`. Auto-expands when ANTHROPIC / OPENAI / STRIPE list. 24/7 trading, moderate DSL. Tick 900s (Discovery Bounds throttle moves). | Onboarding, IPOP, Auto-discover, Tick 900s, 24/7 |

---

### 4. Multi-asset whitelist

**Thesis:** Iterate over a strict whitelist of crypto majors (e.g. BTC/ETH/SOL), score each asset, fire on the best-scoring one. Tighter universe than universe trend-followers — more discipline, less noise.

**Distinctive MCP signature:**

```python
WHITELIST = ["BTC", "ETH", "SOL"]  # strict subset — pick 3-6 majors

best_candidate = None
for asset in WHITELIST:
    ad = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["15m", "1h", "4h"],
        include_funding=True,
    )
    # score_asset() returns (score, direction, reasons) — defined in your producer
    score, direction, reasons = score_asset(ad)
    if score >= MIN_SCORE and (best_candidate is None or score > best_candidate["score"]):
        best_candidate = {
            "asset": asset,
            "score": score,
            "direction": direction,
            "reasons": reasons,
        }

if best_candidate:
    cfg._wrapper_client.push_signal(
        address=STRATEGY_ADDRESS,
        scanner="<agent>_signals",
        asset=best_candidate["asset"],
        direction=best_candidate["direction"],
        score=best_candidate["score"],
        signal_type="<AGENT>_WHITELIST",
        data={"leverage": leverage, "marginUsd": margin},
    )
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_asset_data` (multiple calls per tick — one per whitelisted asset) |
| Typical tick interval | 300s (5 min) |
| Typical risk envelope | 3–6 whitelisted assets, conviction-tier leverage, `max_entries_per_day` 1–3 |
| Example agent | **Bison** (BTC/ETH/SOL) |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/scripts/bison-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/scripts/bison_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bison/runtime.yaml |

**When to use this pattern:** You believe most crypto noise comes from low-cap alts and want to restrict to majors only. Or your thesis is specific to a small known set of assets.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Bison** | v1.0 | BTC · ETH · SOL | Iterates BTC / ETH / SOL per tick, scores each independently, fires the best-scoring candidate above MIN_SCORE. Tick 300s. Max 1–3 entries per day. | Whitelist, Best-of-N, Tick 300s |
| **Hedgehog** | v1.0 | BTC · ETH · SOL | **Onboarding tier.** Same universe as Bison but each asset is independently directional — BTC long + ETH short allowed simultaneously. Up to 3 slots, per-position DSL. 10% margin per leg (30% max committed). | Onboarding, Basket, Per-leg-DSL, 3-slot |
| **Hawk** | v1.0 | BTC · ETH · SOL | **Onboarding tier.** Multi-asset whitelist with breakout-detection scoring (7d high/low). **Tight DSL** — Phase 1 max_loss 8%, T0 locks 30% at +5%. Failed breakouts cut fast. | Onboarding, Breakout, Tight DSL, hard_timeout 24h |
| **Salamander** | v1.0 | BTC · ETH · SOL | **Onboarding tier.** Multi-asset whitelist with pullback-detection scoring (3-7% counter-move in established 4h trend). **Asymmetric DSL** — wider Phase 1 (10%), tight Phase 2 (T0 +5% / lock 30%). | Onboarding, Pullback, Asymmetric DSL |
| **Bobcat** | v1.0 | xyz: big-tech (NVDA · TSLA · AAPL · META · MSFT · GOOGL · AMZN · AMD · MU · INTC · TSM · ORCL) | **Onboarding tier.** Bison-pattern on XYZ big tech equities. 4h trend + SM agreement. 48h hard_timeout for the weekend pricing gap. | Onboarding, XYZ-Big-Tech, hard_timeout 48h |
| **Raccoon** | v1.0 | All XYZ excl. IPOPs | **Onboarding tier — weekend-gated.** ONLY fires Fri 22:00 UTC → Mon 00:00 UTC, the trade.xyz no-external-price window. Captures the Mon-open reconciliation snap-back when external pricing resumes. Tight DSL, 48h hard_timeout forces Mon-open exit. | Onboarding, XYZ-Weekend, Reconciliation, Time-gated |

---

### 5. Trader-follower / hot-streak

**Thesis:** Pull ELITE/RELIABLE traders winning recently, identify their strongest current position, follow it. Hunts coat-tail alpha from quality traders.

**Distinctive MCP signature:**

```python
# Refresh trader pool once per 24h (cache on disk)
if cache_stale_or_empty():
    pool = cfg.mcp_call(
        "discovery_get_top_traders",
        time_frame="MONTHLY",
        sort_by="RETURN_ON_INVESTMENT",
        limit=60,
    )

# Per tick: pull live state of every cached trader
trader_addresses = [t["wallet"] for t in pool]
states = cfg.mcp_call("discovery_get_trader_state", trader_addresses=trader_addresses)

# Find whoever has the highest-conviction current position
# Apply SM-alignment + entry-discipline + per-trader dedupe gates
cfg._wrapper_client.push_signal(
    address=STRATEGY_ADDRESS,
    scanner="<agent>_signals",
    asset=best_position["asset"],
    direction=best_position["direction"],  # whale's direction
    score=normalized_score,
    signal_type="<AGENT>_HOT_STREAK",
    data={
        "leverage": leverage,
        "marginUsd": margin,
        "traderId": best_position["wallet"],
        "tcs": best_position["tcs"],
        "concentration": best_position["concentration"],
    },
)
```

| | |
|---|---|
| Producer-signature for fleet audit | `discovery_get_trader_state` every tick (the cached `discovery_get_top_traders` fires only on cache miss) |
| Typical tick interval | 60–180s |
| Typical risk envelope | conviction-tier leverage based on trader quality + position size, whale entry-discipline gate, per-trader event dedupe |
| Example agent | **Raptor** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/scripts/raptor_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/raptor/runtime.yaml |
**When to use this pattern:** You believe selecting alpha-generating traders and copying them produces better risk-adjusted returns than pure technical scanning.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Raptor** | v3.0 | Multi (follows top traders) | Caches top traders (24h), detects highest-conviction current positions, follows with gates on reputation + position size + SM alignment + per-trader entry discipline. Deduplicates repeated follows. | Coat-tail, 24h cache, Tick 60–180s |
| **Jackal** | v1.0 | Multi (follows top traders) | Maintains an active trader pool, detects new entries, enriches each candidate with TA + funding regime. Strict per-trader contamination rules. | New-entry, TA, Funding |
| **Spider** | v2.0 | Multi (arena-anchored) | Patient anchor sniper. Arena-leader overlap + SM-leaderboard universe + funding + relative strength. Single-leg, 7-day minimum hold, fee-aware. | Patient, Arena-anchor, 7d-hold |
| **Albatross** | v1.0 | Arena leaders (multi-week composite) | **Onboarding tier.** Mirrors Senpi Arena leaders selected by composite conviction score: `0.3 × monthly_roe + 0.7 × mean(weekly_roe) − 0.5 × stdev(weekly_roe)`. Rewards multi-week persistence, penalizes lucky-week luck. Pool refreshes every 4h. **Requires user-scope auth token** (calls `strategy_list` + `discovery_get_trader_state` for other users). | Onboarding, Arena, Multi-week, Conviction-weighted, User-scope-auth-required |

---

### 6. Striker / rank-jump detector

**Thesis:** Detect when an asset jumps the SM-leaderboard ranks aggressively (10+ positions in one tick from #25+). Catches first-jump events before they become crowded top-3 plays. "One amazing trade per day" cadence.

**Distinctive MCP signature:**

```python
# Pull current ranks
markets = cfg.mcp_call("leaderboard_get_markets", limit=100)

# Compare to previous tick (state file: rank-history.json)
prev_ranks = load_rank_history()  # your helper — returns dict from state file
current_ranks = {m["asset"]: m["rank"] for m in markets}

jumpers = []
for asset, current_rank in current_ranks.items():
    prev_rank = prev_ranks.get(asset)
    if prev_rank is None or prev_rank < 25:
        continue  # only track jumps from quiet ranks
    jump = prev_rank - current_rank
    if jump >= 10:
        # Direction comes from the market data — e.g. signed 4h price change
        direction = "LONG" if market_4h_change(asset) > 0 else "SHORT"
        jumpers.append({
            "asset": asset,
            "jump": jump,
            "current_rank": current_rank,
            "direction": direction,
        })

# Pick the highest-quality jumper (quality gates: $3M+ day notional, trader_count >= 50)
if jumpers:
    best = pick_best_jumper(jumpers)  # your helper — returns one dict from jumpers
    cfg._wrapper_client.push_signal(
        address=STRATEGY_ADDRESS,
        scanner="<agent>_signals",
        asset=best["asset"],
        direction=best["direction"],
        score=normalized_score,
        signal_type="<AGENT>_STRIKER",
        data={"leverage": leverage, "marginUsd": margin, "rankJump": best["jump"]},
    )

save_rank_history(current_ranks)
```

| | |
|---|---|
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 180s |
| Typical risk envelope | top 50 HL assets with $3M+ day notional, `max_entries_per_day` 1, conviction-tier leverage |
| Example agent | **Jaguar** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jaguar/scripts/jaguar-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jaguar/scripts/jaguar_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/jaguar/runtime.yaml |
**When to use this pattern:** You want to catch the inflection point when SM interest starts spiking on a previously-quiet asset. Fewer trades per day, higher conviction per trade.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Jaguar** | v3.2 | Multi (rank-jumpers) | Detects 10+ leaderboard rank jumps in a single tick from mid-ranks (#25+). Combines jump magnitude, current rank, day notional ($3M+), and trader count (≥50). Max 1 entry/day. | Rank-jump, 1/day, $3M+ notional |
| **Roach** | v1.0 | Multi (Strikers) | Striker-only signal emitter. FIRST_JUMP / IMMEDIATE_MOVER detection with volume floor. Producer pushes signals only; runtime handles execution. | Striker, FIRST_JUMP, Volume-floor |
| **Roach-B** | v1.0 | Multi (Strikers) | Second wallet instance of the Roach producer. Same FIRST_JUMP / IMMEDIATE_MOVER thesis on a separate strategy wallet. | Striker, Roach-pattern, Multi-wallet |
| **Orca** | v1.0 | Multi (Strikers) | Gen-1 vanilla Striker — FIRST_JUMP + volume + base scoring. The original Striker template before per-asset specializations. | Striker, Vanilla-Gen-1, FIRST_JUMP |

---

### 7. Funding-regime fade

**Thesis:** Detect when funding has been persistently extreme for hours (crowded one direction), then fade the crowd at exhaustion. Combines funding extremity + SM positioning + cooldowns.

**Distinctive MCP signature:**

```python
# Pull universe + funding regime
markets = cfg.mcp_call("leaderboard_get_markets", limit=100)
regime = cfg.mcp_call("market_get_funding_regime")

# For each candidate, check funding history (persistence over hours)
for asset in candidates:
    funding_hist = cfg.mcp_call("market_get_funding_history", asset=asset)
    if funding_persistently_extreme(funding_hist) and exhaustion_signals_fire(asset):
        # Fade the crowd (signal direction is OPPOSITE of funding direction)
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner="<agent>_signals",
            asset=asset,
            direction=fade_direction,
            score=normalized_score,
            signal_type="<AGENT>_FUNDING_FADE",
            data={"leverage": leverage, "marginUsd": margin, "fundingAnn": fr_annualized},
        )
        break  # one trade per tick max
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_funding_regime` every tick |
| Typical tick interval | 300s (5 min — funding doesn't change that fast) |
| Typical risk envelope | crypto perps with OI > $3M, quiet hours (00–04 UTC), post-loss asset cooldowns |
| Example agent | **Pangolin** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/scripts/pangolin-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/scripts/pangolin_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/pangolin/runtime.yaml |
**When to use this pattern:** You believe persistent funding extremity is a leading indicator of forced unwinds, and you want to position opposite the crowd at exhaustion.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Pangolin** | v1.4 | Multi (perps with OI > $3M) | Detects persistent extreme funding over hours and fades the crowd at exhaustion. Funding extremity + persistence duration + SM positioning + cooldowns. Quiet-hours gating (00–04 UTC). | Funding-fade, Tick 300s, Quiet-hours |
| **Dog** | v2.0 | 4-coin whitelist | Fades crowded funding on a 4-coin watchlist. Regime hard-gate: skips entry when funding regime contradicts the fade direction. | 4-coin, Hard-gate, Regime |
| **Vulture** | v2.3 | HYPE | HYPE funding-regime contrarian. Enriches each candidate with `market_get_funding_history` + held-position context. LLM gate is pass-through. | HYPE, Funding-hist, Contrarian |

---

### 8. Contrarian crowding-unwind hunter

**Thesis:** Wait for the crowd to overcommit (high funding + lopsided SM + concentrated OI) AND exhaustion signals to fire (volume decline + price stall + RSI divergence). Enter opposite to the crowd.

**Distinctive MCP signature:**

```python
# Pull universe + SM positioning map (shared across all candidates)
instruments = cfg.mcp_call("market_list_instruments")
sm_map = cfg.mcp_call("leaderboard_get_markets", limit=200)

# Score crowding per asset (funding extremity + SM tilt + OI concentration)
# Score persistence: must stay crowded for 1+ hour before considering exhaustion
# When persistence + exhaustion both fire, emit opposite to crowd direction
for asset, crowding_score in scored:
    if crowding_persisted(asset, hours=1) and combined_score(asset) >= MIN_SCORE:
        exhaustion = detect_exhaustion(asset)  # vol decline + price stall + RSI divergence
        if exhaustion["score"] >= MIN_EXHAUSTION:
            cfg._wrapper_client.push_signal(
                address=STRATEGY_ADDRESS,
                scanner="<agent>_signals",
                asset=asset,
                direction=opposite_of_crowd(asset),
                score=normalized_score,
                signal_type="<AGENT>_CONTRARIAN_UNWIND",
                data={"leverage": leverage, "marginUsd": margin, "crowdDir": crowd_dir},
            )
            break
```

| | |
|---|---|
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick |
| Typical tick interval | 900s (15 min — contrarian setups develop slowly) |
| Typical risk envelope | all crypto perps with OI > $3M, 6h post-loss per-asset cooldown, macro-trend gate blocks fades during trending macro |
| Example agent | **Owl** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/owl/scripts/owl-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/owl/scripts/owl_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/owl/runtime.yaml |
**When to use this pattern:** You believe crowded trades reliably unwind and you have a way to time the unwind (not just detect the crowding). The hard part is exhaustion timing, not crowding detection.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Owl** | v6.1 | Multi (perps with OI > $3M) | Waits for crowding persistence (1+ hour) plus multi-signal exhaustion (volume decline + price stall + RSI divergence). Tick 900s. 6h post-loss per-asset cooldown. Silent unless conditions align. | Crowding, Tick 900s, 6h cooldown |
| **Lemon** | v1.1 | Crypto majors + XYZ | Degen Fader — counter-trades CHOPPY/DEGEN consensus. 15m fading gate. MACRO_TREND_GATE blocks fades when |BTC 4h| > 3%. Conviction-scaled leverage capped at 10x. | Degen-fader, MACRO-gate, MIN_SCORE 9 |

---

### 9. Cross-asset lag detector

**Thesis:** When BTC moves > 2% in 4h, certain alts lag behind and catch up shortly after. Detect the lag and position for the catch-up.

**Distinctive MCP signature:**

```python
# Specialized MCP — pulls laggard alts with follow-rate when BTC moves
flows = cfg.mcp_call("market_get_cross_asset_flows")

# flows contains laggards with follow_rate >= 0.8 when |BTC 4h| > 2%
# If BTC hasn't moved enough, response is empty — patient producer, silence is correct
if flows.get("laggards"):
    best = flows["laggards"][0]  # already sorted by follow_rate
    cfg._wrapper_client.push_signal(
        address=STRATEGY_ADDRESS,
        scanner="<agent>_signals",
        asset=best["asset"],
        direction=best["direction"],  # same direction as BTC's move
        score=normalize(best["follow_rate"]),
        signal_type="<AGENT>_LAG_CATCH",
        data={"leverage": leverage, "marginUsd": margin, "followRate": best["follow_rate"]},
    )
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_cross_asset_flows` every tick |
| Typical tick interval | 60s (lag detection wants fresh BTC moves) |
| Typical risk envelope | filtered laggards only, fires only when BTC's 4h move exceeds threshold (often silent) |
| Example agent | **Mantis** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mantis/scripts/mantis-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mantis/scripts/mantis_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/mantis/runtime.yaml |

**When to use this pattern:** You believe BTC leads the alt market and want to systematically capture the lag. Most ticks are silent (BTC hasn't moved enough); the producer fires only when the macro condition is met.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Mantis** | v5.0 | Multi (BTC-led laggards) | When BTC moves >2% in 4h, identifies laggard alts with follow-rate ≥0.8 and enters for the catch-up close. Direction matches BTC's 4h move sign. Tick 60s. Often silent on quiet BTC days. | BTC-led, Follow-rate ≥0.8, Tick 60s |

---

### 10. Multi-asset XYZ contrarian fader

**Thesis:** Multiple XYZ macro assets (CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100), contrarian direction flip when SM has overconcentrated, spread + freshness gates. Slower XYZ-tuned DSL.

**Distinctive MCP signature:**

```python
import time  # for the stale-order sweep below

XYZ_WHITELIST = ["CL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100"]

# Pre-tick safety: cancel any stale resting orders (v4.1 hot-patch from example agent)
open_orders = cfg.mcp_call(
    "strategy_get_open_orders",
    strategy_wallet=STRATEGY_ADDRESS,
)
for order in open_orders:
    if (time.time() - order["timestamp"]) > 600:  # 10-min stale-cancel
        cfg._wrapper_client.mcp_call(
            "cancel_order",
            strategy_wallet=STRATEGY_ADDRESS,
            order_id=order["id"],
        )

# Universe scan
sm_map = cfg.mcp_call("leaderboard_get_markets", limit=200)
for asset_name in XYZ_WHITELIST:
    asset = f"xyz:{asset_name}"
    ad = cfg.mcp_call("market_get_asset_data", asset=asset, candle_intervals=["1h", "4h"])
    if contrarian_setup(ad, sm_map.get(asset_name)) and spread_ok(ad):
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner="<agent>_signals",
            asset=asset,
            direction=fade_direction,  # OPPOSITE of SM
            score=normalized_score,
            signal_type="<AGENT>_XYZ_CONTRARIAN",
            data={"leverage": leverage, "marginUsd": margin},
        )
        break
```

| | |
|---|---|
| Producer-signature for fleet audit | `leaderboard_get_markets` every tick (plus periodic `market_get_asset_data` per whitelisted asset) |
| Typical tick interval | 300s (5 min) |
| Typical risk envelope | 6 XYZ macro assets, conviction-tier leverage, 600s stale-cancel auto-purge |
| Example agent | **Bald Eagle** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bald-eagle/scripts/eagle-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bald-eagle/scripts/eagle_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/bald-eagle/runtime.yaml |
**When to use this pattern:** You want to trade XYZ commodities/indices with a contrarian thesis (faders, not trend-followers). The XYZ-specific stale-order guard is important because XYZ ALOs can rest for days if not actively managed.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Bald Eagle** | v1.0 | 6 XYZ macro assets | Six XYZ macro assets — CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100. Contrarian setup detection + spread filter + 10-minute stale-cancel auto-purge. Conviction leverage per macro conditions. | 6 XYZ assets, Stale-cancel 600s, Tick 300s |
| **Kestrel** | v1.1 | 13 XYZ macro assets | 13-asset XYZ macro universe (CL, BRENTOIL, GOLD, SP500, XYZ100, and more) with funding-alignment overlay. Broader universe variant of the XYZ contrarian thesis. | 13 XYZ assets, Funding-align, XYZ macro |

---

### 11. Volume engine / market-making (specialized)

**Thesis:** Not a directional trading thesis — runs a two-wallet pair (one volume + one runner) that recycles builder fees and accumulates volume credits.

This pattern is more involved than the others (continuous high-frequency `cancel_order` + `create_position` cycle, two-wallet coordination). Recommend reading the example agent's full source rather than working from an inline snippet.

| | |
|---|---|
| Producer-signature for fleet audit | High-frequency `cancel_order` + `create_position` patterns |
| Typical tick interval | Continuous |
| Typical risk envelope | Two-wallet pair with daily top-ups; net wallet bleed = builder-fee-recycling cost rate |
| Example agent | **Turbine** |
| Example skill directory listing | https://github.com/Senpi-ai/senpi-skills/tree/main/turbine/scripts |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/turbine/runtime.yaml |

**When to use this pattern:** You're not trading direction — you're recycling builder fees against a known volume target. This is a niche use case; most builders should pick one of patterns 1–10.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Turbine** | v3.2 | Two-wallet pair | Volume engine — runs a two-wallet pair (volume + runners) recycling builder fees and accumulating volume credits. Continuous high-frequency `cancel_order` + `create_position` cycle. | Volume engine, Two-wallet, Builder-fee recycle |

---

### 12. Microstructure / order-flow

The first archetype to trade the *order flow underneath* price rather than price/SM alone. It reads `market_get_asset_data`'s `oi_velocity` (open-interest velocity) and the L2 `order_book` (up to 20 levels per side) to detect **forced** or **imbalanced** flow:

- **Liquidation / forced flow:** OI unwinding fast (positions being force-closed) + a violent price move + a thinning book on the side price is running into = a cascade to ride.
- **Order-book imbalance:** a persistent resting-depth skew (bids ≫ asks, or the reverse) as an accumulation / distribution tell.

```python
# Per asset, per tick:
data = mcp_call("market_get_asset_data", asset=a,
                candle_intervals=["5m","1h"], include_order_book=True, include_funding=False)
oi_pct = oi_velocity_1h(data, a)      # from the oi_velocity object — or self-computed from a last-OI cache (it can be null)
move   = price_move_pct(candles_1h, 1)
levels = data["data"]["order_book"]["levels"]   # levels[0]=bids, levels[1]=asks; each {px, sz, n}
bid_depth, ask_depth = sum(l["sz"] for l in levels[0]), sum(l["sz"] for l in levels[1])
# Forced flow: oi_pct <= -3  AND  |move| >= 2  AND  5m still moving the same way  → ride the flow direction
```

**Gotcha:** `oi_velocity` can be `null` — keep a persisted last-OI cache (`state/oi-state.json`) and compute the delta yourself as a fallback (the cache warms after one tick per asset).

**When to use this pattern:** You want an edge from market *microstructure* (forced liquidations, resting-depth imbalance) rather than candle trend or SM positioning. Short-horizon — pair a wide "let winners run" ladder with a `hard_timeout` outer bound.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Piranha** | v1.0 | BTC/ETH/SOL/HYPE | Liquidation-cascade / forced-flow hunter — OI unwinding fast + violent move + thin book ⇒ ride the forced flow. Wide DSL + 24h hard_timeout. | OI velocity, Order book, Forced flow |

*(Marlin — order-book-imbalance momentum — joins this family next.)*

---

## Decision tree — help a user pick their first strategy

This is the guided path an **onboarding agent** walks a new user through. Start broad ("what kind of trader do you want your agent to be?"), narrow **one layer at a time**, and land on a single deployable strategy. Ask one question, show 2–6 options, let them pick, then go deeper. Each leaf names a **real, installable agent** — beginners are routed to the **onboarding tier** (simpler scoring, conservative sizing); the *level up* line is the full-fleet version for once they're comfortable.

> **Conversational rule for the agent:** never dump the whole tree. Surface Layer 1, let the user choose, then reveal only that branch's Layer 2. Explain each option in one plain sentence ("trend-following = when something's moving, ride it and hold"). Always end at exactly one recommended strategy + its risk level + DSL preset, then offer to deploy it. **If the user can't answer Layer 1 — they don't know the words yet — drop to Layer 0 and let the agent suggest.**

### Layer 0 — When the user doesn't know how to answer

Most first-time users can't say "I want a trend-follower" — they don't have the vocabulary. **The agent must recommend without the user self-classifying.** Four paths below.

> ⚡ **Ask before you scan — keep the first moment fast.** Paths **A / B / C are instant** (no data calls). Path **D reads live market data** (several MCP calls, a few seconds). So **lead with the question, and only run D when the user explicitly opts in** — e.g. they pick *"help me choose"* or *"suggest something for me."* Don't pre-fetch market data on entry; fetch on demand, and say *"give me a sec to read the market…"* so the wait is expected, not a freeze.

**A. Express lane — "just pick something simple for me."** The user wants the agent to decide. Recommend the conservative default and go straight to deploy:
- **Default first strategy → Hedgehog** (equal-weight BTC+ETH+SOL trend, diversified) — or **Beaver** (BTC only) for the simplest single-asset version.
- Settings: **`balanced` DSL preset, 20% margin, 3x leverage** — the simplest, most-liquid, lowest-leverage starting point.
- Frame it honestly — **never say "safe."** *"I'll start you on a simple trend-follower on the major coins — it holds them while they're trending and steps out when they stall. It's the lowest-complexity, lowest-leverage place to begin — not risk-free (no strategy is; any single trade can lose), just the least to think about while you learn. We'll tune it the moment you've watched it run."*

**B. Plain-language quiz — map feelings to an archetype.** The user wants some say, but the Layer-1 terms are jargon. Ask these (no trading words), then route:

| Ask (plain English) | If they lean… | Route to |
|---|---|---|
| "When something's already shooting up, do you want to **jump in and ride it**, or **wait for it to fall back first**?" | ride it → trend · wait → contrarian | Layer 2A / 2B |
| "Should the agent **form its own opinion**, or just **copy whoever's winning right now**?" | own opinion → (keep going) · copy → copy-trading | Layer 2C |
| "Do you have a **specific market in mind** (a coin, a stock, oil/gold), or want it to **scan everything** for you?" | specific → single-market · scan → basket/universe | Layer 2D / 2A |
| "A few **big wins you hold for days**, or **lots of small quick ones**?" | big/held → `let_winners_run` · small/quick → tighter preset | sets the DSL preset |

**C. Show, don't ask — pick by vibe.** Some people decide best from concrete examples. Offer 2–3 plain-English one-liners and let them point at one:
- **Beaver** — *"patiently holds BTC while it's trending up, steps out when it stalls."*
- **Egret** — *"bets against the crowd when everyone's piled in one direction and price stops following."*
- **Albatross** — *"copies traders who've won the arena for weeks, not just one lucky day."*
- **Bobcat** — *"trades big-tech stocks (NVDA, TSLA, …) 24/7 on Hyperliquid."*

**D. Contextual suggestion (opt-in — *only after* the user picks "help me choose / suggest for me").** The strongest recommendation, but it reads the live market (several MCP calls, a few seconds) — so **do not run it on entry**; trigger it only when the user explicitly asks for it, and tell them you're reading the market first. When triggered, read their situation + the market, then propose one strategy with reasons.

*Step 1 — read the user* (reads only — nothing is traded):
- `account_get_portfolio` → **budget** (account value) + **current holdings** (assets they already own/know).
- `discovery_get_trader_history` on their wallet → **what they tend to trade** (recurring assets/direction).
- Ask **one** question — *risk comfort / goal*: "cautious" / "balanced" / "aggressive" → maps to leverage + DSL preset.

*Step 2 — read the market* for the candidate asset(s) (candidate = an asset they hold/trade; else a Hyperfeed-hot name; else BTC):
- `leaderboard_get_momentum_events` (tier 3) + `leaderboard_get_top` → **Hyperfeed**: what's in a strong live move right now.
- `market_get_asset_data(asset)` → **regime** from 4h trend structure + `oi_velocity` + funding → `TRENDING` / `RANGEBOUND` / `VIOLENT`.
- `leaderboard_get_markets` → **Smart Money** concentration + direction on that asset.

*Step 3 — map the read to a strategy:*

| What the signals say | Recommend |
|---|---|
| Asset **TRENDING** + SM **aligned** (same dir, ≥55%) | Trend-follower on that asset — 🟢 Beaver/Heron/Hummingbird, or **Hedgehog** basket if no single standout |
| **RANGEBOUND** + SM **extreme & one-sided** (≥70%) price won't confirm | Fader → **Egret** |
| **VIOLENT** move + OI unwinding fast (Hyperfeed lit up) | Microstructure → **Piranha** (*only if risk = "go big"*) |
| Hyperfeed shows a **fresh rank-jump / breakout** | 🟢 **Hawk** (breakout) or **Jaguar** (rank-jump) |
| User would rather **copy winners** / has no asset view | 🟢 **Albatross** (multi-week arena winners) |
| Interest in **stocks / commodities** | 🟢 **Bobcat** (big-tech) · **Dire** (oil) · **Lemur** (pre-IPO) |
| Nothing clean (chop + weak signals) | Default → **Hedgehog** basket, `balanced`, conservative sizing — or honestly say *"nothing's set up cleanly right now; want to start small and watch, or wait?"* |

*Step 4 — filter & size:* drop any candidate whose catalog `min_budget` exceeds their account value; set margin % + leverage + DSL preset from the risk answer (cautious → ~15% / 3x / `balanced`; aggressive → ~25% / 5x / `let_winners_run`).

*Step 5 — recommend with the "why":*
> *"Here's what I can see: you hold **{assets}**, budget **${X}**. Right now **{asset}** is **{regime}** and Smart Money is **{SM read}**{, and the Hyperfeed shows {event}}. I'd start you on **{strategy}** — {one-line thesis} — with the **{preset}** stop profile at {margin}% / {lev}x. Why: {2–3 concrete reasons}. Want me to deploy it?"*

Worked example: *"You hold BTC and ETH; budget $1,500. BTC's 4h structure is higher-lows and Smart Money is 64% long — trend and crowd agree. → **Beaver** (BTC trend-follower), `balanced` preset, 20% margin / 3x. It rides BTC while it trends and steps out when it stalls, and you already hold BTC so it's a market you know."*

**Fallbacks:** no wallet access → use the plain-language quiz (B). Any MCP read fails or signals conflict → fall back to the express default (A). Never block on a missing signal.

Whatever the path, **always land on exactly one strategy**, confirm risk + DSL preset (Layer 3), and deploy. Be honest up front: *the first strategy is a starting point to learn from, not a final answer — and not risk-free; any strategy can lose on a given trade.* Set the expectation that you'll help them tune it after they've watched one trade. **Never describe a strategy or default as "safe."**

### Layer 1 — What should your agent *believe* about markets?

Ask which one sentence sounds most like the user:

| If they say… | They want | Go to |
|---|---|---|
| "When something's trending, ride it and hold." | **Trend-following** | Layer 2A |
| "When the crowd is all-in, bet on the reversal." | **Contrarian / fade** | Layer 2B |
| "Just copy traders who are already winning." | **Copy-trading** | Layer 2C |
| "I want to trade a specific market — a coin, **stocks, commodities, or 🔥 pre-IPO names** (SpaceX, …)." | **Single-market / XYZ** | Layer 2D |
| "Catch explosive breakouts / jumps early." | **Breakout / momentum-jump** | Layer 2E |
| "Earn from market structure, not from picking a direction." | **Structural / neutral** | Layer 2F |

> 💡 **Not just crypto.** Senpi trades **XYZ markets 24/7** (even when TradFi is closed): big-tech **stocks** (NVDA, TSLA, …), **commodities** (oil, gold, indices), and — increasingly popular — **pre-IPO perpetuals (IPOPs)** like **SpaceX**, *tradeable before the company lists*. If a user perks up at stocks or pre-IPO, route straight to **Layer 2D** — it's one of Senpi's most distinctive hooks.

### Layer 2A — Trend-following → what do you want to ride?

- **One major coin** — pick BTC / ETH / SOL / HYPE.
  - 🟢 Beginner: **Beaver** (BTC) · **Heron** (ETH) · **Hummingbird** (HYPE) — SM-gated 4h trend, wide DSL, simple scoring.
  - ⬆️ Level up: **Grizzly** (BTC) · **Polar** (ETH) · **Kodiak** (SOL) · **Wolverine** (HYPE) — Kodiak-family alpha hunters.
- **A basket of majors** (BTC + ETH + SOL together).
  - 🟢 Beginner: **Hedgehog** (equal-weight basket, up to 3 at once).
  - ⬆️ Level up: **Bison** (conviction whitelist) · **Condor** / **Cheetah** (full-universe trend).
- **Only when order flow confirms it** (enter a breakout only if open interest is rising).
  - **Badger** (OI-confirmed breakout). Wide DSL.

### Layer 2B — Contrarian / fade → what extreme do you fade?

- **An overcrowded Smart-Money side** (everyone's long, price won't follow) → 🟢 **Egret** (SM-divergence fader) · ⬆️ **Owl** (crowding-unwind) · **Lemon**.
- **Extreme funding rates** → **Pangolin** (canonical) · **Vulture** (HYPE) · **Dog** (4-coin).
- **Overextended stocks / commodities (XYZ)** → **Bald Eagle** · **Kestrel** (13-asset macro).

### Layer 2C — Copy-trading → who do you copy?

- **Multi-week arena winners** (proven over a month, not one lucky week) → 🟢 **Albatross** (conviction-weighted leader pool).
- **Live hot-streak traders** (whoever's hot right now) → **Raptor** · **Jackal** · **Spider** (arena-anchored).

### Layer 2D — Single-market specialist → which market?

XYZ markets (stocks / commodities / pre-IPO) trade **24/7 on Hyperliquid**, even when TradFi is closed.

- 🔥 **Pre-IPO names (IPOPs — SpaceX, etc.)** → 🟢 **Lemur** — trades pre-IPO perpetuals *before the company lists*; auto-discovers new IPOPs by their funding signature (today: SPCX/SpaceX; auto-expands as trade.xyz lists names like ANTHROPIC, OPENAI, STRIPE). One of Senpi's most distinctive capabilities.
- **Big-tech stocks (XYZ equities)** → 🟢 **Bobcat** (NVDA/TSLA/AAPL/META/MSFT/GOOGL/…).
- **Oil / metals / indices (XYZ)** → **Dire** (BRENTOIL) as the template — tune the asset string.
- **Weekend stock-gap reconciliation** → 🟢 **Raccoon** (weekend-only XYZ snap-back, captures the Mon-open move).
- **A specific crypto major** → see Layer 2A (Kodiak family).

### Layer 2E — Breakout / momentum-jump → what kind of move?

- **Break of the 7-day high/low (majors)** → 🟢 **Hawk** (breakout buyer / breakdown seller) · **Badger** (OI-confirmed).
- **Buy the dip *within* an uptrend** → 🟢 **Salamander** (pullback catcher).
- **Leaderboard rank-jumps caught early** → **Jaguar** · **Orca** · **Roach**.
- **Ride a liquidation cascade / forced flow** (OI unwinding fast + a violent move) → **Piranha** (microstructure / order-flow).

### Layer 2F — Structural / neutral → what structure?

- **BTC-led laggard rotation** (an alt that hasn't caught up to a BTC move yet) → **Mantis** (cross-asset lag).
- **Volume / market-making** (not a directional bet) → **Turbine** (specialized).
- *Expanding set — relative-value pairs, order-book-imbalance momentum (Marlin), and copy-the-copiers are being added. (Microstructure forced-flow is already live — see Piranha under Layer 2E.)*

### Layer 3 — Lock it in (the deploy step)

Once a strategy is chosen, confirm three things with the user, then deploy:

1. **Risk / sizing** — margin % of equity + leverage. *First-strategy default: 20–25% margin, ≤5x.*
2. **DSL preset** — `balanced` (the smart default) for most; `let_winners_run` for conviction trend-holders; `mean_reversion` for faders; `scalp` for high-frequency. See [`dsl-presets.yaml`](dsl-presets.yaml).
3. **Config + launch** — set wallet / chat / decision-model, then `openclaw senpi runtime create` + the disown-safe daemon launch. Each agent's README has the exact steps.

> **First-strategy rule of thumb:** pick an **onboarding-tier** agent (🟢 above — Beaver/Heron/Hummingbird/Hedgehog/Hawk/Salamander/Albatross/Lemur/Bobcat/Raccoon), keep the **`balanced`** DSL preset, size at 20–25% margin / ≤5x. Graduate to fleet agents once they've watched one run.

---

### Builder shortcut — map a thesis to an archetype by structure

For someone who already knows their thesis and just wants the matching producer archetype to fork:

```
Single asset, small whitelist, or universe?
├─ Single crypto asset (BTC/ETH/SOL/HYPE)      → Pattern 2 — Single-asset alpha hunter (Kodiak family)
├─ Single XYZ asset (oil/metals/indices)        → Pattern 3 — Single-asset XYZ specialist
├─ Small whitelist of crypto majors (3–6)       → Pattern 4 — Multi-asset whitelist
├─ Top-N HL universe (scan everything liquid)
│   ├─ trend-continuation?    → Pattern 1 — Universe trend-follower
│   ├─ first-jump detection?  → Pattern 6 — Striker / rank-jump
│   ├─ funding-regime fades?  → Pattern 7 — Funding-regime fade
│   └─ contrarian unwinds?    → Pattern 8 — Contrarian unwind hunter
├─ Multiple XYZ, contrarian   → Pattern 10 — Multi-asset XYZ contrarian fader
├─ Follow specific traders    → Pattern 5 — Trader-follower / hot-streak
└─ BTC-anchored lag           → Pattern 9 — Cross-asset lag detector
```

If a thesis doesn't fit any pattern: it's usually (a) a hybrid of two (most live agents are hybrids of 1–2 archetypes) — copy the closest and layer in the second; or (b) genuinely new — write it from scratch with `senpi_runtime_helpers`; the framework supports any signal flow that can call MCP tools and emit `push_signal`.

---

## Common ingredients (regardless of pattern)

Every pattern above shares these common producer ingredients. Copy them verbatim from any example agent's `<agent>_config.py` and `<agent>-producer.py` (URLs in each pattern's section above):

- **SDK probe** at the top of `<agent>_config.py` — locates `senpi_runtime_helpers` in the standard install paths.
- **Lazy `SenpiClient` wrapper** — instantiates on first MCP call, validates `SENPI_AUTH_TOKEN`.
- **Wallet resolver** — reads from `<AGENT>_WALLET` env var first, then `config.json`.
- **Wallet-hashed daemon name** — `f"<agent>-producer-{sha256(wallet.lower())[:12]}"`.
- **`producer_daemon(fn=main, interval_seconds=N, name=..., wallet=..., scanner=...)`** — long-lived scheduler with built-in reentrancy guard.
- **Final stdout heartbeat per tick** — `{"status": "ok", "scanned": N, "candidates": M, "signals_pushed": K, "_<agent>_producer_version": "X.Y.Z"}` for telemetry + audit.

When you're copying a pattern as a starting template, keep all of these — they're the helpers-native conventions every fleet agent shares. Change only the archetype-specific scoring + thresholds.

---

## Fleet auditor reference

When verifying that a producer is firing on-chain (not silent), audit_query the producer-signature MCP call from the table above for the agent's `senpiUserId`. If the call appears at the configured tick interval, the producer is alive. If runtime-side calls (`strategy_get_clearinghouse_state` every 10s + `market_get_prices` every 30s) appear but the producer-signature calls don't, the daemon is dead or the runtime registration is broken — see [`liveness-verification.md`](liveness-verification.md) for the full diagnostic flow.

---

## Live fleet roster — archetype mapping

Quick cross-reference of every currently-live fleet agent against the archetypes above. Use this to find the closest behavioral match to the strategy you want to build, then fetch that agent's three files via the standard URL pattern:

```bash
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<agent>/scripts/<agent>-producer.py
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<agent>/scripts/<agent>_config.py
curl -fsSL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<agent>/runtime.yaml
```

| Agent | Archetype | Distinguishing notes |
|---|---|---|
| Condor | 1 — Universe trend-follower | Canonical |
| Cheetah | 1 — Universe trend-follower | Top-100 SM universe, multi-signal confluence + trader-quality enrichment |
| Python | 1 — Universe trend-follower | Multi-day-hold trend, mixed signature (`market_list_instruments` + `leaderboard_get_markets` + per-asset scan) |
| Scorpion | 1 — Universe trend-follower | Universe + funding-regime backstop, post-close cooldown |
| Wolverine | 2 — Kodiak family (HYPE) | Canonical HYPE |
| Grizzly | 2 — Kodiak family (BTC) | Canonical BTC |
| Polar | 2 — Kodiak family (ETH) | Canonical ETH |
| Kodiak | 2 — Kodiak family (SOL) | Canonical SOL |
| Dire | 3 — Single-asset XYZ specialist | Canonical (BRENTOIL) |
| Bison | 4 — Multi-asset whitelist | Canonical (BTC/ETH/SOL) |
| Raptor | 5 — Trader-follower | Canonical |
| Jackal | 5 — Trader-follower | Active trader pool + new-entry detector, TA + funding enrichment |
| Spider | 5 — Trader-follower (arena variant) | Arena-leader anchor + SM-leaderboard overlap, 7-day-hold thesis |
| Jaguar | 6 — Striker / rank-jump | Canonical |
| Roach | 6 — Striker / rank-jump | FIRST_JUMP / IMMEDIATE_MOVER + volume |
| Roach-B | 6 — Striker / rank-jump | Second wallet instance of the Roach producer |
| Orca | 6 — Striker / rank-jump | Gen-1 vanilla Striker, FIRST_JUMP + volume + base scoring |
| Pangolin | 7 — Funding-regime fade | Canonical |
| Dog | 7 — Funding-regime fade | 4-coin whitelist with regime hard-gate |
| Vulture | 7 — Funding-regime fade | HYPE funding-regime contrarian, funding-history + held-position enrichment |
| Owl | 8 — Contrarian crowding-unwind | Canonical |
| Lemon | 8 — Contrarian crowding-unwind | Degen Fader on crypto-majors + XYZ whitelist, MACRO_TREND_GATE |
| Mantis | 9 — Cross-asset lag detector | Canonical |
| Bald Eagle | 10 — Multi-asset XYZ contrarian fader | Canonical |
| Kestrel | 10 — Multi-asset XYZ contrarian fader | 13-asset XYZ macro universe with funding alignment |
| Turbine | 11 — Volume engine / market-making | Canonical |
| **Beaver** | 2 — Kodiak family (BTC, onboarding) | Onboarding-tier simplified scoring |
| **Heron** | 2 — Kodiak family (ETH, onboarding) | Onboarding-tier simplified scoring |
| **Hummingbird** | 2 — Kodiak family (HYPE, onboarding) | Onboarding-tier simplified scoring |
| **Hedgehog** | 4 — Multi-asset whitelist (onboarding) | BTC + ETH + SOL basket, per-position DSL, up to 3 simultaneous |
| **Albatross** | 5 — Trader-follower (onboarding) | Arena-leader multi-week composite (4 weekly + 1 monthly), conviction-weighted pool |
| **Hawk** | 4 — Multi-asset whitelist (onboarding) | BTC/ETH/SOL breakout buyer above 7d high / breakdown seller below 7d low. Tight DSL |
| **Salamander** | 4 — Multi-asset whitelist (onboarding) | BTC/ETH/SOL pullback catcher in established 4h trend. Asymmetric DSL |
| **Lemur** | 3 — Single-asset XYZ specialist (onboarding) | Auto-discovers IPOPs via funding signature. Today: xyz:SPCX. Future-proof for new pre-IPO listings |
| **Bobcat** | 4 — Multi-asset whitelist (onboarding) | XYZ big tech: NVDA/TSLA/AAPL/META/MSFT/GOOGL/AMZN/AMD/MU/INTC/TSM/ORCL |
| **Raccoon** | 4 — Multi-asset whitelist (onboarding, time-gated) | Weekend-only XYZ reconciliation. Fri 22:00 UTC → Mon 00:00 UTC. Captures Mon-open snap-back |
| **Badger** | 4 — Multi-asset whitelist (OI-confirmed breakout) | BTC/ETH/SOL/HYPE. Takes a breakout only when rising open interest confirms it (new money, not a fakeout). Wide "let winners run" DSL |
| **Egret** | 8 — Contrarian crowding-unwind (SM-divergence fader) | BTC/ETH/SOL/HYPE. Fades extreme SM crowding (≥70%) that price won't confirm. Tight DSL + maker-only entry + time-cuts on |
| **Piranha** | 12 — Microstructure / order-flow | BTC/ETH/SOL/HYPE. Rides forced flow — OI unwinding fast + violent move + thin book ⇒ liquidation cascade. Wide DSL + 24h hard_timeout |

Sentinel runs an in-house producer that is not currently published to this repo; no public URL.

---

## Where this catalog lives in the broader docs

- `senpi-trading-runtime/SKILL.md` — start here for the runtime + Producer SDK overview
- [`yaml-schema.md`](yaml-schema.md) — full `runtime.yaml` field reference
- [`python-producer-sdk.md`](python-producer-sdk.md) — full Producer SDK reference
- [`strategy-examples.md`](strategy-examples.md) — runtime.yaml templates by strategy type
- **This file** — scanner archetype catalog (you are here)
- [`liveness-verification.md`](liveness-verification.md) — how to confirm your producer is firing on-chain

Pick a pattern from this catalog, fetch the example agent's producer + runtime.yaml from the GitHub URLs in that pattern's section, tune the scoring and thresholds for your thesis, deploy with `openclaw senpi runtime create` + `nohup python3 ... &`. That's the canonical build path.
