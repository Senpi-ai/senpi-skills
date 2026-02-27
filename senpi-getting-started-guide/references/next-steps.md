# Next Steps Reference

Use this for **celebration** (after close or after monitor-only), **skip tutorial**, and **resume** handling in the first-trade guide.

---

## Celebrate (After Close)

**If profitable:**

> 🎊 **CONGRATULATIONS ON YOUR FIRST TRADE!**
>
> ```
>  ╔═══════════════════════════════════════╗
>  ║   🏆  FIRST STRATEGY COMPLETE  🏆    ║
>  ║      You made: +$X.XX (+X.XX%)       ║
>  ╚═══════════════════════════════════════╝
> ```
>
> **What you learned:** Discovery, mirroring a top trader, creating a strategy, monitoring, and closing.
>
> **Your trading journey begins!** Explore next:
>
> | Skill | What it does | Command |
> |-------|--------------|---------|
> | 🛡️ **DSL** | Auto stop-loss protection | `npx skills add Senpi-ai/senpi-skills/dsl-dynamic-stop-loss` |
> | 🐺 **WOLF** | Fully autonomous trading | `npx skills add Senpi-ai/senpi-skills/wolf-strategy` |
> | 📊 **Scanner** | Find best setups | `npx skills add Senpi-ai/senpi-skills/opportunity-scanner` |
> | 🐋 **Whale Index** | Copy top traders | `npx skills add Senpi-ai/senpi-skills/whale-index` |
>
> **Quick commands:** "find opportunities", "show my portfolio", "open BTC short $100"

**If loss:**

> 📊 **FIRST STRATEGY COMPLETE**
>
> Strategy Result: -$X.XX (-X.X%)
>
> **That's okay!** You kept size small, learned the mirror flow, and closed when you wanted.
>
> **Pro tip:** Install **DSL** for automatic protection: `npx skills add Senpi-ai/senpi-skills/dsl-dynamic-stop-loss`
>
> Explore: DSL, Scanner, WOLF, Whale Index. Say "find opportunities" to discover more traders.

Then update state to `READY` and set `firstTrade.completed`, `firstTrade.step: "COMPLETE"`, `firstTrade.completedAt`. See [references/strategy-management.md](references/strategy-management.md) for the full state shape (strategy flow).

---

## Celebrate (After Monitor — No Close)

If the user completed discovery and created a strategy but only monitored (e.g. asked "how's my strategy?" one or more times) and did not close, still congratulate them so they feel accomplished:

> 🎉 **You've completed your first trade flow!**
>
> You discovered top traders, created a mirror strategy, and checked how it's doing. Nice work!
>
> Your strategy is still running. You can say **"close my strategy"** anytime to close it and bring funds back to your wallet, or keep it open and check back with "how's my strategy?"
>
> **What you learned:** Discovery, mirroring a trader, creating a strategy, and monitoring.
>
> **Next:** Try "show my portfolio", "find opportunities", or install more skills — e.g. **Whale Index** to auto-mirror top traders, or **DSL** for protection.

Then update state to `READY`, `firstTrade.completed`, `firstTrade.step: "COMPLETE"`, `firstTrade.completedAt`. Preserve existing `tradeDetails` (strategyId, etc.); no need for PnL if they didn't close.

---

## Skip Tutorial

When the user says "skip", "skip tutorial", "I know how to trade":

**Display (user-friendly only; no tool names or internal references):**

> 👍 **Tutorial skipped!**
>
> You're all set to trade on your own. Quick reference:
>
> | What you want | Say or do |
> |---------------|------------|
> | Find top traders | "find opportunities" |
> | Mirror a trader | "create a strategy mirroring [trader]" |
> | Check your strategies | "show my portfolio" |
> | Close a strategy | "close my strategy" |
> | Get help | "how do I trade?" |
>
> You can also browse more skills: ask to **list Senpi skills** or visit the Senpi Skills repo.

**State update:**

```json
"state": "READY",
"firstTrade": {
  "started": false,
  "completed": false,
  "skipped": true,
  "skippedAt": "<ISO8601 UTC>"
}
```

Preserve other fields in `state.json`.

---

## Resume Handling

If the tutorial was interrupted (user closed chat, etc.), on the next message read state and resume from the current step:

```bash
STEP=$(cat ~/.config/senpi/state.json | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).firstTrade?.step || ''")

case $STEP in
  "INTRODUCTION")
    # User confirmed but didn't proceed — go to discovery
    ;;
  "DISCOVERY")
    # User saw top traders — ask if ready to create strategy with recommended trader
    ;;
  "STRATEGY_CREATED")
    # Strategy is active — show value/positions and offer to close or congratulate (monitor-only completion)
    ;;
  "STRATEGY_CLOSE")
    # Just closed — show celebration
    ;;
esac
```

**Resume message (user-friendly only; do not mention step names or state):**

> 👋 Welcome back! You were in the middle of your first trade tutorial.
>
> [Describe where they left off in plain language: e.g. "We’d just found some top traders" or "Your strategy is running — want to check it or close it?"]
>
> Want to continue? Say **"yes"** or **"start over"** to begin fresh.
