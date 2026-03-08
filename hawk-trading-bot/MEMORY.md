# Memory

Long-term context across sessions.

## User
- Jason Goldberg, CEO of Senpi
- Named me Hawk 🦅 on first session (2026-02-26)

## HAWK Strategy (CLOSED)
- Closed Feb 27, 2026 — zero trades in 20+ hours, signals too complex
- Funds returned to embedded wallet

## Multi-Asset Momentum Strategy (Active)
- Strategy ID: `5997521e-db8c-4170-8173-8a42f217c47d`
- Wallet: `0x3da209967a8b14b82e97329e0ccf2a380437ad4e`
- Budget: $999 → current ~$967 | Assets: BTC/ETH/SOL/HYPE
- 4 crons: Scanner v3 (30s), DSL v5.2 (3min), Hedge v2 (60s), Health (10min)
- Skill path: `/data/workspace/recipes/hype-sniper/`
- DSL state dir: `/data/workspace/dsl/5997521e-db8c-4170-8173-8a42f217c47d/`
- ALO fee optimization on entries (FEE_OPTIMIZED_LIMIT)
- Smart money filter via leaderboard_get_markets
- Max 2 concurrent positions
- DSL: auto-adopt orphans, time decay, pyramiding, partial TP at Tier 4 (40% ROE)
- edit_position: targetMargin for add/reduce, stopLoss, takeProfit (native on HL)
- Native TP only in Phase 2, Tier 3+ (learned: tight TP kills winners)
- 5min SL sync delay on fresh entries (learned: instant SL = instant death)
- 1h trend filter blocks counter-trend entries (learned: LONGs into downtrend = death)
