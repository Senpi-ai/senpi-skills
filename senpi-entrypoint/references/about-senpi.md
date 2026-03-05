# About Senpi

Senpi is an agent-first trading platform on Hyperliquid that lets users discover
opportunities, automate strategies, and manage risk from one MCP-connected workflow.

## Summary Response Contract

Use this section only for explicit summary questions such as:
- "What is Senpi?"
- "Summarize Senpi"
- "Summarize skills/capabilities"
- "How do I install skills?"
- "What's new?"

Do not auto-insert this summary during normal onboarding.

## Mandatory Invocation Procedure (NOT Optional)

Run this procedure for every summary/Q&A response handled by `senpi-entrypoint`:
1. Ensure the pre-response invocation check has been executed exactly once for
   this invocation, and use that `UPDATE_OUTPUT`.
   - If `UPDATE_OUTPUT` is already available from `SKILL.md`'s top-level
     `Pre-Response Check`, reuse it and do **not** run the check again.
   - Run the checker from
     [references/skill-update-checker.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/skill-update-checker.md)
     (`Pre-Response Invocation Check`) only as a fallback when it has not yet
     been run in the current invocation.
2. Compose the core answer using this summary contract.
3. Resolve "What's new" using the rules in the `What's New` section below.
4. If updates exist, append a short user-friendly "What's new" addendum; otherwise omit it.

Completion gate: response is NOT complete until all steps above are executed.

Default response order (compact + actionable):
1. What Senpi is (one short definition)
2. Core capabilities
3. Full skill catalog (bullet list)
4. Install guidance
5. What's new (only if updates exist)
6. Closing question

Behavior rules:
- Use queued startup `UPDATE_OUTPUT` for "what's new" when available.
- If no queued updates exist, run one fresh update check before deciding.
- If both queued and fresh checks show no updates, do not mention "what's new" at all.
- Do not mention CLI/commands in user-facing summary replies.
- If user wants install/setup help, offer to handle it for them directly.
- Present the entire skill catalog as bullet points (no tables).
- For "What is Senpi?", include a short onboarding-status note (`SENPI_AUTH_TOKEN` set/unset and next step).
- For goal-based picks, also consult
  [references/skill-recommendations.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/skill-recommendations.md).
- End with: "Want me to recommend which skills to install next and set them up for you?"

## Core Capabilities

- Discover high-performing traders and market opportunities (Discovery + market tools)
- Copy top traders or run autonomous/custom strategy workflows
- Apply risk controls such as dynamic stop-loss and budget-aware orchestration
- Trade broad markets through Senpi's Hyperliquid-based stack (crypto perps and more)

## Full Skill Catalog

- `senpi-entrypoint`: onboarding flow for Senpi setup, discovery, and first-trade guidance.
- `senpi-onboard`: account + API key + MCP setup workflow.
- `senpi-getting-started-guide`: interactive first-trade walkthrough.
- `dsl-dynamic-stop-loss`: two-phase trailing stop-loss with tiered locking.
- `dsl-tight`: tighter DSL defaults for faster profit protection.
- `opportunity-scanner`: market-wide scoring and setup discovery.
- `emerging-movers`: smart-money rotation detection.
- `autonomous-trading`: orchestrates DSL + scanner + movers.
- `wolf-strategy`: full autonomous trading stack.
- `wolf-howl`: nightly review and self-improvement loop.
- `whale-index`: mirrors top-performing traders.

## Install Skills

Keep this user-facing and non-technical:
- Offer to install and set up selected skills for the user.
- Confirm which skill(s) they want and their goal/budget before setup.
- After setup, summarize what is ready and suggest the next best action.

For tailored recommendations by objective and budget, see
[references/skill-recommendations.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/skill-recommendations.md).

## What's New

Use queued updates from the entrypoint startup pending file
(`$SENPI_STATE_DIR/pending-skill-updates.json` when present).

If no queued updates exist, run one live check with the checker script:

```bash
SENPI_ENTRYPOINT_SCRIPTS=$(node -e "
  const path = require('path'), os = require('os'), fs = require('fs');
  const p = path.join(os.homedir(), '.agents', 'skills', 'senpi-entrypoint', 'scripts');
  console.log(fs.existsSync(path.join(p, 'check-skill-updates.py')) ? p : '');
" 2>/dev/null)

if [ -n "$SENPI_ENTRYPOINT_SCRIPTS" ]; then
  LIVE_UPDATE_OUTPUT=$(python3 "$SENPI_ENTRYPOINT_SCRIPTS/check-skill-updates.py" 2>/dev/null || true)
fi
```

Rendering rule:
- Prefer queued `UPDATE_OUTPUT` when it contains updates.
- Otherwise use `LIVE_UPDATE_OUTPUT` if it contains updates.
- If both are `HEARTBEAT_OK` / empty / invalid, skip this section in the reply.

## Platform Reference

After MCP is active, call
`read_senpi_guide(uri="senpi://guides/senpi-overview")` for full platform details
(wallets, strategies, tool categories, fees, workflows).
