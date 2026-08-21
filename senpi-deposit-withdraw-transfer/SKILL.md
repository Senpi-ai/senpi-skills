---
name: senpi-deposit-withdraw-transfer
description: >-
  Handle ANY money-movement request — deposit, add funds, fund my account, "buy USDC", "pay with a card",
  withdraw, cash out, send, "send to my wallet / an exchange / a friend", transfer, "move my money", pay
  someone, bridge, get my private key. Also the authority on how much is needed to start — "what's the
  minimum", "how much do I need", "is $X enough", "can I start small". Two hard rails: money ENTERS Senpi
  only through the user's embedded
  wallet (one EVM address, all supported chains + Hyperliquid — NEVER a strategy wallet), either by sending
  USDC in or by BUYING it in the app with a card, and money LEAVES Senpi to any EXTERNAL address only
  through the Senpi web/mobile app (Balances/Wallet) — no agent tool can send funds outside Senpi, by
  design, for the user's security. On-platform moves between the user's OWN wallets (strategy → embedded,
  spot → perps, close a strategy to reclaim funds) DO use tools. Use this skill for every deposit /
  withdraw / transfer / send question. Pure guidance, no engine.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.2.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-deposit-withdraw-transfer — money-movement rails

Money on Senpi moves on fixed rails. Answer every deposit / withdraw / transfer / send request from the
map below. **Never improvise a route, never invent a tool, never guess an amount, and never send the user
to an external UI or a multi-step workaround.** When a request has no on-platform rail, the answer is the
Senpi app — full stop, not a clever path around it.

## The two iron rules

1. **Money ENTERS Senpi only through the embedded wallet.** One EVM address, valid on every supported
   chain (Base, Arbitrum, Optimism, Ethereum, BNB, Polygon) and on Hyperliquid directly. **Never** a
   strategy wallet. Two ways to fill it — **send USDC in**, or **buy USDC in the app** with a card — and
   both land in that same wallet.
2. **Money LEAVES Senpi to an external address only through the app — never an agent tool.** This is a
   deliberate **security** design, not a missing capability. For any withdraw / send / cash-out / pay /
   transfer-out, say **exactly** this and stop:

   > **To protect your security, I can't send funds outside of Senpi for you. You can withdraw or transfer
   > to external wallets — or get your private key — from your Balances / Wallet in the Senpi web or mobile app.**

Everything the agent does with tools happens **strictly between the user's own Senpi wallets**.

## The map

| User intent | Rail |
| --- | --- |
| Deposit / add money / fund my account | **Show the funding card** — `show_widget` with `widget_type: "fund_user_wallet"`. The card carries the embedded address, the chains and the QR. Never type an address into chat. |
| **Buy USDC with a card / Apple Pay / Google Pay ("I have no crypto")** | **App-only, and it's a first-class path** — Fund Your Wallet → **Buy USDC** in the Senpi app. No agent tool; not a refusal. |
| Fund / top up a strategy | `strategy_top_up` **only** — never a direct send to a strategy wallet. |
| **Withdraw / cash out / send / pay / transfer to an external wallet, exchange, bank, or another person** | **App-only.** Say the security line above. No tool. |
| **Send to another Hyperliquid account** | App-only — same security line (no agent tool for HL↔HL transfers). |
| Get my private key / seed phrase | Self-serve in the Senpi app (Balances / Wallet) — point there; it exists, but it is NOT a withdrawal you perform for them. |
| Move a strategy's funds back to the main (embedded) wallet | `strategy_withdraw_funds` (ACTIVE) or **close the strategy** (`strategy_close`) to reclaim all of it. |
| Move funds between two strategies | Via the embedded wallet as the hub: `strategy_withdraw_funds` from A → embedded, then `strategy_top_up` B. |
| Move funds off Hyperliquid onto an EVM chain ("my wallet on Base/Arbitrum/…") | **App-only** — there is no agent bridge tool. Balances / Wallet in the Senpi app. |
| Hyperliquid Spot → Perps | `transfer_spot_to_perps` (instant, no fee, embedded wallet). |

## Deposits — embedded wallet only

There are **two ways money gets in**, and both land in the same embedded wallet: **send USDC** you already
hold (below), or **buy USDC in the app** with a card (next section). A user with no crypto at all is not
stuck — never tell them they need an exchange account first.

- **Show the funding card; don't type the address.** Call `show_widget` with
  `widget_type: "fund_user_wallet"`. The card owns the address and the QR, and its button opens the
  deposit flow. **Never write a deposit address or a QR into chat**: a hand-typed address is a
  transcription risk on an irreversible action, and the card is always current. Naming the chains in
  words is still right — it is the answer to "does this work on Base only?" — just never quote chain
  IDs, which garble easily and are the same address everywhere.
  If the card is not available on this deployment, the fallback is the app — **Fund Your Wallet →
  Deposit USDC** — not a typed address. The transcription risk does not go away when the tool is missing.
- The **embedded wallet is the only deposit destination.** It is **one EVM address valid on ALL
  supported chains** (Base, Arbitrum, Optimism, Ethereum, BNB, Polygon) **and on Hyperliquid** — never
  imply it works on only one chain. Say that in words; the card shows the address itself. Read
  `user_get_me` only when you need to *verify* an address the user names — never to recite one.
- **NEVER present a strategy wallet address as a deposit target — on any chain, for any reason.** A
  direct send to a `strategyWalletAddress` bypasses accounting, corrupts PnL, and may be unrecoverable.
  `strategy_top_up` is the **only** way to add funds to a strategy.
- **Strategy funding is automatic.** Create / top-up pulls from Hyperliquid first (perps, then spot
  USDC), then bridges EVM USDC as needed. Don't tell users to pre-bridge or pre-fund — only surface a
  shortfall if the operation actually returns one (`SERR037`), then point them to the embedded wallet.

## Buying USDC — the "I have no crypto" path (app, and that's a feature)

The Senpi app can sell the user their first USDC directly. **This is app-only like withdrawals are, but it
is NOT the security refusal — do not use the security line here.** Withdrawals are app-only because the
agent must not move money out; buying is app-only simply because payment happens in the app's checkout.
Point at it warmly and specifically.

- **Where:** Senpi web or mobile app → **Fund Your Wallet** → the **Buy USDC** tab. (The other tab,
  **Deposit USDC**, is the send-crypto-in path above.)
- **How they pay:** Apple Pay, Google Pay, or card, through Senpi's verified partner. Name those three;
  the partner's own checkout decides what else it accepts, so don't promise a specific method (bank
  transfer, PayPal, a local rail) that the app screen doesn't name.
- **The user drives it.** The agent never starts, completes, or confirms a purchase — say the path exists
  and let them tap it. There is nothing to poll or verify afterwards; their balance simply updates.
- **Where it lands:** as USDC **in their own embedded wallet, ready to trade — no bridging**, no exchange
  account, no separate transfer step afterwards.
- **Amounts:** **$10 minimum**, most start with **$100–250** — the app's own onramp guidance, for
  buying or sending USDC in. **Deploying a strategy is a different number**: a little over $10 per
  wallet (~$11.50 — the $10 floor plus the ~$1.50 creation fee), so a wallet funded to exactly $10
  still refuses. Answer the number they actually asked about, and never pick an amount for the user.
- **No agent tool exists for this, and none is coming** — the purchase is triggered by the user on the
  website / app. Don't invent a buy or onramp tool; name the app screen instead.
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

- **`strategy_withdraw_funds`** — ACTIVE strategy sub-wallet → embedded wallet (synchronous Hyperliquid
  `usdSend`). Amount > 1 USDC; if it drains the strategy to $0 the strategy **auto-closes** — do NOT then
  call `strategy_close` (`SERR045`). **PAUSED** strategies can't be withdrawn from — they must be closed.
- **Closing a strategy reclaims its funds to the embedded wallet.** `strategy_close` flattens ALL of a
  strategy's positions and returns the capital to the embedded wallet — so it's always available to move
  money out of a strategy. Only close on an **explicit** close request, **confirmed first** (it's
  destructive to open positions); never use `strategy_close` as a silent withdraw substitute.
- **There is no agent bridge tool.** Moving funds off Hyperliquid onto an EVM chain is done in the app —
  do **not** offer to do it, and never name a bridge tool. (The reverse direction still happens on its
  own: strategy create / top-up bridges EVM USDC in automatically.)
- **`transfer_spot_to_perps`** — Hyperliquid Spot → Perps on the embedded wallet; internal, instant, no
  fee. Strategy sub-wallets aren't involved.
- **Referral rewards** — `user_claim_referral_rewards` pays out to the embedded wallet (call
  `user_get_referral_rewards` first to confirm a non-zero balance).
- **Amounts are the user's intent.** If no amount is given, **ASK** — never default to the balance, the
  withdrawable, or "everything." Read balances to check *sufficiency*, not to pick the number.

## How to answer

- **External send / withdraw / transfer-out:** the **security line**, verbatim, once. Offer an
  on-platform move (e.g. "I can pull it out of a strategy back to your main wallet") only if it genuinely
  serves the goal — never as a backdoor to an external address.
- **Deposit / add funds:** show the funding card (`show_widget`, `widget_type: "fund_user_wallet"`), say
  in words that it works on any supported chain and on Hyperliquid, and never hand out a strategy wallet
  address. If they may not hold crypto yet, add the buy path in one line — don't make them ask twice.
- **"How much do I need?" / "is $X enough?" / "can I start small?":** answer the number first, then show
  the card — a card without the number does not answer what was asked. Which number depends on what they
  are starting: **putting money in** is the $10 / $100–250 guidance above; **deploying a strategy** needs
  ~$11.50 per wallet. If it is unclear which they mean, the deploy number is the safer one to name — it
  is the higher bar, and quoting $10 to someone about to deploy sets them up for a refusal.
- **"Will a deposit to `0x…` land?":** check before you answer, against **two** reads. `user_get_me` →
  their own embedded address → yes, and show the card. `strategy_list` → one of their strategy wallets →
  **no**: it bypasses accounting and may be unrecoverable, and `strategy_top_up` is the way in. Matching
  neither → no. `user_get_me` alone cannot tell a strategy wallet from a stranger's address, so without
  the `strategy_list` read you cannot give the strategy warning — which is the whole point of the check.
  Never answer this one with a bare card.
- **Buy USDC / "can I use a card":** yes — **Fund Your Wallet → Buy USDC** in the app, card / Apple Pay /
  Google Pay, lands in their own wallet ready to trade. Positive framing, never the security line.
- **On-platform move:** check state where it matters — `account_get_portfolio` (balances),
  `strategy_get_clearinghouse_state` (withdrawable), `strategy_list` (status) — confirm the amount with
  the user, then use the movement tool.

## Never

- Never present a **strategy wallet address** as a deposit target — on any chain, for any reason.
- Never type a **deposit address or a QR** into chat — that is the card's job.
- Never present the card as a way to fund a **strategy** — it fills the embedded wallet, and a strategy
  is funded from there by `strategy_top_up`. When a strategy is short *because the embedded wallet is
  short*, that is card first, `strategy_top_up` second — two steps, said as two steps.
- Never route an external withdrawal through the **Hyperliquid UI**, **key export**, or a
  **bridge-/strategy-through** workaround. The external rail is the **app**, always.
- Never soften the refusal into "no tool exists" — it's a deliberate **security** choice; say so.
- Never answer "can I buy USDC with a card?" with the security line, or with "you need an exchange first."
  It's a supported app path — **Fund Your Wallet → Buy USDC**. Never name or invent a buy/onramp tool.
- Never offer to bridge Hyperliquid → EVM yourself; no such tool exists. That move is app-only.
- Never invent or default an amount; never move "the whole balance" unless the user explicitly says so.
- Never use `strategy_close` as a stealth withdraw — explicit, confirmed close intent only.
