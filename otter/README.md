# 🦦 OTTER v1.0 — Open Interest Velocity Hunter

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Open Interest is the total notional of open perpetual contracts. Spot trading doesn't generate OI — only fresh perp positions do. So OI growth = real new leveraged capital deployed. When 1h OI delta is >= 5% AND price moves in the same direction by >= 0.5%, that's the **TOP-LEFT (LONGS entering)** or **TOP-RIGHT (SHORTS entering)** quadrant of the OI/price matrix — fresh institutional flow with directional conviction. Otter rides that flow for 1-3 hours then exits via DSL hard timeout.

## What's novel

The fleet uses OI as a **snapshot filter** (size threshold). **Otter is the first agent to track OI delta over time** — a uniquely perp-native signal that no other Senpi agent computes. Confirmed by grep across the repo: every existing OI reference is a single-tick read.

## v1.0 architecture

Built v2-runtime-native from day 1 — no v1 to migrate from.

| Layer | Implementation |
|---|---|
| Trading loop | Producer pushes signals via `external-scanner ingest`; runtime owns execution |
| Entry gate | LLM pass-through (producer already filtered) |
| Entry order | FEE_OPTIMIZED_LIMIT, `ensure_execution_as_taker: false` (cancel-and-skip if maker can't fill) |
| Exit order | FEE_OPTIMIZED_LIMIT, `ensure_execution_as_taker: true` (taker fallback as safety) |
| Risk gates | Declarative `runtime.risk.guard_rails` |
| Position lifecycle | Runtime DSL (Phase 1 max_loss 12% / Phase 2 ladder 5/30, 10/55, 15/75, 20/85) |

## Why OI velocity is a real edge

| OI direction | Price direction | Interpretation | Otter trade |
|---|---|---|---|
| **OI ↑** | Price ↑ | New LONGS entering with conviction | LONG (follow flow) |
| **OI ↑** | Price ↓ | New SHORTS entering with conviction | SHORT (follow flow) |
| OI ↓ | Price ↑ | SHORT covering — exhaustion | SKIP (Pangolin/Owl territory) |
| OI ↓ | Price ↓ | LONG unwinding — exhaustion | SKIP (Pangolin/Owl territory) |

Otter only trades the **TOP** quadrants. Bottom quadrants are unwinds that other agents (Pangolin, Owl) already work.

## Differentiation

- **vs Pangolin (funding fader):** Otter trades growth of positioning; Pangolin trades cost of carry. Otter holds 1-3h; Pangolin 24-48h.
- **vs Mantis (cross-asset lag):** Otter is asset-agnostic; Mantis is BTC-led.
- **vs Bald Eagle (XYZ alpha):** Otter is crypto-only; Bald Eagle is XYZ-only.
- **vs Roach / Vulture / Cheetah (SM scanners):** SM = WHO is positioned. OI velocity = HOW MUCH new leverage is entering. Independent signals.

See [`SKILL.md`](SKILL.md) for full setup, env vars, behavior expectations, and bootstrap notes.

## License

MIT — Built by Senpi (https://senpi.ai).
