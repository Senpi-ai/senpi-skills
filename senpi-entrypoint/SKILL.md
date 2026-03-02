---
name: senpi-entrypoint
description: >
  Onboard AI agents into the Senpi trading platform on Hyperliquid. Walks
  through account creation, API key setup, MCP server configuration, skill
  discovery, and first-trade guidance. Use when user says "set up Senpi",
  "get started with Senpi", "connect to Senpi", "install Senpi",
  "onboard to Senpi", or when an agent needs to start trading on Hyperliquid
  via Senpi.
---

# Senpi Agent Entrypoint

Onboard an AI agent into the Senpi trading platform on Hyperliquid via
modular skills: account setup, skill discovery, and trading strategies.

For platform context (wallets, strategies, tool categories, fees), see
[references/about-senpi.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/about-senpi.md).


If any `npx` command fails, consult
[references/error-handling.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/error-handling.md) for recovery
steps.

## Environment Variable Defaults

| Variable | Placeholder | Default |
|---|---|---|
| `SENPI_MCP_ENDPOINT` | `{{SENPI_MCP_ENDPOINT}}` | `https://mcp.prod.senpi.ai` |
| `REFERRAL_CODE` | `{{REFERRAL_CODE}}` | _(empty — optional)_ |

If a placeholder appears as a literal `{{...}}` string (not substituted),
use the default value from the table above.

---

## Agent Behavior

Do not narrate or explain tool calls. Execute silently and only present final results to the user.

---

## Initial check: Skip only Step 1 if already authenticated

Before Step 1, check whether the machine already has a Senpi API key available:

```bash
if [ -n "${SENPI_AUTH_TOKEN}" ]; then
  # Optional: verify MCP responds (e.g. call a read-only tool)
  # If verification fails, clear or ignore and continue to Step 1
  echo "SENPI_AUTH_TOKEN is set; skipping Step 1 only. You MUST still run Step 2, then Step 3, then Step 4."
  SKIP_ONBOARDING=true
else
  SKIP_ONBOARDING=false
fi
```

- **If `SENPI_AUTH_TOKEN` is set and (optionally) MCP responds:** Skip **only Step 1** (Onboard). You **MUST** still run **Step 2: Discover** (list skills and present the catalog to the user), then **Step 3: Guide**, then **Step 4: Expand**. Do not treat "token is set" as "onboarding complete — do nothing."
- **If `SENPI_AUTH_TOKEN` is unset or empty:** Proceed to **Step 1: Onboard** as usual.

After the initial check, follow the step flow: Step 1 (unless skipped) → **Step 2 (always)** → Step 3 → Step 4.

---

## Step 1: Onboard

**Run this step only when** `SENPI_AUTH_TOKEN` is not set (i.e. `SKIP_ONBOARDING` is false). If you skipped here from the initial check, go to Step 2.

Install the onboarding skill:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-onboard -g -y
```

Load and follow the `senpi-onboard` skill instructions. Pass this context:
- `SENPI_MCP_ENDPOINT`: {{SENPI_MCP_ENDPOINT}}
- `REFERRAL_CODE`: {{REFERRAL_CODE}}

**Gate — verify before proceeding:**
1. Senpi account exists
2. API key is generated
3. MCP server is configured and responds to a tool call

---

## Step 2: Discover

**Run this step always:** after Step 1 completes, or immediately when the initial check found `SENPI_AUTH_TOKEN` set. Do not skip this step when the user already has a token — they still need to see the skill catalog.

List available trading skills:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --list
```

Present the catalog to the user. If the command fails, point the user to the
official [Senpi Skills repository](https://github.com/Senpi-ai/senpi-skills)
to browse available skills.

**Gate — verify before proceeding:** User has seen the skill catalog.

---

## Step 3: Guide (Optional)

Ask the user:

> "Would you like to learn how to trade on Senpi? I can walk you through
> your first trade with an interactive guide."

If the user agrees, install the Senpi getting started guide:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-getting-started-guide -g -y
```

If the guide skill is not yet available, suggest these first actions instead:
- "Check your portfolio" (uses `account_get_portfolio` tool)
- "Discover top traders" (uses `discovery_get_top_traders` tool)
- "View market data" (uses `market_get_prices` tool)

---

## Step 4: Expand (User-Driven)

Install additional trading skills on demand based on user interest:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill <skill-name> -g -y
```

Example:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill wolf-strategy -g -y
```

Onboarding is complete. The agent is now equipped with Senpi's trading
toolkit and can install more skills as needed.

---

## Step 5: Skill Update Check (silent, always runs)

Run this step after Step 4 completes. It checks for new or updated Senpi
skills and surfaces them to the user only when there is something relevant.

### Locate the script

Find the `scripts/` directory for this skill — it is relative to wherever
`senpi-entrypoint` was installed globally on this machine:

```bash
SENPI_ENTRYPOINT_SCRIPTS=$(node -e "
  const fs = require('fs'), path = require('path'), os = require('os');
  const home = os.homedir();
  const candidates = [
    path.join(home, '.claude', 'skills', 'senpi-entrypoint', 'scripts'),
    path.join(home, '.agents', 'skills', 'senpi-entrypoint', 'scripts'),
    path.join(home, '.cursor', 'skills', 'senpi-entrypoint', 'scripts'),
    path.join(home, '.config', 'opencode', 'skills', 'senpi-entrypoint', 'scripts'),
    path.join(home, '.cline', 'skills', 'senpi-entrypoint', 'scripts'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, 'check-skill-updates.py'))) {
      console.log(c); process.exit(0);
    }
  }
  console.log('');
" 2>/dev/null)
```

If `SENPI_ENTRYPOINT_SCRIPTS` is empty, skip this step silently — the skill
may not be installed globally yet.

### Run the check

```bash
[ -n "$SENPI_ENTRYPOINT_SCRIPTS" ] && \
  UPDATE_OUTPUT=$(python3 "$SENPI_ENTRYPOINT_SCRIPTS/check-skill-updates.py" 2>/dev/null)
```

### Act on the output

Parse `UPDATE_OUTPUT` as JSON:

- **`heartbeat: "HEARTBEAT_OK"`** — Nothing new. Say nothing, do nothing.

- **`updatedSkills[]` is non-empty** — For each entry, present:
  > **{name}** updated: v{oldVersion} → v{newVersion}
  > Run `npx skills update` to apply all updates at once.

- **`newSkills[]` is non-empty** — For each entry, present:
  > **New skill available: {name}** v{version} — {description}
  > Install: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill {name} -g -y`

Do **not** announce that you ran the check. Only speak if there is
something to surface.

---

## Turning off skill update notifications

If at any point the user says something like "turn off skill update
notifications", "stop notifying me about new skills", or similar:

```bash
node -e "
  const fs = require('fs');
  const p = require('os').homedir() + '/.config/senpi/state.json';
  try {
    const s = JSON.parse(fs.readFileSync(p, 'utf8'));
    s.skillUpdates = { enabled: false };
    fs.writeFileSync(p, JSON.stringify(s, null, 2));
    console.log('done');
  } catch(e) { console.log('state file not found'); }
"
```

Confirm to the user that skill update notifications are off. They can
re-enable by saying "turn on skill update notifications", which sets
`skillUpdates.enabled` to `true` using the same pattern above.

