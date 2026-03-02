# Skill Update Checker Reference

## Output Handling

At session startup the entrypoint reads `~/.config/senpi/pending-skill-updates.json`
(written by the background hourly cron) into `UPDATE_OUTPUT`. Parse it as JSON:

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

---

## Background Cron

Step 5 installs an hourly cron job that runs the checker with `--cron`.
In cron mode the script is fully silent — no stdout, no agent interaction
— and writes any found updates to `~/.config/senpi/pending-skill-updates.json`.
At the next session startup the entrypoint reads and clears that file, then
surfaces the queued updates as described above.

The opt-out flag (`skillUpdates.enabled: false`) also suppresses the cron
— it exits immediately without writing to the pending file.

**View or remove the cron entry:**

```bash
crontab -l | grep "check-skill-updates"          # view
( crontab -l 2>/dev/null | grep -v "check-skill-updates.py" ) | crontab -   # remove
```
