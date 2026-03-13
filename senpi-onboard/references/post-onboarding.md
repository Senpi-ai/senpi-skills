<!-- Used by senpi-entrypoint Step 2 (Welcome). Keep in sync with senpi-entrypoint/references/post-onboarding.md when updating welcome or catalog content. -->
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

## Post-Onboarding Welcome

Send ONLY this message. Do NOT render the strategy catalog here — wait for the user to respond and choose a path.

**Do not include balance or funding status here.** Balance is fetched in Step 2.5 (after this message); Step 2.5 will surface either a balance summary (if funded) or the funding reminder (if &lt; $100). Use the template below as-is.

```
Welcome to Senpi! You're set up on Hyperliquid.

I'm your AI trading agent. I can run autonomous strategies that scan markets 24/7, enter and exit positions, manage trailing stops, and protect your capital — without you watching a screen.

To get started:

🟢 "I'm new" — I'll walk you through your first trade.
🔵 "Show me the strategies" — Full catalog of 15+ AI trading strategies I can deploy.
🟡 "Set me up" — I'll deploy FOX, our top performer, and get you trading in under a minute.

All strategies are open source and tracked live at strategies.senpi.ai
```

---

## Strategy Catalog (ONLY when user requests it)

Only render this section if the user explicitly asks — e.g. "show me the strategies", "what strategies are there", "what can you deploy". Do NOT show it as part of the welcome message.

### If user says "I'm new" or "let's trade" or "first trade"

Walk them through the `senpi-getting-started-guide` interactive tutorial:

1. **Discovery** — Find what smart money is trading
2. **Position sizing** — Understand leverage and risk
3. **Open position** — Enter a small test trade ($50, 3x)
4. **Monitor & close** — Take profit or cut losses
5. **Next steps** — Recommend deploying FOX as their first autonomous strategy

### If user says "Show me the strategies"

Build the catalog dynamically from `catalog.json` in the repo root. Do NOT hardcode skill names. Do NOT show this unless the user asked.

**How to render:**
1. Fetch the skill catalog:

   ```bash
   CATALOG=$(curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/catalog.json)
   ```

2. Parse `CATALOG` as JSON (do not read from a local file — use the fetched content)
3. Fetch live performance data from the senpi-agent-tracker:

   ```bash
   curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":15}}}'
   ```

   Parse the response and build a lookup map: `slug → { roe, totalPnl, totalTrades }`.

4. Group by `group` field, using the `groups` array for display order, emoji, and label
5. Sort within each group by `sort_order`
6. For each skill, show: `{emoji} {name} — {tagline}` and append live performance stats if the skill's `tracker_slug` is in the leaderboard map. Format: `+32.1% ROE · 82 trades`. Do NOT show dollar PnL amounts — percentages only. If no live data, omit the performance stat entirely — do not show stale numbers.
7. If user's balance is known, highlight skills where `min_budget <= balance` and note which ones need more capital

**Template:**
```
Senpi Predators — AI trading strategies, all open source, all tracked live.

{for each group in catalog.groups}
{group.emoji} {group.label}:
{for each skill in group, sorted by sort_order}
{skill.emoji} {skill.name} — {skill.tagline} [{+X% ROE · X trades} if live data present, omit if no data]
{end}
{end}

All tracked live at strategies.senpi.ai

Which sounds interesting? I can explain any in detail or deploy one right now.
```

**When we add a new skill:** add one entry to `catalog.json`. The agent picks it up automatically on next onboarding. No agent code changes needed.

### If user says "Set me up" or "skip tutorial"

Deploy FOX immediately — go through the full setup end to end without stopping:

1. Install fox-strategy:
   ```bash
   npx skills add https://github.com/Senpi-ai/senpi-skills --skill fox-strategy -g -y
   ```
2. Read the installed SKILL.md and run the full bootstrap — create the strategy wallet, fund it, run setup, create all crons. Do not stop to ask for permission between steps. If a step requires user input (e.g. budget amount), ask once and proceed.
3. Confirm when live: "🦊 FOX is deployed and scanning. You'll get Telegram alerts when positions open or close. It runs 24/7 — you don't need to do anything."

**Do not tell the user FOX is unavailable or can't be installed as a standalone.** Always attempt the install above and proceed with setup.

### Budget-Based Recommendations

If the user asks what to deploy, recommend based on their balance:

| Balance | Recommended | Why |
|---|---|---|
| < $500 | None — fund to $500+ | No catalog skill has min_budget below $500 |
| $500-$2,000 | FOX, Viper, Cobra, Scorpion, Wolf, Croc, Owl, Cheetah, Hawk, Shark, Dire Wolf | Mid-range, multiple options |
| $2,000-$5,000 | Any skill | Full catalog available |
| > $5,000 | Grizzly, Bison, Tiger, or multi-skill | Enough for high-leverage or multi-position strategies |

---

## Catalog Rules (Important)

**DO NOT run `npx skills add --list` in the welcome message.** This dumps every folder including infrastructure and confuses new users. Only use it if the user specifically asks for a raw listing.

**DO NOT explain crons, mcporter, DSL internals, or implementation details** unless the user asks. They deployed a trading agent — show them trading strategies, not plumbing.

**DO lead with FOX** as the default recommendation. It's the top performer (check live ROE via `get_leaderboard`), includes both copy trading and autonomous mode, and works with any balance above $500.

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
   - strategies.senpi.ai
