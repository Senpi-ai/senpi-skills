"""SIGNALS — pure helpers for scan.py (no I/O, no MCP). There is no thesis math here: the
package trades nothing. What is pure: where the senpi-signals skill may live, and the state
record a tick leaves behind."""
import os.path


def candidate_dirs(here, skills_dir_env, home):
    """Ordered places senpi-signals/scripts may be: $SENPI_SKILLS_DIR (the runtime's own
    comma-separated knob), every ancestor of `here` (a repo checkout), then the claw defaults.
    Pure string work — scan.py does the existence checks."""
    roots = [r.strip() for r in skills_dir_env.split(",") if r.strip()]
    d = here
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        roots.append(d)
    roots += ["/data/.openclaw/skills", os.path.join(home, ".openclaw", "skills")]
    return [os.path.join(r, "senpi-signals", "scripts") for r in roots]


def tick_record(rep):
    """What a tick persists to ctx.state: the sweep's numbers, never its payload."""
    res = rep.get("result") or {}
    cur = rep.get("current") or {}
    return {
        "ts": res.get("generated") or cur.get("generated"),
        "reads": rep.get("reads"),
        "assets": len(cur.get("asset_metrics") or {}),
        "events": len(cur.get("events") or []),
        "trade": len(res.get("trade") or []),
        "social": len(res.get("social") or []),
        "trend_ready": res.get("trend_ready"),
        "coverage": rep.get("coverage") or {},
    }


def runtime_state_root(argv):
    """The runtime's own state root, read off the launch config the scaffold was started with
    (`python -m scaffold <root>/<runtime>-<wallet>/scanner-launch/<scanner>.launch.json` — the
    layout scanner-launcher.ts documents). None outside the runtime. The scanner child gets no
    SENPI_STATE_DIR (the launcher's env is explicit: creds, PYTHONPATH, the intake URL), so this
    is how the sweep lands beside installed_runtimes.json — where the content automation reads."""
    for a in argv:
        if str(a).endswith(".launch.json"):
            return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(a)), "..", ".."))
    return None
