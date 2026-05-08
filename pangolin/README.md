# 🦔 PANGOLIN v2.0 — Funding Rate Fader. v2-Runtime-Native. Maker Exits.

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When funding rates are elevated (>0.015%/8h ≈ 20% annualized), the crowd is paying to hold their position. Pangolin enters opposite to the funding direction — collecting funding every 8 hours while waiting for the crowded side to capitulate. Conservative 3-5x leverage, very wide DSL (12h hard timeout, 30% Phase 1 max_loss). Scans every Hyperliquid perp with OI > $1M (~60 assets), persistence >= 3 hours, regime-confirmed.

## v2.0 architecture

| Layer | v1.x | v2.0 |
|---|---|---|
| Trading loop | Agent runs scanner + calls `create_position` | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | Agent decides | LLM pass-through gate (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT, taker fallback OFF | Same — `ensure_execution_as_taker: false` preserved (v1 patience) |
| Exit order | DSL + MARKET orders | DSL + **FEE_OPTIMIZED_LIMIT** (maker-first, 60s, taker fallback) |
| Risk gates | Agent enforces in scanner code | Declarative `runtime.risk.guard_rails` |

**Why v2 matters for Pangolin:** v1 entries were already maker-first, but v1 EXITS used MARKET orders (taker fees). v2 brings maker-first to exits too. Fee saving per trade is small (~$0.10-0.20) given Pangolin's small notional, but architectural alignment + runtime-managed lifecycle + declarative risk gates are the real win.

**Thesis preserved verbatim from v1.5/v1.7:** funding rate >= 0.00015, persistence >= 3h, regime confirms or neutral, OI >= $1M, score >= 9, per-asset 240min cooldown, XYZ banned. Phase 1 max_loss 30% (10% price buffer at 3x), Phase 2 ladder starts at 12% ROE (above MAVIA's normal wick noise), `weak_peak_cut` disabled (funding fade takes 24-48h).

See [`SKILL.md`](SKILL.md) for full setup, env vars, and behavior expectations.

## License

MIT — Built by Senpi (https://senpi.ai).
