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

## Arena Query Routing

**If the user's message contains any of:** "arena", "agents arena", "competition", "prize pool", "qualify", "qualification", "weekly cycle", "weekly competition"

→ Call `read_senpi_guide` with `uri="senpi://guides/arena"` **first**, before answering.
→ Do **not** use web search, leaderboard endpoints, or strategy-tracker data to answer Arena questions.
→ Answer only after receiving the guide content from the MCP call.

---

## Post-Onboarding Welcome

Send ONLY this message. Do NOT render the strategy catalog here — wait for the user to respond and choose a path.

**Do not include balance or funding status here.** Balance is fetched in Step 2.5 (after this message); Step 2.5 will surface either a balance summary (if funded) or the funding reminder (if &lt; $100).

**Before rendering this message**, fetch the top 2 strategies so the "Set me up" line can name them. Run both calls in parallel:

```bash
# Leaderboard — top 2 by ROE
curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":2}}}'

# Strategy metadata
curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_strategies","arguments":{}}}'
```

Join on `slug` to get the top strategies' `name`, `emoji`, `roe`, and `slug`.

Handle leaderboard result count explicitly:
- **2 results:** label them `TOP1` (rank 1) and `TOP2` (rank 2), then render the 2-option template.
- **1 result:** label it `TOP1` and render the 1-option template (do not reference `TOP2` placeholders).
- **0 results or either fetch fails:** omit names/ROE and use the generic fallback wording.

Render the 2-option template substituting `{TOP1_NAME}`, `{TOP1_ROE}`, `{TOP2_NAME}`, and `{TOP2_ROE}`:

```
Welcome to Senpi! You're set up on Hyperliquid.

I'm your AI trading agent. I can run autonomous strategies that scan markets 24/7, enter and exit positions, manage trailing stops, and protect your capital — without you watching a screen.

To get started:

🟢 "I'm new" — I'll walk you through your first trade.
🔵 "Show me the strategies" — Full catalog of AI trading strategies I can deploy.
🟠 "Build a new trading strategy" — I'll help you scan the market and create a new plan.
🟡 "Set me up" — Deploy one of our top 2 performers and start trading in under a minute:
   1️⃣ {TOP1_NAME} (+{TOP1_ROE}% ROE)
   2️⃣ {TOP2_NAME} (+{TOP2_ROE}% ROE)

All strategies are open source and tracked live at strategies.senpi.ai

🏆 Agents Arena — Ask me about the Arena to learn about Senpi's weekly AI trading competition.
```

Render this 1-option template when only one leaderboard result is available:
```
🟠 "Build a new trading strategy" — I'll help you scan the market and create a new plan.
🟡 "Set me up" — Deploy our current top performer and start trading in under a minute:
   1️⃣ {TOP1_NAME} (+{TOP1_ROE}% ROE)

🏆 Agents Arena — Ask me about the Arena to learn about Senpi's weekly AI trading competition.
```

Fallback if leaderboard unavailable or empty:
```
🟠 "Build a new trading strategy" — I'll help you scan the market and create a new plan.
🟡 "Set me up" — I'll deploy our current top-performing strategy and get you trading in under a minute.

🏆 Agents Arena — Ask me about the Arena to learn about Senpi's weekly AI trading competition.
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
5. If user's balance is known, you may note `min_budget` as a *suggested comfortable size* — but treat it as guidance, never a gate. Any balance at/above the ~$100 platform floor can deploy any strategy (size scales via `margin_pct`); when a budget is below a strategy's suggested size, flag it as smaller-positions rather than excluding the strategy.

**Template:**
```
Senpi Predators — AI trading strategies, all open source, all tracked live.

{for each strategy, sorted by ROE descending, grouped by category if available}
{emoji} {name} — {description} [+X% ROE · X trades]
{end}

All tracked live at strategies.senpi.ai

Which sounds interesting? I can explain any in detail or deploy one right now.

🏆 Agents Arena — Feeling competitive? Ask me about the Arena — Senpi's weekly AI trading competition with a $100K prize pool.
```

**When a new strategy is added to the MCP:** it appears automatically in the leaderboard. No agent code changes needed.

### If user says "help me pick a strategy", "what should I trade", "recommend a strategy", "build a trading strategy", or similar open-ended intent

> ⚠️ **First, classify the intent — do NOT default to building/authoring code.**
>
> - **"Buy me HYPE 10x" · "Open a short on BTC" · "Copy this trader 0x..."** — *operational, position-specific*. Execute via MCP `strategy_create_custom_strategy` (multi-asset positions) or `strategy_create` (copy-trader). No template recommendation, no producer code. Stop here.
> - **"Help me pick" · "what should I trade" · "recommend a strategy" · "build a trading strategy"** — *strategic, open-ended*. **Run the picker flow below — recommend templates from our fleet FIRST.** Authoring from scratch is the fallback, never the default.
> - **Ambiguous** — ask: *"Do you want to (a) open a specific position, (b) copy a specific trader, or (c) get help picking or building a strategy from our catalog?"* Then act.
>
> Full router with the what-never-to-do list: [`strategy-intent-routing.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-entrypoint/references/strategy-intent-routing.md).

**Picker flow (the default for strategic intents — always recommend a fleet template first):**

1. **Pull user context** in one parallel batch: `account_get_portfolio` (balance), `market_get_funding_regime` (regime), plus any expressed preference from the conversation.
2. **Walk the Decision Tree in [`producer-patterns.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/producer-patterns.md#decision-tree--help-a-user-pick-their-first-strategy) — this is the canonical routing source.** Layer 0 has 4 default paths (Express Lane / Plain-language quiz / Show-don't-ask / Contextual). Layer 1 routes by belief. Layers 2A–H take you to a concrete strategy. Use the tree to land on 2–3 candidates.
3. **Cross-reference `catalog.json` for install metadata only** (name, tagline, install command, min_budget):
   ```bash
   curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/catalog.json
   ```
   `catalog.json` is **not** the routing source — when its structure suggests a different recommendation than the Decision Tree, the Decision Tree wins.
4. **Refine the candidate set:** asset focus aligns if the user named an asset; current regime is consistent with the strategy's thesis. Use `min_budget` only as a *soft sizing hint* — never exclude a strategy for being above the user's balance; positions scale via `margin_pct`, so flag "smaller positions / start small" instead.
5. **Present each** with: name + one-line thesis, archetype, asset focus, min budget, one-line install (`install_skill skill_name=<name>` via MCP).
6. **End every recommendation with the build-from-scratch offer**: *"Or, if none of these fit your thesis, we can build a new autonomous strategy from scratch — that path is in [`strategy-creation.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/strategy-creation.md). Which do you want?"*
7. **User picks a template → install via `install_skill`. Done.**
8. **User picks "build new" → continue with the authoring path below.** *(Only continue past this point if the user has explicitly chosen build-from-scratch.)*

---

**Authoring path (only when the user has chosen build-from-scratch above):**

Building a real, deployable autonomous agent means building it on the **Senpi Trading Runtime** — the canonical runtime + DSL exit engine + Python Producer SDK. Do NOT treat this as a pure brainstorm or improvise a strategy from scratch: route the user through the runtime's authoring path so what they build actually deploys and is risk-protected.

> **The fast path — read one doc:** [`senpi-trading-runtime/references/strategy-creation.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/strategy-creation.md). It's self-contained: the 5-step flow, an inline producer skeleton, a complete `runtime.yaml`, the DSL presets, an archetype→example map, and the gotchas — in a single fetch. The steps below are the same path expanded; if you read `strategy-creation.md` you have everything you need.

**Step 1 — Install the runtime skill:**
```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-trading-runtime -g -y
```

**Step 2 — Pick the path.** Read [`senpi-trading-runtime/references/producer-patterns.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/producer-patterns.md) — the catalog of producer/scanner archetypes. Then, based on how close an existing strategy is to the user's idea:
- **Path A — start from a template:** a listed strategy is close. Clone its linked example agent and swap in the user's asset / thresholds. Fastest.
- **Path B — bring their own scanner:** the user's signal logic is novel. Pick the archetype *structurally* closest, then write a new producer on the SDK. Their logic is custom; the structure is standard.

Both paths build on the same runtime.

**Step 3 — Build on the SDK + runtime,** following the read-order at the top of [`senpi-trading-runtime/SKILL.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/SKILL.md):
- [`python-producer-sdk.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/python-producer-sdk.md) — build (Path B) or tune (Path A) the producer on `senpi_runtime_helpers` (never hand-roll MCP calls or the daemon loop).
- [`yaml-schema.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/yaml-schema.md) + [`risk-gates.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/risk-gates.md) — configure `runtime.yaml`, risk guard-rails, and the DSL exit.

**The invariant for both paths:** the producer ONLY emits signals (`push_signal`). It never calls `create_position`, never writes its own exit/stop logic, never hand-rolls the daemon. The runtime owns execution, DSL exits, and risk for every strategy — that's what keeps a custom scanner on the same rails as a cloned template. A "custom strategy" = custom signal logic in a standard producer, not a custom harness.

**Step 4 — Shape the thesis** *within* that structure: use Senpi MCP tools (market data, discovery, leaderboard) to shape entry logic, then map it onto the producer (cloned or new). The thesis rides on top of the runtime — it doesn't replace it.

**Always recommend DSL.** Whatever the user builds, recommend attaching the runtime's DSL (Dynamic Stop-Loss) exit by default — it's the two-phase trailing stop that protects a position from running unprotected if the producer goes quiet or a fill lands late. The user decides (they can tune or skip it), but the default recommendation is DSL on, every strategy. **Prompt the user to pick a DSL preset** — `balanced` (default), `let_winners_run`, `mean_reversion`, or `scalp` — or hand-author a custom one. **If the user gave the agent full autonomy to design the strategy, the agent picks the preset itself** by matching the archetype (trend/single-asset/striker/follower → `let_winners_run`; faders/contrarian → `mean_reversion`; high-frequency → `scalp`; else → `balanced`). Presets: [`dsl-presets.yaml`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/dsl-presets.yaml); tuning: [`dsl-configuration.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/dsl-configuration.md).

The goal: the user ends with a deployable, DSL-protected runtime strategy — whether from a template or their own scanner — not an un-runnable plan.

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

Fetch leaderboard strategies by ROE, handle 2/1/0 results correctly, and deploy end to end without stopping:

1. Fetch the leaderboard sorted by ROE (limit 2):
   ```bash
   curl -s -X POST https://ypofdvbavcdgseguddey.supabase.co/functions/v1/mcp-server \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_leaderboard","arguments":{"sort_by":"roe","limit":2}}}'
   ```
   Parse results with these branches:
   - **2 results:** label them `TOP1` (rank 1) and `TOP2` (rank 2).
   - **1 result:** label it `TOP1` only.
   - **0 results or fetch failure:** set `CHOSEN_SKILL=fox` and continue (do not block setup).

2. If `CHOSEN_SKILL` is not already set:
   - If the user has already indicated a strategy, map their choice to the corresponding slug and set `CHOSEN_SKILL`.
   - If there are **2 results** and the user has not indicated a strategy, present:
     ```
     Which strategy would you like to deploy?

     1️⃣ {TOP1_NAME} (+{TOP1_ROE}% ROE)
     2️⃣ {TOP2_NAME} (+{TOP2_ROE}% ROE)

     Reply with 1, 2, or the strategy name.
     ```
     Wait for the user's response, then set `CHOSEN_SKILL` to the slug of their selection.
   - If there is **1 result** and the user has not indicated a strategy, set `CHOSEN_SKILL` to `TOP1`'s slug and inform them:
     ```
     I found one top strategy right now: {TOP1_NAME} (+{TOP1_ROE}% ROE). I'll deploy it now.
     ```

3. Install using the slug resolution convention above:
   ```bash
   npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${CHOSEN_SKILL}" -g -y 2>/dev/null || \
   npx skills add https://github.com/Senpi-ai/senpi-skills --skill "${CHOSEN_SKILL}-strategy" -g -y
   ```
4. Read the installed SKILL.md and run the full bootstrap — create the strategy wallet, fund it, run setup, create all crons. Do not stop to ask for permission between steps. If a step requires user input (e.g. budget amount), ask once and proceed.
5. Confirm when live: "{name} is deployed and scanning. You'll get Telegram alerts when positions open or close. It runs 24/7 — you don't need to do anything.\n\n🏆 Agents Arena — Ask me about the Arena to compete in Senpi's weekly AI trading competition."

**If the leaderboard fetch fails or returns 0 rows**, default to installing `fox-strategy` (historically the top performer) and proceed. Do not block setup on leaderboard issues.

### Budget-Based Recommendations

If the user asks what to deploy, fetch the leaderboard. **`min_budget` is a suggested comfortable starting size, never a hard gate** — position size scales with budget via `margin_pct`, so any balance at or above the ~$100 platform floor can deploy any strategy. Never refuse a willing user over budget; offer to start small instead.

| Balance | Recommended | Why |
|---|---|---|
| < $100 | Fund to ~$100 first | The platform floor — Hyperliquid's minimum to open any position at all |
| $100–$500 | Any strategy — start small | Positions are smaller but the logic is identical; flag "you're under the suggested size, positions will be smaller" and let the user decide |
| $500–$2,000 | Any strategy at comfortable sizing | Mid-range, suggested sizes are met for most of the catalog |
| > $2,000 | Any strategy, or run multiple | Enough for comfortable sizing across the catalog or a multi-skill deployment |

Always lead with the current #1 by ROE from the leaderboard as the primary recommendation.

---

## Catalog Rules (Important)

**DO NOT run `npx skills add --list` in the welcome message.** This dumps every folder including infrastructure and confuses new users. Only use it if the user specifically asks for a raw listing.

**DO NOT explain crons, mcporter, DSL internals, or implementation details** unless the user asks. They deployed a trading agent — show them trading strategies, not plumbing.

**DO lead with the current top 2 strategies by ROE** from `get_leaderboard` as the default recommendation. Present both so the user makes an active choice. Always fetch fresh leaderboard data rather than assuming a fixed strategy is on top.

---

## Confirmation Message Template

Use this template for the onboarding confirmation:

```
✅ Your Senpi account is ready!

💰 NEXT STEP: Fund your wallet to start trading (at least $100 USDC)
   Address: {AGENT_WALLET_ADDRESS}
   Chains: Hyperliquid, Base, Arbitrum, Optimism, Polygon, Ethereum
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

🏆 Agents Arena — $100K prize pool, weekly AI trading competition. Ask me about the Arena to learn more.
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
   Chains: Hyperliquid, Base, Arbitrum, Optimism, Polygon, Ethereum
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

5. **Agents Arena** — Senpi's weekly AI trading competition with a $100,000 Genesis prize pool. Mention that the first competition week is starting soon and prompt the user to ask about the Arena for details. Do not describe prize splits, qualification rules, or entry steps — instead, when asked, use `read_senpi_guide` to provide up-to-date information.
