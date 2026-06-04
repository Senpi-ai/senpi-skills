# Thesis Fund — Skill Attribution

**Skill:** thesis-fund-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-04

## What the Thesis Fund is

A new **view-based** product line, distinct from the method-based hedge funds
(Spider/Octopus/Camel/Caracal/Elephant). Those pick a *trading style*; the
Thesis Fund lets a user pick *what they believe will happen* and expresses it
with discipline. It is **one configurable engine** shipped as multiple
catalog "presets" — each a one-tap macro bet.

## Lineage

- **Architecture** — the helpers-native producer pattern (`producer_daemon` +
  fcntl lock, `SenpiClient.push_signal()`, runtime-owned LLM gate + DSL + risk),
  ported from Elephant's macro scorer. Differs structurally: **single wallet,
  single book** (a thesis is one coherent bet, not two separate strategies), and
  **preset-driven** — the `THESIS` env var selects a long/short basket from
  `thesis-presets.json`, and the per-asset direction is fixed by the preset.
- **Thesis** — express a macro view (risk-on/off, war escalation/de-escalation,
  HYPE-vs-market, gold-vs-BTC) as a long/short basket, pressed only when the
  market confirms.

## Design decisions specific to the Thesis Fund

- **Confirmation-gated, not blind conviction.** The preset fixes the *direction*
  per asset, but the engine only *enters* a name when the market is confirming
  that direction (4h structure aligned, momentum aligned). A "short SP500" thesis
  only shorts SP500 once it's actually rolling over — never fights a rising tape.
  This is what keeps a directional macro bet from being a slow bleed.
- **One engine, many presets (catalog variants).** Rather than build a separate
  skill per use-case, one engine reads a preset. Use-cases ship as catalog
  entries with `base_skill: thesis-fund-strategy` + a `thesis` key. Adding a new
  bet is a JSON edit to `thesis-presets.json`, not a code change.
- **Single wallet per thesis.** A thesis is one coherent bet → one wallet holds
  the long+short basket. Cleaner UX (fund one wallet) than the two-wallet style
  funds, and the mixed-direction basket is itself the expression of the view.
- **Opposing presets are flipped baskets.** risk_off↔recovery, war_escalation↔
  war_recovery, gold_over_btc↔btc_over_gold — same engine, mirrored long/short.

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense).
- Per-position margin **12%** (≤ 25% fleet cap; basket of up to 6 = ≤72% committed).
- Drawdown halt **20%**, `drawdown_reset_on_day_rollover: true` — halts the whole
  fund if the thesis is broadly failing.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `thesis / preset / basket_size /
  candidates / signals_pushed`.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. The Thesis
  Fund: strict 5x + a 20% drawdown halt that kills the bet if the view is wrong.
- **Don't fight the tape** — the confirmation gate means a directional thesis is
  only pressed when the market agrees; it de-risks (skip + DSL recycle) when it
  doesn't. The biggest risk for a conviction product is bleeding on a wrong view
  held stubbornly; the gate + drawdown halt address it directly.
- **Theses have a shelf life** — event-driven presets are documented as needing
  retirement/update as the situation resolves (they live in editable JSON).

## Capital provenance

New capital — one wallet per deployed thesis, sized to the conviction behind it.
