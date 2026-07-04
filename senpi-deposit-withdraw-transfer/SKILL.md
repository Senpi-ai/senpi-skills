---
name: senpi-deposit-withdraw-transfer
description: >-
  Handle ANY money-movement request — deposit, add funds, fund my account, withdraw, cash out, send,
  "send to my wallet / an exchange / a friend", transfer, "move my money", pay someone, bridge, get my
  private key. Two hard rails: money ENTERS Senpi only through the user's embedded wallet (one EVM
  address, all supported chains + Hyperliquid — NEVER a strategy wallet), and money LEAVES Senpi to any
  EXTERNAL address only through the Senpi web/mobile app (Balances/Wallet) — no agent tool can send funds
  outside Senpi, by design, for the user's security. On-platform moves between the user's OWN wallets
  (strategy → embedded, Hyperliquid → embedded on EVM, spot → perps, close a strategy to reclaim funds)
  DO use tools. Use this skill for every deposit / withdraw / transfer / send question. Pure guidance, no engine.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.0.0"
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
   strategy wallet.
2. **Money LEAVES Senpi to an external address only through the app — never an agent tool.** This is a
   deliberate **security** design, not a missing capability. For any withdraw / send / cash-out / pay /
   transfer-out, say **exactly** this and stop:

   > **To protect your security, I can't send funds outside of Senpi for you. You can withdraw or transfer
   > to external wallets — or get your private key — from your Balances / Wallet in the Senpi web or mobile app.**

Everything the agent does with tools happens **strictly between the user's own Senpi wallets**.

## The map

| User intent | Rail |
| --- | --- |
| Deposit / add money / fund my account | **Embedded wallet only** — send on any supported EVM chain, or on Hyperliquid, to the one embedded address (`user_get_me`). |
| Fund / top up a strategy | `strategy_top_up` **only** — never a direct send to a strategy wallet. |
| **Withdraw / cash out / send / pay / transfer to an external wallet, exchange, bank, or another person** | **App-only.** Say the security line above. No tool. |
| **Send to another Hyperliquid account** | App-only — same security line (no agent tool for HL↔HL transfers). |
| Get my private key / seed phrase | Self-serve in the Senpi app (Balances / Wallet) — point there; it exists, but it is NOT a withdrawal you perform for them. |
| Move a strategy's funds back to the main (embedded) wallet | `strategy_withdraw_funds` (ACTIVE) or **close the strategy** (`strategy_close`) to reclaim all of it. |
| Move funds between two strategies | Via the embedded wallet as the hub: `strategy_withdraw_funds` from A → embedded, then `strategy_top_up` B. |
| Move funds off Hyperliquid to "my wallet on Base/Arbitrum/…" | `strategy_bridge_funds_from_hyperliquid_to_evm` — lands in the user's OWN embedded wallet on that chain (cannot target a third party). |
| Hyperliquid Spot → Perps | `transfer_spot_to_perps` (instant, no fee, embedded wallet). |

## Deposits — embedded wallet only

- The **embedded wallet is the only deposit destination.** It is **one EVM address valid on ALL
  supported chains** (Base, Arbitrum, Optimism, Ethereum, BNB, Polygon) **and on Hyperliquid** — never
  imply it works on only one chain. Get it from `user_get_me` (`walletType: "embedded"`). Name the
  chains in plain words; don't quote chain IDs (easy to garble, and the address is the same everywhere).
- **NEVER present a strategy wallet address as a deposit target — on any chain, for any reason.** A
  direct send to a `strategyWalletAddress` bypasses accounting, corrupts PnL, and may be unrecoverable.
  `strategy_top_up` is the **only** way to add funds to a strategy.
- **Strategy funding is automatic.** Create / top-up pulls from Hyperliquid first (perps, then spot
  USDC), then bridges EVM USDC as needed. Don't tell users to pre-bridge or pre-fund — only surface a
  shortfall if the operation actually returns one (`SERR037`), then point them to the embedded wallet.

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
  the mobile app, or contact Senpi support. Bridging is **not** a cash-out.

## On-platform moves — between the user's OWN wallets (these DO use tools)

- **`strategy_withdraw_funds`** — ACTIVE strategy sub-wallet → embedded wallet (synchronous Hyperliquid
  `usdSend`). Amount > 1 USDC; if it drains the strategy to $0 the strategy **auto-closes** — do NOT then
  call `strategy_close` (`SERR045`). **PAUSED** strategies can't be withdrawn from — they must be closed.
- **Closing a strategy reclaims its funds to the embedded wallet.** `strategy_close` flattens ALL of a
  strategy's positions and returns the capital to the embedded wallet — so it's always available to move
  money out of a strategy. Only close on an **explicit** close request, **confirmed first** (it's
  destructive to open positions); never use `strategy_close` as a silent withdraw substitute.
- **`strategy_bridge_funds_from_hyperliquid_to_evm`** — Hyperliquid → the user's **own embedded wallet**
  on an EVM chain (Base default; Arbitrum, Optimism, Ethereum, BNB, Polygon). The destination is fixed to
  the embedded wallet — it **cannot pay a third-party address**. ~0.1% Relay fee; amount ≤ withdrawable
  (which excludes unrealized PnL and position margin — never bridge `total_in_hyperliquid`); one-way (the
  reverse happens automatically on strategy create / top-up).
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
- **Deposit / add funds:** give the embedded-wallet address (`user_get_me`), say it works on any
  supported chain and on Hyperliquid, and never hand out a strategy wallet address.
- **On-platform move:** check state where it matters — `account_get_portfolio` (balances),
  `strategy_get_clearinghouse_state` (withdrawable), `strategy_list` (status) — confirm the amount with
  the user, then use the movement tool.

## Never

- Never present a **strategy wallet address** as a deposit target — on any chain, for any reason.
- Never route an external withdrawal through the **Hyperliquid UI**, **key export**, or a
  **bridge-/strategy-through** workaround. The external rail is the **app**, always.
- Never soften the refusal into "no tool exists" — it's a deliberate **security** choice; say so.
- Never invent or default an amount; never move "the whole balance" unless the user explicitly says so.
- Never use `strategy_close` as a stealth withdraw — explicit, confirmed close intent only.
