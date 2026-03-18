# Post-Onboarding Reference

<!-- Used by senpi-entrypoint Step 2 (Welcome). Keep in sync with senpi-onboard/references/post-onboarding.md when updating welcome or catalog content. -->

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

**Do not include balance or funding status here.** Balance is fetched in Step 2.5 (after this message); Step 2.5 will surface either a balance summary (if funded) or the funding reminder (if &lt; $100).

**Before rendering this message**, fetch the top strategy so the "Set me up" line can name it. Run both calls in parallel:

```bash
# Leaderboard — top 1 by ROE
curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":1}}}'

# Strategy metadata
curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_strategies","arguments":{}}}'
```

Join on `slug` to get the top strategy's `name`, `emoji`, `roe`, and `slug`. If either fetch fails, omit the name/ROE and fall back to the generic wording.

Render the template substituting `{TOP_NAME}` and `{TOP_ROE}`:

```
Welcome to Senpi! You're set up on Hyperliquid.

I'm your AI trading agent. I can run autonomous strategies that scan markets 24/7, enter and exit positions, manage trailing stops, and protect your capital — without you watching a screen.

To get started:

🟢 "I'm new" — I'll walk you through your first trade.
🔵 "Show me the strategies" — Full catalog of AI trading strategies I can deploy.
🟡 "Set me up" — I'll deploy {TOP_NAME} (+{TOP_ROE}% ROE), our current top performer, and get you trading in under a minute.

All strategies are open source and tracked live at strategies.senpi.ai
```

Fallback if leaderboard unavailable:
```
🟡 "Set me up" — I'll deploy our current top-performing strategy and get you trading in under a minute.
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
5. **Next steps** — Recommend deploying the current top-performing strategy (fetch via `get_leaderboard` sorted by ROE, pick #1) as their first autonomous strategy

### If user says "Show me the strategies"

Fetch live strategy data from the senpi-agent-tracker MCP. Do NOT hardcode skill names. Do NOT show this unless the user asked.

**How to render:**
1. Run both calls in parallel:

   ```bash
   # All strategy metadata (name, slug, emoji, tagline)
   curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_strategies","arguments":{}}}'

   # Live performance data
   curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":50}}}'
   ```

2. Include all strategies from `list_strategies`. Build a leaderboard lookup map: `slug → { roe, totalTrades }`.
3. For each strategy, join leaderboard data on `slug`. Sort active strategies by ROE descending first, then paused strategies (by ROE descending) at the bottom.
4. For each strategy, show: `{emoji} {name} — {tagline}` and append live stats if available: `+X% ROE · X trades`. Append `(Paused)` for `active: false` strategies. Do NOT show dollar PnL amounts — percentages only. Do NOT omit trade counts for paused strategies — show their historical data.
5. If user's balance is known and `min_budget` is available in the leaderboard response, highlight strategies where `min_budget <= balance` and note which ones need more capital.

**Template:**
```
Senpi Predators — AI trading strategies, all open source, all tracked live.

{for each strategy, sorted by ROE descending, grouped by category if available}
{emoji} {name} — {description} [+X% ROE · X trades]
{end}

All tracked live at strategies.senpi.ai

Which sounds interesting? I can explain any in detail or deploy one right now.
```

**When a new strategy is added to the MCP:** it appears automatically in the leaderboard. No agent code changes needed.

---

### Skill Installation — Slug Resolution

Use this whenever installing any strategy skill, regardless of how the user triggered it ("Set me up", picked from catalog, budget recommendation, etc.).

The leaderboard returns a `slug` (e.g. `fox`, `tiger`, `ghost-fox`). The `npx skills add` command requires the skill folder name, which sometimes differs. Resolve as follows:

```bash
# Try the slug directly first
npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${SLUG}" -g -y 2>/dev/null || \
# Fall back to {slug}-strategy
npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${SLUG}-strategy" -g -y
```

This covers all known cases: slugs that match directly (e.g. `fox`, `viper`, `cobra`) and slugs that need the `-strategy` suffix (e.g. `tiger` → `tiger-strategy`, `ghost-fox` → `ghost-fox-strategy`, `mamba` → `mamba-strategy`).

---

### If user says "Set me up" or "skip tutorial"

Fetch the leaderboard to identify the current top-performing strategy, then deploy it immediately — go through the full setup end to end without stopping:

1. Fetch the leaderboard sorted by ROE to identify the #1 strategy:
   ```bash
   curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":1}}}'
   ```
   Extract the top strategy's `slug` and `name`. Call the slug `TOP_SKILL`.

2. Install using the slug resolution convention above:
   ```bash
   npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${TOP_SKILL}" -g -y 2>/dev/null || \
   npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${TOP_SKILL}-strategy" -g -y
   ```
3. Read the installed SKILL.md and run the full bootstrap — create the strategy wallet, fund it, run setup, create all crons. Do not stop to ask for permission between steps. If a step requires user input (e.g. budget amount), ask once and proceed.
4. Confirm when live: "{name} is deployed and scanning. You'll get Telegram alerts when positions open or close. It runs 24/7 — you don't need to do anything."

**If the leaderboard fetch fails**, default to installing `fox-strategy` (historically the top performer) and proceed. Do not block setup on a failed leaderboard call.

### Budget-Based Recommendations

If the user asks what to deploy, fetch the leaderboard and filter by `min_budget` if that field is available in the response. Recommend based on their balance:

| Balance | Recommended | Why |
|---|---|---|
| < $500 | None — fund to $500+ | Check leaderboard for any strategies with min_budget below $500 |
| $500–$2,000 | Top strategies with min_budget ≤ balance (from leaderboard) | Mid-range, multiple options available |
| $2,000–$5,000 | Any strategy from leaderboard | Full catalog available |
| > $5,000 | Highest min_budget strategies or run multiple | Enough for high-leverage or multi-position strategies |

Always lead with the current #1 by ROE from the leaderboard as the primary recommendation.

---

## Catalog Rules (Important)

**DO NOT run `npx skills add --list` in the welcome message.** This dumps every folder including infrastructure and confuses new users. Only use it if the user specifically asks for a raw listing.

**DO NOT explain crons, mcporter, DSL internals, or implementation details** unless the user asks. They deployed a trading agent — show them trading strategies, not plumbing.

**DO lead with the current #1 strategy by ROE** from `get_leaderboard` as the default recommendation. Always fetch fresh leaderboard data rather than assuming a fixed strategy is on top.

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
