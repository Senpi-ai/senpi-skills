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
  wallets (strategy → funding wallet, spot → perps, close a strategy to reclaim funds) DO use tools. Use
  this skill for every deposit / withdraw / transfer / send question. Pure guidance, no engine.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.3.0"
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
| Fund / top up a strategy | `strategy_top_up` **only** — never the funding card for a strategy, never a direct send to a strategy wallet. |
| **Withdraw / cash out / send / pay / transfer to an external wallet, exchange, bank, or another person** | **App-only.** Say the security line above. No tool. |
| **Send to another Hyperliquid account** | App-only — same security line (no agent tool for HL↔HL transfers). |
| Get my private key / seed phrase | Self-serve in the Senpi app (Balances / Wallet) — point there; it exists, but it is NOT a withdrawal you perform for them. |
| Move a strategy's funds back to the main (funding) wallet | `strategy_withdraw_funds` (ACTIVE) or **close the strategy** (`strategy_close`) to reclaim all of it. |
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
  a **strategy** still needs **$10** to open. So "what's the minimum deposit?" is $8, but "what do I need
  to start trading?" is $10 — quote the $10 whenever the goal is running something.
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
- **Closing a strategy reclaims its funds to the funding wallet.** `strategy_close` flattens ALL of a
  strategy's positions and returns the capital to the funding wallet — so it's always available to move
  money out of a strategy. Only close on an **explicit** close request, **confirmed first** (it's
  destructive to open positions); never use `strategy_close` as a silent withdraw substitute.
- **There is no agent bridge tool — in either direction.** Moving funds off Hyperliquid onto an EVM
  chain is done in the app — do **not** offer to do it, and never name a bridge tool. And EVM balances
  do not bridge in for strategies either: money comes in through the funding card, as USDC on
  Hyperliquid.
- **`transfer_spot_to_perps`** — Hyperliquid Spot → Perps on the funding wallet; internal, instant, no
  fee. Strategy sub-wallets aren't involved.
- **Referral rewards** — `user_claim_referral_rewards` pays out to the funding wallet (call
  `user_get_referral_rewards` first to confirm a non-zero balance).
- **Amounts are the user's intent.** If no amount is given, **ASK** — never default to the balance, the
  withdrawable, or "everything." Read balances to check *sufficiency*, not to pick the number.

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
