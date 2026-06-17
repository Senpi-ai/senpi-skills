# Producer Patterns — Scanner Archetypes Catalog

> **This doc is the canonical routing brain.** Everything about *which strategy to recommend, how to walk a user through picking one, how to handle variants (e.g. Thesis Fund presets), default selections, and how to disambiguate between similar strategies* lives here — primarily in the [Decision Tree](#decision-tree--help-a-user-pick-their-first-strategy) below. The `catalog.json` at the repo root is now **display + install metadata only**; when the two disagree, this doc wins. (See *"Where this catalog lives in the broader docs"* at the bottom for the full source-of-truth contract.)
>
> **When to read it:** [`strategy-creation.md`](strategy-creation.md) is the build doc — start there if you're authoring a brand-new strategy. Come here for one thing: to **pick the right archetype and example agent**, either by walking the Decision Tree with a user or by jumping directly to your archetype's section. You don't need to read cover-to-cover.

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

> **Cross-cutting: 🏦 AI Hedge Funds (multi-book architecture).** Ten skills are
> *funds* rather than single-strategy agents — each runs **two complementary
> books on two wallets under one leg-parameterized producer** (the Spider
> pattern: `<AGENT>_LEG` selects the book; each book has its own wallet, runtime
> YAML, DSL, and risk envelope). They are catalogued below under their
> trading-edge archetype (a builder studying an edge should find them there),
> but as a product line they are: **Spider** (AI/Tech, §4) · **Octopus**
> (relative-value, §13) · **Camel** (carry, §7) · **Caracal** (volatility, §17)
> · **Elephant** (global-macro, §18) · **Wolf** (event-driven regime-rotation,
> §20) · **Rhino** (tail-risk / crisis-alpha, §21) · **Ox** (risk-parity /
> all-weather, §22) · **Cougar** (U.S. equity long/short, §23) · **Magpie**
> (IPO / new-listing event, §24). To build another, clone any of them: same
> `producer_daemon` + fcntl-lock + `push_signal` spine, swap the per-book scorer
> + universe, fund the two wallets, launch two daemons with `setsid`+cron.
> **Wolf and Rhino add a shared "brain" the producer computes once per tick
> before either book scores** — a cross-asset *regime* read (Wolf) or *stress*
> read (Rhino) that gates which book may fire. **Ox adds a different twist:
> per-sleeve *inverse-volatility* sizing** — each position's `marginUsd` is its
> risk-parity weight `(1/vol_i)/Σ(1/vol_j)`, so it depends on the runtime
> honoring per-signal `marginUsd`. **Cougar** is the Octopus dispersion method on
> the tokenized-equity universe; **Magpie** reuses Lemur's IPOP discovery +
> Falcon's conversion detector as a two-book fund.

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
| **Koala** | v1.0 | Operator-chosen single asset (default BTC) | **Onboarding tier — state-trigger variant.** No scoring, no multi-timeframe analysis, no market_get_asset_data call — just a state-file check. Fires ONE LONG signal per lifetime (fire-once mode) and holds with the widest DSL in any Senpi agent (max_loss 30%, retrace 25, 90d hard_timeout). Operators who want a cycling deploy set `fireOnceMode: false` with a `reEntryCooldownHours` (default 7d). For users whose entire trading thesis is "I want to own BTC and have a safety net." | Onboarding, HODL, Fire-once, Ultra-wide DSL, No-scoring |
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
| **Falcon** | v1.0 | xyz: conversion events | **Event-detection layer.** Trades the IPOP→equity **conversion** itself, not the pre-listing basket. Classifies every xyz instrument IPOP vs STANDARD by funding signature, caches it, and fires when one **flips** (funding jumps ~100x, leverage cap lifts, Discovery Bounds throttle removed → free price discovery). Requires post-conversion momentum; rides it with a **wide let-winners-run DSL** (7d hard_timeout). Carries a class-state + conversion-window cache (Badger/Piranha pattern). Tick 600s. | IPOP, Conversion-event, Momentum, Wide DSL, State-cache |

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
| **Tortoise** | v1.0 | BTC · ETH · SOL | **Onboarding tier — time-trigger variant.** DCA scheduler. Doesn't call `market_get_asset_data` for scoring — its "scanner" is a clock. Each tick checks per-asset DCA history; the most-overdue past the interval (default 24h) wins. LONG only. Persisted DCA-history cache. Maker-preferred entry (DCA isn't urgent). Wide let-winners-run DSL + 30d hard_timeout for compounding. THE most beginner-accessible trade — zero prediction skill required. | Onboarding, DCA, Time-trigger, No-prediction, Wide DSL |
| **Sheep** | v1.0 | BTC · ETH · SOL · HYPE | **Onboarding tier.** Long-only triple-EMA-stacked trend. Fires LONG only when `ema(fast) > ema(slow)` on ALL THREE timeframes (15m + 1h + 4h). Never shorts. A visual rule beginners can sanity-check on any chart. Balanced DSL + `weak_peak_cut` 6h/3%. | Onboarding, Long-only, Multi-timeframe, EMA-stack, Balanced DSL |
| **Iguana** | v1.0 | xyz:SP500 · xyz:XYZ100 | **Onboarding tier — XYZ subset.** The simplest XYZ exposure in the fleet — just the broad indices. Picks whichever has the stronger 4-day move past the threshold and trades its direction. No stock-picking, no commodities, no pre-IPO. Closest thing to "an index fund, but 24/7." Balanced DSL + 48h hard_timeout for weekend pricing-gap risk. | Onboarding, XYZ-Macro, Index-only, Balanced DSL, hard_timeout 48h |
| **Sailfish** | v1.0 | BTC · ETH · SOL · HYPE | Relative-Strength Rotator. Ranks the universe by ~2.7d RS each tick and longs the leader iff (a) leader's own RS ≥ 1% AND (b) it beats the runner-up by ≥ 1.5pp (no whipsaw on tight races). Runtime is single-position; "rotation" happens via DSL exit on the holdover + Sailfish's next-tick re-entry on the new leader. Momentum cousin of Chameleon's mean-reversion. Balanced DSL + 96h hard_timeout. | Momentum-rotation, RS-leader, Margin-gate, Balanced DSL |
| **Stag** | v1.0 | BTC · ETH · SOL · HYPE (often deployed single-asset) | **Parabolic-Run Hunter.** Entry-side pair for the new `parabolic_runner` DSL preset (widest in the catalog: max_loss 25%, retrace 18, 2 consecutive breaches required, late first lock +15%, 14d outer bound). Strict 5-gate filter: (1) close > 200-bar 4h SMA AND 7d high within 48h, (2) 7d move ≥ 25% (the parabolic threshold), (3) 24h volume ≥ 1.5× trailing 7d, (4) 4d move ≥ 7d move / 2 (acceleration), (5) SM aligned LONG ≥ 60%. LONG only — parabolic crashes happen too fast for shorts. **Operator-driven** deployment: most ticks return empty by design. Reference setup: HYPE 2026-05 (+60% in 16 days). 1 entry/day max + 24h per-asset cooldown after bad takes. Tick 600s. | Parabolic, 5-gate, LONG-only, Operator-driven, parabolic_runner DSL |
| **Spider** | v5.1.1 | AI/Tech XYZ equities + crypto alts (swing) · BTC · ETH · SOL · HYPE + xyz:BRENTOIL/CL (scalp) | **AI/Tech hedge fund — two style legs on two wallets, one leg-parameterized producer (`SPIDER_LEG`).** SWING: LONG-only AI/Tech multi-day momentum on a dynamic XYZ-equity universe (auto-catches fresh IPO/pre-IPO listings) + crypto alts; conviction 10x; wide let-winners-run DSL; low turnover. SCALP: both-directions macro/majors + energy mean-reversion counter-trading book; strict 5x; tight fast-capture DSL; high turnover. Each leg scores its own universe and pushes signals; the runtime owns the LLM gate, DSL exits, and risk. **Not a copy-trader** (v5.0 replaced the old single-leg anchor sniper). | Two-leg, Hedge-fund, AI/Tech-long, Macro-scalp, Both-direction, Dynamic-universe |

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
| **Albatross** | v1.0 | Arena leaders (multi-week composite) | **Onboarding tier.** Mirrors Senpi Arena leaders selected by composite conviction score: `0.3 × monthly_roe + 0.7 × mean(weekly_roe) − 0.5 × stdev(weekly_roe)`. Rewards multi-week persistence, penalizes lucky-week luck. Pool refreshes every 4h. **Requires user-scope auth token** (calls `strategy_list` + `discovery_get_trader_state` for other users). | Onboarding, Arena, Multi-week, Conviction-weighted, User-scope-auth-required |
| **Remora** | v1.0 | Operator-picked whale set | **Hand-picked mirror.** You name the whales; Remora takes each whale's highest-conviction (largest-notional) position and mirrors the strongest, with a **consensus** multiplier (2 whales +2, 3+ +3) and an ELITE/RELIABLE-tier bonus. Contrast to the universe scanners above — exposure tracks traders YOU choose. Wide let-winners-run DSL + 120h staleness cap (whale-exit mirror is a future enhancement). Tick 600s. | Whale-mirror, Consensus, Operator-picked, Wide DSL |

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
| **Meerkat** | v1.0 | Multi (momentum-event feed) | **Event-feed variant.** Reads `leaderboard_get_momentum_events` directly (not the `_markets` rank table) and snipes the freshest, highest-tier events: tier (3 ≥10% · 2 ≥5%) + freshness (≤30min) gate; SM + volume are bonuses. Wide let-winners-run DSL + SHORT 36h hard_timeout (momentum is time-bounded). Tick 120s. | Momentum-event, Tier-sniper, Freshness-gate, Wide DSL, Tick 120s |

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
| **Vulture** | v4.1 | 25 small/mid-cap Hyperliquid perps (BTC/ETH/SOL/XYZ banned) | **Long-tail momentum rider** (full rewrite at v2.0 from the original HYPE-funding-contrarian — this row's archetype bucket is left for compatibility; Vulture's thesis is closer to a small-cap whitelist with momentum scoring). Score is composed of HEAVY_FLOW (SM concentration), trend persistence, multi-TF alignment, and 15m velocity. v4.1.0 raised MIN_SCORE 7→9 after a 30-trade analysis showed score 7–8 ran 12.5%/28.6% win rates at -3.94%/-3.52% avg ROE; conviction-scaled leverage 5x (score 9-10) / 7x (score 11+); cautious tier removed. | Small-cap, Long-tail, Conviction-scaled, MIN_SCORE 9 |
| **Camel** | v1.0 | Liquid crypto cross-section (harvest + payout books) | **Carry Hedge Fund.** Two equally-funded single-direction books, one leg-parameterized producer (`CAMEL_LEG`): the **harvest** book shorts the most-positive-funding names (short collects), the **payout** book longs the most-negative-funding names (paid to hold) — each gated to an *exhausting* crowd (4h trend against the carry disqualifies; RSI/own-momentum confirm). The edge is funding **carry**, not direction; some-short + some-long also skews slightly net-neutral. Funding ranked from the instrument board `context.funding` in one call (NOT the ClickHouse `funding_history` endpoint). Strict 5x, tighter carry DSL (10% phase1, stall-cuts ON, 3d timeout). | Carry, Funding-collect, Two-wallet, Harvest+payout, Exhaustion-gated |

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
| **Osprey** | v1.0 | BTC → xyz: equity proxies | **Cross-VENUE variant.** When BTC moves, crypto-correlated XYZ equities (COIN, MSTR, miners) lag on the other venue. Osprey self-computes the catch-up gap (`leader move × beta − proxy move`) from candles — it does NOT use `cross_asset_flows` (which only surfaces crypto laggards) — and trades the proxy in the leader's direction while it still owes the gap. Wide let-winners-run DSL, 96h hard_timeout. Tick 300s. | Cross-venue, XYZ-equity, Beta-gap, Wide DSL, Self-computed |

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
| **Marlin** | v1.0 | BTC/ETH/SOL/HYPE | Order-book-imbalance momentum — bid/ask resting-depth skew as the entry-TIMING edge on a momentum thesis (NOT a scalper); holds with a wide DSL + 24h hard_timeout. | Order book, Imbalance, Momentum |

---

### 13. Relative-value / pairs

The first archetype to trade **relative value** instead of a single asset's direction. It measures how stretched a price *ratio* between two correlated assets is (z-score over a lookback) and bets on reversion — ratios mean-revert far more reliably than outright prices.

```python
# Per pair (numerator/denominator, plus the high-beta leg actually traded):
ca = closes("ETH"); cb = closes("BTC")                 # 1h candles for BOTH legs
z, ratio, mean, std = ratio_zscore(ca, cb, lookback=48)  # z of latest ratio vs its window
# z high (numerator rich)  → ratio reverts down → SHORT leg if leg==numerator, LONG if leg==denominator
# z low  (numerator cheap) → mirror.  Enter when |z| >= ~2 AND reversion is starting (|z| shrinking vs last bar).
```

**Single-position note:** a textbook pairs trade is two legs (long A / short B) for market-neutrality. The Senpi runtime is single-position, so this archetype takes the **directional high-beta leg** in the reversion direction (Chameleon) — OR achieves neutrality at the **fund level** with two equally-funded single-direction books, a long book + a short book, whose notionals offset (Octopus). The latter is cross-sectional **dispersion** (long the leaders, short the laggards of one peer group) rather than a single ratio.

**When to use this pattern:** you believe two assets are tethered (BTC/ETH/SOL) and want to trade the *spread* reverting rather than guess absolute direction. Mean-reversion play → tight "bank the snapback" DSL + time-cuts ON (a reversion resolves fast or the thesis failed).

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Chameleon** | v1.0 | ETH/BTC, SOL/ETH, SOL/BTC | Ratio mean-reversion — trades the high-beta leg when a pair's ratio z-score extends past ~2σ and starts reverting. Mean-reversion DSL. | Ratio z-score, Pairs, Mean-reversion |
| **Octopus** | v1.0 | Liquid crypto cross-section (long + short books) | **Market-Neutral Hedge Fund.** Two equally-funded single-direction books, one leg-parameterized producer (`OCTOPUS_LEG`): longs the relative leaders (top relative-strength, trend-confirmed) and shorts the relative laggards (bottom RS, trend-confirmed) of the liquid main-DEX cross-section. Net ~beta-neutral at the FUND level, harvesting cross-sectional DISPERSION rather than a single ratio. RS rank computed once from the instrument board (no per-asset fetch); only top/bottom names get candles. Strict 5x, moderate DSL with stall-cuts ON. | Cross-sectional, Dispersion, Market-neutral, Long+short books, Two-wallet |

---

### 14. Meta-strategy follower / copy-the-copiers

Follows not individual traders but the **top-performing strategies** on the platform — which are themselves copy/algo/trader-following strategies — and trades their **performance-weighted consensus**. The layer above trader-following: it captures the *consensus of the consensus*, and the pool self-cleans (underperformers fall out of the top-N automatically).

```python
strats = top_strategies(limit=12)                      # discovery_get_top_strategies, by realized PnL/ROI
entries = []
for s in strats:                                       # each strategy's live positions
    w = performance_weight(s["roi"])                   # clamp(1 + roi/50, 0.5, cap) — stronger strategy, more say
    for pos in trader_positions(s["wallet"]):          # leaderboard_get_trader_positions (nested data.positions.positions)
        entries.append({"asset": asset(pos), "direction": dir(pos), "weight": w})
consensus = tally_consensus(entries)                   # (asset,dir) -> {count, summed weight}
# fire the highest weighted-consensus candidate that >= minStrategies agree on
```

| | |
|---|---|
| Producer-signature for fleet audit | `discovery_get_top_strategies` every tick (+ `leaderboard_get_trader_positions` per strategy) |
| Typical tick interval | 600s (top-strategy consensus drifts slowly) |
| Typical risk envelope | consensus-gated (≥2 top strategies agree), conviction-tier leverage, wide DSL + staleness cap |
| Example agent | **Cuckoo** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cuckoo/scripts/cuckoo-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cuckoo/scripts/cuckoo_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/cuckoo/runtime.yaml |

**When to use this pattern:** you trust the platform's *aggregate* of best strategies more than any one trader or your own read, and want exposure that auto-rotates toward whatever is currently working. **Requires user-scope auth** (`discovery_get_top_strategies` + `leaderboard_get_trader_positions` for other accounts).

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Cuckoo** | v1.0 | Top-strategy consensus | Auto-discovers the top-N strategies by performance, builds a performance-weighted consensus across their positions, and trades what ≥2 of them agree on most. Wide let-winners-run DSL + 96h staleness cap. Tick 600s. | Meta-follower, Copy-the-copiers, Consensus, Performance-weighted, User-scope-auth-required |

---

### 15. Self-tuning / adaptive-threshold agent

The first archetype where the agent **modifies its own behavior based on its own trade history**. The agent runs a normal scoring producer with a configured `MIN_SCORE`, but on a scheduled cron it pulls its own closed-trade telemetry (via `audit_query`), buckets the trades by entry score, and auto-raises `MIN_SCORE` if any bucket at-or-above the current floor has accumulated enough samples AND is averaging below the bleed threshold.

This is the Vulture v4.1 manual cull turned into a first-class agent pattern. See [the Vulture v4.1 PR](https://github.com/Senpi-ai/senpi-skills/pull/337) for the source story.

```python
# Each tick, if audit interval has elapsed:
trades = mcp_call("audit_query", user_ids=[senpi_user_id], action_type="close", limit=200)
bucket_stats = compute_bucket_stats(trades)            # {score: {n, avg_roe_pct, win_rate_pct}}
recommended = recommend_min_score(bucket_stats, current_min, min_n=8, bleed_pct=-1.0, max_min=7)
if should_update_threshold(current_min, recommended):
    state["current_min_score"] = recommended
    state["adjustments"].append({...})                # full audit-trail log
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_asset_data` per asset (scoring) + `audit_query` per audit interval (self-tuning) |
| Typical tick interval | 300s (5min) for scoring; audit every 6h |
| Typical risk envelope | small whitelist of crypto majors, conviction-tier leverage, MIN_SCORE ratchets upward only (never lowers) |
| Example agent | **Lynx** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lynx/scripts/lynx-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lynx/scripts/lynx_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/lynx/runtime.yaml |

**When to use this pattern:** the agent has a known scoring system AND the data exists (via `audit_query`) to measure which score buckets pay. Caveat: works best when there's enough volume to fill score buckets within an actionable window — for slow / low-frequency strategies, the auto-tune is starved for data. Requires user-scope auth.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Lynx** | v1.0 | BTC · ETH · SOL · HYPE | Multi-asset momentum scorer (max ~8) with a periodic self-tuning audit. Every 6h pulls own closed trades, buckets by entry score, raises `MIN_SCORE` if a bucket at-or-above the floor has ≥8 trades averaging worse than -1% ROE. Caps at `maxMinScore: 7`. Logs every adjustment with bleeding-bucket evidence. | Self-tuning, Adaptive-MIN_SCORE, Audit-cron, RL-on-threshold, User-scope-auth-required |

---

### 16. Regime classifier / meta-router

The first archetype built around **macro-regime classification as a first-class signal**. The agent computes BTC trend strength + realized volatility + cross-asset dispersion, classifies the market into `{TREND_UP / TREND_DOWN / CHOP / UNKNOWN}`, and publishes the classification in every tick output — including ticks where no trade is taken.

For v1, the classifier also takes its own regime-positional trade so it has skin in its own call. The "meta-router" framing is aspirational: future runtime work can let other agents *subscribe* to the regime channel as a gating input (e.g. Tortoise auto-pauses in TREND_DOWN; Stag auto-enables in TREND_UP).

```python
btc_move = pct_move(btc_closes, lookback=42)            # 7d on 4h bars
btc_vol  = realized_vol_pct(btc_closes, lookback=42)    # annualized
dispersion = dispersion_pct({asset: pct_move(...) for asset in universe})  # cross-sectional

regime = classify_regime(btc_move, btc_vol, ...thresholds)   # TREND_UP / TREND_DOWN / CHOP / UNKNOWN
direction = regime_to_direction(regime)                       # LONG / SHORT / None
```

| | |
|---|---|
| Producer-signature for fleet audit | `market_get_asset_data` per universe asset (just for close prices) |
| Typical tick interval | 900s (15min — regimes don't flip in 5 minutes) |
| Typical risk envelope | 1 slot, conservative leverage, 6h cooldown between regime trades |
| Example agent | **Coyote** |
| Example producer (full source) | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/coyote/scripts/coyote-producer.py |
| Example `_config.py` | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/coyote/scripts/coyote_config.py |
| Example runtime.yaml | https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/coyote/runtime.yaml |

**When to use this pattern:** you want a single agent making a single macro call ("is the market trending up / down / sideways?") and everything else flowing from there. The classification is the deliverable; the trade is a consequence.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Coyote** | v1.0 | BTC (positional) + BTC/ETH/SOL/HYPE (dispersion universe) | 3-regime classifier (TREND_UP / TREND_DOWN / CHOP) with explicit volatility-confirmation on the down side (crash = price drop + vol spike, not slow grind). LONG BTC in TREND_UP, SHORT BTC in TREND_DOWN, no trade in CHOP. Regime classification + all three input metrics published on every tick. Balanced DSL. Tick 900s. | Regime-classifier, Meta-router, Macro, Vol-aware, Published-on-every-tick, User-scope-auth-required |

---

### 17. Volatility / breakout-expansion

Trades **movement, not direction**. The thesis is volatility regime: compression precedes expansion, and a breakout *from a low-volatility coil* follows through more than a breakout in already-volatile tape. Fires on the conjunction of (range breakout) + (ATR squeeze) + (expansion surge), and takes the break direction either way.

```python
# Per name (1h candles): is it coiled, did it break, was the break a surge?
prior_high = max(highs[-21:-1]); prior_low = min(lows[-21:-1])
broke = "LONG" if price > prior_high else ("SHORT" if price < prior_low else None)
squeeze = atr(c[-11:], 10) / atr(c[-31:], 30)          # < 0.7-0.9 = coiled spring
surge   = true_range(c[-1]) / atr(c[-31:], 30)          # >= 1.3-2.0x = real expansion
# score = breakout(+3) + coil(+2/+1/-1) + surge(+2/+1) + 4h-agreement(+1/-1)
```

**Distinct from Hawk/Badger (4h breakouts) and Stag (parabolic):** those fire on *any* breakout; this archetype *requires a prior compression* (the coil) plus an expansion surge — a coiled spring, not a chase — and is direction-agnostic (both legs trade long and short). Tight, early-locking DSL: a failed breakout reverses fast.

**When to use this pattern:** you want to harvest volatility expansion as a low-correlation return stream (convexity / managed-futures flavor) independent of market direction. Episodic by design — most ticks return empty.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Caracal** | v1.0 | Crypto (breakout book) + XYZ (catalyst book) | **Volatility Hedge Fund.** Two books on two wallets, one leg-parameterized producer (`CARACAL_LEG`): **breakout** rides coiled-spring breakouts in liquid main-DEX crypto; **catalyst** runs the same compression→expansion engine on XYZ (equities/energy/metals/indices), turning oil-geopolitics + AI-infra moves into direction-agnostic vol events, 24/7. Signal = range break + ATR squeeze (≤0.7-0.9) + expansion surge (≥1.3-2.0×) + 4h agreement; LONG or SHORT per the break. Strict 5x, tight early-locking DSL (12% phase1, lock at +8%→30%, 2d timeout). | Volatility, Compression-expansion, Both-direction, Two-universe, Episodic, Two-wallet |

---

### 18. Global macro / cross-asset

Trades the **macro asset complex** — equity indices, precious metals, energy, FX (all on XYZ) plus BTC as the macro risk asset — which moves on macro *regime* rather than crypto noise. A curated whitelist, intersected with the live board so unavailable names are skipped. Distinct from the crypto-native funds: the only archetype focused on the cross-asset macro classes, deliberately excluding single AI/Tech stocks (Spider's domain).

```python
# trend book: the 4h structure IS the macro trend; confirm + ride.
trend4 = trend_structure(c4)            # BULLISH->LONG, BEARISH->SHORT, NEUTRAL->skip
score  = 3 + confirm(1h) + momentum(24h) + rsi_room
# fade book: fade short-TF over-extension with a 4h regime knife guard.
side   = "LONG" if oversold(rsi,stretch) else "SHORT"
score  = rsi_extreme + stretch - knife_guard(against a strong macro trend)
```

**When to use this pattern:** you want a low-correlation, regime-driven return stream over indices / metals / energy / FX — the global-macro / managed-futures lane — rather than a single-asset or crypto-only book. Two complementary sub-books (trend rides, fade reverts) keep it engaged across regimes.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Elephant** | v1.0 | XYZ indices/metals/energy/FX + BTC (trend + fade books) | **Global-Macro Hedge Fund.** Two books on two wallets, one leg-parameterized producer (`ELEPHANT_LEG`): **trend** rides the medium-term multi-TF macro trend (4h backbone + 1h + 24h momentum), **fade** fades short-TF macro over-extensions back to regime (1h RSI/stretch with a 4h knife guard). Both directions. Curated macro whitelist intersected with the live board. Theme-aware (oil/Iran energy trend, AI-equity index bid) but trades them AS macro, not chases. Trend = wide let-it-run DSL (18% phase1, time-cuts OFF, 7d timeout); fade = tight fast-capture (8% phase1, stall-cuts ON, 2d timeout). Strict 5x; 24/7 (XYZ). | Global-macro, Cross-asset, Trend+fade, Both-direction, Two-wallet, 24/7 |

---

### 19. Thesis fund — preset-driven view expression

A **view-based** pattern, distinct from the edge-based archetypes above: the user picks *what they believe will happen* and the fund expresses it. **One engine** reads a `THESIS` env var that selects a preset (a fixed long/short basket) from a JSON file; the per-asset direction is set by the preset. Use-cases ship as **catalog variants** (`base_skill` + a `thesis` field), so a new bet is a JSON edit, not new code. Single wallet (one coherent bet), both directions.

```python
preset = presets[THESIS]                      # {long:[...], short:[...]}
for asset, target_dir in basket(preset):      # direction FIXED by the preset
    # only PRESS when the market confirms the thesis direction:
    if target_dir == "LONG"  and trend4 == "BEARISH": skip   # don't fight the tape
    if target_dir == "SHORT" and trend4 == "BULLISH": skip
    score = trend_confirm + momentum_confirm + rsi_room       # minScore = the confirmation bar
```

**The key discipline:** the preset fixes the *direction*, but the engine only *enters* when trend + momentum confirm it — so a "short SP500" thesis only shorts SP500 once it's actually rolling over. That + a hard drawdown halt is what keeps a directional macro bet from bleeding on a wrong-but-stubborn view.

**When to use this pattern:** you want to let a user express a macro conviction (risk-on/off, geopolitics, a relative-value call like HYPE-vs-market or gold-vs-BTC) as a one-tap, risk-managed vehicle — rather than offer them a trading *method*. Theses have a shelf life; event-driven presets get retired/updated as the situation resolves.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Thesis Fund** | v1.0 | Per-preset long/short basket (crypto + XYZ macro) | **One engine, many one-tap macro bets.** `THESIS` selects a preset basket; ships as catalog variants — `risk_off` (anti-Trump-economy), `recovery`, `war_escalation`/`war_recovery` (Iran/US/Israel), `hype_vs_market`, `gold_over_btc`/`btc_over_gold`. Holds the basket in ONE wallet, presses each name only when the market confirms the thesis direction (trend+momentum), de-risks via DSL + a 20% drawdown halt. Strict 5x, balanced let-it-work DSL with stall-cuts ON. Add a bet by editing `thesis-presets.json`. | Thesis, Preset-driven, View-based, Variants, Confirmation-gated, Single-wallet |

---

### 20. Event-driven / regime-rotation (shared-brain hedge fund)

A **two-book hedge fund with a shared regime brain.** Before either book scores anything, the producer computes a single market-wide **regime** from cross-asset confirmation, and a book only fires when the regime agrees with its mandate — so capital *rotates* to whichever book the regime favors. The edge is the macro **transition itself**, detected across the whole complex, not a per-asset trend (§1/§18) and not a fixed bet (§19).

```python
# computed ONCE per tick, BEFORE either book scores (no single asset flips it):
on = off = 0
for probe in [equities, oil, gold, btc, dollar]:         # 4h trend of each
    t = trend4(probe.asset)
    if t == probe.risk_on_when:  on  += 1                 # e.g. equities BULLISH, oil BEARISH
    elif t == probe.risk_off_when: off += 1
net = on - off
regime = "RISK_ON" if net >= threshold else "RISK_OFF" if net <= -threshold else "NEUTRAL"
if regime != MY_REGIME: emit_standing_down(); return       # the rotation gate
# only now score the book's universe in the regime-mandated direction
```

**The key discipline:** the *threshold* (net votes, default 2) means no single asset can flip the book — the whole macro complex has to lean one way. A regime flip is handled on the **entry** side (the losing-regime book stops *adding*); open winners still trail out via the DSL ladder, so the fund doesn't dump a book just because the tape turned.

**When to use this pattern:** the market is in a headline-driven, regime-whipsaw environment (war-on/war-off, risk-on/off on macro catalysts) and you want a vehicle that *adapts* — taking the prevailing side and flipping as conditions change — rather than a fixed directional bet.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Wolf** | v1.0 | Risk complex (crypto majors + growth indices) ↔ defensives (gold/oil/$/JPY); regime probes = equities/oil/gold/BTC/$ | **Event-Driven / Regime-Rotation Hedge Fund.** Two books, two wallets, one producer + a shared cross-asset regime detector. `risk_on` book longs beaten-down beta ONLY in a confirmed RISK_ON regime (wide let-it-run DSL); `risk_off` book longs defensives + shorts risk ONLY in RISK_OFF (tighter DSL, risk-off moves reverse fast). Stands down in NEUTRAL. `regimeThreshold` = net cross-asset votes to declare a regime. 50/50 funding (rotation — one book usually active). | Hedge-fund, Event-driven, Regime-rotation, Shared-brain, Adaptive, Two-wallet |

---

### 21. Tail-risk / crisis-alpha (shared-brain hedge fund)

A **two-book hedge fund built for convexity** — designed to bleed a little in calm and pay big in shocks. The shared brain here is a **stress detector** (cross-asset breakdown/breakout + vol-expansion). One book is always-on insurance; the other is dormant dry powder that the stress gate wakes.

```python
stress = sum(probe_fires(p) for p in [oil_up, equities_down, gold_up, btc_down]) \
         + (1 if btc_atr_recent/btc_atr_base >= vol_surge else 0)
stressed = stress >= stressThreshold
# HEDGE book: always-on, LONG defensives that are trending up (no falling-knife hedges), small size
# ESCALATION book: if not stressed -> emit_dormant(); return
#                  else LONG spiking crisis assets + SHORT cratering risk, larger size
```

**The key discipline:** the hedge book is **sized small** (`margin_pct` ~10%) so calm-time bleed is bounded by *position size*, not a tight stop — and a crisis winner runs on a wide DSL. The escalation book sits in **cash as dry powder** (that idle capital *is* the tail hedge) and only deploys under a confirmed stress regime, with a moderate-tight DSL that banks the spike (crises reverse violently — a ceasefire dumps oil/gold).

**When to use this pattern:** you want the portfolio *hedge* of a fund line-up — the thing that's green on the days everything else is red — without requiring the user to hold a view. Best launched in calm (insurance is cheap when nobody wants it).

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Rhino** | v1.0 | Crisis longs (gold/silver/oil/CL/natgas/$/JPY) + risk shorts (crypto majors + growth indices); stress probes = oil/equities/gold/BTC + BTC-vol | **Tail-Risk / Crisis-Alpha Hedge Fund.** Two books, two wallets, one producer + a shared stress detector. `hedge` book: always-on small LONG carry in defensives that are trending up (cheap standing insurance, wide 10d-timeout DSL). `escalation` book: dormant until STRESS confirms, then LONG spiking crisis assets + SHORT cratering risk (larger size, moderate-tight DSL that banks the spike). `stressThreshold` = cross-asset stress probes that must fire. 50/50 funding (hedge runs small; escalation holds dry powder). | Hedge-fund, Tail-risk, Crisis-alpha, Convexity, Shared-brain, Stress-gated, Two-wallet |

---

### 22. Risk parity / all-weather (inverse-volatility hedge fund)

A **two-book hedge fund whose distinctive mechanic is sizing, not signal.** Every other archetype sizes positions at a flat `margin_pct`; this one sizes each sleeve by **inverse realized volatility**, so a low-vol sleeve carries more notional than a high-vol one and no single asset class dominates portfolio risk — true risk parity. It is a *core holding*: always invested, low leverage, low turnover.

```python
# size vol for the WHOLE basket (held + un-held), then weight:
vols = {a: realized_vol(closes_1h[a], n) for a in basket}     # stdev of pct returns
w    = {a: (1/vols[a]) / sum(1/v for v in vols.values()) for a in basket}   # inverse-vol weights
for a in unheld(basket):
    margin_usd = min(budget_pct * equity * w[a], maxWeightPct * equity)     # per-sleeve risk-parity weight
    emit(a, "LONG", margin_usd, leverage=3)                                 # low leverage; LONG only
```

**The key discipline:** weights are computed over the **full basket**, not the un-held subset — otherwise a single re-entry would get weight≈1.0 and be sized to the entire budget. The per-sleeve `marginUsd` IS the product, so the runtime must **honor per-signal `marginUsd`** rather than collapse to a flat `margin_pct` (same code path as the cross-margin sizing fix). Knife guard only governs *adding* a sleeve (won't buy a hard downtrend); the wide DSL holds existing sleeves through normal drawdowns.

**When to use this pattern:** the user wants a diversified, lower-drawdown **core** they hold while betting with the other funds — not a directional view (Wolf), crisis convexity (Rhino), or per-asset trend (Elephant). Best in any regime; especially valued in a whippy, dispersed tape where balance beats concentration.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Ox** | v1.0 | Core sleeves (BTC/ETH/SOL + xyz:SP500/XYZ100/GOLD/COPPER/BRENTOIL/DXY/JPY); ballast = defensives (gold/silver/$/JPY) | **Risk-Parity / All-Weather Hedge Fund.** Two books, two wallets, one producer. `core` book: always-invested vol-balanced LONG basket, inverse-vol sized to a 60% budget, 3x, wide 14d-timeout DSL. `ballast` book: always-on LONG defensives, inverse-vol sized to an 18% base budget that scales ×2 when a light risk-off lean confirms. Low leverage, low turnover (600s tick); per-sleeve `marginUsd` is the risk-parity weight. 70/30 funding. | Hedge-fund, Risk-parity, All-weather, Inverse-vol-sizing, Core-holding, Two-wallet |

---

### 23. U.S. equity long/short (tokenized-equity dispersion hedge fund)

The cross-sectional **dispersion** method (§13 / Octopus) applied to a *different universe*: the tokenized U.S. equity market on Hyperliquid XYZ (trade.xyz: NVDA, TSLA, AAPL, AMZN, … + index products), now the venue's fastest-growing market (HIP-3 stock markets did >$18B in the first half of June 2026; 23 of the top-30 HL assets by OI are equities + commodities). Long the relative-strength leaders, short the laggards, ~beta-neutral.

```python
universe = curated_equity_whitelist ∩ live_board ∩ {dayNtlVlm >= floor}    # not a whole-board scan
mean_rs  = mean(ret_24h(x) for x in universe)
for x in (top if LEG=="long" else bottom)(rank_by(ret_24h - mean_rs)):
    if score(x, excess=ret_24h(x)-mean_rs) >= minScore: emit(x, LEG)        # trend-confirmed, RS-driven
```

**The key discipline:** rank a **coherent peer group** (US equities), not a mix of stocks/commodities/FX — so relative strength means something. Long-leaders + short-laggards on equally funded wallets nets ~beta-neutral; the P&L is the dispersion *spread*. Trend confirmation prevents longing a downtrend / shorting an uptrend; blow-off / capitulation RSI guards prevent chasing extremes.

**When to use this pattern:** equity dispersion is wide (winners and losers far apart) and the tokenized-equity universe is deep + liquid enough to rank — the classic equity hedge-fund play, now viable on-chain.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Cougar** | v1.0 | Curated tokenized-US-equity whitelist (xyz: NVDA/TSLA/AAPL/META/MSFT/GOOGL/AMZN/AMD/MU/INTC/TSM/ORCL/NFLX/AVGO/CRM/COIN/MSTR/PLTR/SMCI/UBER/SHOP/SPCX) | **U.S. Equity Long/Short Hedge Fund.** Two books, two wallets, one producer. `long` book longs the RS leaders, `short` book shorts the RS laggards, trend-confirmed, ~beta-neutral. Octopus's dispersion scorer on the equity universe (lower liquidity floor, longer 7d DSL timeout — equities trend longer than crypto). 50/50 funding. New trade.xyz listings auto-join the whitelist. | Hedge-fund, Equity-long-short, Dispersion, Market-neutral, XYZ-equities, Two-wallet |

---

### 24. IPO / new-listing event (pre-IPO → graduation hedge fund)

An **event-driven** fund on the tokenized-equity listing arc. trade.xyz pre-IPO perpetuals (IPOPs) carry a structural funding signature (`|funding| ≤ ~1e-7`, `max_leverage ≤ 5`); when the company IPOs the product **converts** to a standard equity perp (funding jumps ~100×, the leverage cap lifts, the price throttle comes off → free price discovery). Two books trade the two phases — the SpaceX $1.4B-day-1 pattern.

```python
# pre_listing book — Lemur's IPOP discovery:
for x in instruments(dex="xyz") if is_ipop(x):    # funding+leverage+volume signature
    emit(x, trend_direction(x))                    # ride the pre-listing ramp, SM-confirmed
# graduation book — Falcon's conversion detector (class-state cache):
for x in instruments(dex="xyz"):
    if prev_class[x]=="IPOP" and curr_class[x]=="STANDARD": stamp_conversion(x)   # the flip
for x in conversions_in_window(hours):             # stays eligible for days, not just the flip tick
    if momentum(x) >= min: emit(x, momentum_direction(x))                          # ride price discovery
```

**The key discipline:** the graduation book persists a **class-state cache** and only fires a conversion against a *known prior* class (the first tick seeds, doesn't fire), and stamps each flip into a multi-day **eligibility window** so momentum that develops over hours/days is still tradeable. SM is a *bonus, not a gate*, on fresh names (data is sparse). Detection reuses Lemur (`fetch_ipop_universe`) + Falcon (`classify_instrument` / `detect_conversion`) verbatim.

**When to use this pattern:** new equity listings are flowing onto the venue (IPOPs converting, fresh tickers) and you want the event alpha — distinct from trading the ongoing equity market (Cougar).

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Magpie** | v1.0 | xyz: IPOPs (pre_listing) + freshly-converted equities (graduation); today ~SPCX, auto-expands | **IPO / New-Listing Event Hedge Fund.** Two books, two wallets, one producer. `pre_listing` book: auto-discovers IPOPs by funding signature (Lemur method), rides the pre-listing trend, 3x / 12% / moderate-wide DSL. `graduation` book: detects the IPOP→STANDARD conversion flip via a class-state cache (Falcon method) + a 72h window, rides post-conversion momentum, 5x / 15% / wide let-winners-run DSL. Episodic by design (most ticks empty). 50/50 funding. Requires user-scope auth for SM. | Hedge-fund, Event-driven, IPO, IPOP, Conversion-detection, Class-state-cache, Two-wallet |

---

### 25. Two-speed-market / K-shaped (cross-asset thematic long/short hedge fund)

A **thematic** long/short fund that bets on a *K-shaped divergence* — a structural winner cohort keeps booming while the rest struggles — and spans **both tokenized U.S. equities (XYZ) and main-DEX crypto on one cross-margined wallet**. Unlike the cross-sectional dispersion fund (§23 / Cougar), which ranks a single coherent peer group, the universe here is a **curated thematic whitelist per leg**: the "haves" (long) vs the "have-nots" (short). Membership *is* the thesis; **absolute trend is the gate**, relative strength only a tiebreaker — so a conviction winner is held while it trends even when peers run harder, and a laggard is shorted only while it actually rolls over.

```python
# LONG "haves" universe = AI complex (xyz) + crypto winners (HYPE/SOL, main)
# SHORT "have-nots" universe = broad market (xyz:SP500) + laggard alts (main)
universe = curated_theme_whitelist[LEG] ∩ live_board ∩ {dayNtlVlm >= floor}
mean_rs  = mean(ret_24h(x) for x in universe)
for x in universe:
    if absolute_trend(x) is wrong_way: continue          # HARD GATE (never long a downtrend / short an uptrend)
    s = score(x, excess=ret_24h(x)-mean_rs)              # RS is a tiebreaker, not a gate
    if s >= minScore:
        margin = account_value * marginPct * sizingWeights[bare(x)]   # conviction, not dollars (HYPE 1.5, SOL 0.6, SP500 1.2, alts 0.7)
        if affordable(margin): emit(x, LEG)              # per-candidate free-margin cap
```

**The key discipline:** the universe is the thesis, but the *gate is absolute price action* — a thesis name is only traded while the tape agrees, never on conviction alone. Conviction is expressed purely through **per-group sizing weights** (a multiplier on a budget-scaled slot), never a hardcoded dollar amount. **Net exposure is an explicit operator dial** — the two books run on separate wallets, so the funding split + per-leg knobs (slots / marginPct / sizingWeights) set the posture; the default is a modest net-long tilt. The short book runs **tighter than the long book** (lower leverage, tighter max-loss, faster stall-cuts, smaller alt weights, BTC omitted) because short squeezes are violent. **Long-AI + short-SP500 overlap is intentional** — the index contains the AI names, so the pair isolates the pure AI-vs-broad-market spread.

**When to use this pattern:** you have a *directional thematic view* (one cohort booms, another suffers) that spans asset classes, and you want to express it as a hedged long/short — capturing the dispersion between the two speeds rather than betting on overall market direction.

**Agents in this family:**

| Agent | Version | Asset / Universe | Description | Tags |
|---|---|---|---|---|
| **Lion** | v1.0 | LONG "haves" (full live-board AI complex, 27 names): chips NVDA/AMD/MRVL/ARM/AVGO/INTC/TSM/ASML/CBRS, memory MU/SMSN/SKHX/SNDK, infra CRWV/NBIS/DELL/LITE, software GOOGL/MSFT/META/AMZN/ORCL/PLTR/NOW/IBM, frontier SPCX/QNT, + crypto winners HYPE/SOL. SHORT "have-nots": broad market (xyz:SP500) + laggard alts (ETH/XRP/DOGE/AVAX/LINK/ADA/LTC/NEAR/APT) | **Two-Speed-Market (K-Shaped) Cross-Asset Long/Short Hedge Fund.** Two thematic books, two wallets, one producer. `long` longs the structural winners (AI complex + HYPE/SOL); `short` shorts the laggards (SP500 + laggard alts). Cross-asset on one cross-margined wallet; absolute-trend gate, RS tiebreaker, per-group conviction sizing (HYPE 1.5×, SOL 0.6×, SP500 1.2×, alts 0.7×, speculative frontier SPCX 0.6×/QNT 0.5×/CBRS+NBIS 0.7×). Net exposure an operator dial (default modest net-long). Short book runs tighter (4x vs 5x); BTC omitted from the short basket. AI universe resolved against the live trade.xyz board. | Hedge-fund, Two-speed-market, K-shaped, Thematic-long-short, Cross-asset, Conviction-sizing, AI, HYPE, Two-wallet |

---

## Decision tree — help a user pick their first strategy

This is the guided path an **onboarding agent** walks a new user through. Start broad ("what kind of trader do you want your agent to be?"), narrow **one layer at a time**, and land on a single deployable strategy. Ask one question, show 2–6 options, let them pick, then go deeper. Each leaf names a **real, installable agent** — beginners are routed to the **onboarding tier** (simpler scoring, conservative sizing); the *level up* line is the full-fleet version for once they're comfortable.

> **Conversational rule for the agent:** never dump the whole tree. Surface Layer 1, let the user choose, then reveal only that branch's Layer 2. Explain each option in one plain sentence ("trend-following = when something's moving, ride it and hold"). Always end at exactly one recommended strategy + its risk level + DSL preset, then offer to deploy it. **If the user can't answer Layer 1 — they don't know the words yet — drop to Layer 0 and let the agent suggest.**

### Layer 0 — When the user doesn't know how to answer

Most first-time users can't say "I want a trend-follower" — they don't have the vocabulary. **The agent must recommend without the user self-classifying.** Four paths below.

> ⚡ **Ask before you scan — keep the first moment fast.** Paths **A / B / C are instant** (no data calls). Path **D** *and* the **top-performer path** (below) read live data (several MCP calls, a few seconds). So **lead with the question, and only run the data paths when the user explicitly opts in** — e.g. they pick *"help me choose,"* *"suggest something for me,"* or *"show me what's winning."* Don't pre-fetch on entry; fetch on demand, and say *"give me a sec to read the market…"* so the wait is expected, not a freeze.

**A. Express lane — "just pick something simple for me."** The user wants the agent to decide. Recommend the conservative default and go straight to deploy:
- **Default first strategy → Hedgehog** (equal-weight BTC+ETH+SOL trend, diversified) — or **Beaver** (BTC only) for the simplest single-asset version.
- Variant — *"I just want to accumulate over time, no thinking"* → **Tortoise** (DCA scheduler). Buys a fixed % on cadence; predicts nothing. The easiest possible answer for users who don't want a strategy thesis at all.
- Settings: **`balanced` DSL preset, 20% margin, 3x leverage** — the simplest, most-liquid, lowest-leverage starting point.
- Frame it honestly — **never say "safe."** *"I'll start you on a simple trend-follower on the major coins — it holds them while they're trending and steps out when they stall. It's the lowest-complexity, lowest-leverage place to begin — not risk-free (no strategy is; any single trade can lose), just the least to think about while you learn. We'll tune it the moment you've watched it run."*

**B. Plain-language quiz — map feelings to an archetype.** The user wants some say, but the Layer-1 terms are jargon. Ask these (no trading words), then route:

| Ask (plain English) | If they lean… | Route to |
|---|---|---|
| "When something's already shooting up, do you want to **jump in and ride it**, or **wait for it to fall back first**?" | ride it → trend · wait → contrarian | Layer 2A / 2B |
| "Should the agent **form its own opinion**, or just **copy whoever's winning right now**?" | own opinion → (keep going) · copy → copy-trading | Layer 2C |
| "Do you have a **specific market in mind** (a coin, a stock, oil/gold), or want it to **scan everything** for you?" | specific → single-market · scan → basket/universe | Layer 2D / 2A |
| "Do you have a **strong hunch about the world** — a war, the economy, a coin taking over — you'd bet on?" | yes, a view → thesis fund · no → keep going | Layer 2G |
| "A few **big wins you hold for days**, or **lots of small quick ones**?" | big/held → `let_winners_run` · small/quick → tighter preset | sets the DSL preset |

**C. Show, don't ask — pick by vibe.** Some people decide best from concrete examples. Offer 2–3 plain-English one-liners and let them point at one:
- **Beaver** — *"patiently holds BTC while it's trending up, steps out when it stalls."*
- **Egret** — *"bets against the crowd when everyone's piled in one direction and price stops following."*
- **Albatross** — *"copies traders who've won the arena for weeks, not just one lucky day."*
- **Bobcat** — *"trades big-tech stocks (NVDA, TSLA, …) 24/7 on Hyperliquid."*
- **Risk-Off Thesis Fund** — *"bets against the Trump economy: long gold, short US stocks and Bitcoin — and only presses as the move actually confirms."*
- **Octopus** — *"a market-neutral fund: long the strongest coins, short the weakest, so it can make money even when the market goes nowhere."*

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
| Asset **TRENDING** + SM **aligned** (same dir, ≥55%) | Trend-follower on that asset — 🟢 Beaver/Heron/Hummingbird, 🟢 **Sheep** for long-only triple-EMA-stack, or **Hedgehog** basket if no single standout |
| **Just want to accumulate** (no thesis, no timing) | 🟢 **Tortoise** (DCA scheduler — fixed % on cadence; predicts nothing) |
| Interest in **broad equity exposure** (no stock-picking) | 🟢 **Iguana** (xyz:SP500 + xyz:XYZ100 trend) |
| **RANGEBOUND** + SM **extreme & one-sided** (≥70%) price won't confirm | Fader → **Egret** |
| **VIOLENT** move + OI unwinding fast (Hyperfeed lit up) | Microstructure → **Piranha** (*only if risk = "go big"*) |
| Hyperfeed shows a **fresh rank-jump / breakout** | 🟢 **Hawk** (breakout) or **Jaguar** (rank-jump) |
| User would rather **copy winners** / has no asset view | 🟢 **Albatross** (multi-week arena winners) |
| Interest in **stocks / commodities** | 🟢 **Bobcat** (big-tech) · **Dire** (oil) · **Lemur** (pre-IPO) · **Falcon** (the IPO moment) |
| Nothing clean (chop + weak signals) | Default → **Hedgehog** basket, `balanced`, conservative sizing — or honestly say *"nothing's set up cleanly right now; want to start small and watch, or wait?"* |

*Step 4 — size & advise (don't gate):* `min_budget` is a **guideline, not a gate — never refuse to deploy over it.** If the account is below a candidate's `min_budget`, still offer it, but flag it plainly (*"you're under the ~$X suggested floor, so positions will be small and have less room — you can start tiny now or fund more"*) and let the user decide. Never dead-end a willing user; offer to start small or watch first instead of saying "no." Set margin % + leverage + DSL preset from the risk answer (cautious → ~15% / 3x / `balanced`; aggressive → ~25% / 5x / `let_winners_run`). Whatever the size, tell them to **fund only what they can afford to lose.**

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
| "I have a **strong view on the world** — a war, the economy, one coin beating the rest." | 🎯 **Thesis fund (view-based)** | Layer 2G |
| "I want a **hedge-fund-style return profile** — AI/tech, market-neutral, income, volatility, or macro." | 🏦 **Hedge fund (method-based)** | Layer 2H |
| "🏆 Just run whatever's **performing best right now**." | **Top performer (live ROE)** | *Run a current top performer* (below) |

> 💡 **Not just crypto.** Senpi trades **XYZ markets 24/7** (even when TradFi is closed): big-tech **stocks** (NVDA, TSLA, …), **commodities** (oil, gold, indices), and — increasingly popular — **pre-IPO perpetuals (IPOPs)** like **SpaceX**, *tradeable before the company lists*. If a user perks up at stocks or pre-IPO, route straight to **Layer 2D** — it's one of Senpi's most distinctive hooks.

> 🎯🏦 **Two higher-level products — and the one rule that routes them.** Beyond single strategies, Senpi packages two kinds of multi-leg product. **Thesis Funds** take a *view* — "the war drags on," "bet against the Trump economy," "HYPE beats the market" — and express it as a disciplined long/short basket that only presses each name as the market *confirms* the thesis. **Hedge Funds** take a *return style* — AI/tech, market-neutral, carry/income, volatility, global-macro — each a packaged two-wallet long/short book. **Routing rule: an opinion ("I think X will happen") → a Thesis Fund (Layer 2G); a return goal ("I want X kind of returns") → a Hedge Fund (Layer 2H).** And when a user names a *theme but not a side* ("bet on the war," "trade gold vs. Bitcoin"), ask **one** sharp follow-up — *escalation or de-escalation? which one wins?* — before deploying; a fund's direction is fixed by its preset, so never guess the side.

### Layer 2A — Trend-following → what do you want to ride?

- **One major coin** — pick BTC / ETH / SOL / HYPE.
  - 🟢 Beginner: **Beaver** (BTC) · **Heron** (ETH) · **Hummingbird** (HYPE) — SM-gated 4h trend, wide DSL, simple scoring.
  - 🟢 Beginner — *long only, multi-timeframe agreement*: **Sheep** (BTC/ETH/SOL/HYPE). Fires LONG only when 15m + 1h + 4h EMAs are all stacked bullishly. Never shorts — for users intimidated by directional choice.
  - ⬆️ Level up — *rotation across majors*: **Sailfish** (RS leader). Always holds the strongest of BTC/ETH/SOL/HYPE; rotates via DSL exit + re-entry.
  - 🎯 *Operator-driven — "I see a parabolic running, deploy something to ride it"*: **Stag** (parabolic-run hunter). Strict 5-gate entry filter (7d ≥ 25%, vol surge, acceleration, SM ≥60% LONG, structural trend); pairs with the widest DSL in the catalog (`parabolic_runner`: max_loss 25, retrace 18, 14d outer bound). Bleeds in chop — deploy only when you've identified the setup. Reference: HYPE 2026-05.
  - 🟢 *Even simpler — "I just want to own BTC and have a safety net"*: **Koala** (set-and-forget HODL). One asset, one entry per lifetime, the widest DSL in any Senpi agent (max_loss 30%, retrace 25, 90d hard_timeout). No scoring, no decisions after deploy. The simplest possible Senpi agent.
  - 🧠 *Advanced — "watch the agent learn from its own trades"*: **Lynx** (adaptive MIN_SCORE self-tuner). Productizes the Vulture v4.1 manual cull as a scheduled audit. Every 6h Lynx pulls its own closed-trade history and raises its MIN_SCORE if a bucket below the floor is bleeding. First fleet agent that modifies its own behavior based on its own track record.
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
- **Live hot-streak traders** (whoever's hot right now) → **Raptor** · **Jackal**.
- **Specific whales YOU pick** (name the traders, mirror their biggest bet) → **Remora** — hand-picked whale mirror with a consensus boost when several agree.
- **The best strategies, automatically** (don't pick anyone — follow whatever's working) → **Cuckoo** — auto-discovers the top-performing strategies and trades their performance-weighted consensus (copy-the-copiers).

### Layer 2D — Single-market specialist → which market?

XYZ markets (stocks / commodities / pre-IPO) trade **24/7 on Hyperliquid**, even when TradFi is closed.

- 🔥 **Pre-IPO names (IPOPs — SpaceX, etc.)** → 🟢 **Lemur** — trades pre-IPO perpetuals *before the company lists*; auto-discovers new IPOPs by their funding signature (today: SPCX/SpaceX; auto-expands as trade.xyz lists names like ANTHROPIC, OPENAI, STRIPE). One of Senpi's most distinctive capabilities.
- 🔥 **The IPO moment itself (when a pre-IPO name goes public)** → **Falcon** — sits out the pre-listing phase and fires only *around the conversion*, when an IPOP flips to a standard equity perp (funding jumps ~100x, leverage cap lifts, the price throttle comes off → free price discovery). Rides the post-conversion momentum with a wide let-winners-run DSL. Pairs naturally with Lemur (Lemur holds the IPOP; Falcon trades its graduation).
- **Big-tech stocks (XYZ equities)** → 🟢 **Bobcat** (NVDA/TSLA/AAPL/META/MSFT/GOOGL/…).
- **Just the broad indices (no stock-picking)** → 🟢 **Iguana** (xyz:SP500 + xyz:XYZ100). The closest thing to an index fund, but 24/7. Beginners who want stock-market exposure without choosing individual names.
- **Oil / metals / indices (XYZ)** → **Dire** (BRENTOIL) as the template — tune the asset string.
- **Weekend stock-gap reconciliation** → 🟢 **Raccoon** (weekend-only XYZ snap-back, captures the Mon-open move).
- **A specific crypto major** → see Layer 2A (Kodiak family).

### Layer 2E — Breakout / momentum-jump → what kind of move?

- **Break of the 7-day high/low (majors)** → 🟢 **Hawk** (breakout buyer / breakdown seller) · **Badger** (OI-confirmed).
- **Buy the dip *within* an uptrend** → 🟢 **Salamander** (pullback catcher).
- **Leaderboard rank-jumps caught early** → **Jaguar** · **Orca** · **Roach**.
- **Fresh momentum events the instant they fire** (snipe the strongest, just-formed moves off the momentum feed) → **Meerkat** (tier + freshness sniper).
- **Ride a liquidation cascade / forced flow** (OI unwinding fast + a violent move) → **Piranha** (microstructure / order-flow).
- **Trade order-book pressure** (resting bid/ask depth skew, confirmed by momentum + SM) → **Marlin** (microstructure / order-flow).

### Layer 2F — Structural / neutral → what structure?

- **BTC-led laggard rotation** (an alt that hasn't caught up to a BTC move yet) → **Mantis** (cross-asset lag).
- **Crypto stocks that lag a BTC move** (COIN / MSTR / miners on XYZ haven't caught up yet) → **Osprey** (cross-VENUE lag — self-computes the catch-up gap from each proxy's beta).
- **Volume / market-making** (not a directional bet) → **Turbine** (specialized).
- **Trade the spread between two coins** (a pair's ratio stretched far from its mean) → **Chameleon** (relative-value / pairs — ratio mean-reversion).
- *Expanding set — already live: microstructure (Piranha, Marlin — Layer 2E), relative-value / pairs (Chameleon), and copy-the-copiers meta-following (Cuckoo — Layer 2C).*

### Layer 2G — Thesis fund (view-based) → what's your view?

The user brings a **worldview** and the fund expresses it as a disciplined long/short basket in **one wallet**. It is *not* a blind bet: each name is only pressed when the market is **confirming** the thesis direction (trend + momentum aligned), and the DSL + drawdown gate de-risk when it isn't. Every variant below is the **same `thesis-fund` engine** with a different `THESIS` preset (`config/thesis-presets.json`) — pick the row that matches what the user just said.

| If the user says… (plain English) | Their view | Deploy — `THESIS` preset |
|---|---|---|
| "Bet against the Trump economy." · "A recession's coming." · "Tariffs will wreck things." | risk-off | 🎯 **Risk-Off — Bet Against the Trump Economy** (`risk_off`) — long gold/metals, short US indices + BTC |
| "America roars back." · "I'm betting on the soft landing." | recovery | 🎯 **U.S. Recovery — Risk-On** (`recovery`) — long US indices + BTC, short gold (the mirror) |
| "The Iran/Israel war drags on." · "The Middle East blows up." · "The conflict gets worse." | war escalation | 🎯 **War Escalation** (`war_escalation`) — long oil + gold, short equities + BTC |
| "A ceasefire's coming, markets calm down." · "Peace and recovery." | war de-escalation | 🎯 **War De-escalation — Recovery** (`war_recovery`) — short oil + gold, long equities + BTC |
| "HYPE keeps eating everyone's lunch." · "Long HYPE, but not the whole market." | HYPE outperforms | 🎯 **HYPE vs. the Rest of the Market** (`hype_vs_market`) — long HYPE, short the BTC/ETH/SOL basket (~market-neutral) |
| "Real gold beats crypto in a crisis." | gold > BTC | 🎯 **Gold over Bitcoin** (`gold_over_btc`) — long gold, short BTC |
| "Bitcoin is the new gold — it takes over." | BTC > gold | 🎯 **Bitcoin over Gold** (`btc_over_gold`) — long BTC, short gold |
| "I have a view, but it's not on this list." | custom | Fork the `thesis-fund` engine — add a preset to `thesis-presets.json` (a `long` basket + a `short` basket that together express the view). No new code. |

> **Ask one follow-up when the side is ambiguous.** "Bet on the war" → *escalation or de-escalation?* "Trade gold vs. Bitcoin" → *which one wins?* A Thesis Fund's direction is **fixed by the preset**, so pin the side before deploying — don't guess. Single wallet, both books at once, drawdown-halt at 20%; deploy via the `thesis-fund` README with the chosen `THESIS=`.

### Layer 2H — Hedge fund (method-based) → what return style?

The user wants a **style of return**, not a market view. These are packaged **two-wallet** long/short funds — one leg-parameterized producer running a long (or long-biased) book on one wallet and a short (or counter) book on the other. Pick by the kind of return they describe.

| If the user says… | Return style | Deploy |
|---|---|---|
| "I'm all-in on the AI boom." · "Get me AI + chip exposure." | AI/Tech momentum | 🏦 **Spider — AI/Tech Hedge Fund** — an AI/tech long book (swing) + a macro/majors long-short counter book (scalp) |
| "Make money in any market — up, down, or sideways." · "Returns that don't move with Bitcoin." | market-neutral | 🏦 **Octopus — Market-Neutral Hedge Fund** — longs the relative leaders, shorts the laggards of the liquid cross-section (~beta-neutral dispersion) |
| "I want steady income, not big swings." · "Put my crypto to work while I sleep." | carry / income | 🏦 **Camel — Carry Hedge Fund** — shorts the most-positive-funding names (short collects), longs the most-negative (paid to hold) — harvests funding both ways |
| "Just catch the big moves — I don't care which way it breaks." · "Comes alive when it's volatile." | volatility | 🏦 **Caracal — Volatility Hedge Fund** — trades volatility *expansion*, not direction (coiled-spring breakouts), across crypto + XYZ |
| "Trade the whole macro board, not just crypto." · "I want gold, oil, and indices too." | global macro | 🏦 **Elephant — Global-Macro Hedge Fund** — equity indices, metals, energy, FX (XYZ) + BTC; a trend book that rides the macro direction + a fade book |
| "Trade the turn — ride risk-on rallies, flip defensive when it rolls over." · "Adapt to the macro mood." | event-driven / regime-rotation | 🏦 **Wolf — Event-Driven Hedge Fund** — a shared cross-asset regime detector rotates the book: long beaten-down beta in risk-on, long defensives + short risk in risk-off |
| "Protect me when things break." · "Make money in a crash." · "I want a hedge." | tail-risk / crisis-alpha | 🏦 **Rhino — Tail-Risk Hedge Fund** — a small always-on hedge in crisis beneficiaries (gold/oil/$) + a stress-gated book that fires hard when a shock confirms (long crisis, short risk) |
| "Give me a diversified core I can just hold." · "Lower drawdown, set-and-forget." · "Balance my risk across everything." | risk-parity / all-weather | 🏦 **Ox — Risk-Parity Hedge Fund** — a vol-balanced LONG basket across crypto/indices/metals/energy/FX, each sleeve sized by *inverse volatility* so no asset class dominates risk, plus a defensive ballast that scales up on risk-off. The core you hold while betting with the others |
| "Trade the stock market." · "Long the best stocks, short the worst." · "I want tokenized equities." | U.S. equity long/short | 🏦 **Cougar — U.S. Equity Long/Short Hedge Fund** — longs the relative-strength leaders and shorts the laggards of the tokenized US-equity universe (NVDA/TSLA/AAPL/…), ~market-neutral; harvests equity dispersion |
| "Trade the SpaceX-style IPOs." · "Get me in on new listings early." · "Play the pre-IPO names." | IPO / new-listing event | 🏦 **Magpie — IPO / New-Listing Event Hedge Fund** — a pre-listing book that accumulates pre-IPO perpetuals into the IPO + a graduation book that rides the explosive momentum when one converts to a full equity perp |

> **Funds size differently from single strategies.** Each Hedge Fund spans **two wallets** (fund both legs per the fund's README split — Spider defaults 60% swing / 40% scalp); Thesis Funds use **one**. These are **not onboarding-tier** — route a brand-new user to a single onboarding agent first (Layer 3), and offer a fund once they want a packaged long/short book rather than a single position stream.

### Run a current top performer (by live ROE)

Some users don't want a thesis — they want whatever's working *now*. The agent can rank live performance and let them pick. **This reads live data, so it's opt-in** (same ask-before-scan rule as path D) — only run it when asked.

*How:*
- Pull live performance for **deployable** strategies: `arena_leaderboard` (7-day rolling ROE %, resets Thu 00:00 UTC) and/or the Senpi Agent Tracker `get_performance` per strategy (ROI / PnL / equity).
- **Prefer consistency over a single hot week.** A strategy up big over 7 days can give it all back the next — multi-week blow-ups are real. If you have more than one window, weight steady performers over one-week wonders (this is exactly Albatross's "multi-week, not one lucky day" logic).
- **Filter:** only strategies that exist as installable skills (cross-reference `catalog.json`; exclude unpublished agents like Sentinel). `min_budget` is a **guideline, not a gate** — don't drop a strategy for being above the user's balance; if they want it, flag that they're under the suggested floor (smaller positions, less room) and let them decide, or top up.

*Present* the top 2–3 with: recent ROE + **the window** + a one-line thesis — then the required caveat.

> ⚠️ **Required framing — recent ≠ future.** Say it plainly: *"These are **recently** top-performing, not guaranteed winners. A strategy that's up this week can reverse hard next week — past performance doesn't predict future results."* Never present a leaderboard ROE as an expected return.

Example: *"Over the last 7 days the top deployable performers are **Vulture** +X% (HYPE funding-regime contrarian), **Condor** +Y% (universe trend), **Bison** +Z% (conviction whitelist). Vulture's leading right now — want to run it? Heads-up: that's a 7-day window and it can reverse; if you'd rather something proven over a longer stretch, **Albatross** specifically tracks multi-week winners."*

### Layer 3 — Lock it in (the deploy step)

Once a strategy is chosen, confirm three things with the user, then deploy:

1. **Risk / sizing** — margin % of equity + leverage. *First-strategy default: 20–25% margin, ≤5x.*
2. **DSL preset** — `balanced` (the smart default) for most; `let_winners_run` for conviction trend-holders; `mean_reversion` for faders; `scalp` for high-frequency. See [`dsl-presets.yaml`](dsl-presets.yaml).
3. **Config + launch** — set wallet / chat / decision-model, then `openclaw senpi runtime create` + the disown-safe daemon launch. Each agent's README has the exact steps.

> **First-strategy rule of thumb:** pick an **onboarding-tier** agent (🟢 above — Beaver/Heron/Hummingbird/Hedgehog/Hawk/Salamander/Albatross/Lemur/Bobcat/Raccoon/Tortoise/Sheep/Iguana), keep the **`balanced`** DSL preset (or `let_winners_run` for Tortoise), size at 20–25% margin / ≤5x. Graduate to fleet agents once they've watched one run.

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
├─ BTC-anchored lag           → Pattern 9 — Cross-asset lag detector
└─ Packaged long/short FUND (two books)?
    ├─ view-based (war / economy / one coin wins)  → Pattern 19 — Thesis fund (one engine, preset-driven; single wallet)
    └─ method-based return style                   → Hedge-fund composite (two wallets, one leg-parameterized producer):
        ├─ AI/tech momentum      → Spider   (Pattern 4 long book + macro/majors counter book)
        ├─ market-neutral        → Octopus  (Pattern 13 — cross-sectional dispersion, long leaders / short laggards)
        ├─ carry / income        → Camel    (Pattern 7 — funding harvest both directions)
        ├─ volatility expansion  → Caracal  (Pattern 17 — coiled-spring breakout, crypto + XYZ)
        ├─ global macro          → Elephant (Pattern 18 — indices/metals/energy/FX + BTC, trend book + fade book)
        ├─ event-driven / regime → Wolf     (Pattern 20 — shared regime brain, risk-on book ↔ risk-off book)
        ├─ tail-risk / crisis    → Rhino    (Pattern 21 — shared stress brain, always-on hedge + stress-gated escalation)
        ├─ risk-parity / core    → Ox       (Pattern 22 — inverse-vol sizing, all-weather core + defensive ballast)
        ├─ U.S. equity long/short → Cougar  (Pattern 23 — dispersion on tokenized equities, long leaders / short laggards)
        └─ IPO / new-listing event → Magpie (Pattern 24 — IPOP discovery + conversion detection, pre-listing + graduation)
```

> **Thesis vs. Hedge fund — which to fork.** A **Thesis Fund** is *one* engine (`thesis-fund`) whose behavior is entirely data: add a `{long: […], short: […]}` preset to `thesis-presets.json` and set `THESIS=` — no new code. A **Hedge Fund** is *two* runtime YAMLs + one leg-parameterized producer (`<FUND>_LEG=…`), each wallet running a different scoring book. Fork the closest fund above and re-tune the two books' scoring + universes.

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
| Spider | 4 — Multi-asset whitelist (two-leg hedge fund) | AI/Tech long book (swing) + macro/majors long-short counter-trading book (scalp); two wallets, one leg-parameterized producer |
| Jaguar | 6 — Striker / rank-jump | Canonical |
| Roach | 6 — Striker / rank-jump | FIRST_JUMP / IMMEDIATE_MOVER + volume |
| Roach-B | 6 — Striker / rank-jump | Second wallet instance of the Roach producer |
| Orca | 6 — Striker / rank-jump | Gen-1 vanilla Striker, FIRST_JUMP + volume + base scoring |
| Pangolin | 7 — Funding-regime fade | Canonical |
| Dog | 7 — Funding-regime fade | 4-coin whitelist with regime hard-gate |
| Vulture | 7 — Funding-regime fade (legacy bucket; thesis is actually long-tail momentum on small caps since v2.0) | 25 small/mid-cap perps, conviction-scaled leverage 5x/7x. v4.1.0: MIN_SCORE 7→9 after 30-trade analysis culled the losing buckets |
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
| **Marlin** | 12 — Microstructure / order-flow | BTC/ETH/SOL/HYPE. Order-book imbalance (bid/ask depth skew) as entry-timing on a momentum thesis — not a scalper. Wide DSL + 24h hard_timeout |
| **Chameleon** | 13 — Relative-value / pairs | ETH/BTC · SOL/ETH · SOL/BTC. Ratio mean-reversion — trades the high-beta leg when a pair's z-score extends ~2σ and starts reverting. Mean-reversion DSL |
| **Falcon** | 3 — Single-asset XYZ specialist (event-detection layer) | xyz: conversion events. Detects the IPOP→equity flip (funding jumps ~100x, leverage cap lifts, throttle off) and rides post-conversion price-discovery momentum. Class-state + conversion-window cache. Wide let-winners-run DSL, 7d hard_timeout |
| **Osprey** | 9 — Cross-asset lag detector (cross-VENUE variant) | BTC → xyz: equity proxies (COIN/MSTR/miners). Self-computes the catch-up gap (leader move × beta − proxy move) from candles; trades the proxy in the leader's direction while it owes the gap. NOT cross_asset_flows (crypto-only). Wide let-winners-run DSL, 96h hard_timeout |
| **Remora** | 5 — Trader-follower (hand-picked whale mirror) | Operator-picked whale set. Mirrors each whale's largest-notional position, scored by consensus (2 whales +2, 3+ +3) + ELITE-tier bonus. Unwraps the nested leaderboard_get_trader_positions shape. Wide let-winners-run DSL, 120h staleness cap |
| **Cuckoo** | 14 — Meta-strategy follower / copy-the-copiers | Auto-discovers the top-N strategies by performance and trades their performance-weighted consensus (weight = clamp(1 + roi/50, 0.5, cap)); gate ≥2 strategies agree. Wide let-winners-run DSL, 96h staleness cap. User-scope auth |
| **Meerkat** | 6 — Striker / rank-jump (momentum-event-feed variant) | Reads leaderboard_get_momentum_events directly; snipes the freshest (≤30min), highest-tier (3 ≥10% · 2 ≥5%) momentum events in the move's direction. SM + volume bonuses. Wide let-winners-run DSL + short 36h hard_timeout. Tick 120s. User-scope auth |
| **Tortoise** | 4 — Multi-asset whitelist (time-trigger variant, onboarding) | DCA scheduler — time-trigger, no `market_get_asset_data` for scoring. Most-overdue past interval wins. LONG only. Wide DSL + 30d hard_timeout. Persisted DCA-history cache |
| **Sheep** | 4 — Multi-asset whitelist (onboarding) | BTC/ETH/SOL/HYPE long-only triple-EMA-stacked trend. Fires only when 15m + 1h + 4h EMAs are all stacked bullishly. Balanced DSL + weak_peak_cut 6h/3% |
| **Iguana** | 4 — Multi-asset whitelist (XYZ subset, onboarding) | xyz:SP500 + xyz:XYZ100. Simplest possible XYZ exposure — index-fund equivalent. Balanced DSL + 48h hard_timeout for weekend pricing-gap risk |
| **Sailfish** | 4 — Multi-asset whitelist (momentum-rotation) | BTC/ETH/SOL/HYPE. Ranks by ~2.7d RS, longs the leader iff leader RS ≥ 1% AND beats runner-up by ≥ 1.5pp (no whipsaw). Rotation via DSL exit + re-entry. Balanced DSL + 96h hard_timeout |
| **Stag** | 4 — Multi-asset whitelist (parabolic-run hunter, operator-driven) | BTC/ETH/SOL/HYPE (often single-asset). Strict 5-gate filter (7d ≥ 25% + vol surge + accel + 200-SMA + SM ≥60% LONG). LONG only. Entry-side pair for new `parabolic_runner` DSL preset (max_loss 25%, retrace 18, 2 breaches required, 14d outer bound). Reference: HYPE 2026-05 +60% in 16 days |
| **Koala** | 2 — Single-asset alpha hunter (state-trigger variant, onboarding) | Operator-chosen single asset (default BTC). No scoring — state-file fire-once entry, then the widest DSL in any Senpi agent (max_loss 30%, retrace 25, 90d hard_timeout). The simplest possible Senpi agent |
| **Lynx** | 15 — Self-tuning / adaptive-threshold agent | BTC/ETH/SOL/HYPE. Simple momentum scorer with a 6h audit cron that pulls own closed-trade history via audit_query, buckets by entry score, and raises MIN_SCORE if a bucket below the floor is bleeding. First fleet agent that modifies its own behavior based on its own track record |
| **Coyote** | 16 — Regime classifier / meta-router | BTC (positional) + BTC/ETH/SOL/HYPE (dispersion universe). 3-regime classifier (TREND_UP / TREND_DOWN / CHOP) with vol-confirmation on the down side. Publishes regime + all input metrics on every tick. LONG BTC in TREND_UP, SHORT BTC in TREND_DOWN, no trade in CHOP |
| **Otter** | 1 — Universe trend-follower | Universe-scan variant; see catalog.json for current tagline + min_budget |
| **Spider** | (hedge fund) AI/Tech — two-leg long/short | AI/Tech long book (swing) + macro/majors long-short counter-trading book (scalp). Two wallets, one leg-parameterized producer. Catalog group: `hedge-fund`, archetype `ai-tech`. (Also appears above under #4 Multi-asset whitelist — the same two-leg producer; this row reflects its hedge-fund packaging) |
| **Octopus** | (hedge fund) Market-neutral — relative-value dispersion | Longs the relative leaders, shorts the relative laggards of the liquid crypto cross-section (~beta-neutral). Returns that don't move with Bitcoin. Catalog group: `hedge-fund`, archetype `relative-value`. Structurally related to #13 Relative-value / pairs |
| **Camel** | (hedge fund) Carry / income — funding harvest, two-sided | Two-wallet funding-harvest fund: shorts the most-positive-funding names (collects on short side), longs the most-negative (paid to hold). Harvests funding both ways. Catalog group: `hedge-fund`, archetype `carry`. Structurally related to #7 Funding-regime fade |
| **Caracal** | 17 — Volatility / breakout-expansion (hedge fund variant) | Trades volatility *expansion*, not direction — coiled-spring breakouts across crypto + XYZ. Catalog group: `hedge-fund`, archetype `volatility` |
| **Elephant** | 18 — Global macro / cross-asset (hedge fund variant) | Equity indices, metals, energy, FX (XYZ) + BTC. Trend book that rides the macro direction + a fade book. Catalog group: `hedge-fund`, archetype `global-macro` |
| **Wolf** | 20 — Event-driven / regime-rotation (hedge fund variant) | Shared cross-asset regime brain (equities/oil/gold/BTC/$ 4h votes) gates which book fires: `risk_on` longs beaten-down beta in RISK_ON (wide DSL), `risk_off` longs defensives + shorts risk in RISK_OFF (tighter DSL). Stands down in NEUTRAL. Two wallets, one leg-parameterized producer, 50/50 funding. Catalog group: `hedge-fund`, archetype `event-driven` |
| **Rhino** | 21 — Tail-risk / crisis-alpha (hedge fund variant) | Shared stress brain (oil/equities/gold/BTC breaks + BTC vol-expansion). `hedge` book: always-on small LONG defensives carry (wide 10d DSL). `escalation` book: dormant until stress, then LONG spiking crisis + SHORT cratering risk (larger size, moderate-tight DSL). Two wallets, one leg-parameterized producer, 50/50 funding. Catalog group: `hedge-fund`, archetype `tail-risk` |
| **Ox** | 22 — Risk-parity / all-weather (hedge fund variant) | Inverse-volatility sizing — each sleeve's marginUsd = budget × (1/vol)/Σ(1/vol), over the full basket. `core` book: always-invested vol-balanced LONG basket (crypto/indices/metals/energy/FX), 60% budget, 3x, wide 14d DSL. `ballast` book: always-on LONG defensives, 18% budget ×2 on a risk-off lean. Low leverage, low turnover (600s). Two wallets, one leg-parameterized producer, 70/30 funding. Catalog group: `hedge-fund`, archetype `risk-parity` |
| **Cougar** | 23 — U.S. equity long/short (hedge fund variant) | Cross-sectional dispersion (Octopus method) on the tokenized US-equity universe (trade.xyz: NVDA/TSLA/AAPL/…). `long` book longs the RS leaders, `short` book shorts the laggards, trend-confirmed, ~beta-neutral. 5x, 20% margin, 7d DSL (equities trend longer than crypto). Two wallets, one leg-parameterized producer, 50/50 funding. Catalog group: `hedge-fund`, archetype `equity-long-short` |
| **Magpie** | 24 — IPO / new-listing event (hedge fund variant) | `pre_listing` book: auto-discovers IPOPs by funding signature (Lemur method), rides the pre-listing ramp (3x/12%, moderate-wide DSL). `graduation` book: detects the IPOP→STANDARD conversion flip via a class-state cache (Falcon method) + 72h window, rides post-conversion momentum (5x/15%, wide DSL). Episodic. Two wallets, one leg-parameterized producer, 50/50 funding. Requires user-scope auth. Catalog group: `hedge-fund`, archetype `event-driven-ipo` |
| **thesis-risk-off** | 19 — Thesis fund (preset: `risk_off`) | "Bet against the Trump economy" — long gold/metals, short US indices + BTC. Variant of the `thesis-fund-strategy` engine; deploy via base_skill + `THESIS=risk_off` |
| **thesis-recovery** | 19 — Thesis fund (preset: `recovery`) | "U.S. Recovery — Risk-On" — long US indices + BTC, short gold. Mirror of `risk_off`. Deploy via base_skill + `THESIS=recovery` |
| **thesis-war-escalation** | 19 — Thesis fund (preset: `war_escalation`) | "War Escalation" — long oil + gold, short equities + BTC. Deploy via base_skill + `THESIS=war_escalation` |
| **thesis-war-recovery** | 19 — Thesis fund (preset: `war_recovery`) | "War De-escalation — Recovery" — short oil + gold, long equities + BTC. Deploy via base_skill + `THESIS=war_recovery` |
| **thesis-hype-vs-market** | 19 — Thesis fund (preset: `hype_vs_market`) | "HYPE vs. the Rest of the Market" — long HYPE, short the BTC/ETH/SOL basket (~market-neutral). Deploy via base_skill + `THESIS=hype_vs_market` |
| **thesis-gold-over-btc** | 19 — Thesis fund (preset: `gold_over_btc`) | "Gold over Bitcoin" — long gold, short BTC. Deploy via base_skill + `THESIS=gold_over_btc` |
| **thesis-btc-over-gold** | 19 — Thesis fund (preset: `btc_over_gold`) | "Bitcoin over Gold" — long BTC, short gold. Deploy via base_skill + `THESIS=btc_over_gold` |

Sentinel runs an in-house producer that is not currently published to this repo; no public URL. Roach-B (above) is the second-wallet instance of the Roach producer — same producer, different wallet — and therefore appears in this roster but not as a separately-installable entry in `catalog.json`.

---

## Where this catalog lives in the broader docs

- `senpi-trading-runtime/SKILL.md` — start here for the runtime + Producer SDK overview
- [`yaml-schema.md`](yaml-schema.md) — full `runtime.yaml` field reference
- [`python-producer-sdk.md`](python-producer-sdk.md) — full Producer SDK reference
- [`strategy-examples.md`](strategy-examples.md) — runtime.yaml templates by strategy type
- **This file** — scanner archetype catalog + Decision Tree (you are here) — **the canonical routing source**
- [`liveness-verification.md`](liveness-verification.md) — how to confirm your producer is firing on-chain
- `/catalog.json` (repo root) — install + display metadata only; defers to this doc for all routing logic

### Source-of-truth contract — catalog.json vs. this doc

The two artifacts answer two different questions. Don't mix them up:

| Question | Authoritative source |
|---|---|
| *"What's installable, and what's its display info (name, emoji, tagline, install command, min_budget, predators_url)?"* | `catalog.json` |
| *"Which strategy do we recommend when a user says X?"* | **This doc — the Decision Tree** |
| *"What's the conversational flow to land a user on a strategy?"* | **This doc — the Decision Tree (Layers 0–3)** |
| *"How do we handle variants (e.g. Thesis Fund presets)?"* | **This doc — Layer 2G + the variant rows in the roster** (catalog.json carries `base_skill` + `thesis` as install-time fields, but the routing/UX intent lives here) |
| *"How do we order strategies for display?"* | **This doc — Layer 1 / Layer 2 ordering** (catalog.json `sort_order` is a per-group display tiebreaker only) |
| *"What's the default first strategy for a brand-new user?"* | **This doc — Layer 0A Express Lane** (Hedgehog / Beaver — catalog.json doesn't encode defaults) |
| *"How do we frame `min_budget`?"* | **This doc — Layer 0D, Layer 3** (guideline, not a gate; never refuse over budget) |

When `catalog.json` and this doc disagree, this doc wins. `catalog.json`'s `_instructions` field now states this explicitly and points back here.

Pick a pattern from this catalog, fetch the example agent's producer + runtime.yaml from the GitHub URLs in that pattern's section, tune the scoring and thresholds for your thesis, deploy with `openclaw senpi runtime create` + `nohup python3 ... &`. That's the canonical build path.
