"""SIGNALS — the senpi-signals sweep on the runtime's clock. TRADES NOTHING.

Every tick: run senpi-signals/scripts/sweep.py through ctx.senpi_mcp (so the reads are counted
and a `senpi validate` tick is a real, proven read), which writes
$SENPI_STATE_DIR/signals/{current.json, signals.md} and advances score.py's snapshot ring —
then return []. No signal is ever emitted. The runtime is the scheduler; no model call is made.

Read-only + single-pass. The sweep locates the skill's script the way the runtime's own
skills-manager does (SENPI_SKILLS_DIR, default /data/.openclaw/skills), or up the tree from a
repo checkout.
"""
import os
import sys

import scoring


def _sweep():
    for d in scoring.candidate_dirs(os.path.dirname(os.path.abspath(__file__)),
                                    os.environ.get("SENPI_SKILLS_DIR", ""),
                                    os.path.expanduser("~")):
        if os.path.isfile(os.path.join(d, "sweep.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            import sweep
            return sweep
    return None


def _out_dir(inputs):
    """Where current.json, signals.md and the ring land. An explicit `outDir` input wins; with
    SENPI_STATE_DIR / SENPI_SIGNALS_STATE in the env score.py resolves its documented path; in the
    runtime (no env) the root comes off the launch config, so the files sit at
    <runtime state root>/signals/ — the same $SENPI_STATE_DIR/signals/ the claw exports."""
    if inputs.get("outDir"):
        return str(inputs["outDir"])
    if os.environ.get("SENPI_SIGNALS_STATE") or os.environ.get("SENPI_STATE_DIR"):
        return None
    root = scoring.runtime_state_root(sys.argv)
    return os.path.join(root, "signals") if root else None


def scan(inputs, ctx):
    sweep = _sweep()
    if sweep is None:
        # No read happens on this path, so validate reports UNPROVEN — the right verdict: the host
        # cannot find its skill. Set SENPI_SKILLS_DIR to where senpi-signals is installed.
        print("[signals.scan] senpi-signals/scripts/sweep.py not found — set SENPI_SKILLS_DIR",
              file=sys.stderr)
        return []
    # a validate tick is a real sweep (it warms the ring) but must not spend the content
    # feed's anti-repeat memory, so it ranks under its own consumer name
    consumer = "validate" if getattr(ctx, "dry_run", False) else str(inputs.get("consumer") or "social")
    out_dir = _out_dir(inputs)
    try:
        rep = sweep.run(ctx.senpi_mcp.call_tool, consumer=consumer,
                        top_n=int(inputs.get("universeTopN") or sweep.UNIVERSE_TOP_N),
                        out_dir=out_dir,
                        state=os.path.join(out_dir, "state.json") if out_dir else None)
    except Exception as exc:  # noqa: BLE001 — a broken sweep is a logged tick, never a crash loop
        print(f"[signals.scan] sweep failed: {exc!r}", file=sys.stderr)
        return []
    print(f"[signals.scan] {rep['summary']}", file=sys.stderr)
    for k, v in rep["coverage"].items():
        print(f"[signals.scan] coverage {k}: {v}", file=sys.stderr)
    if ctx.state is not None:
        try:
            ctx.state.append(scoring.tick_record(rep))
        except Exception as exc:  # noqa: BLE001
            print(f"[signals.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return []
