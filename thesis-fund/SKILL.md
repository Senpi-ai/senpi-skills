---
name: thesis-fund-strategy
description: >-
  THESIS FUND v1.0 — one configurable engine, many one-tap macro bets. You
  bring the market VIEW; the fund expresses it with discipline. The THESIS env
  var selects a preset from thesis-presets.json (e.g. risk_off = bet against
  the Trump economy, recovery = US risk-on, war_escalation / war_recovery =
  Iran/US/Israel, hype_vs_market, gold_over_btc / btc_over_gold). Each preset
  is a long basket + a short basket that together express the view, held in
  ONE wallet — but it is NOT a blind hold: each name is only pressed when the
  market is CONFIRMING the thesis direction, and the DSL + drawdown gate
  de-risk when it isn't. NOT a copy-trader; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🎯 THESIS FUND v1.0 — bet your view, run with discipline

The Thesis Fund is **one engine that expresses any of several macro views.**
Instead of picking a *trading style*, you pick *what you believe will happen* —
and the fund trades the basket that expresses it. **One producer**
(`thesis-producer.py`), one wallet, driven by the `THESIS` env var which selects
a preset from `config/thesis-presets.json`.

> **Disciplined conviction, not a hope trade.** The fund holds the preset's
> long/short basket, but it only **presses** a name when the market is
> *confirming* the thesis direction (4h/1h trend + 24h momentum aligned). A
> long-basket name in a fresh downtrend is skipped; a short-basket name in a
> fresh uptrend is skipped. The DSL + drawdown gate de-risk when the thesis
> stops working. You express the view; the engine enforces the discipline.

## Presets (the one-tap bets)

Each preset = a `THESIS` value. Opposing presets are just flipped baskets.

| THESIS | The bet | Long | Short |
|---|---|---|---|
| `risk_off` | Bet against the Trump economy / risk-off | gold, silver | SP500, XYZ100, BTC |
| `recovery` | U.S. recovery / risk-on (the mirror) | SP500, XYZ100, BTC | gold |
| `war_escalation` | Iran/US/Israel quagmire deepens | BRENTOIL, CL, gold | SP500, XYZ100, BTC |
| `war_recovery` | De-escalation / recovery | SP500, XYZ100, BTC | BRENTOIL, CL, gold |
| `hype_vs_market` | HYPE keeps outrunning the majors | HYPE | BTC, ETH, SOL |
| `gold_over_btc` | Real gold beats digital gold | gold | BTC |
| `btc_over_gold` | Digital gold beats real gold | BTC | gold |

Add or edit presets in `thesis-presets.json` — **no code change needed.**

## How it scores (confirmation, not blind hold)

Each tick, for every name in the active preset's basket, the producer fetches
1h+4h candles and scores how strongly the market is **confirming** the thesis
direction for that name (the direction is fixed by the preset):

| Component | Pts |
|---|---|
| 4h structure aligned with the thesis direction | +3 / +1 neutral / **skip** if opposed |
| 1h confirmation | +1 aligned / −1 opposed |
| 24h momentum aligned | +2 (≥`momThresholdPct`) / +1 (≥0) |
| RSI room (not overbought for a long / not oversold for a short) | +1 |

`minScore` (4) is the confirmation bar. A name whose 4h structure *opposes* the
thesis is skipped entirely — the fund waits for the view to start working rather
than fighting the tape.

## Execution & exit

- slots **6** (the basket), `margin_pct` **12%**, tick **300s**, **strict 5x** clamp (then venue max)
- DSL **balanced let-it-work**: phase1 max_loss **14%** / retrace 9 / 1 breach; `weak_peak_cut` **ON** (8h @ 2.0) + `dead_weight_cut` **ON** (16h) recycle a name that stops confirming; `hard_timeout` **5d**; phase2 ladder `10%→0 / 22%→45 / 40%→65 / 70%→80 / 120%→90`
- `drawdown_halt_pct` **20%** halts the whole fund if the thesis is broadly failing.

## XYZ handling

Many baskets include XYZ (indices/metals/energy) — candle fetches route with
`dex="xyz"` (the producer keys off the `xyz:` prefix). XYZ trades 24/7. The
`main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined wallet,
so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Files

| File | Purpose |
|---|---|
| `runtime.yaml` | Single-wallet runtime spec (DSL, risk, LLM gate) |
| `scripts/thesis-producer.py` | Preset-driven producer daemon |
| `scripts/thesis_config.py` | Thesis resolution + preset load + SenpiClient wrapper |
| `config/thesis-presets.json` | The thesis baskets (edit/add presets here) |
| `config/thesis-config.json` | Shared tunables (sizing, scoring, leverage) |

## Operator install

See [README.md](README.md) — set `THESIS=<preset>`, fund **one** wallet, launch
**one** daemon.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the
**DSL ratchet engine** (exit path). User-conversation sessions are
**read-only**. The producer daemon handles real signals on its next tick.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
