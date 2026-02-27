# Next Steps Reference

Use this for **celebration** (after first strategy is created), **after close** (result + next steps), **skip tutorial**, and **resume** handling in the first-trade guide.

---

## Celebrate (After First Strategy Created)

**Right after** the user's first strategy is successfully created (Step 4), congratulate them and show next steps:

> 🎉 **You opened your first strategy!**
>
> You discovered top traders, picked one to mirror, and created a strategy — nice work!
>
> Your strategy is running. You can:
> - Ask **"how's my strategy?"** to see value and positions
> - Say **"close my strategy"** anytime to close and return funds to your wallet
>
> **What you learned:** Discovery, mirroring a top trader, and creating a strategy.
>
> **Next:** Try "show my portfolio", "find opportunities", or install more skills — e.g. **Whale Index** to auto-mirror top traders, or **DSL** for protection.

Then update state to `READY` and set `firstTrade.completed: true`, `firstTrade.step: "COMPLETE"`, `firstTrade.completedAt` (ISO 8601). Preserve `tradeDetails` (strategyId, mirroredTraderId, budgetUsd, etc.). See [references/strategy-management.md](references/strategy-management.md) for the full state shape.

---

## After Close (Result + Next Steps)

When the user closes their first strategy, show the result and suggest next steps. **Do not repeat the main congratulations** — that was already shown when they opened their first strategy.

**If profitable:**

> 📊 **Strategy closed**
>
> Result: +$X.XX (+X.XX%)
>
> **Next:** Explore more skills (DSL, Scanner, WOLF, Whale Index) or say "find opportunities" to discover more strategies.

**If loss:**

> 📊 **Strategy closed**
>
> Result: -$X.XX (-X.X%). You kept size small and closed when you wanted.
>
> **Pro tip:** Install **DSL** for automatic protection. Say "find opportunities" to discover more.

Update state: set `firstTrade.step: "STRATEGY_CLOSE"`, add `tradeDetails.closedAt`, `tradeDetails.pnl`, `tradeDetails.pnlPercent`, `tradeDetails.duration`. State remains `READY` with `firstTrade.completed: true` (already set when they created the strategy).

---

## After Monitor (No Close)

If the user only monitors (e.g. asks "how's my strategy?" one or more times) and does not close: do **not** show a separate congratulations (already shown when they created the strategy). Show strategy value and positions, then offer:

> Your strategy is still running. Say **"close my strategy"** anytime to close, or ask **"how's my strategy?"** to check again.

No state change — `firstTrade.completed` and `step: "COMPLETE"` were already set when they created the strategy.

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
    # Strategy is active — show value/positions and offer to close or keep monitoring
    ;;
  "STRATEGY_CLOSE")
    # Just closed — show result (PnL) and next steps
    ;;
esac
```

**Resume message (user-friendly only; do not mention step names or state):**

> 👋 Welcome back! You were in the middle of your first trade tutorial.
>
> [Describe where they left off in plain language: e.g. "We’d just found some top traders" or "Your strategy is running — want to check it or close it?"]
>
> Want to continue? Say **"yes"** or **"start over"** to begin fresh.
