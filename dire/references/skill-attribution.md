# Skill Attribution — DIRE v1.0

## Author

Jason Goldberg — Senpi CEO / co-founder

## Lineage

- **Base skill:** Kodiak v5.0 (SOL specialist)
- **Execution pattern:** Wolverine pattern (Jaguar v3.2 operator originated)
- **DSL engine:** Senpi dynamic-stop-loss runtime
- **MCP toolkit:** Senpi MCP (mcporter CLI)

## Family

Kodiak-family single-asset specialists:

| Agent | Asset | DEX | Status |
|---|---|---|---|
| Kodiak v5.0 | SOL | Main | Proven green |
| Polar v3.1 | ETH | Main | Kodiak-family compliant |
| Grizzly v3.0 | BTC | Main | Kodiak-family compliant |
| Wolverine v3.0 | HYPE | Main | Spec written, pending implementation |
| **Dire v1.0** | **BRENTOIL** | **XYZ** | **First XYZ specialist — experimental** |

## Design decisions specific to Dire

1. **3x leverage cap** (vs 5x crypto ports) — oil vol tail risk
2. **30% max margin** (vs 25% crypto) — Dire runs max 1 position, concentration OK
3. **ISOLATED margin forced** — XYZ DEX requirement
4. **60s scan cadence** (vs 3min crypto) — oil news breaks faster
5. **Aggressive early DSL lock** (T0 at +5%→25% HW) — oil reverses hard on news overshoot
6. **No funding_regime / funding_history** — not applicable to XYZ
7. **Volume spike scoring** — news-impact proxy unique to Dire
8. **Price cleanliness gate** — rejects entries immediately after adverse wicks
9. **OI velocity flat-path extraction from day 1** — avoids known parser bug from Kodiak-family PR #204
10. **Emergency close on DSL attach failure** — position safety > fee savings

## Negative-lesson inputs (what Dire explicitly avoids)

From `reference_cobra_antipattern.md`:
- Fixed high leverage on every trade (Dire scales with score)
- Multi-asset rotation (Dire is BRENTOIL only)
- No DSL (Dire mandates DSL attach inline)
- No drawdown circuit breaker (Dire has 15% from 7-day peak)
- Short-hold scalping with fee churn (Dire uses DSL trail, let winners run)
- FEE_OPTIMIZED_LIMIT without taker fallback (Dire uses ensureExecutionAsTaker: true)

## Capital provenance

Dire's $1,000 initial budget comes from:
- $393 — returned from Cobra v1.1 shutdown (ROI -60%, architecture antipattern)
- $607 — from Grizzly Horribilis shutdown (thesis failed twice: loose gates -35%, tight gates zero activity)

## Success criteria escalation

If Dire achieves Arena-week green within 30 days, enable the next XYZ specialist:
- Candidates: NVDA, GOOGL, TSLA, COPPER, BZ (nickel)

If Dire fails, conduct architecture autopsy before attempting another XYZ port.
