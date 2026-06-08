# senpi-helpers state-file schema history

Companion to `senpi-helpers-cli.md`. Keeps the deep "why this schema
exists" context out of the operator-facing doc so the main reference
stays focused on usage and short enough to fit comfortably in an agent's
context window.

Operators rarely need this file. Read it when:
- A daemon's state file has an unfamiliar `schema` value.
- You're auditing migrations in a debugging session.
- You're authoring a new schema and want the protocol.

## Versioning protocol

Each state file (`pid.json`, `boot.json`, `heartbeat.json`) has its own
`schema` integer field. Schemas evolve independently — bumping one
doesn't require touching the others. Readers tolerate older AND newer
schemas (forward-compat); when `state._read_json` sees an unsupported
schema it logs a `state_unknown_schema` event and returns the data
anyway, so callers using `.get(...)` degrade gracefully.

Current supported schemas:

| file              | schemas  |
|-------------------|----------|
| `pid.json`        | {1, 2}   |
| `boot.json`       | {1, 2}   |
| `heartbeat.json`  | {1}      |

## boot.json — schema 1 → 2

### Schema 1 (legacy, pre-2026-05-14)

```json
{
  "argv": ["/path/script.py"],
  "script_path": "/path/script.py",
  "cwd": "/some/cwd",
  "env_snapshot": { ... }
}
```

`argv` captured only `sys.argv`, which is the script + script args.
Python's interpreter and flags (`-u`, `-O`, etc.) are consumed before
`sys.argv` is populated and aren't recoverable from inside the script.

### Schema 2 (current)

```json
{
  "argv": ["/usr/bin/python3", "-u", "/path/script.py"],
  "script_path": "/path/script.py",
  "cwd": "/some/cwd",
  "env_snapshot": { ... },
  "log_path": "/tmp/<name>.log"
}
```

Two additions:
- `argv[0]` is `sys.executable`; `argv[1]` is `-u`.
- `log_path` persists here in addition to `pid.json`.

### Why the schema 2 bump was needed

The operator playbook launches daemons as
`nohup python3 -u script.py &`. The script never needs to be `+x` because
the interpreter is explicit on the command line. But `senpi-helpers
restart` does `Popen(argv, ...)` which `execve`'s `argv[0]` directly —
needing the script to be executable with a working shebang. On a real
production box (vulture, 2026-05-13) this fails:

```
Popen raised: PermissionError [Errno 13] Permission denied:
'/data/workspace/skills/vulture-strategy/scripts/vulture-producer.py'
```

The stop half of `restart` succeeded; the start half died silently
because the agent's `exec` session terminated before `senpi-helpers`
flushed its stderr. Operator's-eye view: daemon went away and didn't
come back.

`log_path` persisted to boot.json for the same reason: pid.json is
removed on graceful exit; if the operator cleanly stopped the daemon,
the next `restart` lost the log target.

### Migration

One-shot per daemon, transparent on read:
- `manage._normalize_argv` prepends `[sys.executable, "-u"]` when
  `argv[0]` is a `.py` script (heuristic: doesn't look like a python
  interpreter binary, ends with `.py`).
- The newly-spawned daemon's own `write_boot()` rewrites the file as
  schema 2.
- `--json` includes `argv_normalized: true|false` so automation can
  detect the migration.

Operators do nothing — schema-1 boot files keep working, get rewritten
on first restart.

## pid.json — schema 1 → 2

### Schema 1

```json
{
  "pid": 1234,
  "start_time_iso": "...",
  "wallet": "0x...",
  "scanner": "...",
  "log_path": "/tmp/<name>.log"
}
```

### Schema 2 (current)

Adds two fields for the pid-recycle guard:

```json
{
  "pid": 1234,
  ...
  "cmdline_fingerprint": "<sha256 of /proc/<pid>/cmdline>",
  "start_time_jiffies": 1234567890
}
```

### Why the schema 2 bump was needed

`os.kill(pid, 0)` reports any pid that exists in the kernel's process
table as "alive" — including a recycled pid pointing at an unrelated
process. In Docker/Railway containers where `pid_max` defaults to 32k,
recycling on a long-running box is plausible. A `senpi-helpers stop`
that signals the recycled pid would SIGTERM a stranger.

The two fingerprints catch this: at signal-time `state.pid_alive_and_matches`
cross-checks `/proc/<pid>/cmdline`'s sha256 + `/proc/<pid>/stat` field 22
against the values `write_pid` captured at daemon launch. Mismatch ⇒
treated as already-dead (the daemon is gone; this pid belongs to
something else). The CLI never signals.

Schema-1 pid.json (no fingerprints) degrades to plain `pid_alive` — no
recycle protection until the next launch writes schema 2. Migration is
one-shot per daemon, same as boot.json.

## heartbeat.json

Schema 1, unchanged. Rewritten after every daemon tick; ephemeral —
recoverable from the next tick.

## Authoring a new schema bump

1. Add the new schema number to `_SUPPORTED_SCHEMAS` in `state.py`.
2. Update the writer (e.g. `write_pid`) to emit the new shape. Keep the
   payload superset of the prior schema so old readers ignore new
   fields gracefully.
3. If reading the new shape needs new field access in the CLI, use
   `.get(...)` so old-schema files don't KeyError.
4. Document the bump in this file: schema, why it exists, migration
   approach, what older readers see.
