#!/usr/bin/env python3
"""Validate a Senpi strategy PACKAGE (scanner.py + runtime.yaml(s) + strategy.yaml).

Usage:  python3 validate_strategy.py <package-dir> [<package-dir> ...]

Asserts the manifest ↔ runtime ↔ package consistency the deterministic install
relies on. Exit 0 = all packages valid; exit 1 = at least one error.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import yaml  # prefer PyYAML when present
except ImportError:
    import _yaml as yaml  # vendored stdlib-only fallback — agent hosts block pip/PyYAML (PEP 668),
    #                       so importing PyYAML fatally is why this validator was unrunnable in prod.


# external_scanner fields the runtime REQUIRES (senpi-trading-runtime/references/runtime-yaml.md →
# "external_scanner field set"). A scanner missing these — or with interval_seconds ≤ 0 — registers
# but silently never trades: exactly the failure this validator now catches before deploy.
def _external_scanner_errs(name, rt_rel, rt_text):
    errs = []
    try:
        doc = yaml.safe_load(rt_text) or {}
    except Exception:  # noqa: BLE001 — unparseable YAML is already flagged by the caller
        return errs
    if not isinstance(doc, dict):
        return errs
    scanners = doc.get("scanners") or []
    ext = [s for s in scanners if isinstance(s, dict) and s.get("type") == "external_scanner"]
    if not ext:
        # a signal-driven package with an OPEN_POSITION action but no scanner to feed it can never
        # open a position (the "only position_tracker runs" trap). Pure tracker packages are legal.
        opens = [a for a in (doc.get("actions") or [])
                 if isinstance(a, dict) and a.get("action_type") == "OPEN_POSITION"]
        if opens:
            errs.append(f"instance {name}: {rt_rel} has an OPEN_POSITION action but NO external_scanner "
                        f"to feed it — it registers ACTIVE but never opens a position")
        return errs
    for es in ext:
        sn = es.get("name", "?")
        for f in ("path", "entrypoint", "signal_data_schema", "default_signal_validity_seconds"):
            if es.get(f) is None:
                errs.append(f"instance {name}: {rt_rel} external_scanner {sn!r} missing required '{f}'")
        iv = es.get("interval_seconds")
        if iv is not None and (not isinstance(iv, int) or isinstance(iv, bool) or iv <= 0):
            errs.append(f"instance {name}: {rt_rel} external_scanner {sn!r} interval_seconds={iv!r} must "
                        f"be a positive integer (0/negative → the scanner is never scheduled)")
        dv = es.get("default_signal_validity_seconds")
        if dv is not None and (not isinstance(dv, int) or isinstance(dv, bool) or dv <= 0):
            errs.append(f"instance {name}: {rt_rel} external_scanner {sn!r} "
                        f"default_signal_validity_seconds={dv!r} must be a positive integer")
        sch = es.get("signal_data_schema")
        if isinstance(sch, dict):
            if not sch:
                errs.append(f"instance {name}: {rt_rel} external_scanner {sn!r} signal_data_schema is "
                            f"empty (declare every data{{}} key your scan() emits)")
            for key, spec in sch.items():
                t = spec.get("type") if isinstance(spec, dict) else None
                if t not in ("string", "number", "boolean", "object", "array"):
                    errs.append(f"instance {name}: {rt_rel} signal_data_schema.{key} invalid type {t!r} "
                                f"(must be string/number/boolean/object/array)")
        elif sch is not None:
            errs.append(f"instance {name}: {rt_rel} signal_data_schema must be a map of key→{{type}}")
    # NOTE: whether scan()'s ACTUAL output keys match this schema can only be known by RUNNING scan()
    # — that's the deploy.py smoke gate + `diagnose.py --run-scan`, not a static check.
    return errs


def validate(pkg: Path) -> list:
    errs = []
    man_path = pkg / "strategy.yaml"
    if not man_path.is_file():
        return [f"{pkg}: missing strategy.yaml"]
    try:
        man = yaml.safe_load(man_path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        return [f"{man_path}: unparseable ({e})"]

    sid = man.get("id")
    if sid != pkg.name:
        errs.append(f"id {sid!r} != package dir {pkg.name!r}")
    if not man.get("version"):
        errs.append("missing version (single source for catalog + attribution)")
    if not man.get("instances"):
        errs.append("no instances[]")

    seen_wallet_envs = set()
    for inst in man.get("instances", []):
        name = inst.get("name", "?")
        rt_rel = inst.get("runtime")
        wenv = inst.get("wallet_env")

        rt = pkg / rt_rel if rt_rel else None
        if not rt or not rt.is_file():
            errs.append(f"instance {name}: runtime {rt_rel!r} not found")
            continue
        rt_text = rt.read_text()
        errs += _external_scanner_errs(name, rt_rel, rt_text)

        # data_retention: Runtime 3.0 uses data_retention_seconds (integer 3600–604800);
        # the v2 data_retention_hours field is deprecated. (See senpi-trading-runtime/references/runtime-yaml.md.)
        if re.search(r"^\s*data_retention_hours\s*:", rt_text, re.M):
            errs.append(f"instance {name}: {rt_rel} uses deprecated 'data_retention_hours' — "
                        f"use 'data_retention_seconds' (integer 3600-604800; hours x 3600)")
        m = re.search(r"^\s*data_retention_seconds\s*:\s*([0-9]+)", rt_text, re.M)
        if m and not (3600 <= int(m.group(1)) <= 604800):
            errs.append(f"instance {name}: {rt_rel} data_retention_seconds {m.group(1)} "
                        f"out of range [3600, 604800] (1h-7d)")

        # guard_rails cooldowns: the runtime REJECTS below-minimum values at registration
        # (cooldown_seconds >= 60, per_asset_cooldown_seconds >= 300) — a 0 fails to register.
        # (See senpi-trading-runtime/references/runtime-yaml.md.)
        for _field, _lo in (("cooldown_seconds", 60), ("per_asset_cooldown_seconds", 300)):
            cm = re.search(rf"^\s*{_field}\s*:\s*([0-9]+)", rt_text, re.M)
            if cm and int(cm.group(1)) < _lo:
                errs.append(f"instance {name}: {rt_rel} {_field} {cm.group(1)} below runtime min {_lo}")

        # Protection is not optional: every instance must ship a DSL exit block (the built-in
        # trailing stop-loss / two-phase exit). Downstream skills (senpi-portfolio / -strategy-ops)
        # treat a deployed strategy as risk-managed — a strategy with no DSL exit is a naked position.
        if not re.search(r"^\s*(exit|dsl_preset)\s*:", rt_text, re.M):
            errs.append(f"instance {name}: {rt_rel} has no DSL exit block (exit:/dsl_preset:) — "
                        f"every strategy must ship built-in protection")

        # Self-describing is not optional: every instance needs a substantive top-level `description`.
        # The runtime REGISTERS it (installed_runtimes.json) and senpi-portfolio reads it back as the
        # strategy's mandate — "is it doing its job?". A missing/stub description makes an authored
        # strategy invisible to portfolio analysis (and works the same for user-authored strategies).
        dlines, capture, dbody = rt_text.splitlines(), False, []
        for ln in dlines:
            if not capture and re.match(r"^description\s*:", ln):
                capture = True
                dbody.append(re.sub(r"^description\s*:\s*[>|]?\s*", "", ln))
                continue
            if capture:
                if ln.strip() == "" or ln[:1] in (" ", "\t"):
                    dbody.append(ln.strip())
                else:
                    break
        if len(re.sub(r"\s+", "", " ".join(dbody))) < 40:
            errs.append(f"instance {name}: {rt_rel} has no meaningful top-level description: — write "
                        f"2-4 sentences on what it trades / the edge / how it exits; the runtime "
                        f"registers it and senpi-portfolio reads it back as the strategy's mandate")

        # Runtime 3.0 scanner package: <runtime_dir>/scanners/scan.py exports scan(inputs, ctx);
        # the thesis math is a sibling scanners/scoring.py imported as `import scoring` (NO __init__.py).
        scn_dir = rt.parent / "scanners"
        scan_py = scn_dir / "scan.py"
        scoring_py = scn_dir / "scoring.py"
        if not scan_py.is_file():
            errs.append(f"instance {name}: missing {scan_py.relative_to(pkg)} (Runtime 3.0 scan() entrypoint)")
        elif "def scan(" not in scan_py.read_text():
            errs.append(f"instance {name}: {scan_py.relative_to(pkg)} does not define scan(inputs, ctx)")
        if not scoring_py.is_file():
            errs.append(f"instance {name}: missing sibling {scoring_py.relative_to(pkg)} ('import scoring' will fail)")
        if (scn_dir / "__init__.py").is_file():
            errs.append(f"instance {name}: {(scn_dir / '__init__.py').relative_to(pkg)} present — remove it (sibling-import model)")
        if not wenv or ("${%s}" % wenv) not in rt_text:
            errs.append(f"instance {name}: wallet_env {wenv!r} not bound as ${{{wenv}}} in {rt_rel}")
        seen_wallet_envs.add(wenv)

    # multi-instance must use distinct wallets
    if len(man.get("instances", [])) > 1 and len(seen_wallet_envs) < len(man["instances"]):
        errs.append("multi-instance strategy must declare a distinct wallet_env per instance")

    # scanner present + parses; no '@senpi/runtime' without -ai anywhere
    for py in pkg.rglob("*.py"):
        try:
            ast.parse(py.read_text())
        except SyntaxError as e:
            errs.append(f"{py.name}: syntax error ({e})")
    for f in pkg.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".yaml", ".md"):
            t = f.read_text(errors="ignore")
            if "@senpi/runtime" in t and "@senpi-ai/runtime" not in t.replace("@senpi/runtime", ""):
                # crude: flag any bare @senpi/runtime occurrence
                if "@senpi/runtime" in t:
                    errs.append(f"{f.name}: contains '@senpi/runtime' (use '@senpi-ai/runtime')")
                    break
    return errs


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for d in argv[1:]:
        pkg = Path(d).resolve()
        errs = validate(pkg)
        if errs:
            bad += 1
            print(f"✗ {pkg.name}:")
            for e in errs:
                print(f"    - {e}")
        else:
            man = yaml.safe_load((pkg / "strategy.yaml").read_text())
            print(f"✓ {pkg.name} v{man.get('version')} ({len(man.get('instances', []))} instance(s))")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv)
