---
name: senpi-trade
description: >-
  Execute a DIRECT trade with the user — a one-off manual position (open / edit /
  close) or mirroring a specific Hyperliquid trader — ONE decision at a time. Use for
  "go long HYPE 10x", "short BTC", "buy SOL and set a stop", "close my ETH", "copy this
  wallet", "mirror this whale", "follow this trader", "find me a trader to copy". Both
  paths can carry protection and it is OPTIONAL: bare, a static stop/TP, or a DSL
  trailing stop — DSL is NOT runtime-only, `ratchet_stop_add` attaches a real
  HL-native trailing stop to any open position (manual or mirrored) with no runtime
  alive. Steer users to a MANAGED strategy when they want ongoing autonomy —
  senpi-strategy-author (custom runtime) or a template via senpi-strategy-discover,
  including the named mirror templates (Remora, Shadow, Oxpecker, Raptor, Cuckoo) that
  size to the user, enter fresh-only, and auto-trail DSL on every fill. Pairs with senpi-trader-research, which finds and vets the trader
  (this skill runs the mirror once one is chosen). NOT for
  authoring a strategy or deploying a template yourself.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trader-research
    - senpi-strategy-discover
---

# Senpi Trade — execute a direct trade *with* the user, one decision at a time

This skill runs the two **direct** trade paths: a **manual one-off position** (open / edit / close)
and a **mirror** of a specific trader. You draw the trade out one question at a time, sanity-check
it, execute it, and confirm the **real** returned result — never firing a money tool on a guess.

## Protection is OPTIONAL — and it is NOT runtime-only

> Both a manual position and a mirror can run **bare, with a static stop/TP, or with DSL** (a
> trailing stop + tiered profit-lock). **DSL is not runtime-only.** `ratchet_stop_add` attaches a
> **real, Hyperliquid-native** trailing stop to any *open* position — manual or mirrored — with no
> runtime alive: it subscribes the position to the price feed and writes the SL server-side, so
> nothing agent-side has to stay running. **Never tell a user "you can't trail-stop this, go build a
> runtime."** Offer protection, let them choose, add it if they want it.

**When a managed strategy / template IS the better answer** — steer there, but for the *real* reason,
not a false DSL monopoly:

- they want it **managed for them going forward** — DSL auto-attached to **every** new fill without
  you babysitting, **budget-relative sizing**, **fresh-entry-only** (no chasing a runner). A raw mirror
  + per-position DSL means *you* wrap each new fill by hand; a template does all of it automatically.

| The user wants… | Route to | Why (real reason) |
|---|---|---|
| Ongoing, hands-off, protected-on-every-fill | **senpi-strategy-author** (custom runtime) | continuous management + DSL on every fill |
| "Something already built" | **senpi-strategy-discover** (100+ templates) | DSL + risk gates + budget-relative sizing built in |
| To **copy / mirror a trader, managed** | **senpi-strategy-discover** → a **named mirror template** | fresh-entry, budget-relative sizing, auto-DSL |
| A genuine one-off, or to mirror **one specific trader hands-on** | **stay here** | direct execution the user is driving |

**The 5 named mirror templates — surface them by name for the copy intent:**
**Remora** (whale-cohort, or name your whales) · **Shadow** (multi-trader fresh-entry, name 2–3) ·
**Oxpecker** (elite conviction — their single biggest concentrated bet) · **Raptor** (hot-streak —
traders winning right now) · **Cuckoo** (copy-the-copiers — consensus of top strategies).

Offer the managed option **once**, then respect a "no." For mirror, the templates fix the exact pains
a raw mirror causes (tiny size, stale entries) — offer one before you reach for a raw `strategy_create`.

---

## Branch A — Manual position (open / edit / close)

### Opening is a FORK — ask which product, never assume
On "go long HYPE 10x" / "buy BTC" / "short NVDA", do **not** just place it. Ask which:
- **(A) A managed strategy** — named, supervised, auto-DSL. → hand to **senpi-strategy-author**. Stop here.
- **(B) A one-off position** — you place it, protection is your call. → proceed below.

> **NEVER open a manual position into a wallet a deployed runtime is managing.** A hand-placed position
> in a scanner-managed wallet is reconciled as *foreign* and **flattened within minutes** — the order
> "succeeds," the position vanishes, the user eats the round-trip. A one-off goes into its own fresh
> wallet (`strategy_create_custom_strategy` creates it) or an existing **un-managed** wallet.

### The interview (one question at a time; pre-fill anything already said)
1. **Asset & direction** — verify the coin against `market_list_instruments` (exact casing — `kPEPE`
   not `KPEPE`; XYZ needs the `xyz:` prefix). Reject unknowns; do not retry them.
2. **Size & leverage** — you set `marginAmount` (USD collateral) + `leverage`; the engine derives size
   (`notional = marginAmount × leverage`). Min notional $10 (auto-bumped to $12). Look up `max_leverage`
   per asset — never hardcode. **Never invent the amount — if unstated, ASK; don't default to the balance.**
3. **Entry** — MARKET (immediate, taker) or FEE_OPTIMIZED_LIMIT (maker, cheaper; add
   `ensureExecutionAsTaker` for a guaranteed fill).
4. **Protection — OPTIONAL, offer all three:** (a) none, (b) a **static** stop/TP (`stopLoss`/`takeProfit`,
   `percentage` XOR `price`; margin-relative %; one fixed trigger, won't trail), or (c) **DSL** via
   `ratchet_stop_add` (trailing + tiered profit-lock, a real HL stop, no runtime needed). Explain the
   difference in one line; let them pick.

Then **replay the full spec, get an explicit "yes"**, and place.

### Execute & manage
- **Open:** `strategy_create_custom_strategy` (fresh wallet + position [+ static SL/TP]) or `create_position`
  (into an existing un-managed wallet). Async — poll `strategy_list` to ACTIVE; **report the real returned
  status**, never assume success.
- **Protect (if chosen):** `ratchet_stop_add` on the open position (asset + tier config; it auto-reads the live position).
- **Edit:** `edit_position` — `targetMargin` is **absolute, not a delta**; a direction flip does NOT carry
  SL/TP over. Partial close = `edit_position` with a lower `targetMargin`.
- **Close:** `close_position` (full only; best-effort cancels resting SL/TP + DSL).

---

## Branch B — Mirror a specific trader

### 0. Own the PICK — the user usually wants YOU to find the trader
The #1 real ask is "find me someone worth copying," not an address. **Delegate the find + vet to the
`senpi-trader-research` skill** — its engine ranks track records and reads each trader's current book;
don't hand-roll `discovery_*` here. Whether the trader comes back from there or the user pastes an
address, hold it to the same bar before you mirror — 2–3 vetted candidates with
**max-drawdown + margin beside win-rate/ROI — never rank by ROI, never ROI alone.** Two things the data
will try to fool you on:

- **A 100% win rate is a warning, not a credential** — it usually means near-zero closed trades or hidden
  unrealised drawdown. **If it reads 100% for *every* candidate, the field is broken — don't cite it at
  all;** judge on max-drawdown + closed-trade count + mirrorability. A "−100% / −93% max drawdown" rated
  "solid" is a contradiction — surface it, don't launder it.
- **Mirrorability is the go/no-go — check it before you recommend anyone.** Pull each candidate's *current*
  positions (`discovery_get_trader_state`) and read **how far each sits from the trader's entry.** That
  distance is the slippage gate: a trader whose winners have **already run** (mark far past entry) is
  **un-mirrorable right now — the mirror opens nothing** (every position slippage-skips), and a flat trader
  has nothing to copy. The "best track record" is often the worst mirror *today* for exactly this reason.
  When the book has already moved, that's the cue to **steer to a fresh-entry template (Shadow / Remora),
  which waits for their *next* open instead of chasing the old book** — or find a trader entering now.

If the user pasted an address, still run **both** checks on it before mirroring.

### Steer the product FIRST — the copy questionnaire (lead with capital use)
Before you run a raw mirror, find the right **shape**. Ask one at a time, pre-fill from the opening ask;
the goal is to land them on **raw mirror / a named template / custom** — offer as peers, recommend the fit.

1. **Capital use — ask this first; it is the #1 pain.** *"Do you want to use **most** of your capital in a
   few concentrated positions, or replicate the trader's exact proportional book?"*
   - *"use most of my funds / a few big orders / not 5% sitting idle"* → **a budget-relative template
     (Shadow / Remora)** — they size to *your* capital and open a few full-size positions, which a
     proportional mirror won't (it tracks the trader's proportions). **Common answer.**
   - *"exactly proportional to the trader"* → **raw mirror** (below), sized via the multiplier. If the
     trader's account **dwarfs** the budget, match a closer-sized trader or raise the multiplier so
     positions clear the $10 floor — a proportional mirror preserves *their* utilization %, it doesn't
     shrink yours.
2. **Hands-on or hands-off?** Drive one trader yourself → **raw mirror**. Set-and-forget, auto-DSL every
   fill → **template**.
3. **What shape of copy?** → one specific trader (**raw**) · a whale cohort (**Remora**) · 2–3 named
   traders, fresh entries only (**Shadow**) · one elite's single biggest conviction bet (**Oxpecker**) ·
   traders hot right now (**Raptor**) · consensus of the top copy strategies (**Cuckoo**) · a rule the
   templates don't cover (**→ senpi-strategy-author**).
4. **Budget** — quote the trader's **computed minimum** (`senpi-trader-research` returns
   `min_mirror_budget`: floor / recommended / full-book at 1×). Recommend at least the **recommended**
   figure; if their ask is below the **floor**, say plainly the mirror would open nothing and offer a
   closer-sized trader, a higher multiplier, or a budget-relative template. The pre-fund sim confirms it.
5. **Protection** — default **follow their exits**; offer an added DSL safety-net, especially if the
   trader runs without stops.

**Route the answer:** template → hand to **senpi-strategy-discover** by name · custom → **senpi-strategy-author**
· raw mirror of one specific trader → continue below. Offer the managed option **once**, then respect a "no."

### How a mirror actually works — explain it from the single source
When the user needs the mechanics — and many do (*"why did it open at a 30% different entry?"*, *"why so
small?"*, *"do I need my own stop?"*, *"spot or perps?"*, *"how much do I need?"*) — explain from
**`references/mirror-trading-explained.md`**, the one source every skill quotes. Always hit: **sizing
reality** (small budget vs big trader = dust; the multiplier is locked; concentrated-use → a template),
**slippage is the entry gate** (too tight opens nothing), **you mirror their exits** (unrealised PnL
doesn't transfer), **protection is optional and stacks** (default: follow their exits). Never paraphrase a
different version of this anywhere.

### Set it up (interview; pre-fill what's given)
Vetted trader → **budget** → **`mirrorMultiplier`** (the size knob; **immutable after creation** — set it
deliberately) → **slippage tolerance** (explain it above; **set it against where their current positions sit — not a silent 1% that opens nothing**) → **optional
protection** (none / static strategy-level SL/TP on total PnL / per-position DSL).

### The hero check — simulate BEFORE funding
Run `execution_estimate_position_opening` at the user's budget × multiplier × slippage **before** creating
anything. It returns, per position, `open` / `skipped(slippage)` / `skipped(budget)` + `minimumBudgetRequired`
— i.e. **exactly what would open for them and at what size.** Show the real **$ and %**. If little would
open, STOP and offer: (a) more budget, (b) a higher multiplier, (c) a trader closer to their size, or
(d) a fresh-entry template (Shadow). This one check prevents the core failure: funding capital that then
barely trades.

### Create + verify — don't fabricate
`strategy_create` with the agreed params. Poll `strategy_list` to ACTIVE, then read
`strategy_get_clearinghouse_state` and confirm positions actually opened. If the wallet is idle past a short
window, **tell the user** and adjust target / budget / multiplier — **do not** close+recreate (see below).

### DSL on the mirror
If they want it: `ratchet_stop_add` per opened position — works, no runtime. If they want it on **every
future fill, hands-off**, that's a template (**Shadow / Remora**) — offer it. Don't hand-wrap 40 fills a day.

---

## The guardrails — every one earned from real mirror-trading churn

| If you're about to… | Don't — because | Do instead |
|---|---|---|
| Fund a mirror without simulating it | It can deploy a **tiny fraction of the budget** — the rest sits idle | Run `execution_estimate_position_opening` first (Branch B) |
| Mirror a whale whose account dwarfs the budget | A small budget on a whale-sized account = **dust** — positions round below the $10 floor | Check trader-account ÷ budget up front; if ~100×+, raise the multiplier or pick a closer-sized trader |
| Tell a user a manual/mirror position "can't be protected without a runtime" | **False** — `ratchet_stop_add` writes a real HL stop with no runtime | Offer DSL; add it per-position if they want it |
| Say funds are "stuck" / "lost" / "file a ticket" | `PENDING_FUNDING` **self-completes**; `FAILED` **auto-refunds** to the embedded wallet | Poll transient states with backoff; check the on-chain balance before any alarm |
| Explain a failure with an "approval gateway / approve again" step | No such step exists — the user clicked a phantom control | Surface the **verbatim** tool error; poll `strategy_list` for the real status |
| Fire a fund-movement tool on partial args | A bridge call with `{amount:0.01}` errored `nan` | Build fund calls from a validated template; never proceed as if funds moved when it errored |
| Rank copy targets by raw ROI | Surfaced 100%-win / −100%-drawdown / 99.6%-margin wallets as "best" | Filter on drawdown + margin + closed-trade count + copyability first |
| Recommend a "top" trader without checking their book is mirrorable | The best track record is often the worst mirror *today* — the winners already ran, so the mirror opens **nothing** | Read current-position **distance-from-entry** first; if it's run, steer to a fresh-entry template |
| Leave slippage at a silent / too-tight default | 1% on a trader whose positions already moved opens **nothing** — the mirror sits flat and looks broken | Set slippage against the trader's current distance-from-entry; warn before funding if nothing would open |
| Close + recreate a mirror to "fix" it not trading | Each round-trip skims ~$1.50 in fees; funds fragment | The fix is **target / budget / multiplier**, not re-create |
| Re-derive state fresh each session and misread it | User had to repeat "you didn't do what I asked" 3× | Persist intent + strategy IDs; **reconcile intended-vs-actual** before replying |

> **State machine is transient, not terminal — *up to a point*.** `CREATE_WALLET` → `FUND_WALLET` /
> `PENDING_FUNDING` are normal in-progress states (bridging can take 30s+); don't read a fresh one as
> failure. `SERR045` ("requires ACTIVE") on a pending strategy means *wait*, not *broken*. `FAILED` money
> is refunded automatically. **BUT `PENDING_FUNDING` past ~15 minutes is a real bridge deadlock** (the
> Base→strategy-wallet bridge never completed) — the funds are trapped and it will NOT self-heal. Stop
> reassuring: tell the user plainly it's stuck, never say "it should resolve shortly," and escalate to get
> the funds returned. (Agents have churned users by promising a stuck deposit would clear when it never did.)

---

## Handoff & boundaries
- **Finding / vetting the trader → `senpi-trader-research`.** It ranks records + reads current books and
  hands the *action* (set up the mirror) back to this skill; you own the mechanics — slippage, sizing,
  the pre-fund sim, execution.
- **Ongoing hands-off management / DSL-on-every-fill / fresh-entry / budget-relative sizing →**
  `senpi-strategy-author` (custom) or a template via `senpi-strategy-discover` (mirror: Remora / Shadow /
  Oxpecker / Raptor / Cuckoo). This skill executes the **direct** trade and can add **per-position** protection.
- **Mirror with *custom rules* the templates don't cover** — a position-count cap ("max 5; if he opens
  more, don't mirror"), a per-position % cap ("each ≤ 5% of my capital"), "don't copy his shorts",
  "capture his adds", or a conditional exit → a **custom mirror runtime** via `senpi-strategy-author`; the
  named templates are the fast path for common shapes, author covers bespoke rules. Note the
  **`mirrorMultiplier` is immutable after creation** — a user who wants to change size live must redeploy;
  there is no in-place edit.
- **"How's my mirror doing?" / compare my mirrors / is my trader still active / why didn't it fire →**
  read **`references/mirror-monitoring.md`** — it composes your mirror's state (`senpi-portfolio` /
  `strategy_get_clearinghouse_state`) with the OG's current book (`senpi-trader-research`) and diffs them
  (are you still tracking, did the OG go idle, which positions drifted). After you create a mirror, tell
  the user they can ask any time.
- **Close / withdraw / rebalance a mirror →** `senpi-strategy-ops` (close a mirror, reclaim funds, top-up,
  or shift budget between mirrors). A mirror **is** a strategy — lifecycle actions live in ops, not here.
- **A pasted address (`I want to mirror trade 0x…`) is the single most common entry** — vet it through
  `senpi-trader-research` (mirrorability + drawdown) **before** mirroring, even when it's a popular wallet
  everyone is asking about. Don't rubber-stamp a hot address.
- **Never** send USDC to an external address (no tool for it — direct the user to the app), and never
  present a strategy wallet as a deposit target.
- Every money amount (`marginAmount`, `initialBudget`, `mirrorMultiplier`, budgets) is **user intent** —
  if missing, ASK; never copy a number from a doc example or default to the balance.

## Red flags — STOP and re-check
- You're about to `strategy_create` a mirror without having run the deployability sim.
- You're about to tell a user a position **can't have a trailing stop without a runtime** (it can).
- You're about to tell the user funds are "stuck" or to "approve again."
- You're about to open a manual position into a wallet a runtime is managing.
- You're about to close+recreate a mirror that "isn't trading."
- You're quoting a trader's ROI/win-rate with no drawdown beside it.
- You're about to recommend mirroring a trader whose current positions have already run past their entry — the mirror would open **nothing**.
- You're about to promise a mirror "will replicate shortly" or a stuck strategy "should resolve" — verify on-chain first; never predict a sync that hasn't happened.

All of these mean: stop, run the check, correct the framing, or route to the managed path.
