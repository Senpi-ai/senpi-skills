# Coin symbols must be LIVE instruments — the `xyz:` prefix rule

**Every coin you name — a scan's `signal.asset`, a raw `positions[].coin`, a hardcoded whitelist — must be
an exact live Hyperliquid instrument.** Get it wrong and the order is REJECTED. On the raw-create path the
whole strategy is then marked **FAILED after funding**, and the capital sits parked in the sub-wallet until
someone recovers it.

> **The incident this exists to prevent (M404726):** coins were passed as `NVDA`, `AVGO`, `SKHX` instead of
> `xyz:NVDA`, `xyz:AVGO`, `xyz:SKHX`. Every position rejected → three strategies marked `FAILED` → ~$6,700
> parked in sub-wallets. Root cause: the `xyz:` prefix was omitted and nothing dry-ran the coins before funding.

## The rule
- **Crypto perps (main DEX):** plain symbol — `BTC`, `ETH`, `SOL`, `HYPE`.
- **XYZ DEX (equities, indices, metals, commodities):** **MUST carry the `xyz:` prefix** — `xyz:NVDA`,
  `xyz:AVGO`, `xyz:XYZ100`, `xyz:BRENTOIL`, `xyz:GOLD`. A bare `NVDA` is **not** a live instrument; it rejects.
- The source of truth for what's live is **`market_list_instruments`** — never emit a symbol from memory.

## Validate BEFORE money moves — by path

**Runtime strategies (`deploy.py`):** the pre-fund **smoke gate** runs `scan()` once and cross-checks every
emitted `asset` against the live instrument list; a bare `NVDA` is **BLOCKED before any wallet is funded**
(`did you mean 'xyz:NVDA'?`). Nothing to do — just don't `--no-smoke` past a real block.

**Raw custom strategies (`strategy_create_custom_strategy` / `create_position`):** these run through **no**
deploy gate — you must check first:
1. **Dry-run** with `estimate_custom_strategy_positions_opening` (read-only; places no orders). A bad coin
   shows up as a failed/insufficient position. Only create once the estimate is clean.
2. Or preflight the coin list directly:
   ```
   python3 senpi-strategy-ops/scripts/validate_universe.py --coins xyz:NVDA,AVGO,BTC
   ```
   → flags `AVGO` with `→ did you mean 'xyz:AVGO'?` and exits non-zero. Fix, then create.

## If a strategy already FAILED
The funds are **NOT lost.** They sit `withdrawable` on-chain in the strategy sub-wallet — check with
`strategy_get_clearinghouse_state`. `FAILED` is a Senpi *record* status, not an on-chain freeze; Senpi
custodies the wallet key, so it's recoverable via the platform's failure-refund (which it queues) or an ops
withdrawal. **Never conclude "locked/lost"** from a `FAILED` status — read the chain first.
