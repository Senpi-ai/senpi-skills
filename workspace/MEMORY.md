# Memory

Long-term context across sessions.

## User
- <your_name>
- Telegram: @<your_username>, chat ID <your_chat_id>
- Main wallet (embedded): `<your_embedded_wallet>`
- Injected wallet: `<your_injected_wallet>`
- Tiger subwallet: `<your_subwallet>`
- Prefers DSL trailing stops over fixed TP — "You miss the upside with TP"
- Building Tiger as POC for other Senpi users

## Tiger Strategy — v4 Mission 6 ACTIVE
- **Strategy ID:** `cc9a2b85-5379-4685-a354-3bd0615f5620`
- **Wallet:** `<your_strategy_wallet>`
- **Budget:** $3,048 | **Target:** $3,660 (+20%) | **Deadline:** 14 days (Mar 7–21)
- **Aggression:** CONSERVATIVE (~1.3%/day needed)
- **All 12 crons active** as of 2026-03-07 17:03 UTC
- **v4 optimizations applied:** see OPTIMIZATION-PLAN.md

### History
- Mission 1 COMPLETE: $2,999 → $3,554 in 2.5 days (+18.5%, 7W/1L) — best run
- Mission 2-4 RETIRED: Progressively aggressive targets all failed
- Mission 5: Reopened with $3,050 after $1K transfer, closed flat
- Peak balance: $8,099 (Mar 2)

### v3 Config Changes (applied Mar 7)
- **Realistic targets**: 20% over 14 days (~1.5%/day) instead of 8%/day moonshots
- **Tighter risk**: max loss 3% per trade, 8% daily, 15% drawdown (was 5/12/20)
- **2 slots** (was 3) — concentration beats diversification at $3K
- **Higher confluence**: CONSERVATIVE 0.75, NORMAL 0.65, ELEVATED 0.55
- **Tighter trailing locks**: NORMAL 70% (was 60%), CONSERVATIVE 85% (was 80%)
- **Aggression bands lowered**: ABORT at >15%/day (was >25%), CONSERVATIVE <3% (was <8%)
- **Margin sizing fixed**: 30% of balance per slot as MARGIN (not balance÷slots÷leverage)
- **Volume gate**: Momentum entries need volume_ratio ≥ 1.2 (XPL lesson)
- **RSI gates tightened**: 70/30 (was 75/25)
- **DSL T3 added**: 15% trigger → 11% lock (fills the old gap)
- **10 DSL tiers** now (was 9) — smoother profit locking curve

### Key Lessons (distilled)
1. Test exit scripts after EVERY edit — crashes are invisible
2. Exchange SLs first, DSL state second — exchange is safety net
3. 24h correlation signals stale when 4h diverges — check alignment
4. Fees at 10x = 1.8% margin per round-trip — they compound
5. Volume ratio < 1.2 = no conviction for momentum entries
6. Realistic targets (1.5-3%/day) >> moonshots (8%/day)
7. margin_pct sizing >> balance÷slots formula
8. LIMIT SL unreliable — always MARKET orderType for stops

## Senpi MCP Notes
- 48 tools via mcporter, server name "senpi"
- `close_position` needs function-call syntax, not JSON args
- ~6s per asset candle fetch; max 8 assets per 55s scan window
- Always call `read_senpi_guide` with `senpi://guides/senpi-overview` on startup
