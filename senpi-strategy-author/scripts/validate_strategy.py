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

try:
    import yaml  # prefer PyYAML when present
except ImportError:
    # Agent hosts may lack PyYAML AND pip (externally-managed Python) — never make the author
    # pip-install just to validate. Same vendored stdlib-only fallback _pkg.py (strategy-ops) uses.
    import _yaml as yaml


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MARGIN_PCT_RE = re.compile(r"margin_?pct", re.I)


def margin_fraction_offenders(doc, path=""):
    """Margin-percent keys whose value is a fraction (0,1] where a PERCENT (0,100] is required
    (`marginPct: 0.10` meant 10 — 100× too small). Walks the whole doc (scanners[].inputs,
    strategy.margin_pct, or a top-level emit; any key: marginPct / marginPctBase / margin_pct).
    Returns [(dotted_key, value), ...]."""
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            kp = f"{path}.{k}" if path else str(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and _MARGIN_PCT_RE.search(str(k)) and 0 < v <= 1:
                out.append((kp, v))
            else:
                out.extend(margin_fraction_offenders(v, kp))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(margin_fraction_offenders(v, f"{path}[{i}]"))
    return out


# Candles (market_get_asset_data) are keyed o/h/l/c/v; the long forms (open/high/low/close) don't exist,
# so `candle.get("close")` is always None → the scan silently emits nothing. A file that reads a long key
# with NO short-form counterpart anywhere is the bug (0 fleet false positives). volume/v is EXCLUDED —
# scanners legitimately read a `volume` field from market rows.
_OHLCV_LONG = {"open": "o", "high": "h", "low": "l", "close": "c"}
_CANDLE_ACCESS = {k: re.compile(r"""(?:\.get\(|\[)\s*['"]%s['"]""" % k)
                  for k in list(_OHLCV_LONG) + list(_OHLCV_LONG.values())}


def candle_key_bug(text):
    """Long-form OHLCV keys accessed on a dict with NO short-form counterpart in the file — the silent
    candle-key bug (`candle.get("close")` where Senpi candles are keyed `c`). Returns [(long, short), ...]."""
    return [(lng, sht) for lng, sht in _OHLCV_LONG.items()
            if _CANDLE_ACCESS[lng].search(text) and not _CANDLE_ACCESS[sht].search(text)]


# A signal `data` field declared `type: number|string|...` in signal_data_schema is REJECTED by the
# runtime when its value is null — even with `required: false`. The whole candidate is dropped
# (`candidate_rejected`), silently, so the strategy funds and never trades. An optional field that
# does not apply to this signal must be OMITTED, not set to None. (ibis shipped 100% dead on this.)
_NONE_IN_DATA = re.compile(r'"(\w+)":\s*(?:th\[[^\]]+\]|None)\s*(?:,|\})')
_STRIPS_NONE = re.compile(r'if\s+v\s+is\s+not\s+None')


def null_signal_field_offenders(scan_src, scoring_src, schema):
    """Fields emitted into a signal's `data` that can be None while declared as a typed schema
    field. Returns [(field, declared_type), ...]. Empty when the scanner strips Nones at emit."""
    if not schema or _STRIPS_NONE.search(scan_src):
        return []
    nullable = set(re.findall(r'"(\w+)":\s*None', scoring_src or ""))
    out = []
    for m in re.finditer(r'"(\w+)":\s*th\["(\w+)"\]', scan_src):
        camel, snake = m.group(1), m.group(2)
        ty = (schema.get(camel) or {}).get("type") if isinstance(schema.get(camel), dict) else None
        if snake in nullable and ty in ("number", "string", "boolean", "array"):
            out.append((camel, ty))
    return sorted(set(out))


def _runtime_docs(pkg: Path):
    """Every parsed runtime.yaml in the package (flat or nested)."""
    out = []
    for rt in pkg.rglob("runtime.yaml"):
        try:
            d = yaml.safe_load(rt.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


# ── dual-DEX (main / xyz) blindness — both failures are SILENT ───────────────
# Hyperliquid is two sub-DEXes behind ONE cross-margined wallet, and the two APIs disagree about how a
# name is spelled. Neither mistake raises: the strategy just never sees an `xyz:` name, or never sees the
# positions it already holds. Both checks below are POSITIVE-EVIDENCE: they ask "did this file do the
# thing that makes it correct?", never "does it contain one particular wrong spelling" — an enumeration
# of wrong spellings is defeated by any rewording of the same bug.
_DEX_EVIDENCE = re.compile(
    r'''["']dex["']|\bdex\s*=|\.dex\b'''            # consults the row's dex field
    r'''|removeprefix\(\s*["']xyz:|startswith\(\s*["']xyz:'''   # …or normalises the xyz: prefix
    r'''|split\(\s*["']:["']''')
_LEADERBOARD_TOOL = "leaderboard_get_markets"


def _calls_leaderboard(tree):
    """True only when the tool is actually CALLED — the name appearing in a docstring or comment
    (scoring modules routinely describe the row shape they are handed) is not a use."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Constant) and arg.value == _LEADERBOARD_TOOL:
                    return True
    return False
_ASSET_POS_KEY = "assetPositions"
_CH_TOOL = "strategy_get_clearinghouse_state"


# POSITIVE xyz exposure only. The mere word "xyz" is not exposure — several packages mention it solely
# to BAN it ("XYZ banned", `xyzBanned: true`), and those can never receive a prefixed name, so the
# leaderboard rule would be pure noise for them.
_XYZ_EXPOSURE = re.compile(
    r"[\"']?xyz:[A-Za-z0-9]"                                   # a prefixed asset literal (xyz:NVDA)
    r"|xyz_equities|xyz_?commodit|xyz_?indic"                  # an xyz asset class
    r"|includeXyz\s*:\s*true|maxXyzNames|xyzVolFloor|xyzAssets",  # an xyz universe/derivation input
    re.I)
_XYZ_BANNED = re.compile(r"xyz_?banned\s*:\s*true", re.I)


def _pkg_is_main_only(ctx):
    """True when the package declares no POSITIVE xyz exposure — no prefixed asset, no xyz asset class,
    no xyz universe input — or bans xyz outright. Such a package can never receive a prefixed name, so
    the leaderboard rule would only add noise. `ctx` is the merged strategy.yaml + runtime.yaml text;
    absent -> False (never suppress on missing context)."""
    if not ctx:
        return False
    s = str(ctx)
    if _XYZ_BANNED.search(s):
        return True
    return not _XYZ_EXPOSURE.search(s)


def _iter_is_dex_sections(node):
    """True when a `for` iterates BOTH sub-DEX sections — in either order, or generically.

    Accepts the literal ("main", "xyz") / ("xyz", "main") in any container, and any `.values()` /
    `.items()` walk of the clearinghouse dict (which visits both sections by construction)."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        vals = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        return {"main", "xyz"} <= vals
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("values", "items")
    return False


def _clearinghouse_names(tree):
    """Names bound to the RAW clearinghouse result — the call itself, and any `x.get("data", x)`
    unwrap of one. These are the receivers on which `.get("assetPositions")` is the bug; a per-section
    dict is a different receiver and is never collected here."""
    names = set()
    for _ in range(3):                       # settle chained unwraps (ch -> data -> d)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not node.targets:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            src = ast.dump(node.value)
            if _CH_TOOL in src:
                names.add(tgt.id)
            elif '"data"' in src.replace("'", '"'):
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Name) and sub.id in names:
                        names.add(tgt.id)
                        break
    return names


def _top_level_asset_position_reads(tree, ch_names):
    """`<clearinghouse>.get("assetPositions")` (or `[...]`) reached OUTSIDE a both-sections loop.

    Scoped to the read itself, not the file: a file that handles the sections correctly in one place
    and grows a new top-level read elsewhere is still flagged — that is the likeliest regression."""
    bad = []

    def walk(node, in_dex_loop):
        for child in ast.iter_child_nodes(node):
            nxt = in_dex_loop or (isinstance(node, ast.For) and _iter_is_dex_sections(node.iter))
            recv = key = None
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "get" and child.args
                    and isinstance(child.args[0], ast.Constant)):
                recv, key = child.func.value, child.args[0].value
            elif (isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant)):
                recv, key = child.value, child.slice.value
            if key == _ASSET_POS_KEY and isinstance(recv, ast.Name) and recv.id in ch_names \
                    and not nxt:
                bad.append(getattr(child, "lineno", 0))
            walk(child, nxt)

    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]:
        # A function that ALREADY reads both sections has handled the shape; a top-level read beside it
        # is a deliberate legacy-flat fallback, not the bug. Scoped to the FUNCTION, never the file — a
        # new function that grows a top-level read has no such read of its own and is still flagged.
        if any(_iter_is_dex_sections(n.iter) for n in ast.walk(fn) if isinstance(n, ast.For)):
            continue
        walk(fn, False)
    return sorted(set(bad))


def dex_blind_offenders(text, pkg_context=None):
    """Silent dual-DEX bugs in one scanner file. `pkg_context` is the package's declared exposure
    (strategy.yaml + runtime inputs, as text) — used only to suppress the leaderboard rule for a
    package with no xyz exposure at all. Returns a list of message strings; [] on unparseable source
    (the syntax check reports that separately)."""
    out = []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    if _calls_leaderboard(tree) and not _DEX_EVIDENCE.search(text) \
            and not _pkg_is_main_only(pkg_context):
        out.append("uses `leaderboard_get_markets` but never consults a `dex` field or normalises the "
                   "`xyz:` prefix. Leaderboard rows carry a BARE ticker plus a separate `dex`; a universe "
                   "carries `xyz:NAME`. Match bare tickers AND require the dex to agree — otherwise every "
                   "xyz name reads as 'no smart-money data' and a hard gate blocks it silently.")

    ch_names = _clearinghouse_names(tree)
    for line in _top_level_asset_position_reads(tree, ch_names):
        out.append(f"line {line}: reads `assetPositions` off the raw clearinghouse result. "
                   f"`{_CH_TOOL}` returns {{'main': ..., 'xyz': ...}} and the positions live INSIDE each "
                   f"section — off the top level it is ALWAYS empty, so the scanner re-opens names it "
                   f"already holds. Enumerate both sections.")
    return out


def dex_scan_files(pkg):
    """The .py files BOTH validators check for dual-DEX blindness — identical on the author and ops
    sides so author-green == deploy-green. Package sources only: `tests/` is excluded because a
    deliberate bug fixture there is not a defect."""
    return sorted(p for p in pkg.rglob("*.py") if "tests" not in p.parts)


def dex_pkg_context(pkg):
    """The package's declared exposure (manifest + every runtime.yaml), as text. Used ONLY to suppress
    the leaderboard rule for a package with no xyz exposure at all."""
    parts = []
    for name in ("strategy.yaml",):
        f = pkg / name
        if f.is_file():
            parts.append(f.read_text(errors="ignore"))
    for rt in pkg.rglob("runtime.yaml"):
        parts.append(rt.read_text(errors="ignore"))
    return "\n".join(parts)


def _flat_wallet_env(pkg: Path, sid) -> str:
    """Mirror the deployer's flat-instance synthesis: bind wallet_env to the ${...} the flat
    runtime.yaml already uses for its wallet, falling back to <ID>_WALLET."""
    try:
        doc = yaml.safe_load((pkg / "runtime.yaml").read_text()) or {}
        m = _VAR_RE.search(str((doc.get("strategy") or {}).get("wallet") or ""))
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001 — best-effort; the binding check below flags a miss
        pass
    return re.sub(r"[^A-Za-z0-9]", "_", str(sid or "")).upper().strip("_") + "_WALLET"


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
        # FLAT single-instance package — the layout agents naturally scaffold; the deployer accepts
        # it (strategy-ops v2.4.0+) by synthesizing the canonical `main` instance. Synthesize the SAME
        # instance here so every code-level check below still runs — a red author validator on a
        # package the deployer would accept is exactly the author↔ops drift this file must not have.
        if (pkg / "runtime.yaml").is_file():
            man = dict(man)
            man["instances"] = [{"name": "main", "runtime": "runtime.yaml",
                                 "wallet_env": _flat_wallet_env(pkg, sid)}]
        else:
            errs.append("no instances[] (and no flat root runtime.yaml to synthesize one from)")

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

        # Linkage convention — the deployer + runtime engine key on these, and it was the #1
        # tripwire in the 3-model deploy bake-off (every model wrote `name: <id>`): the runtime's
        # `name:` must be `<id>-<instance>` and `group:` must be `<id>`. Same prescriptive wording
        # as strategy-ops `_pkg.validate`, so author-green ≈ deploy-green.
        try:
            rt_doc = yaml.safe_load(rt_text) or {}
        except Exception:  # noqa: BLE001 — unparseable YAML surfaces via the checks below
            rt_doc = None
        if isinstance(rt_doc, dict):
            expect = f"{sid}-{name}"
            if rt_doc.get("name") != expect:
                errs.append(f"instance {name}: set runtime `name: {expect}` (found {rt_doc.get('name')!r})")
            if rt_doc.get("group") != sid:
                errs.append(f"instance {name}: set runtime `group: {sid}` (found {rt_doc.get('group')!r})")
            # marginPct is a PERCENT in (0,100]; a value <= 1 is the fraction slip (0.10 meant 10, 100x
            # too small). Flag it pre-deploy with the exact fix. (See scan-contract.md.)
            for kp, val in margin_fraction_offenders(rt_doc):
                errs.append(f"instance {name}: `{kp}` must be a PERCENT in (0,100] — set {val * 100:g} "
                            f"(not {val})")

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

    # scanner present + parses; candle-key sanity; no '@senpi/runtime' without -ai anywhere
    for py in pkg.rglob("*.py"):
        src = py.read_text()
        try:
            ast.parse(src)
        except SyntaxError as e:
            errs.append(f"{py.name}: syntax error ({e})")
        for lng, sht in candle_key_bug(src):
            errs.append(f"{py.name}: candles are keyed `o/h/l/c/v` — use `candle['{sht}']`, not `{lng}` "
                        f"(no such key → always None → the scan emits nothing).")

    # dual-DEX blindness — positive-evidence checks, same file scope as strategy-ops
    _dex_ctx = dex_pkg_context(pkg)
    for py in dex_scan_files(pkg):
        for msg in dex_blind_offenders(py.read_text(errors="ignore"), _dex_ctx):
            errs.append(f"{py.name}: {msg}")

    # null-in-typed-schema: an optional signal field set to None is REJECTED by the runtime
    # (candidate_rejected, silently) — omit it instead. Checked per scanner dir so scan.py,
    # its sibling scoring.py, and the runtime's signal_data_schema are compared together.
    for scan_py in pkg.rglob("scanners/scan.py"):
        scoring_py = scan_py.with_name("scoring.py")
        scoring_src = scoring_py.read_text() if scoring_py.is_file() else ""
        for rt_doc in _runtime_docs(pkg):
            for sc in (rt_doc.get("scanners") or []):
                if not isinstance(sc, dict) or sc.get("type") != "external_scanner":
                    continue
                for field, ty in null_signal_field_offenders(
                        scan_py.read_text(), scoring_src, sc.get("signal_data_schema") or {}):
                    errs.append(
                        f"{scan_py.name}: signal field `{field}` is declared `type: {ty}` but can be "
                        f"None — OMIT it when it doesn't apply (a null fails schema validation and the "
                        f"runtime drops the whole candidate silently). Build data as "
                        f"`{{k: v for k, v in {{...}}.items() if v is not None}}`.")
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
            n = len(man.get("instances") or [])
            label = f"{n} instance(s)" if n else "flat single-instance"
            print(f"✓ {pkg.name} v{man.get('version')} ({label})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv)
