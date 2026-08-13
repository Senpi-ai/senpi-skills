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

- A small budget against a big trader = **tiny positions.** $100 mirroring a $1M account is ~1/10,000
  of their book — often below the $10 floor, so little or nothing opens. This is the single most common
  complaint ("it only used 5% of my funds", "$3 of $190").
- The **`mirrorMultiplier` is the size knob** (0.1×–4×) and it is **locked after creation** — set it
  deliberately.
- **If what the user actually wants is "use most of my capital in a few concentrated positions"** — the
  most common ask — a raw proportional mirror is the wrong tool; it will always feel too small. That is
  exactly what the **budget-relative templates (Shadow, Remora)** are for: they size to *the user's*
  capital, not the whale's, and open a handful of full-size positions. Steer there the moment a user
  says "use most of my funds / not 5% while 95% sits / 1–3 big orders."

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

## Protection — optional, and it stacks on top
Two valid philosophies — surface both, recommend the default:
- **Follow their exits (the default).** Copy trading means inheriting their risk management — their
  stops become your stops. Adding your own tight stop can knock you out of a trade the trader rides back
  to profit (a real user: *"us putting our own in screwed us"*).
- **Add your own safety net.** A per-position DSL trailing stop (`ratchet_stop_add` — a **real
  Hyperliquid-native** stop, **no runtime needed**) layered on top. This earns its place **when the
  trader runs without stops** (many do — then you are as exposed as they are). Tell the user which case
  they are in.

**Never** tell a user a mirror "can't be protected without a runtime" — it can, per position, with DSL.

## The three ways to deploy a copy
| Way | What it is | Best when |
|---|---|---|
| **Raw hands-on mirror** | You pick one trader; Senpi runs it; the user drives size / slippage / protection | Copy one specific trader and stay in control |
| **Managed mirror template** (Remora · Shadow · Oxpecker · Raptor · Cuckoo) | Set-and-forget: **budget-relative sizing**, **fresh-entry-only**, **auto-DSL on every fill** | Hands-off, sized to *your* capital, protected automatically — **most users** |
| **Custom runtime** | A bespoke copy strategy authored to the user's thesis | Rules the templates don't cover |

The five templates by job: **Remora** (whale cohort) · **Shadow** (2–3 named traders, fresh-entry) ·
**Oxpecker** (one elite trader's single biggest conviction bet) · **Raptor** (traders hot right now) ·
**Cuckoo** (consensus of the top copy strategies).

## Economics — answer "how much do I need" honestly
- **$10 minimum notional per position** (auto-bumped to ~$12). A mirror needs enough budget that
  positions clear that floor *given the trader's size* — see the sizing section; this is why $10–$20
  mirrors of a large trader often do nothing.
- You pay **Hyperliquid trading fees on every mirrored fill**, so copying a hyper-active trader costs
  more in fees over time.
- **Always simulate before funding** (`execution_estimate_position_opening`) — it shows exactly what
  would open, what would be skipped, and the minimum budget needed. The one check that prevents funding
  capital that then barely trades.

## What Senpi can't do here
- **No spot copy** — perpetuals only.
- **No guaranteed profit or income** — you inherit the trader's losses too; never say "safe".
- **Hyperliquid traders only** — not other venues or off-chain desks.
