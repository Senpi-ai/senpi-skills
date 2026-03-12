# Post-Onboarding Reference

## About Senpi

Senpi is a trading platform on Hyperliquid — a high-performance perpetual futures DEX.

**What agents can do:**
- Run autonomous AI trading strategies that scan markets 24/7
- Discover and mirror profitable traders (Hyperfeed + Discovery)
- Trade 200+ crypto perps, plus equities, metals, and indices via XYZ DEX

**Core loop:** spot what's profiting → validate with data → trade or copy.

**After the MCP server is active**, call `read_senpi_guide(uri="senpi://guides/senpi-overview")` for the full platform reference (wallets, strategies, tool categories, fees, workflows, gotchas).

---

## Next Steps: First Interaction

Once the wallet is funded, offer the user three paths. Keep the first message short — don't dump the full catalog.

### Welcome Message (use this template)

```
Welcome to Senpi! Your wallet is funded with $[BALANCE] USDC and ready to trade on Hyperliquid.

I'm your AI trading agent. I can run autonomous strategies that scan markets 24/7, enter and exit positions, manage trailing stops, and protect your capital — without you watching a screen.

To get started:

🟢 "I'm new" — I'll walk you through your first trade. Takes 5 minutes.
🔵 "Show me the strategies" — Full catalog of 15+ AI trading strategies I can deploy.
🟡 "Set me up" — I'll deploy FOX, our best performer at +18% ROI, and get you trading in under a minute.

All strategies are open source and tracked live at senpi.ai/predators
```

### If user says "I'm new" or "let's trade" or "first trade"

Walk them through the `senpi-getting-started-guide` interactive tutorial:

1. **Discovery** — Find what smart money is trading
2. **Position sizing** — Understand leverage and risk
3. **Open position** — Enter a small test trade ($50, 3x)
4. **Monitor & close** — Take profit or cut losses
5. **Next steps** — Recommend deploying FOX as their first autonomous strategy

### If user says "Show me the strategies"

Build the catalog dynamically from `catalog.json` in the repo root. Do NOT hardcode skill names.

**How to render:**
1. Fetch the skill catalog:

   ```bash
   CATALOG=$(curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/feature/modify-onboarding/catalog.json)
   ```

2. Parse `CATALOG` as JSON (do not read from a local file — use the fetched content)
3. Group by `group` field, using the `groups` array for display order, emoji, and label
4. Sort within each group by `sort_order`
5. For each skill, show: `{emoji} {name} — {tagline}` and append `{performance}` if present
6. If user's balance is known, highlight skills where `min_budget <= balance` and note which ones need more capital

**Template:**
```
Senpi Predators — AI trading strategies, all open source, all tracked live.

{for each group in catalog.groups}
{group.emoji} {group.label}:
{for each skill in group, sorted by sort_order}
{skill.emoji} {skill.name} — {skill.tagline} {skill.performance if present}
{end}
{end}

All tracked live at senpi.ai/predators

Which sounds interesting? I can explain any in detail or deploy one right now.
```

**When we add a new skill:** add one entry to `catalog.json`. The agent picks it up automatically on next onboarding. No agent code changes needed.

### If user says "Set me up" or "skip tutorial"

Deploy FOX immediately:
1. Install fox-strategy from main branch
2. Agent runs bootstrap (creates all crons automatically)
3. Confirm: "🦊 FOX is deployed and scanning. You'll get Telegram alerts when positions open or close. It runs 24/7 — you don't need to do anything."

### Budget-Based Recommendations

If the user asks what to deploy, recommend based on their balance:

| Balance | Recommended | Why |
|---|---|---|
| < $500 | None — fund to $500+ | No catalog skill has min_budget below $500 |
| $500-$2,000 | FOX, Viper, Cobra, Scorpion, Wolf, Croc, Owl | Mid-range, multiple options |
| $2,000-$5,000 | Any skill | Full catalog available |
| > $5,000 | Grizzly, Bison, Tiger, or multi-skill | Enough for high-leverage or multi-position strategies |

---

## Catalog Rules (Important)

**DO NOT show infrastructure skills in the catalog.** These are shared plugins that every strategy uses automatically. Users don't need to know they exist:
- `dsl-dynamic-stop-loss` — trailing stop engine (used by all strategies)
- `fee-optimizer` — fee optimization (used by all strategies)
- `autonomous-trading` — generic wrapper (users should pick a specific strategy)
- `opportunity-scanner` — FOX's internal scanner
- `emerging-movers` — FOX/WOLF's internal scanner
- `whale-index` — replaced by Scorpion
- `wolf-howl` — WOLF's internal analysis loop

**DO NOT run `npx skills add --list` in the welcome message.** This dumps every folder including infrastructure and confuses new users. Only use it if the user specifically asks for a raw listing.

**DO NOT explain crons, mcporter, DSL internals, or implementation details** unless the user asks. They deployed a trading agent — show them trading strategies, not plumbing.

**DO lead with FOX** as the default recommendation. It's proven (+18% ROI), includes both copy trading and autonomous mode, and works with any balance above $500.

---

## Confirmation Message Template

Use this template for the onboarding confirmation:

```
✅ Your Senpi account is ready!

💰 NEXT STEP: Fund your wallet to start trading (at least $100 USDC)
   Address: {AGENT_WALLET_ADDRESS}
   Chains: Base, Arbitrum, Optimism, Polygon, Ethereum
   Currency: USDC
   Minimum: $100 to start your first trade

   I'll automatically detect when your wallet is funded and guide you through your first trade.

🔗 Your referral link: senpi.ai/skill.md?ref={USER_REFERRAL_CODE}

📁 Credentials saved to: ~/.config/senpi/credentials.json

📲 Download the Senpi app:
   • iOS: https://apps.apple.com/us/app/senpi/id6751465837
   • Android: https://play.google.com/store/apps/details?id=app.senpi.ai&pli=1

💬 Join our community: https://t.me/+i41Tfz8n4WxhNDMx

🏆 Senpi Points — Trading on Senpi earns rewards! Ask me about Senpi Points for details.
```

**If wallet was generated, append:**

```
⚠️ Generated Wallet — Action Required

Your wallet credentials are stored at: ~/.config/senpi/wallet.json

Back up this file immediately. If lost, the wallet and any funds cannot be recovered.
```

---

## Funding Reminder Template (UNFUNDED)

When showing a funding reminder (balance < $100, up to 3 times — or when user says "let's trade" but still unfunded), always include the agent wallet address:

```
💰 Fund your wallet to start trading
   You need at least $100 USDC to start your first trade.

   Address: {AGENT_WALLET_ADDRESS}
   Chains: Base, Arbitrum, Optimism, Polygon, Ethereum
   Currency: USDC

   I'll automatically check your balance on each message and notify you once you're ready to trade.
```

---

## Get More from Senpi

Share these with the user after confirmation:

1. **Senpi mobile app** — Recommend downloading for on-the-go portfolio monitoring.
   - iOS: https://apps.apple.com/us/app/senpi/id6751465837
   - Android: https://play.google.com/store/apps/details?id=app.senpi.ai&pli=1

2. **Telegram community** — Invite the user to join for updates, tips, and support.
   - https://t.me/+i41Tfz8n4WxhNDMx

3. **Senpi Points** — Mention that trading on Senpi earns rewards through Senpi Points. Do not describe the program in detail — instead, prompt the user to ask about Senpi Points. The agent should use Senpi MCP tools to provide up-to-date information when asked.

4. **Senpi Predators** — Live public tracker showing all trading strategies running with real money.
   - senpi.ai/predators
