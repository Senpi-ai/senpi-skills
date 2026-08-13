# How mirror (copy) trading works on Senpi — the single source

This is the **one** place the "how does copy trading work / what can Senpi do / how much do I need"
answer lives. Every skill routes that question here (senpi-trade, senpi-trader-research,
senpi-strategy-discover, senpi-market-pulse) — never re-explain it differently somewhere else. When a
user asks how it works, hit the load-bearing points below in plain language.

## What it is
You copy a specific Hyperliquid trader into **your own isolated strategy sub-wallet**. **Perpetuals
only — there is no spot copy.** You fund the wallet; Senpi opens and manages positions that track the
trader, and you can stop or withdraw any time.

## How your size is decided — read this first (it is the #1 confusion)
Your position ≈ **(your budget ÷ the trader's account value) × their position × your `mirrorMultiplier`.**

- **Your capital utilization mirrors the trader's, times your multiplier.** If they deploy 40% of their
  account, you deploy ~40% of your budget (× multiplier). A small budget does **not** by itself lower how
  much of your capital gets used — proportional copying *preserves* their utilization %.
- **What leaves capital idle is the $10 per-position floor.** When the trader's account **dwarfs** your
  budget, each of their positions scales below $10 and is **skipped** — so a diversified whale copied with
  a small budget opens only its largest one or two names and the rest of your budget sits. This is a
  trader-size-vs-budget **mismatch**, not the trader under-deploying. Fix it by **raising the multiplier**
  or picking a **closer-sized trader** so positions clear the floor.
- The **`mirrorMultiplier` is the size knob** (0.1×–4×), **locked after creation** — set it deliberately.
- **If the user wants "most of my capital in a few concentrated positions" *regardless* of how the trader
  sizes**, that isn't what a proportional mirror does (it tracks *their* proportions). The
  **budget-relative templates (Shadow, Remora)** do exactly that — size to *the user's* capital and open a
  handful of full-size positions. Steer there for "use most of my funds / 1–3 big orders".
- **Always confirm with the sim** (`execution_estimate_position_opening`) before funding — it shows
  exactly what clears the floor and what gets skipped, so you never guess at utilization.

## How entry works — slippage is the gate
**Slippage tolerance** = how far the *current* price may sit from *the trader's* entry and still open
for you.
- On start, each of the trader's *current* positions opens **only if** it is still within your slippage
  of their entry. Anything that already ran past it is **skipped** (this is why a mirror can open with a
  large entry gap, or open nothing at all).
- **Too tight (e.g. 1%)** on a trader whose positions already moved opens **nothing** — the mirror sits
  flat and looks broken. **Too loose** chases a runner into a worse price than the trader got.
- Set it against where their book actually sits (the mirrorability check), never a silent default.

## What gets mirrored
Their **opens, adds, reduces, and exits** — you close when they close. Their **unrealized PnL does not
transfer**; you enter at the *current* price, not their original entry.

**A mirror only opens when the OG opens.** If they trade rarely or are dormant (sitting on an old
position), your mirror sits idle — that's by design, not a bug. And a trader parked on a big *unrealized*
winner is worth little to copy: you can't inherit their gain, and if the position already ran past their
entry your mirror won't even open it. Say this **before** mirroring a low-activity trader, so an idle
mirror doesn't read as broken.

## Protection — optional, and it stacks on top
Two valid philosophies — surface both, recommend the default:
- **Follow their exits (the default).** Copy trading means inheriting their risk management — their
  stops become your stops. Adding your own tight stop can knock you out of a trade the trader rides back
  to profit (a real user: *"us putting our own in screwed us"*).
- **Add your own safety net — but know its limits without a runtime.** `ratchet_stop_add` adds a
  **profit-lock ladder** (no runtime): it trails a stop **up as you gain**, but places **no downside
  floor** (the Phase-1 max-loss is silently dropped), so you're bare on the losing side until a profit
  tier triggers. For a downside cap, add a **static stop** (`edit_position`) — fixed, doesn't ratchet.
  This matters most **when the trader runs without stops** (many do). For a **real two-phase ratcheting
  DSL** (downside floor → breakeven → profit locks, coordinated), you need a **runtime** — a managed
  mirror template has it built in.

**Don't** tell a user a mirror "can't be protected" — it can (profit-lock + a static SL). **But don't call
that DSL:** the integrated two-phase DSL is runtime-only. For real two-sided protection → a managed template.

## The three ways to deploy a copy
| Way | What it is | Best when |
|---|---|---|
| **Raw hands-on mirror** | You pick one trader; Senpi runs it; the user drives size / slippage / protection | Copy one specific trader and stay in control |
| **Managed mirror template** (Remora · Shadow · Oxpecker · Raptor · Cuckoo) | Set-and-forget: **budget-relative sizing**, **fresh-entry-only**, **auto-DSL on every fill** | Hands-off, sized to *your* capital, protected automatically — **most users** |
| **Custom runtime** | A bespoke copy strategy authored to the user's thesis | Rules the templates don't cover |

Direct-mirror templates (copy specific traders): **Remora** (whale cohort) · **Shadow** (2–3 named
traders, fresh-entry) · **Oxpecker** (one elite trader's single biggest conviction bet) · **Raptor**
(traders hot right now) · **Cuckoo** (consensus of the top copy strategies).

Smart-money-by-signal templates position by where the **whole cohort** leans — many proven traders at
once, not a 1:1 copy of anyone's book: **Stingray** (rotates long/short by net smart-money conviction
across the board) · **Starling** (buys on a flock of top wallets piling into one name) · **Whalehunter**
(with the smart cohort, against the crowd). Use these for *"follow the smart money"* rather than one trader.

## Economics — answer "how much do I need" honestly
- **$10 minimum notional per position** (auto-bumped to ~$12). A mirror needs enough budget that
  positions clear that floor *given the trader's size* — see the sizing section; this is why $10–$20
  mirrors of a large trader often do nothing.
- **For a *specific* trader, state the minimum — don't estimate, and don't recommend a trade size.**
  `senpi-trader-research` computes a per-trader **`min_mirror_budget.min_budget_usd`** — the minimum to run
  it properly — from their live account value + position sizes, the copy-trading analog of a template's
  catalog minimum. Answer "how much do I need for *this* OG" with it as a factual floor; the sim confirms it.
- You pay **Hyperliquid trading fees on every mirrored fill**, so copying a hyper-active trader costs
  more in fees over time.
- **Always simulate before funding** (`execution_estimate_position_opening`) — it shows exactly what
  would open, what would be skipped, and the minimum budget needed. The one check that prevents funding
  capital that then barely trades.

## What Senpi can't do here
- **No spot copy** — perpetuals only.
- **No guaranteed profit or income** — you inherit the trader's losses too; never say "safe".
- **Hyperliquid traders only** — not other venues or off-chain desks.
