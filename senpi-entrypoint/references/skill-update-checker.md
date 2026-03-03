# Skill Update Checker Reference

## Pre-Response Invocation Check

Run this check every time `senpi-entrypoint` is invoked, before composing a
user-facing response:

```bash
SENPI_STATE_DIR="${SENPI_STATE_DIR:-$HOME/.config/senpi}"
SENPI_PENDING="$SENPI_STATE_DIR/pending-skill-updates.json"
if [ -f "$SENPI_PENDING" ]; then
  UPDATE_OUTPUT=$(cat "$SENPI_PENDING") && rm -f "$SENPI_PENDING"
fi
```

If `UPDATE_OUTPUT` contains updates, preserve it for response assembly and
rendering. If it is heartbeat/empty, continue to normal response flow.

---

## Output Handling

At session startup the entrypoint reads
`$SENPI_STATE_DIR/pending-skill-updates.json` (default:
`~/.config/senpi/pending-skill-updates.json` if `SENPI_STATE_DIR` is unset)
(written by the background daily cron) into `UPDATE_OUTPUT`. Parse it as JSON:

- **`heartbeat: "HEARTBEAT_OK"`** — Nothing new. Say nothing, do nothing.

- **`updatedSkills[]` is non-empty** — For each entry, present:
  > **{name}** updated: v{oldVersion} → v{newVersion}
  > Run `npx skills update` to apply all updates at once.

- **`newSkills[]` is non-empty** — For each entry, present:
  > **New skill available: {name}** v{version} — {description}
  > Install: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill {name} -g -y`

Do **not** announce that you ran the check. Only speak if there is something to surface.

---

## Turning Off Notifications

If the user says anything like "turn off skill update notifications" or "stop
notifying me about new skills":

```bash
node -e "
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const stateDir = (process.env.SENPI_STATE_DIR || '~/.config/senpi').replace(/^~(?=$|\/)/, os.homedir());
  const p = path.join(stateDir, 'state.json');
  let s;
  try {
    s = JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    const isMissing = e && e.code === 'ENOENT';
    const isCorrupt = e instanceof SyntaxError;
    if (!isMissing && !isCorrupt) throw e;
    fs.mkdirSync(path.dirname(p), { recursive: true });
    s = {};
  }
  s.skillUpdates = { enabled: false };
  fs.writeFileSync(p, JSON.stringify(s, null, 2));
  console.log('done');
"
```

Confirm to the user that notifications are off.

## Turning On Notifications

If the user asks to re-enable, set `skillUpdates.enabled` to `true` using
the same pattern:

```bash
node -e "
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const stateDir = (process.env.SENPI_STATE_DIR || '~/.config/senpi').replace(/^~(?=$|\/)/, os.homedir());
  const p = path.join(stateDir, 'state.json');
  let s;
  try {
    s = JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    const isMissing = e && e.code === 'ENOENT';
    const isCorrupt = e instanceof SyntaxError;
    if (!isMissing && !isCorrupt) throw e;
    fs.mkdirSync(path.dirname(p), { recursive: true });
    s = {};
  }
  s.skillUpdates = { enabled: true };
  fs.writeFileSync(p, JSON.stringify(s, null, 2));
  console.log('done');
"
```

Confirm to the user that notifications are back on.

---

## Step 5 Agent Behaviour

Run Step 5 **silently** with **NO_REPLY** — do not confirm, announce, or surface
anything to the user. No message that you did it, no script output, no errors.
Update notifications only surface later at the next session startup via the
pending file; never mention them or the checker inline.

---

## Background Cron (OpenClaw)

Step 5 installs a daily **OpenClaw cron** job named `senpi-skill-update-check`.
Each run is an isolated agent turn that executes the checker script with
`--cron` and produces no delivery. The script is fully silent and writes any
found updates to `$SENPI_STATE_DIR/pending-skill-updates.json` (default:
`~/.config/senpi/pending-skill-updates.json` if `SENPI_STATE_DIR` is unset).
At the next session startup the entrypoint reads and clears that file, then
surfaces the queued updates as described above.

The opt-out flag (`skillUpdates.enabled: false`) also suppresses the checker
— the script exits immediately without writing to the pending file.

**View, pause, or remove the OpenClaw cron job:**

```bash
openclaw cron list                              # view (look for senpi-skill-update-check)
openclaw cron pause "senpi-skill-update-check"  # pause
openclaw cron resume "senpi-skill-update-check" # resume
openclaw cron remove "senpi-skill-update-check" # remove
```

Jobs are stored in `$OPENCLAW_STATE_DIR/cron/jobs.json` (default: `~/.openclaw/cron/jobs.json` if `OPENCLAW_STATE_DIR` is unset).
