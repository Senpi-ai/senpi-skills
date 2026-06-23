# senpi-market-pulse

A Senpi skill that answers **"what's happening in the markets today?"** with thoughtful, structured,
cross-asset analysis — the read a human couldn't assemble alone, not just "BTC is up."

Modeled on `senpi-strategy-discover`: a **hidden deterministic engine** does the streamlined,
resilient data-gathering and computes the concrete cross-asset signals; the **LLM (SKILL.md)** does
the analysis, the narration, the catalyst (web-search) layer, and the closing CTAs.

## Why a skill

"What's moving today?" has dozens of valid tool calls and a hundred orderings. Left freeform, the
agent wanders, leaves out asset classes, leads with the wrong thing, and stalls on infra hiccups —
and stops at "BTC is down 3%." This skill fixes the **call plan**, the **output contract**, the
**resilience rules**, and the **analysis framework** so the gold-standard answer is the *first*
answer, every time.

## Architecture

```
scripts/pulse.py     hidden engine — parallel multi-class pull, prevDayPx resilience,
                     smart-money (leaderboard) layer (health-gated), deterministic signals → JSON
scripts/_mcp.py      self-contained streamable-HTTP MCP client (stdlib only; read-only)
SKILL.md             the analyst — golden rules, top-down output contract, the two CTAs
references/analysis-framework.md   the cross-asset reasoning (dispersion, confirmation checklist, K-shape)
tests/               offline fixture test for the engine (no network)
```

## How it works

1. **One health-check + parallel bulk pull.** `market_list_instruments` on both dexes gives
   price + `prevDayPx` for the whole universe — so daily moves never depend on candles, and a
   candle 500 or a Hyperfeed outage can't drop an asset class.
2. **Capped parallel deep-pull** of the biggest movers for volume/funding conviction.
3. **Health-gated smart-money layer** (`leaderboard_*`) — cohort concentration, top traders,
   momentum events; degrades to `null` cleanly if Hyperfeed is down.
4. **Deterministic signals** — dispersion (index vs. components), the gold/DXY/VIX confirmation
   checklist, a coarse day classification. The LLM turns these into the thesis.

The engine **fails open** end-to-end: partial data still returns valid JSON with `meta.warnings`.

## Output

Top-down, fixed shape: macro picture → indices → epicenter sector (with gradient) → the divergence →
commodities/macro → crypto → notables → bottom line + "what to watch" → **the two CTAs**:

> 1. Want me to check how our strategies and positions are positioned in this?
> 2. Want me to create a new strategy catered to this market?

CTA 1 routes to a positions read; CTA 2 hands **senpi-strategy-author** a brief pre-built from the
market thesis (proposes, never auto-builds).

## Run

```sh
python3 scripts/pulse.py            # full live pull
python3 scripts/pulse.py --no-smart # skip the leaderboard layer
python3 scripts/pulse.py --fixture tests/fixtures/pulse_fixture.json   # offline (tests)
```

Env: `SENPI_AUTH_TOKEN`, `SENPI_MCP_URL` (defaults to prod).

## Status / review notes

- **v1** per the scope: Senpi MCP + the deterministic framework. The catalyst web-search and the
  CTA-2 brief live in the SKILL (LLM) layer. Primary macro/FX feeds and holdings-personalization are
  v1.1 / v2 (see scope doc).
- **Verify before merge:** the `market_*` field names in `pulse.py` (`markPx`/`prevDayPx`/`funding`/
  `dayNtlVlm` and the `market_list_instruments` shape) are taken from `senpi-strategy-discover`'s
  live usage + observed responses. Confirm against one live call and adjust the `_field()` fallbacks
  if the schema differs — the engine is written defensively but a schema check is cheap insurance.
- The asset universe in `pulse.py` (`CRYPTO`, `XYZ_GROUPS`) is meant to be edited by the team.
