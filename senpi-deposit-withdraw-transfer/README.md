# senpi-deposit-withdraw-transfer

The money-movement rails skill. Handles every deposit / withdraw / send / transfer / bridge request —
routing on-platform moves to the right tool, and refusing external sends with one consistent,
security-framed answer.

**The rails:**

- **In:** deposits land only in the user's **embedded wallet** (one EVM address, all supported chains +
  Hyperliquid). Strategies are funded via `strategy_top_up` only — never a direct send to a strategy wallet.
- **Out:** sends / withdrawals to any **external** address are **app-only, by design** — the agent can't
  send funds outside Senpi (the external-send tool is removed for user security). The user withdraws /
  transfers / exports their key from **Balances / Wallet** in the Senpi web or mobile app.
- **Within:** moves between the user's own wallets DO use tools — `strategy_withdraw_funds` and
  `strategy_close` (strategy → embedded), `strategy_bridge_funds_from_hyperliquid_to_evm` (own embedded
  wallet only), `strategy_top_up` (embedded → strategy), `transfer_spot_to_perps`.

No engine — pure routing governance. Exists because agents otherwise improvise withdrawal paths that
don't work or misdeliver funds (a strategy address as a deposit target, the Hyperliquid UI, key export,
"bridge through a strategy then close it") instead of cleanly pointing to the app.
