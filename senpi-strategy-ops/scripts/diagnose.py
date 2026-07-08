#!/usr/bin/env python3
"""Why isn't my strategy trading? — a deterministic doctor for a non-firing external_scanner.

  python3 diagnose.py <id>              # diagnose every instance of a deployed package
  python3 diagnose.py <id> --run-scan   # ALSO run scan() once against live MCP + show the literal return
  python3 diagnose.py <id> --json

`status.py` answers "is the runtime up?"; this answers the harder one: "the runtime is up and funded
but no positions are opening — WHY?" It names ONE cause instead of making you infer, checking in order
of what's cheap-and-definitive first:

  STATIC (runtime.yaml on disk — no host needed)
    1. is an external_scanner even declared?        (only position_tracker → nothing ever trades)
    2. are its required fields present?             (path/entrypoint/signal_data_schema/validity)
    3. interval_seconds > 0?                         (0/negative → the scanner is never scheduled)
    4. does the entrypoint resolve on disk?          (scan.py / sibling scoring.py actually there?)
  RUNTIME (this host, via `openclaw senpi …`)
    5. is a runtime registered + running for it?
    6. is the external_scanner registered on that runtime? (or did only position_tracker load?)
    7. is it erroring?   (consecutive-error latch → scan() throws every tick)
    8. is it ticking?    (via `senpi scanner` liveness — NOT runCount, which counts EMITS not runs)
    9. is it BARREN?     (alive + has run + 0 signals → runs clean but emits nothing: the divergence-play
                          case — thresholds too tight, OR data{} fails signal_data_schema → dropped)
  OPTIONALLY (--run-scan) runs scan() once and shows the literal return + traceback + schema violations,
  which splits case 9 for you: `[]` ⇒ logic/thresholds; a shape violation ⇒ fix your data{} keys.

The fix is ALWAYS: correct the source package + redeploy (`close.py` → author → `deploy.py`). NEVER
hand-edit deployed state — the runtime owns it and will overwrite you. This tool only READS.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli   # noqa: E402
import _pkg   # noqa: E402
import _smoke  # noqa: E402
import _fetch  # noqa: E402

_ICON = {"live": "✅", "not-ticked-yet": "⏳", "barren": "⚠", "scanner-erroring": "❌",
         "no-scanner": "❌", "malformed-scanner": "❌", "interval-zero": "❌", "entrypoint-missing": "❌",
         "no-runtime": "❌", "runtime-stopped": "❌", "scanner-not-registered": "❌",
         "host-unknown": "·", "unknown": "·"}

# external_scanner fields the runtime REQUIRES (runtime-yaml.md → external_scanner field set).
# interval_seconds defaults to 30 so absence is legal — but 0/negative never schedules, checked apart.
_REQUIRED_ES = ("path", "entrypoint", "signal_data_schema", "default_signal_validity_seconds")


# ---------------------------------------------------------------- host: scanner health RPC

def _scanner_health(runtime_name):
    """`openclaw senpi scanner -r <name> --json` — the runtime's OWN per-scanner health digest (run/
    error/consecutive-error counts, cumulative signals, external `alive` heartbeat, BARREN flag). This
    is the purpose-built read for "is my scanner producing?"; returns the parsed JSON or None."""
    return _cli.cli_json(["openclaw", "senpi", "scanner", "-r", runtime_name, "--json"], timeout=20)


def _state_rpc(runtime_name):
    return _cli.cli_json(["openclaw", "senpi", "state", "-r", runtime_name, "--json"], timeout=20)


def _find_scanner_entry(obj, name):
    """Deep-find the dict describing scanner `name` in a scanner/state payload — shape-tolerant (the
    exact JSON layout isn't pinned, so match on a name field + any scanner-ish counter key)."""
    KEYS = ("runcount", "runs", "ticks", "signals", "signalcount", "alive", "barren",
            "lastrunfinishedat", "lastrunstartedat", "errorcount", "consecutiveerrors", "nextrun")
    if isinstance(obj, dict):
        nm = _cli.dig(obj, "name", "scanner", "scannerName", "scanner_name")
        if nm == name and any(k.lower() in KEYS for k in obj):
            return obj
        for v in obj.values():
            r = _find_scanner_entry(v, name)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_scanner_entry(v, name)
            if r:
                return r
    return None


def _num(d, *keys):
    v = _cli.dig(d or {}, *keys)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _read_disk_state(runtime_name):
    """The handoff's ground-truth artifact: the on-disk state.json, read directly (not via the CLI,
    which can swallow output). Returns (path, parsed-or-None). Best-effort — locates the state dir via
    `senpi config get state-dir`, else the default, then globs for the runtime's state.json."""
    base = None
    got = _cli.run_cli(["openclaw", "senpi", "config", "get", "state-dir"], timeout=15)
    if got[0] == 0 and got[1].strip():
        base = got[1].strip().splitlines()[-1].strip()
    candidates = [base] if base else []
    candidates += [os.path.expanduser("~/.openclaw/senpi-state"), os.path.expanduser("~/.openclaw")]
    for root in candidates:
        if not root or not Path(root).is_dir():
            continue
        # prefer a state.json under a dir named for the runtime; else any that mentions it
        hits = list(Path(root).rglob("state.json"))
        named = [p for p in hits if runtime_name in str(p)]
        for p in (named or hits):
            try:
                return str(p), json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
    return None, None


# ---------------------------------------------------------------- per-instance diagnosis

def diagnose_instance(pkg, inst, host_ok, want_scan):
    """Return a verdict dict for one instance: {instance, status, headline, fix, evidence{...}}."""
    es = inst.external_scanner
    rt_name = inst.runtime_name or f"{pkg.id}-{inst.name}"
    ev = {"runtime": rt_name, "scanner": es.get("name") if es else None}

    def V(status, headline, fix, **extra):
        ev.update(extra)
        return {"instance": inst.name, "status": status, "headline": headline, "fix": fix, "evidence": ev}

    # ---- STATIC (runtime.yaml) ----
    if inst.runtime_doc is None:
        return V("malformed-scanner", "runtime.yaml is missing or not valid YAML",
                 f"Rebuild the package with senpi-strategy-author; validate with "
                 f"validate_strategy.py before redeploying.")
    if not es:
        scn = [s.get("type") for s in (inst.runtime_doc.get("scanners") or []) if isinstance(s, dict)]
        return V("no-scanner",
                 f"no external_scanner declared (scanners: {scn or 'none'}) — only position_tracker "
                 f"runs, so nothing ever OPENS a position",
                 "Add the external_scanner block (path/entrypoint/signal_data_schema/"
                 "default_signal_validity_seconds) in source, then close.py → deploy.py.")
    missing = [f for f in _REQUIRED_ES if not es.get(f)]
    if missing:
        return V("malformed-scanner",
                 f"external_scanner is missing required field(s): {', '.join(missing)}",
                 "Add them in the runtime.yaml (see runtime-yaml.md → external_scanner field set); "
                 "fix source + redeploy.")
    iv = es.get("interval_seconds")
    if iv is not None and (not isinstance(iv, (int, float)) or isinstance(iv, bool) or iv <= 0):
        return V("interval-zero",
                 f"interval_seconds = {iv!r} → the scanner is never scheduled (must be a positive integer)",
                 "Set a positive interval_seconds (e.g. 300) in source; fix source + redeploy.")
    sub = (es.get("path") or ".").lstrip("./")
    ep = inst.runtime_path.parent / sub / es.get("entrypoint", "scan.py")
    if not ep.is_file():
        return V("entrypoint-missing",
                 f"scan entrypoint not found on disk: {ep}",
                 "The package is broken — re-fetch/rebuild it and redeploy.", entrypoint=str(ep))
    scoring = ep.with_name("scoring.py")
    ev["entrypoint"] = str(ep)
    ev["scoring_present"] = scoring.is_file()

    # optional: run scan() now (works regardless of host — needs only SENPI_AUTH_TOKEN + the package)
    scan_result = None
    if want_scan:
        smp = (inst.runtime_doc or {}).get("strategy", {}).get("margin_pct")
        scan_result = _smoke.smoke(str(inst.runtime_path), es, wallet=None, strategy_margin_pct=smp)
        ev["scan"] = {k: scan_result[k] for k in ("status", "detail", "n_signals", "violations",
                                                   "sizing_warnings", "returned_repr", "traceback")}

    # ---- RUNTIME (host) ----
    if not host_ok:
        base = ("Package looks well-formed. openclaw isn't on THIS host, so runtime/scanner liveness "
                "is UNKNOWN from here — run diagnose.py on the runtime host.")
        if scan_result:
            base += f"  scan() smoke: {scan_result['status']} — {scan_result['detail']}"
        return V("host-unknown", "package is well-formed; runtime state unknown (no openclaw here)", base)

    rt = _cli.find_runtime(rt_name)
    if not rt:
        return V("no-runtime", f"no runtime registered for {rt_name!r} (funded but not autonomous)",
                 f"python3 deploy.py runtime {pkg.id}  (or close.py {pkg.id} to recover funds).")
    if not _cli.runtime_running(rt):
        return V("runtime-stopped", f"runtime {rt_name!r} is registered but NOT running",
                 "Start the OpenClaw gateway, or redeploy: close.py → deploy.py.")

    # scanner-health RPC — the BARREN-aware read
    health = _scanner_health(rt_name)
    entry = _find_scanner_entry(health, es.get("name")) if health else None
    if entry is None:  # fall back to the full state RPC, then to disk
        st = _state_rpc(rt_name)
        entry = _find_scanner_entry(st, es.get("name")) if st else None
    disk_path, disk_state = _read_disk_state(rt_name)
    if disk_path:
        ev["state_json"] = disk_path
        if entry is None and disk_state is not None:
            entry = _find_scanner_entry(disk_state, es.get("name"))

    if entry is None:
        # the runtime is up but the external_scanner isn't in its scanner set → it didn't register
        return V("scanner-not-registered",
                 f"external_scanner {es.get('name')!r} is not registered on runtime {rt_name!r} — it "
                 f"failed to load (only position_tracker is running)",
                 f"Read the load error:  openclaw senpi scanner -r {rt_name} --json  and  openclaw "
                 f"senpi state -r {rt_name} --json  (and {disk_path or 'the on-disk state.json'}); "
                 f"fix the cause in source + redeploy.",
                 scanner_rpc_empty=True)

    runs = _num(entry, "runCount", "runs", "ticks") or 0
    signals = _num(entry, "signals", "signalCount", "signalsProduced")
    errs = _num(entry, "consecutiveErrors", "consecutive_errors", "errorCount", "errors") or 0
    alive = _cli.dig(entry, "alive")
    barren_flag = _cli.dig(entry, "barren")
    ev.update(runs=runs, signals=signals, consecutive_errors=errs, alive=alive)

    if errs and errs > 0:
        d = f"scanner is erroring ({errs} consecutive) — scan() likely throws every tick"
        if scan_result and scan_result["status"] == "threw":
            d += f"; smoke confirms: {scan_result['detail']}"
        return V("scanner-erroring", d,
                 f"See the traceback:  openclaw senpi events -r {rt_name} --level error   (or re-run "
                 f"with --run-scan). Fix the exception in scan()/scoring.py; fix source + redeploy.")

    barren = bool(barren_flag) or (bool(alive) and runs > 0 and (signals == 0))
    if runs == 0 and not alive:
        return V("not-ticked-yet",
                 f"registered but hasn't run yet (runs=0) — first scan() fires on its "
                 f"{iv or es.get('interval_seconds') or '?'}s interval; normal right after deploy",
                 f"Re-check after the interval:  python3 diagnose.py {pkg.id}")

    if barren:
        d = (f"BARREN — scanner is alive and has run ({runs}x) but has emitted 0 signals. It runs "
             f"cleanly; it just never produces a candidate.")
        fix = (f"Either it's correctly quiet (regime/tail-risk strategies sit idle by design), OR the "
               f"thresholds are too tight / the universe is empty, OR the emitted data{{}} fails "
               f"signal_data_schema and is dropped. Run  python3 diagnose.py {pkg.id} --run-scan  to "
               f"split it: [] ⇒ loosen the thesis/inputs; a shape violation ⇒ fix your data{{}} keys. "
               f"Fix source + redeploy — NEVER hand-edit state.")
        if scan_result:
            if scan_result["status"] == "empty":
                d += "  --run-scan: scan() returned [] → the LOGIC/THRESHOLDS never fire (not a shape bug)."
            elif scan_result["status"] == "bad-shape":
                d += (f"  --run-scan: scan() emitted {scan_result['n_signals']} signal(s) but the SHAPE "
                      f"is invalid → the runtime drops them. Violations: "
                      f"{'; '.join(scan_result['violations'][:3])}")
            elif scan_result["status"] == "threw":
                d += f"  --run-scan: scan() THREW → {scan_result['detail']}"
            elif scan_result["status"] == "clean":
                d += (f"  --run-scan: scan() emitted {scan_result['n_signals']} VALID signal(s) just now "
                      f"— the earlier barrenness may have been transient; re-check liveness.")
        return V("barren", d, fix)

    if signals and signals > 0:
        return V("live", f"healthy — scanner alive, {runs} run(s), {signals} signal(s) emitted "
                 f"(positions open when risk gates + slots allow)",
                 "Nothing to fix. If positions still aren't opening, check risk eligibility: "
                 f"openclaw senpi risk -r {rt_name} --json")
    # ran, not flagged barren, signals unknown — inconclusive
    return V("unknown", f"scanner has run {runs}x; signal count unavailable from the RPC",
             f"Inspect directly:  openclaw senpi scanner -r {rt_name} --json")


# ---------------------------------------------------------------- driver

def ensure_pkg(arg, ref, log):
    try:
        return _pkg.load(arg)
    except _pkg.BadPackage as e:
        sid = Path(arg).name
        log(f"package not on disk — fetching {sid}…")
        try:
            _fetch.fetch_package(sid, "strategies", ref=ref)
            return _pkg.load(sid)
        except (_fetch.FetchError, _pkg.BadPackage):
            raise SystemExit(f"error: {e}")


def main(argv):
    ap = argparse.ArgumentParser(description="Diagnose why a deployed strategy isn't opening positions.")
    ap.add_argument("package", help="Strategy id (e.g. divergence-play) or package dir.")
    ap.add_argument("--run-scan", action="store_true",
                    help="Also run scan() once against live MCP and show the literal return + traceback "
                         "(needs SENPI_AUTH_TOKEN). Read-only: it can never place a trade.")
    ap.add_argument("--ref", default=None, help="Branch/ref to fetch the package from if not on disk.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    pkg = ensure_pkg(a.package, a.ref, log)
    dup_copies = _pkg.duplicate_copies(pkg.id, pkg.dir)
    for d in dup_copies:
        print(f"⚠ another on-disk copy of {pkg.id!r} exists at {d} — this run reads {pkg.dir}. If you "
              f"edited that other copy, your fix isn't in the copy the runtime loads.", file=sys.stderr)
    errs = _pkg.validate(pkg)  # structural sanity first (mirrors deploy)
    host_ok = _cli.run_cli(["openclaw", "--version"], timeout=15)[0] == 0

    verdicts = [diagnose_instance(pkg, inst, host_ok, a.run_scan) for inst in pkg.instances]

    if a.json:
        print(json.dumps({"strategy": pkg.id, "version": pkg.version, "openclaw_available": host_ok,
                          "package_errors": errs, "instances": verdicts}, indent=2))
        return 0

    print(f"\n{pkg.id} v{pkg.version} — scanner diagnosis"
          + ("" if host_ok else "  (openclaw not on this host — runtime checks limited)"))
    if errs:
        print("  ⚠ package structural errors (fix these first):")
        for e in errs:
            print(f"      - {e}")
    worst = None
    for v in verdicts:
        icon = _ICON.get(v["status"], "·")
        print(f"\n  {icon} {v['instance']}: {v['status']}")
        print(f"      {v['headline']}")
        print(f"      → {v['fix']}")
        sc = v["evidence"].get("scan")
        if sc and a.run_scan:
            print(f"      scan(): {sc['status']} — {sc['detail'][:100]}")
            if sc.get("returned_repr"):
                print(f"        returned: {sc['returned_repr'][:200]}")
            for vio in (sc.get("violations") or [])[:5]:
                print(f"        • {vio}")
            for sw in (sc.get("sizing_warnings") or [])[:5]:
                print(f"        ⚠ sizing: {sw}")
            if sc.get("traceback"):
                tb = sc["traceback"].strip().splitlines()
                print("        traceback (tail):")
                for ln in tb[-6:]:
                    print(f"          {ln}")
        if v["status"] not in ("live", "not-ticked-yet", "host-unknown"):
            worst = worst or v
    if worst:
        print(f"\n  Verdict: {worst['instance']} → {worst['headline']}")
    elif all(v["status"] in ("live",) for v in verdicts):
        print("\n  Verdict: all scanners are producing — this strategy is trading (or gated by risk/slots).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
