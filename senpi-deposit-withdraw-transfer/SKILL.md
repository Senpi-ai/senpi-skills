---
name: senpi-deposit-withdraw-transfer
description: >-
  Handle ANY money-movement request — deposit, add funds, fund my account, "buy USDC", "pay with a card",
  withdraw, cash out, send, "send to my wallet / an exchange / a friend", transfer, "move my money", pay
  someone, bridge, get my private key. Two hard rails: money ENTERS Senpi only through the funding card —
  the agent shows it with show_widget (widget_type "fund_user_wallet"), which opens the app's Fund Your
  Wallet modal (deposit crypto to a network-scoped deposit address, or buy USDC with a card) — the agent
  NEVER writes a deposit address in chat and NEVER points at a strategy wallet; and money LEAVES Senpi to
  any EXTERNAL address only through the Senpi web/mobile app (Balances/Wallet) — no agent tool can send
  funds outside Senpi, by design, for the user's security. On-platform moves between the user's OWN
  wallets (strategy → funding wallet, spot → perps, close a strategy to reclaim funds) DO use tools —
  and this skill owns their mechanics: the perps precheck before a top-up, polling it, a FAILED top-up
  that parked the money in Spot (recover it, never loop), "withdraw everything" as the exact figure,
  and fees stated before money moves. Use this skill for every deposit / withdraw / transfer / send /
  top-up question. Pure guidance, no engine.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.4.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-deposit-withdraw-transfer — money-movement rails

Money on Senpi moves on fixed rails. Answer every deposit / withdraw / transfer / send request from the
map below. **Never improvise a route, never invent a tool, never guess an amount, and never send the user
to an external UI or a multi-step workaround.** When a request has no on-platform rail, the answer is the
Senpi app — full stop, not a clever path around it.

## The two iron rules

1. **Money ENTERS Senpi only through the funding card.** On any deposit / add-money / fund-my-account /
   buy-USDC intent, call `show_widget` with `widget_type: "fund_user_wallet"`. The card opens the app's
   **Fund Your Wallet** modal — **Deposit Crypto** (a network-scoped deposit address with QR) or **Buy
   USDC** (card / Apple Pay / Google Pay) — and deposits land **as USDC on Hyperliquid in the user's own
   funding (embedded) wallet, ready to trade**. The card owns the address, the networks, and the copy:
   **NEVER write a deposit address, QR code, network list, or any wallet address as a deposit target in
   chat** — deposit addresses are network-scoped, and a hand-typed address can lose funds. **Never** a
   strategy wallet. If `show_widget` is not available in this host, point the user to **Fund Your
   Wallet** in the Senpi web or mobile app — still no address in chat.
2. **Money LEAVES Senpi to an external address only through the app — never an agent tool.** This is a
   deliberate **security** design, not a missing capability. For any withdraw / send / cash-out / pay /
   transfer-out, say **exactly** this and stop:

   > **To protect your security, I can't send funds outside of Senpi for you. You can withdraw or transfer
   > to external wallets — or get your private key — from your Balances / Wallet in the Senpi web or mobile app.**

Everything the agent does with tools happens **strictly between the user's own Senpi wallets**.

## The map

| User intent | Rail |
| --- | --- |
| Deposit / add money / fund my account | **The funding card** — `show_widget` (`widget_type: "fund_user_wallet"`). Never an address in chat. |
| **Buy USDC with a card / Apple Pay / Google Pay ("I have no crypto")** | **The funding card too** — its **Buy USDC** tab. Not a refusal; a first-class path. |
| Fund / top up a strategy | `strategy_top_up` **only** — after the perps precheck, then poll `strategy_get_top_up_status`; a FAILED top-up may have parked the money in Spot (see **Topping up a strategy**). Never the funding card for a strategy, never a direct send to a strategy wallet. |
| **Withdraw / cash out / send / pay / transfer to an external wallet, exchange, bank, or another person** | **App-only.** Say the security line above. No tool. |
| **Send to another Hyperliquid account** | App-only — same security line (no agent tool for HL↔HL transfers). |
| Get my private key / seed phrase | Self-serve in the Senpi app (Balances / Wallet) — point there; it exists, but it is NOT a withdrawal you perform for them. |
| Move a strategy's funds back to the main (funding) wallet | `strategy_withdraw_funds` (ACTIVE) or **close the strategy** (`strategy_close`) to reclaim all of it. |
| "Withdraw everything / all of it" from a strategy | `strategy_withdraw_funds` with the **exact available figure** the tool reports — never rounded; on `SERR037` retry once with `details.available`. Dust left in an ACTIVE strategy keeps scanning — offer to close it. |
| Move funds between two strategies | Via the funding wallet as the hub: `strategy_withdraw_funds` from A → funding wallet, then `strategy_top_up` B. |
| Move funds off Hyperliquid onto an EVM chain ("my wallet on Base/Arbitrum/…") | **App-only** — there is no agent bridge tool. Balances / Wallet in the Senpi app. |
| Hyperliquid Spot → Perps | `transfer_spot_to_perps` (instant, no fee, funding wallet). |

## Deposits — the funding card only

There are **two ways money gets in**, and both live in the same card: **send USDC** the user already
holds (the card's **Deposit Crypto** tab), or **buy USDC** with a card (its **Buy USDC** tab). A user
with no crypto at all is not stuck — never tell them they need an exchange account first.

- **Show the card; don't describe the plumbing.** Call `show_widget` with
  `widget_type: "fund_user_wallet"` whenever money should arrive in the user's own Senpi wallet — an
  explicit deposit ask, an empty wallet, or a balance too short for what they asked. The user picks the
  token and network **in the card**, which shows the matching deposit address and QR.
- **NEVER write a deposit address, QR code, or network list in chat.** The deposit address is
  **network-scoped** ("only send USDC on Base to this address") — repeating an address or implying it
  works on any chain can lose funds. Do not read the address out of `user_get_me` either: the wallet
  list there is identity data, not a deposit target.
- **Where deposits land:** as USDC on Hyperliquid in the user's own funding (embedded) wallet, ready to
  trade — no separate transfer step afterwards.
- **Two different minimums — don't merge them.** The **deposit** floor is **$8** (the card's own `MIN $8`);
  a **strategy** still needs **$10** to open (plus the ~$1.50 creation-fee reserve — see **Costs**). So
  "what's the minimum deposit?" is $8, but "what do I need to start trading?" is $10 — quote the $10
  whenever the goal is running something.
- **NEVER present a strategy wallet address as a deposit target — on any chain, for any reason.** A
  direct send to a `strategyWalletAddress` bypasses accounting, corrupts PnL, and may be unrecoverable.
  `strategy_top_up` is the **only** way to add funds to a strategy — and the funding card is **only**
  for the user's own wallet, never for a strategy.
- **Strategy funding draws on the funding wallet only.** Create / top-up pulls from the user's funding
  wallet balance on Hyperliquid (perps, then spot USDC). There is **no bridging from EVM chains** —
  USDC on other networks cannot fund strategies. Don't pre-empt a shortfall; only act if the operation
  actually returns one — `SERR151` (the USDC is on an EVM chain), or `SERR158`/`SERR037` (genuinely
  short) — then show the funding card, and `strategy_top_up` / retry once the deposit lands.

## Buying USDC — the "I have no crypto" path (the card's Buy USDC tab)

The funding card can sell the user their first USDC directly. **This is NOT the security refusal — do
not use the security line here.** Withdrawals are app-only because the agent must not move money out;
buying simply happens in the card's own checkout. Point at it warmly and specifically.

- **Where:** the same funding card (`show_widget`, `widget_type: "fund_user_wallet"`) → the **Buy USDC**
  tab. (The other tab, **Deposit Crypto**, is the send-crypto-in path above.) Without `show_widget`,
  it's **Fund Your Wallet → Buy USDC** in the Senpi web or mobile app.
- **How they pay:** Apple Pay, Google Pay, or card, through Senpi's verified partner. Name those three;
  the partner's own checkout decides what else it accepts, so don't promise a specific method (bank
  transfer, PayPal, a local rail) that the card doesn't name.
- **The user drives it.** The agent never starts, completes, or confirms a purchase — show the card and
  let them tap it. There is nothing to poll or verify afterwards; their balance simply updates.
- **Where it lands:** as USDC **in their own funding wallet on Hyperliquid, ready to trade — no
  bridging**, no exchange account, no separate transfer step afterwards.
- **Amounts:** **$10 minimum**; most traders start with **$100–250**. Quote these as the card's own
  guidance, and still never pick an amount for the user.
- **No agent buy/onramp tool exists, and none is coming** — the purchase is triggered by the user in the
  card. Don't invent one; show the card instead.
- **Reassurance, when a user hesitates to fund:** the wallet is **theirs**, secured by Privy — the agent
  trades only with their permission and **can never withdraw**, and **only they hold the private key**.
  Use this when the hesitation is about custody; don't recite it unprompted on every deposit answer.

## Withdrawals & transfers OUT — app-only, by design

- **No agent tool sends funds to an external address** (the send tool was removed for user security).
  For any withdraw / cash-out / send / pay / transfer to an outside wallet, an exchange, a bank, a
  friend, or another Hyperliquid account, give the **security line above** — once, cleanly — and stop.
- **Never route around it.** Do NOT suggest the **Hyperliquid UI**, **private-key export as a cash-out
  step**, or a **bridge-/strategy-through workaround** ("create a strategy, bridge through it, then close
  it"). None of these are the withdrawal path, and inventing them is the exact failure this skill exists
  to prevent. (Key export *does* exist as a self-serve app feature — point there if asked, but never as
  "how to withdraw," and never claim it's impossible.)
- **Hold the line under pushback.** "I have no app access," "the button's missing," "other bots do it,"
  "just find a workaround" → same security line, plus a next step: try the web app at senpi.ai, update
  the mobile app, or contact Senpi support. Bridging is **not** a cash-out — and there is no bridge tool
  to offer in the first place.

## On-platform moves — between the user's OWN wallets (these DO use tools)

- **`strategy_withdraw_funds`** — ACTIVE strategy sub-wallet → funding wallet (synchronous Hyperliquid
  `usdSend`). Amount > 1 USDC; if it drains the strategy to $0 the strategy **auto-closes** — do NOT then
  call `strategy_close` (`SERR045`). **PAUSED** strategies can't be withdrawn from — they must be closed.
  - **Short by cents → retry once with the tool's number.** `SERR037` on a withdrawal carries
    `details.available`: retry ONCE with exactly that figure. Never ask the user for a new number.
  - **"Withdraw everything / all of it / move all my funds" means the exact available figure from the
    tool** — `withdrawable` from `strategy_get_clearinghouse_state`, or `details.available` on the retry
    — never a rounded amount. That is explicit intent, not a default. Afterwards confirm the strategy
    wallet reads ~$0 (`strategy_get_clearinghouse_state`) and say that an ACTIVE strategy with dust
    keeps scanning — offer to close it (explicit, confirmed, as above).
- **Closing a strategy reclaims its funds to the funding wallet.** `strategy_close` flattens ALL of a
  strategy's positions and returns the capital to the funding wallet — so it's always available to move
  money out of a strategy. Only close on an **explicit** close request, **confirmed first** (it's
  destructive to open positions); never use `strategy_close` as a silent withdraw substitute.
- **There is no agent bridge tool — in either direction.** Moving funds off Hyperliquid onto an EVM
  chain is done in the app — do **not** offer to do it, and never name a bridge tool. And EVM balances
  do not bridge in for strategies either: money comes in through the funding card, as USDC on
  Hyperliquid.
- **`transfer_spot_to_perps`** — Hyperliquid Spot → Perps on the funding wallet; internal, instant, no
  fee. Strategy sub-wallets aren't involved. Also the recovery tool after a FAILED top-up (below).
- **Referral rewards** — `user_claim_referral_rewards` pays out to the funding wallet (call
  `user_get_referral_rewards` first to confirm a non-zero balance).
- **Amounts are the user's intent.** If no amount is given, **ASK** — never default to the balance, the
  withdrawable, or "everything." Read balances to check *sufficiency*, not to pick the number.

## Topping up a strategy — precheck, poll, recover

`strategy_top_up` is async and draws on the funding wallet's **perps** USDC. The worker moves the money
in two legs — perps → Spot on the funding wallet, then Spot → the strategy wallet — so a failure can leave
the money parked in **Spot**. Four rules, in this order:

1. **Precheck the FREE perps figure, then say it.** Two reads:
   - **Free perps USDC — the gate:** `withdrawable` on the `main` side of `strategy_get_clearinghouse_state`
     for the funding wallet. Its address is the `walletType: embedded` entry in `user_get_me` — read it to
     check the balance, **never** to hand out as a deposit address (deposits go through the funding card).
   - **Spot USDC:** `account_get_portfolio` with `forceFetch: true` (the default read is cached for hours);
     its balance fields sit under `data.portfolio`, Spot is `total_spot_usd_in_hyperliquid` (`spot_balances`
     lists the entry). EVM USDC in `token_balances` does not count.
   `total_in_hyperliquid` is the perps **account value** — free USDC *plus* margin locked in open positions —
   so it over-states what a top-up can draw whenever a position is open. It is an **upper bound only, never
   the gate**: if even it is short of the amount, the top-up will certainly fail, so go straight to the
   funding card.
   **The amount must not exceed free perps (`withdrawable`)** — a top-up accepted against too little also
   ends FAILED, and the deposit may still be on an EVM chain or in Spot. State both numbers in one line
   before the call: "Your funding wallet has $X free in perps; topping up $Y." Spot covers the gap →
   `transfer_spot_to_perps` first, then top up. Nothing covers it → the funding card, and top up once the
   deposit lands.
2. **Poll, don't re-submit.** Keep `data.top_up_request.id` and poll `strategy_get_top_up_status` until
   `COMPLETED` or `FAILED`. `PENDING` / `FUNDS_IN_TRANSIT` = keep polling; `totalFunded` stays stale until
   completion. Re-submitting while PENDING is how strategies get double-funded.
3. **FAILED — find the money before you say anything.** The status message can say "no funds moved …
   still in the funding wallet … safe to re-submit" when the first leg *did* run: treat it as a hint, not
   a fact. Re-take both rule-1 reads — free perps (`withdrawable`) and Spot (`account_get_portfolio`,
   `forceFetch: true`) — and compare them with the precheck:
   - **Spot USDC rose by about the top-up amount** → the money is in the funding wallet's **Spot**
     balance. Move it back with `transfer_spot_to_perps` for that amount, then say where it was and
     where it is now: "The top-up failed after its first leg — your $X was in your funding wallet's Spot
     balance. I've moved it back to perps; it's available again." Do **not** re-submit on your own.
   - **Perps and Spot both unchanged** → nothing moved; say "still in your funding wallet's **perps**
     balance."
   - **Anything else, or the read fails** → say what you confirmed and what you couldn't, don't
     re-submit, and hand the top-up request id to Senpi support (`SERR159`/`SERR160`: money may have
     moved).
   **Never say "still in the funding wallet" without naming the balance — perps or Spot.**
4. **At most one re-submit — only if the user asks, and never for the same strategy twice.** Recover the
   Spot leg (rule 3), re-run the precheck (rule 1), then submit once with a **new** idempotency key. A
   second FAILED for the same strategy means **something is wrong that a retry won't fix**. Apply rule 3
   again, say where the money is, and hand the case to **Senpi support with the top-up request id**.
   **Never offer a fresh deploy as the fix for a failed top-up:** it costs another wallet-creation fee and
   leaves the old strategy running. Never a third submit, never a retry loop, never "contact support"
   before the money is located.

| Say | Never say |
| --- | --- |
| "Your funding wallet has $X free in perps; topping up $Y." — before the call | A top-up amount with no free-perps figure next to it; the account value (`total_in_hyperliquid`) quoted as free |
| "The top-up failed after its first leg — your $X was in your funding wallet's **Spot** balance; I've moved it back to perps and it's available again." | "The money is still in your funding wallet" with no balance named; "safe to re-submit" read off the status message |
| "The top-up failed again, so I've stopped retrying. Your $X is in your funding wallet's **perps** (or **Spot**) balance, and Senpi support can trace it with request id …" — after the second FAILED | "Let me try again" a third time; a fresh deploy offered as the fix for a failed top-up; "contact support" before the money is located |
| "Creating a strategy reserves a creation fee — about $1, budgeted as $1.50 per wallet — on top of the $10 minimum." | A fee figure no tool returned |

## Costs — say them before money moves

- **Creating a strategy reserves a creation fee — about $1, budgeted as $1.50 per wallet by the
  minimum-budget math** — so a wallet opens at the $10 floor with ~$11.50 funded. Say it whenever the plan
  creates a wallet.
- **Several wallets → the per-wallet minimum and fee up front:** "N wallets × ($10 minimum + ~$1.50 fee)
  ≈ $Z" — before the budget question, not after a refusal.
- **Quote what the tool returned, nothing else.** A fee, a "funds in transit" figure, or an `available`
  figure in a tool result is quoted as-is. Never invent a fee a tool did not return.
  `transfer_spot_to_perps` is fee-free — that is the tool's contract, not a guess.

## How to answer

- **External send / withdraw / transfer-out:** the **security line**, verbatim, once. Offer an
  on-platform move (e.g. "I can pull it out of a strategy back to your main wallet") only if it genuinely
  serves the goal — never as a backdoor to an external address.
- **Deposit / add funds:** show the funding card (`show_widget`, `widget_type: "fund_user_wallet"`) and
  say the deposit lands in their own wallet ready to trade. Never write an address, never hand out a
  strategy wallet address. If they may not hold crypto yet, mention the card's Buy USDC tab in one
  line — don't make them ask twice.
- **Buy USDC / "can I use a card":** yes — show the funding card and point at its **Buy USDC** tab; card
  / Apple Pay / Google Pay, lands in their own wallet ready to trade. Positive framing, never the
  security line.
- **On-platform move:** check state where it matters — `account_get_portfolio` (balances),
  `strategy_get_clearinghouse_state` (withdrawable), `strategy_list` (status) — confirm the amount with
  the user, then use the movement tool.
- **Top-up:** the perps figure and the amount in one line, call, poll. On FAILED locate the money first
  (Spot rose → `transfer_spot_to_perps` it back), say which balance it is in, and never re-submit on your
  own.
- **"Withdraw everything":** the exact figure from the tool; on `SERR037` retry once with
  `details.available`; confirm ~$0; offer to close the dust.

## Never

- Never write a **deposit address, QR code, or network list in chat** — the funding card owns them, and
  the address is network-scoped. Never read a "deposit address" out of `user_get_me`.
- Never present a **strategy wallet address** as a deposit target — on any chain, for any reason — and
  never show the funding card as a way to fund a **strategy** (that is `strategy_top_up`, from the
  funding wallet).
- Never claim EVM balances **bridge in automatically** — they don't; USDC on other networks cannot fund
  strategies.
- Never route an external withdrawal through the **Hyperliquid UI**, **key export**, or a
  **bridge-/strategy-through** workaround. The external rail is the **app**, always.
- Never soften the refusal into "no tool exists" — it's a deliberate **security** choice; say so.
- Never answer "can I buy USDC with a card?" with the security line, or with "you need an exchange first."
  It's the funding card's **Buy USDC** tab. Never name or invent a buy/onramp tool.
- Never offer to bridge Hyperliquid → EVM yourself; no such tool exists. That move is app-only.
- Never invent or default an amount; never move "the whole balance" unless the user explicitly says so.
- Never use `strategy_close` as a stealth withdraw — explicit, confirmed close intent only.
- Never re-submit a FAILED top-up on your own, never more than once per strategy, never while PENDING —
  and never say "still in your funding wallet" without naming **perps** or **Spot**.
- Never ask the user for a new number when `SERR037` returned `details.available` — retry once with that
  figure. Never round "everything" — it is the exact available figure.
- Never invent a fee a tool did not return; never leave the creation fee out of a multi-wallet plan.
