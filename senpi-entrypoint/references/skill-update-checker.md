# Skill Update Checker — Step 5 Reference

## Output Handling

Parse `UPDATE_OUTPUT` as JSON after running `check-skill-updates.py`:

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
  const p = require('os').homedir() + '/.config/senpi/state.json';
  try {
    const s = JSON.parse(fs.readFileSync(p, 'utf8'));
    s.skillUpdates = { enabled: false };
    fs.writeFileSync(p, JSON.stringify(s, null, 2));
    console.log('done');
  } catch(e) { console.log('state file not found'); }
"
```

Confirm to the user that notifications are off.

## Turning On Notifications

If the user asks to re-enable, set `skillUpdates.enabled` to `true` using
the same pattern:

```bash
node -e "
  const fs = require('fs');
  const p = require('os').homedir() + '/.config/senpi/state.json';
  try {
    const s = JSON.parse(fs.readFileSync(p, 'utf8'));
    s.skillUpdates = { enabled: true };
    fs.writeFileSync(p, JSON.stringify(s, null, 2));
    console.log('done');
  } catch(e) { console.log('state file not found'); }
"
```

Confirm to the user that notifications are back on.
