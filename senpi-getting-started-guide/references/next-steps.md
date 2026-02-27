# Next Steps Reference

Use this for **celebration**, **skip tutorial**, and **resume** handling in the first-trade guide.

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

## Skip Tutorial

When the user says "skip", "skip tutorial", "I know how to trade":

**Display:**

> 👍 **Tutorial skipped!**
>
> You're all set to trade on your own. Quick reference:
>
> | Action | Command |
> |--------|---------|
> | Find top traders | "find opportunities" / discovery_get_top_traders |
> | Mirror a trader | "create strategy mirroring [trader]" / strategy_create |
> | Check strategies | "show my portfolio" / strategy_list, account_get_portfolio |
> | Close a strategy | "close my strategy" / strategy_close |
> | Get help | "how do I trade?" |
>
> **Recommended skills:** `npx skills add Senpi-ai/senpi-skills --list`

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
    # Strategy is active — check status and offer to close
    ;;
  "STRATEGY_CLOSE")
    # Just closed — show celebration
    ;;
esac
```

**Resume message:**

> 👋 Welcome back! You were in the middle of your first trade tutorial.
>
> [Show current status based on step: discovery → recommend trader; strategy created → show strategy status and offer to close.]
>
> Want to continue? Say "yes" or "start over" to begin fresh.
