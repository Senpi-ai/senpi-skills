#!/usr/bin/env python3
"""Validate a Senpi strategy PACKAGE (scanner.py + runtime.yaml(s) + strategy.yaml).

Usage:  python3 validate_strategy.py <package-dir> [<package-dir> ...]

Asserts the manifest ↔ runtime ↔ package consistency the deterministic install
relies on. Exit 0 = all packages valid; exit 1 = at least one error.

Two channels. `validate()` returns ERRORS (the exit code); `warnings()` returns ADVISORY findings —
things a green package will still make the user feel in their first week (a stop inside intraday
noise, multi-slot sizing that the second open cannot fund, a daily entry cap at or below the slot count, a daily entry cap at or below the slot count). Warnings never change the exit code; the author relays them.
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
# The keys a SLOT SIZE is written under, and the only two places one is read: `strategy.margin_pct`
# (the single margin the runtime itself interprets) and the `marginPct` / `marginPctBase` a scanner
# passes through to its emitted signal. Deliberately NOT a `*marginPct*` walk of the whole document:
# a scanner's clamp bounds (`minMarginPct`) and its private tier tunables are in whatever units that
# scanner defines and are converted before emit, so a pattern refuses CORRECT packages — it made
# caribou, dire and hydra undeployable. Same scoping the runtime settled on independently
# (`findMarginPctFraction`, senpi-trading-runtime src/validate/recipe-checks.ts).
_SIZING_KEYS = {"marginpct", "margin_pct", "marginpctbase", "margin_pct_base"}
_SIZING_PARENTS = {"strategy", "inputs"}


def margin_fraction_offenders(doc, path="", parent=None):
    """Slot-size keys whose value is a fraction (0,1] where a PERCENT (0,100] is required
    (`marginPct: 0.10` meant 10 — 100× too small, so every order lands under the min notional).
    Scoped to `_SIZING_KEYS` under `_SIZING_PARENTS`; an emitted per-signal value is checked at the
    live stage instead, where the real number is visible rather than inferred. Kept identical to
    senpi-strategy-ops `_pkg.margin_fraction_offenders`. Returns [(dotted_key, value), ...]."""
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            kp = f"{path}.{k}" if path else str(k)
            if parent in _SIZING_PARENTS and str(k).lower() in _SIZING_KEYS \
                    and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1:
                out.append((kp, v))
            else:
                out.extend(margin_fraction_offenders(v, kp, str(k).lower()))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(margin_fraction_offenders(v, f"{path}[{i}]", parent))
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
    # Same rule, same wording as strategy-ops `_pkg.validate`, so author-green == deploy-green: the id
    # becomes the wallet's `skillName` stamp verbatim while the backend stores it case-normalized, so
    # a mixed-case id is stamped under one spelling and looked up under another.
    if sid and str(sid) != str(sid).lower():
        errs.append(f"id {str(sid)!r} must be lowercase — set `id: {str(sid).lower()}` in "
                    f"strategy.yaml and rename the package directory to match. The id is written "
                    f"into the wallet's `skillName` stamp verbatim and read back case-normalized, so "
                    f"a mixed-case id is stamped under one spelling and looked up under another")
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
        # The runtime REGISTERS it and senpi-portfolio reads it back (via `openclaw senpi runtime list
        # --json`, never a registry file) as the strategy's mandate — "is it doing its job?". A
        # missing/stub description makes an authored strategy invisible to portfolio analysis (and
        # works the same for user-authored strategies).
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
        # Asked of the PARSED `strategy.wallet`, never of the file's text. A `${WALLET_ENV}` sitting
        # anywhere at all — a `note:`, a comment, an input nothing reads — satisfies a text search
        # while `strategy.wallet` holds a hardcoded address; the render then substitutes that stray
        # token harmlessly and leaves no `${...}`, so the unresolved-placeholder check clears it too.
        # Deploy would fund a fresh wallet and install the strategy, exit engine included, against
        # the pinned one. The runtime refuses that (`E_VALIDATE_WALLET_UNBOUND`) off this same field,
        # so asking a weaker question here is how author-green stops meaning deploy-green.
        # An OPEN_POSITION action with the taker fallback OFF is accepted by every static check and
        # cannot be executed: the executor does not rest maker orders, so it fails every order or —
        # worse — a partial maker fill is reconciled as a foreign position and DSL-closed, fees both
        # ways for no position (five days on a live book, 68 of 79 signals). Decidable here, so it is
        # an error here, not prose.
        if isinstance(rt_doc, dict):
            for act in rt_doc.get("actions") or []:
                if not isinstance(act, dict) or act.get("action_type") != "OPEN_POSITION":
                    continue
                params = act.get("params") if isinstance(act.get("params"), dict) else {}
                aname = act.get("name", "?")
                ot = params.get("order_type")
                # The action-path `params` is an open record in the runtime schema, so `order_type` is
                # checked nowhere before the executor — and the executor does not refuse an unknown one,
                # it falls back to MARKET with a warning: full taker fees on every entry while the recipe
                # says otherwise. A LIMIT open needs a price the runtime's open action never supplies.
                if ot is not None and (not isinstance(ot, str) or ot not in OPEN_ORDER_TYPES):
                    errs.append(
                        f"instance {name}: [exec] action {aname!r} has `params.order_type: {ot!r}` — not an "
                        f"order type the runtime knows ({', '.join(OPEN_ORDER_TYPES)}). It would not fail: the "
                        f"runtime logs a warning and falls back to MARKET, so every entry pays full taker fees "
                        f"while the recipe says otherwise. Write `FEE_OPTIMIZED_LIMIT` (a maker window, then "
                        f"taker) or `MARKET`")
                elif ot == "LIMIT":
                    errs.append(
                        f"instance {name}: [exec] action {aname!r} opens with `params.order_type: LIMIT` — a "
                        f"LIMIT order needs a `limitPrice`, and the runtime's open action never supplies one, "
                        f"so the order cannot be placed as written. Use `FEE_OPTIMIZED_LIMIT` for a maker-first "
                        f"entry, or `MARKET`")
                folo = params.get("fee_optimized_limit_options")
                if ot == "FEE_OPTIMIZED_LIMIT" and isinstance(folo, dict) and folo.get("ensure_execution_as_taker") is False:
                    errs.append(
                        f"instance {name}: [exec] action {aname!r} opens with "
                        f"`ensure_execution_as_taker: false` — a maker-only entry. The executor cannot rest "
                        f"a maker order: it fails every order, or a partial fill is reconciled as a foreign "
                        f"position and closed (fees both ways, no position). Set "
                        f"`ensure_execution_as_taker: true` and keep `execution_timeout_seconds` as the maker "
                        f"window before the taker fallback")
        if not wenv:
            errs.append(f"instance {name}: missing wallet_env in strategy.yaml")
        elif isinstance(rt_doc, dict):
            _strat = rt_doc.get("strategy")
            _bound = _strat.get("wallet") if isinstance(_strat, dict) else None
            _bound = _bound.strip() if isinstance(_bound, str) else None
            if _bound != "${%s}" % wenv:
                _found = "no strategy.wallet" if _bound is None else repr(_bound)
                errs.append(f"instance {name}: set runtime `strategy.wallet: \"${{{wenv}}}\"` in "
                            f"{rt_rel} (found {_found}) — deploy substitutes the wallet it creates, "
                            f"so a literal address there funds one wallet and trades another")
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


# ---------------------------------------------------------------------------------------------
# ADVISORY warnings — a channel of their own, never an error. Each one is a fact the deploy report
# cannot say and that a validate PASS does not cover: the package runs, and then behaves like this.
# ---------------------------------------------------------------------------------------------

# A hard stop closer than this to entry, in PRICE terms, sits inside ordinary intraday movement on
# most perps. It is hit by a wick, not by the thesis failing, and every hit pays a round trip of fees;
# the user then reads a string of small losses as "the strategy is broken". `max_loss_pct` is ROE %,
# so the price distance is max_loss_pct ÷ leverage — more leverage is a TIGHTER stop.
WARN_STOP_PRICE_PCT = 1.0
# [fees] — fee spend as % of the budget per DAY above which we warn. An ESTIMATE from the recipe's own
# cadence / cap / slots / margin / leverage at an ASSUMED 0.05% per side (taker + builder, stated in the
# message, never a quote): 1,314 fills on a $200 book paid $59 in fees against −$46 of PnL — fees, not
# direction, took the money, and nothing said so before the user funded it.
WARN_FEE_PCT_PER_DAY = 0.5
# The order types the runtime's open path accepts (its constants/order-types.ts VALID_OPEN_ORDER_TYPES).
# Anything else is not refused by the runtime: it logs a warning and falls back to MARKET.
OPEN_ORDER_TYPES = ("MARKET", "LIMIT", "FEE_OPTIMIZED_LIMIT")
FEE_RATE_PER_SIDE = 0.0005

_PRESETS_FILE = Path(__file__).resolve().parent.parent / "references" / "dsl-presets.yaml"
# The free-margin reads a multi-slot scanner makes before it emits (the camel `_get_positions` gate):
# without one, every signal in a tick is sized off the same balance and only the first can fund.
# Searched across every module in `scanners/` — authored packages split the read into a sibling file.
_FREE_MARGIN_RE = re.compile(
    r"withdrawable|free_margin|freeMargin|available_margin|availableMargin|marginSummary|totalMarginUsed")


def _num(v):
    """A real number, or None — bools are not numbers here (`enabled: true` is not a leverage)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _nums(v):
    """Every number inside a scalar / list / dict value (a `leverage: {conservative: 3, …}` map, a
    `leverageTiers` list of tiers) — the shapes the catalog actually uses for these knobs."""
    if isinstance(v, dict):
        return [n for x in v.values() for n in _nums(x)]
    if isinstance(v, list):
        return [n for x in v for n in _nums(x)]
    n = _num(v)
    return [n] if n is not None else []


def _preset_max_loss(name):
    """`max_loss_pct` of a NAMED preset, read from this skill's own dsl-presets.yaml (None if unknown)."""
    try:
        doc = yaml.safe_load(_PRESETS_FILE.read_text()) or {}
        p = ((doc.get("presets") or {}).get(name) or {}).get("dsl_preset") or {}
        v = _num(p.get("max_loss_pct"))
        return v if v is not None else _num((p.get("phase1") or {}).get("max_loss_pct"))
    except Exception:  # noqa: BLE001 — an unreadable preset file is "cannot tell", never a warning
        return None


def resolved_max_loss_pct(rt_doc):
    """The hard-stop ROE % this recipe will enforce, or None when it cannot be told from the file.
    Same order the runtime resolves it in (senpi-trading-runtime/references/runtime-yaml.md:
    `dsl_preset.max_loss_pct` → `phase1.max_loss_pct`); a named preset is looked up in
    `references/dsl-presets.yaml`."""
    ex = rt_doc.get("exit") if isinstance(rt_doc, dict) else None
    if not isinstance(ex, dict):
        return None
    pre = ex.get("dsl_preset")
    if isinstance(pre, str):
        return _preset_max_loss(pre)
    for scope in ((pre if isinstance(pre, dict) else {}), ex):
        v = _num(scope.get("max_loss_pct"))
        if v is None and isinstance(scope.get("phase1"), dict):
            v = _num(scope["phase1"].get("max_loss_pct"))
        if v is not None:
            return v
    return None


def max_leverage(rt_doc):
    """The highest leverage the recipe can trade at, or None: `strategy.default_leverage`, the
    per-risk `leverage_multipliers`, and the scanner inputs' `leverage` / `maxLeverage` /
    `leverageTiers` in whatever shape they take (scalar, per-risk map, tier list)."""
    found = []
    strat = rt_doc.get("strategy") if isinstance(rt_doc, dict) else None
    if isinstance(strat, dict):
        found += _nums(strat.get("default_leverage")) + _nums(strat.get("leverage_multipliers"))
    for sc in (rt_doc.get("scanners") or []) if isinstance(rt_doc, dict) else []:
        inp = sc.get("inputs") if isinstance(sc, dict) else None
        if isinstance(inp, dict):
            for k in ("leverage", "maxLeverage", "max_leverage", "leverageCap"):
                found += _nums(inp.get(k))
            found += tier_leverages(inp.get("leverageTiers"))
    found = [v for v in found if v > 0]
    return max(found) if found else None


def tier_leverages(tiers):
    """The LEVERAGE column of `leverageTiers`, in the two shapes the catalog uses. A list of rows is
    documented as `[[min_score, lev, margin_pct], ...]` (condor: `[[15, 10, 80], ...]`) — flattening it
    reads a score threshold or a margin percentage as leverage (condor came out as 80x). A map is
    `{apex: 5, good: 4, base: 3}` — every value is a leverage."""
    if isinstance(tiers, dict):
        return _nums(tiers)
    if isinstance(tiers, list):
        out = []
        for row in tiers:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                v = _num(row[1])
                if v is not None:
                    out.append(v)
            else:
                out += _nums(row)
        return out
    return []


def tier_margins(tiers):
    """The MARGIN column (index 2) of row-shaped `leverageTiers`; a map carries no margin."""
    out = []
    if isinstance(tiers, list):
        for row in tiers:
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                v = _num(row[2])
                if v is not None:
                    out.append(v)
    return out


def slot_plan(rt_doc):
    """(slots, margin % per slot or None). Slots from `strategy.slots` (the ceiling the runtime
    enforces), else the scanner's `maxSlots`; margin from `strategy.margin_pct`, else the largest
    `marginPct`/`marginPctBase` the scanner inputs carry (a PERCENT — see margin_fraction_offenders)."""
    strat = rt_doc.get("strategy") if isinstance(rt_doc, dict) else None
    strat = strat if isinstance(strat, dict) else {}
    slots = _num(strat.get("slots"))
    margin = _num(strat.get("margin_pct"))
    for sc in (rt_doc.get("scanners") or []) if isinstance(rt_doc, dict) else []:
        inp = sc.get("inputs") if isinstance(sc, dict) else None
        if not isinstance(inp, dict):
            continue
        if slots is None:
            slots = _num(inp.get("maxSlots"))
        if margin is None:
            ms = _nums(inp.get("marginPct")) + _nums(inp.get("marginPctBase")) + tier_margins(inp.get("leverageTiers"))
            margin = max(ms) if ms else None
    return (int(slots) if slots and slots >= 1 else 1), margin


def _daily_cap(rt_doc):
    risk = rt_doc.get("risk") if isinstance(rt_doc, dict) else None
    rails = risk.get("guard_rails") if isinstance(risk, dict) else None
    return _num(rails.get("max_entries_per_day")) if isinstance(rails, dict) else None


def _interval_seconds(rt_doc):
    """The fastest external scanner cadence in the recipe, or None."""
    best = None
    for sc in (rt_doc.get("scanners") or []) if isinstance(rt_doc, dict) else []:
        if isinstance(sc, dict) and sc.get("type") == "external_scanner":
            v = _num(sc.get("interval_seconds"))
            if v and v > 0 and (best is None or v < best):
                best = v
    return best


def fee_drag_pct_per_day(rt_doc):
    """(pct_of_budget_per_day, entries_per_day, basis) or None — an ESTIMATE of fee spend:
    entries/day × margin × leverage × 2 sides × FEE_RATE_PER_SIDE. Entries/day is the daily cap when
    the recipe has one; else the slots turning 3×/day at a cadence ≤ 15 min, 1×/day up to 4 h, ½×/day
    beyond. No margin or no leverage in the recipe → None (nothing to size)."""
    slots, margin = slot_plan(rt_doc)
    lev = max_leverage(rt_doc)
    if not margin or not lev:
        return None
    cap, interval = _daily_cap(rt_doc), _interval_seconds(rt_doc)
    if cap:
        entries, basis = float(cap), "the daily cap"
    elif interval:
        turns = 3.0 if interval <= 900 else (1.0 if interval <= 4 * 3600 else 0.5)
        entries = slots * turns
        basis = "%d slot(s) turning %g×/day at a %s cadence" % (slots, turns, _cadence(interval))
    else:
        return None
    pct = entries * (margin / 100.0) * lev * 2 * FEE_RATE_PER_SIDE * 100.0
    return pct, entries, basis


def _cadence(seconds):
    return "%gm" % (seconds / 60) if seconds < 3600 else "%gh" % (seconds / 3600)


def _instances(pkg: Path):
    """[(name, runtime.yaml path)] — declared instances, or the synthesized flat `main`."""
    try:
        man = yaml.safe_load((pkg / "strategy.yaml").read_text()) or {}
    except Exception:  # noqa: BLE001 — validate() already reported it
        return []
    insts = man.get("instances") if isinstance(man, dict) else None
    if not insts:
        return [("main", pkg / "runtime.yaml")] if (pkg / "runtime.yaml").is_file() else []
    return [(i.get("name", "?"), pkg / i["runtime"]) for i in insts
            if isinstance(i, dict) and i.get("runtime")]


def warnings(pkg: Path) -> list:
    """Advisory findings for a package — never errors, never the exit code. Each names its fix, and
    each is worded to be RELAYED to the user, because every one of them is something the user will
    otherwise discover from their fills."""
    out = []
    for name, rt in _instances(pkg):
        try:
            rt_doc = yaml.safe_load(rt.read_text()) or {}
        except Exception:  # noqa: BLE001 — unparseable YAML is validate()'s error
            continue
        if not isinstance(rt_doc, dict):
            continue
        tag = f"instance {name}"

        # [exec] fee options under an order type that never reads them: the runtime forwards
        # `fee_optimized_limit_options` only for FEE_OPTIMIZED_LIMIT, so under MARKET (the default when
        # order_type is absent) or LIMIT the maker window is configured and never used — every entry is
        # a taker order while the recipe reads as maker-first.
        for act in rt_doc.get("actions") or []:
            if not isinstance(act, dict) or act.get("action_type") != "OPEN_POSITION":
                continue
            params = act.get("params") if isinstance(act.get("params"), dict) else {}
            ot = params.get("order_type")
            if isinstance(params.get("fee_optimized_limit_options"), dict) and ot != "FEE_OPTIMIZED_LIMIT":
                out.append(
                    f"{tag}: [exec] action {act.get('name', '?')!r} sets `fee_optimized_limit_options` under "
                    f"`order_type: {ot if ot is not None else 'MARKET (the default)'}` — the runtime reads those "
                    f"options only for FEE_OPTIMIZED_LIMIT, so the maker window is never used and every entry "
                    f"is a taker order. Set `order_type: FEE_OPTIMIZED_LIMIT`, or drop the options")

        # [stop] the hard stop's distance in PRICE, at the leverage the recipe can reach
        ml, lev = resolved_max_loss_pct(rt_doc), max_leverage(rt_doc)
        if ml and lev:
            price = ml / lev
            if price <= WARN_STOP_PRICE_PCT:
                out.append(
                    f"{tag}: [stop] the hard stop is {price:.2f}% of price from entry (`max_loss_pct` "
                    f"{ml:g}% ROE at {lev:g}x). For a crypto perp that is inside ordinary intraday "
                    f"movement, so expect stop-outs on wicks rather than on the thesis failing, each paying "
                    f"a round trip of fees; for an `xyz:` equity, index or commodity, judge it against that "
                    f"market's own daily range. Widen `max_loss_pct`, lower the leverage, or tell the user "
                    f"this is a known cost before they fund it (a run of small losses otherwise reads as 'broken')")

        # [sizing] multi-slot: every slot must be fundable, and emits must be gated on free margin
        slots, margin = slot_plan(rt_doc)
        if slots > 1:
            if margin and slots * margin > 100:
                short = slots - int(100 // margin)
                out.append(
                    f"{tag}: [sizing] {slots} slots × {margin:g}% margin = {slots * margin:g}% of the "
                    f"account — the last {short} slot(s) can never fund, and every tick the scanner "
                    f"emits for them the runtime logs a failed open (`position_open_failed`). Size so "
                    f"slots × margin ≤ 100, or run fewer slots")
            scn = rt.parent / "scanners"
            src = "\n".join(f.read_text() for f in sorted(scn.glob("*.py"))) if scn.is_dir() else ""
            if src and not _FREE_MARGIN_RE.search(src):
                out.append(
                    f"{tag}: [sizing] a {slots}-slot scanner with no free-margin gate — every signal "
                    f"in one tick is sized off the same balance read, so the second and later opens "
                    f"land `position_open_failed` (insufficient margin), tick after tick. Read "
                    f"`withdrawable` (free margin) in scan.py and emit only what it funds — the camel "
                    f"`_get_positions` gate")

        # [cap] a daily entry cap is normal practice (most catalog packages set one) and the How-it-runs
        # summary already says it; only a cap AT OR BELOW the slot count is worth a warning — the book
        # fills once, and every re-entry after a stop-out waits for UTC midnight.
        # [fees] the recipe's own turnover, priced: what the book pays before direction earns anything
        fd = fee_drag_pct_per_day(rt_doc)
        if fd and fd[0] >= WARN_FEE_PCT_PER_DAY:
            pct, entries, basis = fd
            out.append(
                f"{tag}: [fees] at {entries:g} entries/day ({basis}), {margin:g}% margin at {lev:g}x, fees "
                f"run ≈{pct:.2f}% of the budget per day (≈{pct * 30:.0f}%/month) at an assumed 0.05% per "
                f"side — direction has to beat that before the book makes anything, and on a small budget "
                f"that is the whole edge. Lengthen the cadence, cut slots or leverage, or say the number to "
                f"the user before they fund it")

        cap = _daily_cap(rt_doc)
        if cap and cap <= slots:
            out.append(
                f"{tag}: [cap] `max_entries_per_day: {cap:g}` is at or below the {slots} slot(s): the "
                f"book fills once, then every re-entry after a stop-out waits for 00:00 UTC — the runtime "
                f"logs `Runtime paused: Max Entries/Day` until then. That is the strategy's own rule, not "
                f"a fault: say it in the How-it-runs summary, or the quiet reads as a dead strategy; raise "
                f"the cap or lower the slots if re-entries are meant to happen")
    return out


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
        # Advisory, under the verdict, whatever the verdict was: the exit code is the errors' alone.
        warns = warnings(pkg)
        if warns:
            print(f"  ⚠ {len(warns)} advisory warning(s) — not blocking; relay each to the user:")
            for w in warns:
                print(f"    - {w}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv)
