# Post-Onboarding Reference

## About Senpi

Senpi is a trading platform on Hyperliquid -- a high-performance perpetual futures DEX.

**What agents can do:**
- Discover profitable traders (Hyperfeed + Discovery)
- Mirror top traders or run custom strategies with full position control
- Trade 200+ crypto perps, plus equities, metals, and indices via XYZ DEX

**Core loop:** spot what's profiting -> validate with data -> trade or copy.

**After the MCP server is active**, call `read_senpi_guide(uri="senpi://guides/senpi-overview")` for the full platform reference (wallets, strategies, tool categories, fees, workflows, gotchas).

---

## Next Steps: First Trade

Once the wallet is funded, the `senpi-getting-started-guide` skill will guide the user through their first trade:

1. **Discovery** -- Find what smart money is trading
2. **Position sizing** -- Understand leverage and risk
3. **Open position** -- Enter a small test trade ($50, 3x)
4. **Monitor & close** -- Take profit or cut losses
5. **Celebrate** -- Suggest next skills to install

To start the tutorial, user says: "let's trade" or "first trade"

To skip: "skip tutorial" (sets state to READY)

---

## Confirmation Message Template

Use this template for the Step 7 confirmation:

```
✅ **Your Senpi account is ready!**

💰 **NEXT STEP: Fund your wallet to start trading (at least $100 USDC)**
   Address: {AGENT_WALLET_ADDRESS}
   Chains: Base, Arbitrum, Optimism, Polygon, Ethereum
   Currency: USDC
   Minimum: $100 to start your first trade

   I'll automatically detect when your wallet is funded and guide you through your first trade.

🔗 **Your referral link:** senpi.ai/skill.md?ref={USER_REFERRAL_CODE}

📁 **Credentials saved to:** ~/.config/senpi/credentials.json

Once your wallet is funded, I'll walk you through your **first trade**! 🚀

📲 **Download the Senpi app:**
   • iOS: https://apps.apple.com/us/app/senpi/id6751465837
   • Android: https://play.google.com/store/apps/details?id=app.senpi.ai&pli=1

💬 **Join our community:** https://t.me/+i41Tfz8n4WxhNDMx

🏆 **Senpi Points** -- Trading on Senpi is rewarding! Ask me about Senpi Points to learn how you earn rewards.

While you fund your wallet, you can browse available skills:
`npx skills add Senpi-ai/senpi-skills --list`
```

**If wallet was generated, append:**

```
⚠️ **Generated Wallet -- Action Required**

Your wallet credentials are stored at: ~/.config/senpi/wallet.json

**Back up this file immediately.** If lost, the wallet and any funds cannot be recovered.
```

---

## Get More from Senpi

Share these with the user after the confirmation message:

1. **Senpi mobile app** -- Recommend downloading for on-the-go portfolio monitoring.
   - iOS: https://apps.apple.com/us/app/senpi/id6751465837
   - Android: https://play.google.com/store/apps/details?id=app.senpi.ai&pli=1

2. **Telegram community** -- Invite the user to join for updates, tips, and support.
   - https://t.me/+i41Tfz8n4WxhNDMx

3. **Senpi Points** -- Mention that trading on Senpi earns rewards through Senpi Points. Do not describe the program in detail -- instead, prompt the user to ask about Senpi Points. The agent should use Senpi MCP tools to provide up-to-date information when asked.

---

## Funding Reminder Template (UNFUNDED)

When showing a funding reminder (balance < $100, up to 3 times — or when user says "let's trade" but still unfunded), **always include the agent wallet address** so the user can easily send USDC. Read the address from `state.json` → `account.agentWalletAddress` or `wallet.address`.

**Template:**

```
💰 **Fund your wallet to start trading**
   You need at least **$100 USDC** to start your first trade.

   **Address:** {AGENT_WALLET_ADDRESS}
   **Chains:** Base, Arbitrum, Optimism, Polygon, Ethereum
   **Currency:** USDC

   I'll automatically check your balance on each message and notify you once you're ready to trade.
```

Use this for each of the 3 automatic reminders and when responding to "let's trade" / "first trade" while still UNFUNDED.

---

## Skills Catalog

When user asks about skills, run:

```bash
npx skills add Senpi-ai/senpi-skills --list
```

Popular skills to mention:
- 🛡️ **DSL** -- Trailing stop loss ($100 min)
- 📊 **Scanner** -- Find setups ($100 min)
- 🐺 **WOLF** -- Autonomous trading ($500 min)
- 🐋 **Whale Index** -- Mirror top traders ($500 min)
