# senpi-deposit-withdraw-transfer

The money-movement rails skill. Routes every deposit / withdraw / send / transfer / bridge request to
the correct rail — and refuses the rest with one consistent answer.

**The rails:**

- **In:** deposits land only in the user's **embedded wallet** (one EVM address, all supported chains
  + Hyperliquid directly). Strategies are funded via `strategy_top_up` only — never a direct send to a
  strategy wallet.
- **Out:** sends/withdrawals to any **external** address are **app-only** — Withdraw in the Balances
  tab of the Senpi web or mobile app. No agent tool can pay an outside address.
- **Within:** moves between the user's own wallets use the movement tools —
  `strategy_withdraw_funds`, `strategy_bridge_funds_from_hyperliquid_to_evm` (own embedded wallet
  only), `transfer_spot_to_perps`.

No engine — pure routing governance. Exists because agents otherwise improvise withdrawal paths
(Hyperliquid UI, wallet export, bridge-through-strategy) that don't work or misdeliver funds.
