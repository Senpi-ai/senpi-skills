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
| Other live examples | **Cheetah** (top-100 SM universe, multi-signal confluence + trader-quality enrichment), **Python** (multi-day-hold thesis, mixed `market_list_instruments` + `leaderboard_get_markets` + per-asset deep scan), **Scorpion** (universe + funding-regime backstop, post-close cooldown). Fetch each via `https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<agent>/scripts/<agent>-producer.py`. |

**When to use this pattern:** You want broad market coverage and entries when multiple confirmations align across timeframes. Best for trend-continuation theses.

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
| Other live examples | **Jackal** (active trader pool + new-entry detector, TA + funding enrichment), **Spider** (arena-leader anchor + SM-leaderboard overlap, patient 7-day-hold thesis — also runnable as an arena top-trader mirror variant). |

**When to use this pattern:** You believe selecting alpha-generating traders and copying them produces better risk-adjusted returns than pure technical scanning.

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
| Other live examples | **Roach** (Striker-only signal emitter, FIRST_JUMP / IMMEDIATE_MOVER + volume), **Orca** (Gen-1 vanilla Striker, FIRST_JUMP + volume + base scoring). Roach-B is a second wallet instance of the Roach producer. |

**When to use this pattern:** You want to catch the inflection point when SM interest starts spiking on a previously-quiet asset. Fewer trades per day, higher conviction per trade.

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
| Other live examples | **Dog** (4-coin whitelist with regime hard-gate: skips entry when funding regime contradicts the fade), **Vulture** (HYPE funding-regime contrarian, also enriches with `market_get_funding_history` + held-position context). |

**When to use this pattern:** You believe persistent funding extremity is a leading indicator of forced unwinds, and you want to position opposite the crowd at exhaustion.

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
| Other live examples | **Lemon** (Degen Fader — counter-trades CHOPPY/DEGEN consensus on a crypto-majors + XYZ whitelist, MACRO_TREND_GATE blocks fades during strong BTC trends). |

**When to use this pattern:** You believe crowded trades reliably unwind and you have a way to time the unwind (not just detect the crowding). The hard part is exhaustion timing, not crowding detection.

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
| Other live examples | **Kestrel** (13-asset XYZ macro universe — CL, BRENTOIL, GOLD, SP500, XYZ100, etc. — with funding-alignment overlay). |

**When to use this pattern:** You want to trade XYZ commodities/indices with a contrarian thesis (faders, not trend-followers). The XYZ-specific stale-order guard is important because XYZ ALOs can rest for days if not actively managed.

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
