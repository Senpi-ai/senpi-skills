# Worked examples

Real runs, and how to frame them. The point of each: **define the jargon, weight the signal by its
actual size, lead with what's robust, stay observation-only.**

---

## WLFI — reacting to a bullish headline the market rejected (Aug 15, 2026)

**The trigger:** @unusual_whales — *"World Liberty Financial (the Trump family's crypto venture) got
preliminary OCC approval to become a bank."* Headline reads bullish.

**What the live reads showed** (all sourced: `market_get_asset_data`, `leaderboard_get_markets`,
`market_list_instruments`):
- Price spiked **+8.6%** on the news (~$0.0549 → $0.0596) on **~100× normal hourly volume**, then
  **gave nearly all of it back within ~2h** (back to ~$0.0561).
- **Open interest is draining** (−1.86% / 4h) — positions closing, not building; no fresh conviction.
- Funding is ordinary (~1.4%/yr) — **not** a squeeze. Small market (~$5M 24h volume, ~$11.25M OI).
- Top-trader positioning: the **dominant side is short — but thin**: 24 traders, **1.23%** of
  top-trader PnL concentration.

**The unique Senpi insight:** a price chart shows a spike-and-fade. Only the order flow + the proven
cohort shows *nobody's buying the dip and the smart lean is the other way* — the market read a
bullish headline and **rejected it**.

### The mistake to avoid (what the first draft did)
It **led with the thin signal** — "Hyperliquid's top traders aren't buying it" — resting the whole
tweet on 1.23% / 24 traders. On a $5M market that's low weight; a sharp reader checks it and uses the
overstatement to discredit the account. **Never upgrade "leans short (1.23%)" to "smart money is
short."**

### The corrected framing (lead with the robust, verifiable facts)
> World Liberty spiked +8.6% on the OCC bank-approval headline — ~100× its normal volume — then gave
> nearly all of it back within two hours.
>
> On Hyperliquid: open interest is draining, not building, and the profitable traders who are
> positioned lean short.
>
> The tape read the headline and shrugged.

Why it's right: leads with the **pump-and-fade + OI draining** (independently checkable), uses the
short-lean as **corroboration** ("lean short," honest for 1.23%), no advice, no returns. Before
posting, **refresh the numbers** — funding/OI/price/positioning age fast (staleness rule).

**The lesson, generalized:** robust + checkable facts lead; thin positioning is color. Define every
term (which leaderboard, what "4h", what "% of PnL" means). Observation, never a call.
