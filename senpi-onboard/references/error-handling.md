# Error Handling Reference

## Table of Contents
- [Network Errors](#network-errors)
- [GraphQL API Errors](#graphql-api-errors)
- [Manual Flow Fallback](#manual-flow-fallback)
- [Wallet Generation Failure](#wallet-generation-failure)
- [Missing Node.js](#missing-nodejs)
- [Recovery](#recovery)

---

## Network Errors

If `curl` returns a non-zero exit code or the response is empty:
1. Wait 3 seconds and retry once.
2. If it fails again, inform the user the Senpi API is unreachable and suggest trying later.

---

## GraphQL API Errors

Parse the error from `response.errors[0].message`.

| Error | Cause | Action |
|-------|-------|--------|
| Invalid wallet address | Subject is not a valid 42-char hex Ethereum address starting with `0x` | Ask user for a valid wallet address |
| Invalid from action | `from` field is not `WALLET` or `TELEGRAM` | Use only supported identity types |
| Invalid referral code | Referral code is malformed or nonexistent | Proceed without referral code, or ask user to verify |
| You cannot refer yourself as a referrer | Referral code belongs to the same user | Remove referral code and retry |
| User already exists / User already exists. Search using \<username\> | Identity already has a Senpi account | Direct user to Manual Flow Fallback below |
| Invalid sign up method for user, can't create user | Identity cannot create an account | Try a different identity type |
| User cannot be created | Internal error | Retry once; if it persists, use Manual Flow Fallback |

---

## Manual Flow Fallback

Use when the API returns "User already exists" or automated onboarding fails for any reason.

Tell the user:

> Your identity is associated with an existing Senpi account. Please create an API key manually using the steps below, then share it with me so I can configure the MCP server connection.
>
> **Step 1: Login to Senpi**
> 1. Go to senpi.ai
> 2. Click the Register Agent button
> 3. Login using the same method you use for the Senpi mobile app
>
> **Step 2: Create a New API Key**
> 1. Click New Key
> 2. Enter your preferred key name (e.g., "trading-bot", "openclaw-sniper" "claude-assistant")
> 3. Select your preferred expiry duration (24 hours to 1 year)
> 4. Click Generate to create your API key
>
> Warning: You won't be able to view your API key again after this screen. Copy it immediately.

If the user provides an API key manually, skip to Step 5 (persist) and Step 6 (configure MCP) in the main workflow.

---

## Wallet Generation Failure

If all fallback methods in the wallet generation script fail:
1. Verify Node.js is installed: `node --version`
2. Check npm is functional: `npm --version`
3. Ensure network connectivity for package downloads.
4. If all approaches fail, inform the user and ask them to provide a wallet address or Telegram username instead (fall back to Option A or B).

Do not prompt the user during fallback attempts -- try each method silently before reporting failure.

---

## Missing Node.js

If `npx` is not available, the MCP server cannot be configured (it requires `mcp-remote` via `npx`). Instruct the user to install Node.js:

- macOS: `brew install node`
- Linux: `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs`
- Or visit: https://nodejs.org

---

## Recovery

If the API key or MCP configuration is lost:

1. **Check credentials backup:** `cat ~/.config/senpi/credentials.json`
2. **If credentials file exists:** Re-run Step 6 (Configure MCP Server) using the saved API key.
3. **If credentials file is missing:** Restart onboarding from Step 1. The API will either create a new account or return "User already exists" -- follow the appropriate flow.

**Generated wallet recovery:**
- The wallet private key and mnemonic are stored **only** in `~/.config/senpi/wallet.json`. There is no server-side backup.
- If `wallet.json` is deleted and no external backup exists, the wallet and any funds are **permanently lost**.
- If `credentials.json` shows `"walletGenerated": true`, always verify `wallet.json` exists before proceeding.
