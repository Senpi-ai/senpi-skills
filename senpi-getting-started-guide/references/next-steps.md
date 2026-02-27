# Next Steps Reference

Use this for **celebration**, **skip tutorial**, and **resume** handling in the first-trade guide.

---

## Celebrate (After Close)

**If profitable:**

> 🎊 **CONGRATULATIONS ON YOUR FIRST TRADE!**
>
> ```
>  ╔═══════════════════════════════════════╗
>  ║   🏆  FIRST TRADE COMPLETE  🏆        ║
>  ║      You made: +$X.XX (+X.XX%)       ║
>  ╚═══════════════════════════════════════╝
> ```
>
> **What you learned:** Discovery, sizing, open, monitor, close.
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

> 📊 **FIRST TRADE COMPLETE**
>
> Trade Result: -$X.XX (-X.X%)
>
> **That's okay!** You kept size small, learned the cycle, and controlled the exit.
>
> **Pro tip:** Install **DSL** for automatic protection: `npx skills add Senpi-ai/senpi-skills/dsl-dynamic-stop-loss`
>
> Explore: DSL, Scanner, WOLF. Say "find opportunities" to scan again.

Then update state to `READY` and set `firstTrade.completed`, `firstTrade.step: "COMPLETE"`, `firstTrade.completedAt`. See [references/position-management.md](position-management.md) for the full state shape.

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
> | Find setups | "find opportunities" |
> | Open trade | "open ETH long $100" |
> | Check positions | "show my portfolio" |
> | Close trade | "close my ETH position" |
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
SENPI_STATE_DIR="${SENPI_STATE_DIR:-$HOME/.config/senpi}"
STEP=$(cat "$SENPI_STATE_DIR/state.json" | node -p "JSON.parse(require('fs').readFileSync(0,'utf8')).firstTrade?.step || ''")

case $STEP in
  "INTRODUCTION")
    # User confirmed but didn't proceed — go to discovery
    ;;
  "DISCOVERY")
    # User saw opportunities — ask if ready to open
    ;;
  "POSITION_OPEN")
    # Position is open — check status and offer to close
    ;;
  "POSITION_CLOSE")
    # Just closed — show celebration
    ;;
esac
```

**Resume message:**

> 👋 Welcome back! You were in the middle of your first trade tutorial.
>
> [Show current status based on step]
>
> Want to continue? Say "yes" or "start over" to begin fresh.
