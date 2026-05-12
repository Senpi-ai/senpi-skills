# `senpi-helpers` CLI reference

Operator command-line interface for `senpi_runtime_helpers` producer daemons.
The CLI bypasses the openclaw gateway by design — same way the wrapper itself
does. It reads the self-describing state files (`pid.json`, `boot.json`,
`heartbeat.json`) each daemon writes and sends signals to control them.

For usage from a producer's POV, read [`../SKILL.md`](../SKILL.md). This doc
is the **operator** reference.

---

## Invocation

Invoke the wrapper script:

```bash
~/.openclaw/skills/senpi-trading-runtime/senpi-helpers <subcommand> [args]
```

The wrapper lives at `~/.openclaw/skills/senpi-trading-runtime/senpi-helpers` (e.g. `/data/.openclaw/skills/senpi-trading-runtime/senpi-helpers` on Railway hosts) and is marked executable. The wrapper finds the `senpi_runtime_helpers` package via its own sibling directory.

Alias it for convenience:

```bash
alias senpi-helpers=~/.openclaw/skills/senpi-trading-runtime/senpi-helpers
```

## Global options

| Flag                  | Default                                | Notes                                                                                |
|-----------------------|----------------------------------------|--------------------------------------------------------------------------------------|
| `--state-dir <path>`  | `$SENPI_HELPERS_STATE_DIR`             | Override the state directory for this invocation.                                    |
|                       | → `/data/.openclaw/senpi-helpers/`     | The default lives on the persistent Railway volume so state survives restarts.       |

Every subcommand that takes a daemon name accepts it as a positional argument.
On hosts with **exactly one** registered daemon the name is optional; on
multi-daemon hosts (most production boxes) it is required, otherwise the CLI
exits 2 with the available daemon list.

---

## `senpi-helpers list`

Show every daemon known to this host — one row per subdirectory of the state
directory.

```bash
senpi-helpers list [--json]
```

Default output is an aligned text table with `NAME PID RUNNING WALLET SCANNER
TICKS ERRORS LAST_TICK` columns. Wallet addresses are abbreviated to
`0xabc…1234` so long 42-char addresses don't blow out the column.

`--json` emits a stable `{ "daemons": [...] }` envelope with every documented
field (the human columns plus `interval_seconds`, `start_time_iso`, `log_path`,
`last_tick_status`, `last_tick_code`).

Exit code: always 0 (even when no daemons are registered — that's a valid
state, not an error).

---

## `senpi-helpers health <name>`

Read pid.json + heartbeat.json for one daemon; report its health.

```bash
senpi-helpers health [<name>] [--json]
```

Health states (the `health` field, stable strings for scripting):

| state                | meaning                                                                                |
|----------------------|----------------------------------------------------------------------------------------|
| `healthy`            | Running, recent tick, last status was ok.                                              |
| `down`               | pid.json missing OR the recorded pid is not alive.                                     |
| `no_ticks_yet`       | Running but no heartbeat yet — daemon just started.                                    |
| `stale_ticks`        | Running but `last_tick_age > 2 × interval_seconds`. One overrun cycle absorbed.        |
| `last_tick_failed`   | Running, recent tick, but `last_tick_status != ok` (error / timeout / skipped_locked). |

Exit codes:

| code | meaning                                                                          |
|------|----------------------------------------------------------------------------------|
| 0    | healthy                                                                          |
| 1    | unhealthy (any non-`healthy` state)                                              |
| 2    | not found (no state files for `<name>`, or multi-daemon host with no `<name>`)   |

Default output is a single-block summary including name, health, running
(yes/no + pid + uptime), wallet (shortened), scanner, interval, last tick
(iso + status + code + age), ticks total / errors, log path, last error
(truncated to 200 chars).

`--json` emits everything the human view shows plus raw `last_tick_iso` and
`last_tick_age_seconds` for callers that render their own time-since.

---

## `senpi-helpers stats <name>`

Aggregate the daemon's stderr log file into wall-clock UTC hourly buckets.

```bash
senpi-helpers stats [<name>] [--hours N] [--json]
```

| option         | default | notes                                                                |
|----------------|---------|----------------------------------------------------------------------|
| `--hours <N>`  | `72`    | Window in hours. 72 = 3 days.                                        |
| `--json`       | off     | Emit the full JSON envelope instead of the human summary.            |

The log path comes from `pid.json.log_path`, recorded by the daemon at boot
via `state.detect_log_path()`. The daemon reads `/proc/self/fd/2` (Linux
symlink to wherever stderr was redirected) or `SENPI_HELPERS_LOG_PATH` if
that env var is set. If the daemon was started with an interactive stderr
(no redirect), no log_path is recorded and `stats` errors out with guidance.

Events recognized (from the daemon's structured log emissions):

| event                | counters updated                                                |
|----------------------|-----------------------------------------------------------------|
| `mcp_call`           | `mcp_calls_ok` / `mcp_calls_failed` + code histogram             |
| `cache_hit`          | `cache_hits`                                                    |
| `signal_post`        | `signals_posted_ok` (sums `batch_size`) / `signals_posted_failed`|
|                      | + per-item `failed_by_code` or `envelope_code`                   |
| `daemon_tick_finished` | `ticks_by_status` + `ticks_errors_by_code`                    |

Human output sections:

```
<name> — last <N> hours
log: /tmp/<name>.log
events parsed: <count>  (earliest: <iso>, log size: <bytes>)

Totals
  MCP calls         <ok> ok       <failed> failed
  Signals posted    <ok> ok       <failed> failed
  Cache hits        <count>
  Ticks             <ok> ok   <error>   <timeout>   <skipped>

Errors by code (last <N>h)
  mcp_call      503                     2
  signal_post   INVALID_REQUEST         1
  tick          SenpiClientError        3
  (omitted entirely when no errors)

Hourly breakdown (<N> buckets, oldest first)
  HOUR (UTC)              MCP    SIG  CACHE  T_OK  T_ERR  T_TO  T_SKIP
  2026-05-09 12:00          0      0      0     0      0     0       0
  ...
  2026-05-12 09:00        120     12     80    12      0     0       0
```

`--json` envelope:

```json
{
  "name": "<name>",
  "log_path": "/tmp/<name>.log",
  "window_hours": 72,
  "total_events_counted": 12341,
  "earliest_event_iso": "2026-05-09T12:34:56.789Z",
  "log_size_bytes": 8421376,
  "totals": { ... same shape as a bucket, summed ... },
  "buckets": [
    {
      "hour_start_iso": "2026-05-12T09:00:00Z",
      "mcp_calls_ok": 120,
      "mcp_calls_failed": 2,
      "mcp_errors_by_code": { "503": 1, "NETWORK_ERROR": 1 },
      "cache_hits": 80,
      "signals_posted_ok": 12,
      "signals_posted_failed": 0,
      "signals_errors_by_code": {},
      "ticks_by_status": { "ok": 12, "error": 0, "timeout": 0, "skipped_locked": 0 },
      "ticks_errors_by_code": {}
    },
    ...
  ]
}
```

Exit codes:

| code | meaning                                                                                                              |
|------|----------------------------------------------------------------------------------------------------------------------|
| 0    | aggregation succeeded                                                                                                |
| 1    | log path unknown OR log file unreadable (`STATS_NO_LOG`)                                                             |
| 2    | no pid.json for `<name>` (daemon never started OR exited cleanly; pid.json was cleared by clean exit) (`STATS_NOT_FOUND`) |

---

## `senpi-helpers stop <name>`

Stop a running daemon. SIGTERM first, poll for exit, escalate to SIGKILL on
timeout.

```bash
senpi-helpers stop [<name>] [--timeout 30] [--json]
```

| option              | default | notes                                                                |
|---------------------|---------|----------------------------------------------------------------------|
| `--timeout <secs>`  | `30`    | Ceiling on the SIGTERM wait. SIGKILL fires after this many seconds. |
| `--json`            | off     | Emit a JSON result envelope.                                         |

**The timeout is a ceiling, not a fixed wait.** A daemon that exits in 2 s
makes `stop` return in ~2 s. The CLI polls every 250 ms; the timeout only
triggers SIGKILL escalation if the daemon hasn't exited yet.

Outcomes (stable strings for `--json` consumers):

| outcome                        | meaning                                                              |
|--------------------------------|----------------------------------------------------------------------|
| `already_dead`                 | Recorded pid wasn't alive when `stop` was called.                    |
| `stopped_via_sigterm`          | SIGTERM worked; daemon exited cleanly within `--timeout`.            |
| `stopped_via_sigkill`          | SIGTERM timed out; SIGKILL killed it.                                |
| `still_alive_after_sigkill`    | Pid still alive even after SIGKILL + grace window. Kernel-level issue. |
| `permission_denied`            | Caller lacks permission to signal the pid.                           |
| `invalid_pid`                  | pid.json had a bad value (None/0/non-int).                           |

Exit codes:

| code | meaning                                                                                                |
|------|--------------------------------------------------------------------------------------------------------|
| 0    | success — daemon is no longer running (any of `already_dead`, `stopped_via_sigterm`, `stopped_via_sigkill`) |
| 1    | failure (`still_alive_after_sigkill`, `permission_denied`, `invalid_pid`)                             |
| 2    | not found (no pid.json for `<name>`, or multi-daemon host with no `<name>`)                            |

After a successful SIGKILL escalation OR an `already_dead` outcome the CLI
clears `pid.json` (the SIGKILL'd daemon can't run its own clean-up; an
`already_dead` daemon's stale pid.json should not linger). On a clean
SIGTERM, the daemon's own `finally` block already cleared the file —
`clear_pid` is idempotent.

---

## `senpi-helpers restart <name>`

Stop the daemon and re-launch it from the argv + cwd recorded in `boot.json`.

```bash
senpi-helpers restart [<name>] [--timeout 30] [--json]
```

| option              | default | notes                                                  |
|---------------------|---------|--------------------------------------------------------|
| `--timeout <secs>`  | `30`    | Stop-phase timeout, same semantics as `stop`.          |
| `--json`            | off     | Emit a JSON result envelope.                           |

What `restart` does, in order:

1. Read `boot.json` for `<name>`. Missing → friendly error explaining the
   daemon was never started under the helper, so `restart` has no record of
   which script / env / cwd to use; operator must start manually first
   (typical `nohup python3 -u <producer>.py …` form with the skill's
   required env vars). Exit 2.
2. Verify `script_path` still exists on disk. Moved/deleted → friendly
   error asking operator to start manually so a fresh boot.json is written
   next time. Exit 1.
3. If the daemon is currently running, run the `stop` flow first.
4. Re-exec the daemon as a detached process:
   - argv from boot.json
   - cwd from boot.json
   - env from the CLI's **current** environment (NOT the captured
     env_snapshot — so wallet / auth / decision-model changes since the
     daemon was started take effect)
   - stdout + stderr both → the original `log_path` (so `stats` continues
     to parse the same file)
   - detached via POSIX setsid; survives CLI exit
5. Wait up to 3 s for the new daemon to write its `pid.json`. Found →
   "pid.json confirmed". Not found → soft warning; the relaunch already
   succeeded, but verify with `health`.

The new daemon's env_snapshot in boot.json will be rewritten with the new
environment.

Exit codes:

| code | meaning                                                                                                       |
|------|---------------------------------------------------------------------------------------------------------------|
| 0    | restarted (or relaunched cold when no prior daemon was running)                                               |
| 1    | restart failed (stop_pid failed, script_path missing, log_path unknown, or `relaunch_daemon` returned failure) |
| 2    | no daemon record found (boot.json missing or multi-daemon name ambiguous)                                     |

---

## State file layout

Every daemon writes (and the CLI reads) three files under
`${SENPI_HELPERS_STATE_DIR}/<name>/`:

```
<state_dir>/
├── my-producer-6e92/
│   ├── pid.json         # pid, start_time_iso, wallet, scanner, interval,
│   │                    # tick_timeout, log_path, version. REMOVED on clean exit.
│   ├── boot.json        # argv, script_path, cwd, env_snapshot.
│   │                    # PERSISTS across runs — needed by `restart`.
│   └── heartbeat.json   # last_tick_iso, last_tick_status, last_tick_code,
│                        # last_tick_duration_ms, last_tick_error,
│                        # tick_count, error_count. Rewritten every tick.
├── kodiak-tracker-aaaa/
│   └── ... (same shape)
└── ...
```

All writes are atomic (tempfile + os.replace). All writes are tolerant —
state-file I/O failures emit `state_write_failed` events to stderr but
NEVER raise from the daemon. State files are observability — never on the
critical signal-emission path.

`boot.json` excludes sensitive env vars (`SENPI_AUTH_TOKEN`,
`OPENCLAW_GATEWAY_TOKEN`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`). The
captured snapshot is informational only — `restart` uses the CLI's current
process env, not the snapshot.

---

## Common operator recipes

### "Is anything actually ticking right now?"

```bash
senpi-helpers list
```

Look for `RUNNING=yes` and a recent `LAST_TICK`.

### "Are the signals reaching the runtime?"

```bash
senpi-helpers stats my-producer-6e92 --hours 1
```

Check `Totals → Signals posted`. Failures broken down by code.

### "What's going wrong?"

```bash
senpi-helpers health my-producer-6e92
```

If `health` is `last_tick_failed` or `stale_ticks`, follow up with
`senpi-helpers stats my-producer-6e92 --hours 1` for error histograms.

### "I changed an env var; the daemon needs a fresh start."

```bash
senpi-helpers restart my-producer-6e92
```

`restart` inherits the CLI's current env, so the new env var lands.

### "Stop the daemon — I need to debug."

```bash
senpi-helpers stop my-producer-6e92
```

Clean SIGTERM. The daemon clears its own pid.json on exit; the next
`list` will show it gone.

### "It's wedged. Hard-stop it."

```bash
senpi-helpers stop my-producer-6e92 --timeout 5
```

Short timeout = quick escalation to SIGKILL. After SIGKILL the CLI
clears pid.json itself.

---

## Pinning the daemon to a specific log path

The auto-detect (`/proc/self/fd/2`) works for the canonical recipe
(`nohup python3 -u … > /tmp/foo.log 2>&1 &`). If you start the daemon
under supervisord, systemd, or another supervisor that doesn't redirect
stderr to a file, set the path explicitly:

```bash
SENPI_HELPERS_LOG_PATH=/var/log/<skill>-producer.log \
  python3 -u ${OPENCLAW_WORKSPACE}/skills/<skill-name>/scripts/<skill-name>-producer.py &
```

The daemon records this in `pid.json.log_path`; `stats` and `restart`
both pick it up.

---

## See also

- [`../SKILL.md`](../SKILL.md) — producer-side wrapper usage and migration.
