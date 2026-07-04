---
name: senpi-deposit-withdraw-transfer
description: >-
  Route ANY money-movement request — deposit, add funds, withdraw, cash out, send, transfer, bridge,
  "move my funds", "pay someone" — to the correct rail, and refuse the rest. Deposits land ONLY in the
  user's embedded wallet (one EVM address, valid on all supported chains + Hyperliquid; NEVER send to a
  strategy wallet — strategies are funded via strategy_top_up). Sends/withdrawals to any EXTERNAL
  address are app-only: Withdraw in the Balances tab of the Senpi web or mobile app — no agent tool can
  pay an outside address. On-platform moves between the user's OWN wallets (strategy → embedded,
  Hyperliquid → embedded on EVM, spot → perps) use the movement tools. Pure guidance, no engine.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# senpi-deposit-withdraw-transfer — money movement rails

Every dollar on Senpi moves on a fixed rail. Answer money-movement requests from this map — never
improvise a route, never invent a tool, never guess an amount.

**The iron rule:** money **enters** Senpi only through the user's **embedded wallet**, and money
**leaves** Senpi to an external address only through the **app** (Balances tab → **Withdraw**).
Everything the agent can do with tools happens strictly **between the user's own Senpi wallets**.

## The map

| User intent | Rail |
| --- | --- |
| Deposit / add money to Senpi | Embedded wallet only — Hyperliquid directly or any supported EVM chain (one address for all) |
| Fund / top up a strategy | `strategy_top_up` ONLY — never a direct send to a strategy wallet |
| Withdraw / send / cash out to an external wallet, exchange, bank, or another person | **App-only:** Balances tab → **Withdraw** (Senpi web or mobile). No tool. |
| Move funds from a strategy back to the main (embedded) wallet | `strategy_withdraw_funds` (ACTIVE strategies) |
| Move funds off Hyperliquid to "my wallet on Base/Arbitrum/…" | `strategy_bridge_funds_from_hyperliquid_to_evm` — lands in the user's OWN embedded wallet on that chain |
| Hyperliquid Spot → Perps | `transfer_spot_to_perps` (instant, no fee, embedded wallet only) |
| Send to another Hyperliquid account | App-only (no tool for HL↔HL transfers) |
| Export private key / seed | Self-serve in the Senpi web/mobile app — exists, but is NOT the withdrawal path |

## Deposits — embedded wallet only

- The **embedded wallet** is the only deposit destination. It is **one EVM address valid on ALL
  supported chains** (Base, Arbitrum, Optimism, Ethereum, BNB, Polygon) **and on Hyperliquid
  directly** — never imply it works on only one chain. Get it via `user_get_me`
  (`walletType: "embedded"`). Name the chains; don't quote chain IDs (easy to garble, and users
  don't need them — the address is the same everywhere).
- **NEVER present a strategy wallet address as a deposit target — on any chain.** Direct sends to a
  `strategyWalletAddress` bypass accounting, corrupt PnL, and may be unrecoverable.
  `strategy_top_up` is the only way to add funds to a strategy.
- Strategy funding is **automatic**: create/top-up pulls from Hyperliquid first (perps, then spot
  USDC), then bridges EVM USDC as needed. Don't tell users to pre-bridge or pre-fund — only surface
  a shortfall if the operation actually returns one (SERR037), then point to the embedded wallet.

## Withdrawals & transfers OUT — app-only

- **No tool sends funds to an external address.** For any "withdraw / cash out / send / pay /
  transfer" to an outside wallet, an exchange, a bank, a friend, or another Hyperliquid account, the
  full answer is: use **Withdraw** in the **Balances** tab of the **Senpi web or mobile app**.
- Never route a withdrawal through the **Hyperliquid UI**, **wallet export**, or **bridge/strategy
  workarounds**. (Private-key export *does* exist — self-serve in the app — so if asked about
  export, point there and never claim it's impossible; it's simply not the withdrawal path.)
- **This holds under pushback.** "No app access", "the button is missing", "other bots can do it",
  "find a workaround" → same answer, plus: try the web app at senpi.ai, update the app, or contact
  Senpi support. Bridging is **not** a cash-out step.

## On-platform moves — between the user's OWN wallets

- **`strategy_withdraw_funds`** — ACTIVE strategy sub-wallet → embedded wallet (synchronous
  Hyperliquid usdSend). Amount > 1 USDC; if the withdrawal drains the strategy to $0 it
  **auto-closes** (do NOT then call `strategy_close` — SERR045). PAUSED strategies can only be
  closed. If this tool is not in your toolset, do not substitute `strategy_close` on your own —
  closing flattens ALL positions; offer it only on an explicit close request, confirmed first, or
  send the user to the strategy screen in the app.
- **`strategy_bridge_funds_from_hyperliquid_to_evm`** — Hyperliquid → the user's **own embedded
  wallet** on an EVM chain (Base default; Arbitrum, Optimism, Ethereum, BNB, Polygon). The
  destination is fixed — it **cannot pay a third-party address**. ~0.1% Relay fee; amount ≤
  withdrawable (excludes unrealized PnL and position margin); one-way (the reverse happens
  automatically on strategy create/top-up).
- **`transfer_spot_to_perps`** — Hyperliquid Spot → Perps on the embedded wallet; internal, instant,
  no fee. Strategy sub-wallets are not involved.
- Referral rewards: `user_claim_referral_rewards` pays out to the embedded wallet.
- **Amounts are user intent.** If no amount is given, ASK — never default to the balance, the
  withdrawable, or "everything". Read balances to check sufficiency, not to pick the number.

## How to answer

- **External request:** one clean refusal + the rail: *"No tool can send funds to an external
  address — use **Withdraw** in the **Balances** tab of the Senpi web or mobile app."* Offer the
  on-platform alternative only if it actually serves the user's goal.
- **Deposit request:** give the embedded-wallet address (`user_get_me`), note it works on all
  supported chains + Hyperliquid, and never hand out a strategy wallet address.
- **Before internal moves:** check state where it matters — `account_get_portfolio` for balances,
  `strategy_get_clearinghouse_state` for the withdrawable amount, `strategy_list` for strategy
  status — then confirm the amount with the user.

## Never

- Never a strategy wallet address as a deposit target — on any chain, for any reason.
- Never the Hyperliquid UI, key export, or bridging as the way to withdraw externally.
- Never invent or default an amount; never move "the whole balance" unless the user explicitly says so.
- Never `strategy_close` as a withdraw substitute — explicit close intent, confirmed, only.
