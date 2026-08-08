#!/usr/bin/env python3
"""Deploy a strategy PACKAGE in three short, resumable steps (so no single call blocks past the
tool/session timeout). The SCRIPT does the work deterministically — the agent just runs the steps
in order and re-runs a step until it reports done.

  python3 deploy.py create  <id> --budget <usd> [--max-wait S] [--dry-run] [--json]
  python3 deploy.py runtime  <id> [--decision-model M] [--dry-run] [--json]
  python3 deploy.py verify   <id> [--max-wait S] [--json]
  python3 deploy.py status   <id> [--json]

Step 1 `create`  — creating wallets & funding them: per instance strategy_create_custom_strategy (records
                   strategyId IMMEDIATELY), then poll strategy_list until ACTIVE, BOUNDED by --max-wait.
                   Not all ACTIVE yet → exits `creating`; re-run `create` to RESUME (never re-creates).
                   Refuses if it finds skillName==<id> strategies not in the state file (close first).
Step 2 `runtime` — setting up the autonomous trading strategy: render each runtime.yaml with its wallet →
                   openclaw senpi runtime create. Fast, self-healing. AFTER THIS, DEPLOY IS DONE — the
                   strategy trades autonomously (scans on its own interval). Do NOT wait for the first tick.
`verify`         — OPTIONAL, only if asked "is it scanning yet?": one non-blocking check that a scan fired.

State lives in <pkg>/.deploy-state.json: per instance {strategyId, wallet, status}. Every sub-action
is persisted, so a kill mid-step just means re-run that step. The package is fetched from the remote
on first use if it isn't on disk.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _fetch  # noqa: E402
import _pkg  # noqa: E402
from mcp_client import MCPClient, MCPError  # noqa: E402
# Vendored byte-identically with senpi-trading-runtime/scripts (gen_catalog). deploy computes the
# minimum LOCALLY because custom-authored packages never pass through catalog generation.
from min_budget import WALLET_FLOOR as MIN_WALLET, FEE_BUFFER, strategy_min_budget  # noqa: E402

SUBMIT_TIMEOUT = 60     # HTTP timeout for the async create submit
POLL_HTTP_TIMEOUT = 15  # HTTP timeout for fast read polls
DEFAULT_MAX_WAIT = 150  # per-call poll budget (s) — stays under the ~180s tool timeout
VERIFY_BUFFER = 120
POLL_EVERY = 10
# MIN_WALLET (the $10 platform wallet floor) + FEE_BUFFER are imported from min_budget.py above —
# one source of truth for the platform physics ($10 floor, $12 bumped notional, $1.50 fee buffer).
ORDER = ("pending", "creating", "active", "registered", "live")


# ---------- package + state ----------

def ensure_pkg(arg, ref, log):
    # A package that EXISTS on disk is authoritative — load it and let any BadPackage surface as the
    # real, fixable error. NEVER fall through to a (possibly stale) remote fetch just because a local
    # package is invalid: that silently deploys the wrong version and discards the author's local fixes.
    # Only a bare id that isn't a local directory triggers the catalog fetch from remote.
    if (_pkg.resolve_pkg_dir(arg) / "strategy.yaml").is_file():
        return _pkg.load(arg)
    sid = Path(arg).name
    # Fetch to the DURABLE root (absolute, CWD-independent), never a CWD-relative path: a relative
    # dest resolved inside a managed skill dir gets wiped on the next SKILL.md version bump.
    dest_root = _pkg.strategies_root()
    # A dest dir carrying deploy state but no loadable strategy.yaml is a partially-wiped DEPLOYED
    # package — fetching would graft pristine catalog files onto live deploy state, and `runtime`
    # would then render catalog defaults onto the live wallet. Refuse; this needs eyes, not a fetch.
    if (dest_root / sid / ".deploy-state.json").is_file():
        raise SystemExit(
            f"error: {dest_root / sid} carries deploy state (.deploy-state.json) but no loadable "
            f"strategy.yaml — refusing to fetch the catalog copy over a deployed package's remains.\n"
            f"  Inspect the directory and restore its files (or move the state aside) first.")
    log(f"package {sid!r} not on disk — fetching from remote into {dest_root}…")
    try:
        _fetch.fetch_package(sid, dest_root, ref=ref)
        return _pkg.load(dest_root / sid)
    except (_fetch.FetchError, _pkg.BadPackage) as e:
        raise SystemExit(
            f"error: {e}\n"
            f"  {arg!r} is not a package on disk (tried {arg!r}, {dest_root / sid}, and "
            f"'strategies/{sid}' relative to the current directory) and could not be fetched as a "
            f"catalog id.\n"
            f"  Deploying a locally-authored package? Pass its DIRECTORY path instead of a bare id, "
            f"e.g.: deploy.py validate /data/workspace/strategies/{sid}")


def full_validate(pkg):
    """Every error deploy.py can see, in ONE pass, with NO side effects: structural (`_pkg.validate`)
    plus a render dry-run per instance (unresolved `${...}`, a `decision_mode: llm` with no model). Lets
    `validate` and the `create` preflight report everything BEFORE a wallet is funded. (Runtime-engine
    schema errors still surface at `runtime`, but everything modellable here is caught first.)"""
    errs = list(_pkg.validate(pkg))
    for inst in pkg.instances:
        if inst.runtime_doc is None:
            continue  # already reported by _pkg.validate
        try:
            inst.render("0x0000000000000000000000000000000000000000",
                        model_env=pkg.model_env, model="validation-model")
        except _pkg.BadPackage as e:
            errs.append(str(e))
    return errs


def _state_path(pkg):
    return pkg.dir / ".deploy-state.json"


def load_state(pkg):
    p = _state_path(pkg)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"id": pkg.id, "version": pkg.version, "instances": {}}


def save_state(pkg, st):
    _state_path(pkg).write_text(json.dumps(st, indent=2) + "\n")


def _safe_unlink(p):
    """Delete a file, ignoring if it's already gone."""
    try:
        Path(p).unlink()
    except OSError:
        pass


def delete_state(pkg):
    """Remove the ephemeral deploy state — called once a deploy is fully live, or on close. Also sweeps
    any rendered `<inst>.deploy.runtime.yaml` build artifacts: they carry a baked-in wallet, and a stale
    one left on disk is exactly what a lost-state manual redeploy wrongly picks up (the reuse trap)."""
    for inst in pkg.instances:  # sweep the rendered build artifacts for THESE instances either way
        if inst.runtime_path:
            _safe_unlink(inst.runtime_path.with_name(f"{inst.name}.deploy.runtime.yaml"))
    # A SCOPED op (`--instance`, so pkg was narrowed below its true arity) must NOT unlink the whole file —
    # that discards the SIBLINGS' entries (a mid-deploy sibling's strategyId/wallet included), contradicting
    # `_scope_pkg`'s "siblings untouched". Drop only this arm's entry (+ the per-arm `_upgrade` marker);
    # remove the file only when nothing remains. A full deploy (unscoped) clears the whole file as before.
    scoped = getattr(pkg, "full_instance_count", len(pkg.instances)) > len(pkg.instances)
    p = _state_path(pkg)
    if not scoped:
        _safe_unlink(p)
        return
    if not p.is_file():
        return
    try:
        st = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        _safe_unlink(p)
        return
    for inst in pkg.instances:
        st.get("instances", {}).pop(inst.name, None)
    st.pop("_upgrade", None)
    if st.get("instances"):
        p.write_text(json.dumps(st, indent=2) + "\n")
    else:
        _safe_unlink(p)


def inst_state(st, name):
    return st["instances"].setdefault(name, {"status": "pending"})


def _num(*vals):
    """First of `vals` that parses as a number, else 0.0. The payload mixes numbers, numeric strings
    (`spot_balances` rows carry `"5.46"`) and nulls — a null must fall through to the next candidate
    rather than zero the row, because on this path an under-count is a false `underfunded` halt."""
    for v in vals:
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _usdc_usd(rows, *keys):
    """USD held as USDC in a balance list (`spot_balances` / `token_balances`). Filtered by symbol
    because the funding waterfall pulls USDC, while the `total_*` scalars lump in every other token —
    a spot HYPE bag or EVM WETH is not fundable balance. Chain-agnostic: create bridges from Base,
    Optimism, Arbitrum, BNB, Polygon and Ethereum today, and hardcoding that list here is how this
    gate would silently start refusing fundable deploys again the day a chain is added."""
    return sum(_num(*(_cli.dig(r, k) for k in keys)) for r in (rows or [])
               if isinstance(r, dict) and str(_cli.dig(r, "tokenSymbol") or "").strip().upper() == "USDC")


def available_usd(mcp):
    """Total USDC `strategy_create_custom_strategy` can fund from — its whole waterfall, in order:
    HL perps → HL spot USDC → EVM USDC (auto-bridged). None if unreadable → caller proceeds.

    These are DISJOINT buckets of `total_balance_usd` (they sum to it exactly), so reading one reads
    a FRACTION of the fundable balance — and because `plan_funding`'s shortfall is a HARD halt that
    returns before any wallet exists, a missing bucket doesn't under-report, it REFUSES a deploy
    create would have funded itself. That is the Starling incident: $440 rejected as
    `[E_FUNDS_SHORT] only $198.42 is accessible` with ~$248 USDC idle on Base.

    Excludes the 4th bucket, `total_withdrawable` — that is free margin sitting inside OTHER strategy
    wallets, not spendable here (senpi-portfolio calls this the balance-bucket trap). The old
    `dig(port, "total_in_hyperliquid", "total_withdrawable")` never reached that key anyway: `dig`
    returns the first key PRESENT and both always are.

    Over-counting is the SAFE direction — create auto-funds and surfaces a genuine shortfall as
    SERR037, which is exactly what the `account_get_portfolio` contract asks for ("do not declare a
    strategy unfundable from token_balances alone"). Under-counting is the bug above. So this stays a
    cheap pre-check whose only job is avoiding a half-funded multi-wallet package; create remains the
    authority on sufficiency."""
    try:
        # forceFetch: account_get_portfolio caches HL data for ~12h otherwise, so a deposit or a just
        # closed strategy would read stale and halt a deploy the user has already funded.
        res = mcp.mcp_call("account_get_portfolio", forceFetch=True, timeout=POLL_HTTP_TIMEOUT)
    except MCPError:
        return None
    port = _cli.dig(_cli.dig(res, "data", default={}) or {}, "portfolio", default={}) or {}
    # Unknown shape (no bucket field at all) → None, never a false halt on a payload change.
    if not any(_cli.dig(port, k) is not None for k in
               ("total_in_hyperliquid", "total_spot_usd_in_hyperliquid", "spot_balances", "token_balances")):
        return None
    rows = _cli.dig(port, "spot_balances")
    perps = _num(_cli.dig(port, "total_in_hyperliquid"))
    spot = (_usdc_usd(rows, "usdValue", "total") if isinstance(rows, list) and rows
            else _num(_cli.dig(port, "total_spot_usd_in_hyperliquid")))
    evm = _usdc_usd(_cli.dig(port, "token_balances"), "balanceInUSD", "formattedBalance")
    return round(perps + spot + evm, 2)


def plan_funding(need, budget, available):
    """Per-instance initialBudget for the instances still needing a wallet, split by funding_share and
    floored at MIN_WALLET. Returns (amounts, shortfall).

    The requested budget is a HARD TARGET, not a suggestion: if the live balance can't cover it (minus a
    per-wallet fee buffer) we return a `shortfall` dict and the caller HALTS — we never silently fund
    LESS than asked. The old behaviour scaled every wallet down to fit `available`, which quietly turned
    a "$1,000 / $2,000" request into two $100 floor wallets; that silent under-funding is the bug this
    removes. (`available` unreadable → shortfall stays None → proceed; create would fail loudly anyway.)"""
    shares = [(i.funding_share or (1.0 / len(need))) for i in need]
    raw = {i.name: max(MIN_WALLET, round((budget or 0) * s, 2)) for i, s in zip(need, shares)}
    total = round(sum(raw.values()), 2)
    shortfall = None
    if available is not None:
        usable = max(0.0, round(available - FEE_BUFFER * len(need), 2))
        if total > usable:
            shortfall = {"requested": total, "available": round(float(available), 2),
                         "usable": usable, "short_by": round(total - usable, 2),
                         "wallets": len(need), "shares": shares}
    return raw, shortfall


def usd(v):
    """Money for an agent-facing note: comma-grouped, two decimals, trailing `.00` trimmed,
    NEVER scientific notation. `%g` (the old house style) rounds to 6 significant digits — it
    printed `$1e+06` at $1M and rounded `$99,999.99` UP to `$100000`, so a hint could name a
    ceiling ABOVE the live balance. `usd(1234567.5)` -> `$1,234,567.50`; `usd(100.0)` -> `$100`."""
    s = f"{float(v):,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return f"${s}"


def budget_arg(v):
    """A dollar amount as the `--budget` flag accepts it (`type=float`): bare digits, no `$`,
    no comma grouping — `usd()`'s commas fail argparse at ≥ $1,000, so a hinted command an agent
    copies verbatim must render the flag value with this, and `usd()` only in prose."""
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def max_feasible_budget(shares, usable):
    """The largest budget `b` whose funding plan still fits within `usable` — i.e.
    `b* = max { b : Σᵢ max(MIN_WALLET, round(b·shareᵢ, 2)) ≤ usable }`, floored to whole cents.

    Computed with the SAME per-wallet rounding `plan_funding` uses, so re-running `create` at the
    hinted `--budget ≤ $b*` round-trips with NO shortfall — even for uneven shares, where the old
    `usable` ceiling was wrong (2 wallets 0.6/0.4, usable $230 → the small leg floors to $100, so
    the true max is $216.67, not the hinted $230). Bisection in integer cents; exact to the cent."""
    def total_cents(cents):
        b = cents / 100.0
        return int(round(sum(max(MIN_WALLET, round(b * s, 2)) for s in shares) * 100))

    usable_cents = int(round(usable * 100))
    if total_cents(0) > usable_cents:  # even the all-floor minimum can't fit → no feasible budget
        return 0.0
    lo, hi = 0, usable_cents + len(shares) * int(round(MIN_WALLET * 100)) + 100
    while total_cents(hi) <= usable_cents:  # keep hi a true upper bound (defensive vs Σshare < 1)
        hi *= 2
    while lo < hi:  # largest cents with total_cents(mid) <= usable_cents
        mid = (lo + hi + 1) // 2
        if total_cents(mid) <= usable_cents:
            lo = mid
        else:
            hi = mid - 1
    return lo / 100.0


def underfunded_note(shortfall):
    """Agent-facing halt text for a funding shortfall. The lower-budget escape is only rendered
    when the usable balance can still fund every wallet at the MIN_WALLET floor — below that NO
    budget is valid, and suggesting one produces nonsense the agent follows ("--budget ≤ $0",
    a $0-accessible-budget churn). Codes: docs/error-code-taxonomy.md (repo root)."""
    floor_needed = shortfall["wallets"] * MIN_WALLET
    facts = (f"Requested {usd(shortfall['requested'])} across {shortfall['wallets']} wallet(s) "
             f"(min {usd(MIN_WALLET)}/wallet), but only {usd(shortfall['available'])} is accessible "
             f"({usd(shortfall['usable'])} after fees) — short by {usd(shortfall['short_by'])}. "
             f"NOT funding; no wallet was created. ")
    if shortfall["usable"] < floor_needed:
        # From `available`, not `usable`: usable is clamped to 0 when the balance doesn't even
        # cover the fee reserve, and `floor - usable` would then understate the deposit by up to
        # FEE_BUFFER × wallets — depositing the hinted amount must always clear the floor check.
        missing = round(floor_needed + FEE_BUFFER * shortfall["wallets"] - shortfall["available"], 2)
        return ("[E_FUNDS_BELOW_FLOOR] " + facts +
                f"No budget can fund {shortfall['wallets']} wallet(s) below the "
                f"{usd(MIN_WALLET)}/wallet floor — at least {usd(missing)} more USDC is needed. "
                f"Next: help the user deposit (senpi-deposit-withdraw-transfer skill), then "
                f"re-run `create`. Do NOT retry with a lower --budget: no lower budget is valid.")
    # The real max-feasible budget for THIS package's shares — never the bare `usable`, which
    # over-hints for uneven shares (a floored small leg pushes the funded total above the budget).
    b_star = max_feasible_budget(shortfall.get("shares") or [1.0 / shortfall["wallets"]] * shortfall["wallets"],
                                 shortfall["usable"])
    return ("[E_FUNDS_SHORT] " + facts +
            f"Either add USDC, or confirm a lower amount with the user and re-run `create` with "
            f"--budget ≤ {budget_arg(b_star)} (the largest budget your accessible balance can fully fund "
            f"across these {shortfall['wallets']} wallet(s) at the {usd(MIN_WALLET)}/wallet floor).")


def report(pkg, st, overall, note=None, as_json=False):
    insts = [{"instance": i, **st["instances"].get(i, {"status": "pending"})}
             for i in [x for x in st["instances"]]]
    out = {"strategy": pkg.id, "version": pkg.version, "status": overall, "instances": insts}
    for _k in ("min_budget", "below_min", "min_budget_note", "min_budget_unresolved"):
        if st.get(_k) is not None:                       # the soft-warn tier must reach --json too
            out[_k] = st[_k]
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{pkg.id} v{pkg.version}: {overall}")
        for r in insts:
            print(f"  - {r['instance']}: {r['status']}"
                  + (f"  requested=${r['requested']:g}" if r.get("requested") else "")
                  + (f"  wallet={r['wallet']}" if r.get("wallet") else "")
                  + (f"  ({r['error']})" if r.get("error") else ""))
        if note:
            print(f"\n{note}")
    return out


# ---------- step 1: create wallets ----------

def _sanitize_strategy_name(raw, fallback):
    """The backend strategyName sanitizer: whitespace → '-', keep only [A-Za-z0-9_-], trim
    leading/trailing -/_, cap at 40 chars; an empty result falls back to the (truncated) package id.
    Shared by create (naming a wallet) and `_recover_wallet` (matching it back) so the two never drift."""
    s = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"\s+", "-", str(raw).strip())).strip("-_")[:40]
    return s or str(fallback)[:40]


def _wallet_name(pkg, inst):
    """The strategyName `create` assigns this instance's wallet: `<id>-<instance>` for a multi-instance
    package, else the bare `<id>` — sanitized. Recovery re-derives the SAME name to match a wallet back
    to its instance, so a lost-state redeploy binds to the right wallet instead of guessing."""
    # Arity from the ORIGINAL package, not the possibly-scoped `pkg.instances` — `_scope_pkg` narrows that
    # list to 1 for a single-arm op, and a multi-arm arm must still be named `<id>-<arm>`, not the bare
    # `<id>`. The getattr default leaves every unscoped caller (create, _recover_wallet) on today's path.
    multi = getattr(pkg, "full_instance_count", len(pkg.instances)) > 1
    raw = f"{pkg.id}-{inst.name}" if (multi and inst.name) else str(pkg.id)
    return _sanitize_strategy_name(raw, pkg.id)


def strategy_min(pkg):
    """The package's CALCULATED minimum budget, computed locally (custom-authored packages never pass
    through catalog generation, so deploy can't read it off catalog.json). Same function gen_catalog
    bakes into the card, so 'what the card promised' == 'what deploy enforces'."""
    manifest = {
        "instances": [{"name": i.name, "funding_share": i.funding_share} for i in pkg.instances],
        "catalog": getattr(pkg, "catalog", {}) or {},
    }
    runtimes = {i.name: (i.runtime_doc or {}) for i in pkg.instances}
    return strategy_min_budget(manifest, runtimes)


def _scope_pkg(pkg, instance_name):
    """Narrow a multi-instance package to ONE instance for a single-arm op (redeploy/upgrade one sleeve,
    leaving siblings running). Mutates pkg.instances in place — each command runs in a fresh process — so
    every per-instance loop, plus the create live-guard, acts on this arm only; the siblings' deploy-state
    entries are left untouched. Raises on an unknown instance name."""
    names = [i.name for i in pkg.instances]
    if instance_name not in names:
        raise SystemExit(f"error: no instance {instance_name!r} in {pkg.id} (have: {', '.join(names)})")
    kept = [i for i in pkg.instances if i.name == instance_name]
    # Fund THIS arm with the full --budget: its fractional share of the whole package is irrelevant when
    # it's the only arm being (re)deployed, and keeping it would scale the budget down (a 0.10-share sleeve
    # funded at 10% of what the user asked). Treat the scoped arm as the whole.
    kept[0].funding_share = 1.0
    # Preserve the TRUE arity before narrowing — `_wallet_name` derives `<id>-<arm>` vs bare `<id>` from
    # it, and reading the post-narrow `len(pkg.instances)` (== 1) would collapse a multi-arm arm's wallet
    # name to the bare `<id>`, breaking every by-name lookup (upgrade's fallback, scoped create's naming).
    pkg.full_instance_count = len(names)
    pkg.instances = kept


def _scope_flag(a):
    """` --instance <arm>` when the current op is scoped, else "". Threaded into every resume hint so an
    agent following one mid-single-arm-op re-runs SCOPED — an unscoped `create` on a multi-arm package
    refuses on live siblings and can close a runtime-less sibling WITHOUT consent."""
    return f" --instance {a.instance}" if getattr(a, "instance", None) else ""


def _arm_wallet(pkg, inst, mcp):
    """This arm's ``(wallet, kind)``. ``kind`` is None on a clean resolve; otherwise a REFUSAL kind the
    caller must NOT treat as "safe to fund fresh":
      None         — resolved: ``wallet`` is the arm's address (its live runtime, or the unique ACTIVE
                     strategy carrying the name ``create`` gave it).
      "none"       — verified ABSENT: the read succeeded and no ACTIVE <id> wallet matches → fund fresh is safe.
      "unreadable" — the ``strategy_list`` read FAILED → we don't know; refuse (a money path can't fund blind).
      "unnamed"/"ambiguous" — a wallet exists but no UNIQUE name match (a name-rejection fallback, or a prior
                     double-fund left two) → one may be a funded LIVE arm; refuse, never fund next to it.
    Prefers the live runtime; falls back to the arm's stable strategyName via ``_recover_wallet`` (shared
    tri-state, so this and ``create``'s guard resolve identically and can't drift). The fail-CLOSED read +
    tri-state is what stops an unreadable/ambiguous backend from disarming BOTH the consent gate and the
    double-fund guard at once."""
    rt = _cli.find_runtime(inst.runtime_name)
    wallet = _cli.runtime_wallet(rt) if rt else None
    if wallet:
        return wallet, None
    active = _cli.strategies_for_or_none(mcp, skill_name=pkg.id, statuses=["ACTIVE"])
    if active is None:
        return None, "unreadable"
    w, kind, _why = _recover_wallet(pkg, inst, active)
    return w, kind


def cmd_create(pkg, a, log):
    st = load_state(pkg)
    st["budget"] = a.budget
    # Two-tier enforcement. The HARD floor ($10 x wallets) is downstream: plan_funding floors each
    # wallet at MIN_WALLET and underfunded_note halts with [E_FUNDS_BELOW_FLOOR]. Here we add the SOFT
    # tier — at/above the floor but below the CALCULATED minimum the design runs degraded. Warn (naming
    # the binding sleeve), record it, and PROCEED: users size their own budgets.
    if a.budget is not None:
        _mb = strategy_min(pkg)
        st["min_budget"], st["min_wallet_count"] = _mb["min_budget"], _mb["wallet_count"]
        note = None
        if _mb.get("unresolved_wallets"):
            st["min_budget_unresolved"] = _mb["unresolved_wallets"]
            note = (f"[E_BUDGET_UNRESOLVED] could not compute a reliable minimum for {pkg.id} — sleeve(s) "
                    f"{_mb['unresolved_wallets']} exposed no resolvable marginPct, so the "
                    f"${_mb['min_budget']:g} figure may be understated. Size conservatively.")
        elif a.budget < _mb["min_budget"]:
            st["below_min"] = True
            note = (f"[E_BUDGET_BELOW_STRATEGY_MIN] ${a.budget:g} is below {pkg.id}'s calculated minimum "
                    f"${_mb['min_budget']:g} ({_mb['wallet_count']} wallet(s); binding sleeve "
                    f"'{_mb['binding_wallet']}'). It will DEPLOY but run DEGRADED — fewer slots than "
                    f"designed, each position a larger share of its wallet. Fund ${_mb['min_budget']:g}+ "
                    f"for the authored design.")
        if note:
            st["min_budget_note"] = note
            log("  " + note)
    if a.dry_run:
        for inst in pkg.instances:
            s = inst_state(st, inst.name)
            s.setdefault("status", "pending")
            intended = max(MIN_WALLET, round((a.budget or 0) * (inst.funding_share or 1.0), 2))
            s["plan"] = f"strategy_create_custom_strategy(~${intended:g} capped to live balance, skillName={pkg.id}, skillVersion={pkg.version})"
        return report(pkg, st, "planned", as_json=a.json)
    if a.budget is None:
        raise SystemExit("error: --budget <total> is required for `create`")

    mcp = MCPClient()

    # Universe preflight — refuse to fund a package whose hardcoded tickers aren't live HL
    # instruments (a dead name silently no-trades; the xyz:NASDAQ incident). Best-effort: if the
    # live list itself is unreachable we proceed (create would fail loudly on MCP anyway).
    try:
        import validate_universe as _vu
        unknown = _vu.unknown_tickers(_vu.package_tickers(str(pkg.dir)), _vu.live_instruments())
        if unknown:
            raise SystemExit(
                f"error: {pkg.id} hardcodes instrument(s) not live on Hyperliquid: {', '.join(unknown)}\n"
                f"Fix the package universe first (senpi-strategy-author edit path); details:\n"
                f"  python3 {Path(__file__).with_name('validate_universe.py')} {pkg.dir}")
    except SystemExit:
        raise
    except Exception as e:  # noqa
        log(f"  (universe preflight skipped: {e})")

    # Reconcile recorded strategies against the backend — drop any that aren't ACTIVE so we never
    # reuse a CLOSED wallet or get stuck on a FAILED one. Self-heals stale state; no manual editing.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        sid = s.get("strategyId")
        if not sid:
            continue
        m = _cli.strategies_for(mcp, strategy_id=sid, timeout=POLL_HTTP_TIMEOUT)
        status = str(_cli.strategy_status(m[0]) if m else "").upper()
        if status != "ACTIVE":
            log(f"  [{inst.name}] recorded strategy {sid[:8]} is {status or 'gone'} — discarding, will recreate")
            st["instances"][inst.name] = {"status": "pending"}
    save_state(pkg, st)

    # NEVER reuse an existing strategy's wallet. Re-using a funded, runtime-less wallet is the trap an
    # agent keeps falling into (creates <id>, never deploys the runtime, keeps landing back on the same
    # dead wallet); creating a second alongside it double-funds. So every `create` deploys on a FRESH
    # wallet, resolving any existing OPEN <id> strategy first:
    #   • RUNNING runtime → a live, working deploy: REFUSE to silently flatten it; redeploy is explicit
    #     (close.py first). Protects a real book from an accidental `create`.
    #   • no running runtime → the funded-but-stuck trap: CLOSE it (recovers its funds), then this deploy
    #     creates a fresh wallet. strategy_close is async, so hand off and re-run `create` once it's closed.
    runtimes = _cli.list_runtimes()
    existing_open = [s for s in _cli.strategies_for(mcp, skill_name=pkg.id) if _cli.strategy_open(s)]
    if getattr(a, "instance", None):
        # single-arm: consider ONLY this arm's strategy, never siblings — and do it fail-CLOSED. An
        # unreadable strategy_list must REFUSE, not disarm the live-guard + close-existing by reading []
        # (the double-fund trap). `_arm_wallet`'s tri-state resolves a runtime-less open arm by name too;
        # a verified-absent arm returns ("none") → no match → fresh wallet.
        _rows = _cli.strategies_for_or_none(mcp, skill_name=pkg.id)
        if _rows is None:
            raise SystemExit(f"error: couldn't read strategy_list for {pkg.id} — refusing to fund a fresh "
                             f"{a.instance} wallet blind. Re-run once the backend is readable.")
        existing_open = [s for s in _rows if _cli.strategy_open(s)]
        w, wkind = _arm_wallet(pkg, pkg.instances[0], mcp)
        if wkind in ("unreadable", "unnamed", "ambiguous"):
            raise SystemExit(f"[E_STATE_AMBIGUOUS] {pkg.id}-{a.instance}: can't safely resolve the arm's "
                             f"wallet ({wkind}) — refusing to fund a fresh wallet blind. Triage read-only: "
                             f"python3 {Path(__file__).with_name('status.py').name} {pkg.id}")
        existing_open = [s for s in existing_open if w and _cli.wallet_match(_cli.strategy_wallet(s), w)]

    def _has_running_runtime(s):
        rt = _cli.find_runtime_by_wallet(_cli.strategy_wallet(s))
        return bool(rt) and _cli.runtime_running(rt)

    live = [s for s in existing_open if _has_running_runtime(s)]
    if live:
        # If we got here mid-upgrade (a stale `_upgrade` marker steered us into redeploy while the arm is
        # actually live), CLEAR the marker before refusing — else the next `upgrade` skips PHASE A, lands
        # back here, and refuses "use upgrade" forever (the circular-refusal loop). Cleared, the next
        # `upgrade` re-checks liveness from PHASE A.
        if st.get("_upgrade"):
            st.pop("_upgrade", None); save_state(pkg, st)
        _inst_flag = f" --instance {a.instance}" if getattr(a, "instance", None) else ""
        raise SystemExit(
            f"error: {pkg.id} is already deployed AND running ({len(live)} live wallet(s)) — `create` will not "
            f"silently close a live strategy.\n"
            f"To APPLY AN EDIT to it (re-score / re-scan / re-tune), use `upgrade` — it closes and redeploys "
            f"on a fresh wallet, consent-gated:\n"
            f"  python3 {Path(__file__).name} upgrade {pkg.id}{_inst_flag} --budget <usd>\n"
            f"To tear it down instead:  python3 {Path(__file__).with_name('close.py')} {pkg.id}\n"
            f"Or just re-check it:  python3 {Path(__file__).name} verify {pkg.id}")

    if existing_open:  # open but NOT running → the runtime-less trap: close (recover funds) → fresh wallet
        import close as _close  # noqa: E402 — sibling module, lazy import
        for s in existing_open:
            _close.close_one(pkg.id, s, runtimes, False, log)
        for inst in pkg.instances:  # forget the old ids so the re-run makes NEW wallets, never resumes them
            prev = inst_state(st, inst.name)
            st["instances"][inst.name] = ({"status": "pending", "requested": prev["requested"]}
                                          if prev.get("requested") else {"status": "pending"})
        save_state(pkg, st)
        return report(pkg, st, "closing-existing", note=(
            f"Found {len(existing_open)} existing {pkg.id} strateg(y/ies) with NO running runtime — closing "
            f"them (recovering funds) so this deploys on a FRESH wallet, never reusing the runtime-less one. "
            f"strategy_close is async; re-run `python3 {Path(__file__).name} create {pkg.id}{_scope_flag(a)} --budget "
            f"{budget_arg(a.budget)}` once they're CLOSED and the funds are back."), as_json=a.json)

    need = [i for i in pkg.instances if not inst_state(st, i.name).get("strategyId")]

    # Size the to-create instances against the LIVE available balance. The requested --budget is a HARD
    # TARGET: if the balance can't cover it, HALT with the shortfall (fund more / lower the ask) rather
    # than silently funding the $100 floor. Nothing is created on this path.
    amounts, shortfall = plan_funding(need, a.budget, available_usd(mcp)) if need else ({}, None)
    if shortfall:
        return report(pkg, st, "underfunded", note=underfunded_note(shortfall), as_json=a.json)

    # create any instance that has no strategyId yet — record the id IMMEDIATELY (before polling),
    # so an interrupted run resumes instead of re-creating.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        if s.get("strategyId"):
            continue
        amt = amounts.get(inst.name, max(MIN_WALLET, round((a.budget or 0) * (inst.funding_share or 1.0), 2)))
        s["requested"] = amt  # remember what the user asked to fund → reconciled against actual at verify
        # Name each wallet for its role in the strategy (matches the pkg-instance runtime naming),
        # so wallets are legible in the app / balances / notifications instead of a bare 0x address.
        # e.g. a WhaleHunter deploy → "whalehunter-long", "whalehunter-short". `_wallet_name` applies
        # the strategyName sanitizer; `_recover_wallet` re-derives the same name to match wallets back.
        sname = _wallet_name(pkg, inst)
        log(f"  [{inst.name}] creating wallet {sname!r} (initialBudget=${amt:g})…")

        def _create(name=None):
            kw = dict(initialBudget=amt, positions=[], skillName=pkg.id, skillVersion=pkg.version)
            if name:
                kw["strategyName"] = name
            return mcp.mcp_call("strategy_create_custom_strategy", timeout=SUBMIT_TIMEOUT, **kw)

        try:
            res = _create(sname)
        except MCPError as e:
            # Naming is best-effort — a name conflict/format rejection must never block the deploy.
            if any(c in str(e) for c in ("SERR055", "SERR056", "SERR058")) or "name" in str(e).lower():
                log(f"  [{inst.name}] name {sname!r} rejected ({e}); creating without a custom name")
                try:
                    res = _create()
                except MCPError as e2:
                    s["status"] = "pending"
                    s["error"] = f"create submit: {e2}"
                    save_state(pkg, st)
                    return report(pkg, st, "failed", as_json=a.json)
            else:
                s["status"] = "pending"
                s["error"] = f"create submit: {e}"
                save_state(pkg, st)
                return report(pkg, st, "failed", as_json=a.json)
        sid = _cli.strategy_id_of(res)
        if not sid:
            s["error"] = f"create returned no strategyId: {res!r}"
            save_state(pkg, st)
            return report(pkg, st, "failed", as_json=a.json)
        s.update(strategyId=sid, status="creating", error=None)
        save_state(pkg, st)  # ← persist before any polling

    # poll to ACTIVE, bounded by --max-wait (resume on re-run)
    deadline = time.time() + a.max_wait
    while True:
        pending = []
        for inst in pkg.instances:
            s = inst_state(st, inst.name)
            if s.get("status") in ("active", "registered", "live"):
                continue
            m = _cli.strategies_for(mcp, strategy_id=s["strategyId"], timeout=POLL_HTTP_TIMEOUT)
            status = str(_cli.strategy_status(m[0]) if m else "").upper()
            addr = _cli.strategy_wallet(m[0]) if m else None
            if status == "ACTIVE" and addr:
                s.update(wallet=addr, status="active")
                save_state(pkg, st)
            else:
                pending.append(f"{inst.name}={status or '…'}")
        if not pending:
            return report(pkg, st, "wallets-ready", note="Next: deploy.py runtime " + pkg.id + _scope_flag(a), as_json=a.json)
        if time.time() >= deadline:
            return report(pkg, st, "creating",
                          note="Wallets still funding. Re-run `deploy.py create " + pkg.id + _scope_flag(a) + "` to resume.",
                          as_json=a.json)
        log(f"  waiting on {', '.join(pending)}…")
        time.sleep(POLL_EVERY)


# ---------- step 2: deploy runtimes ----------

def _recover_wallet(pkg, inst, active):
    """Re-resolve one instance's FRESH wallet from the live ACTIVE <id> strategies when the deploy
    state was lost (the sub-agent died before persisting it). Returns (wallet, kind, why):
    (addr, None, None) on success; and, on refusal, a kind the caller maps to a refusal code —
      "none"       — the backend has NO <id> wallet at all → safe to create fresh (E_STATE_NO_WALLETS);
      "unnamed"    — <id> wallet(s) EXIST but none carries this instance's name (the create-time
                     name-rejection fallback funds with no custom name; a renamed wallet lands here too)
                     → NOT "nothing exists": refuse conservatively so the caller never steers to create,
                     which would tear the unmatched — possibly funded LIVE — wallet down (E_STATE_AMBIGUOUS);
      "unreadable" — candidate(s) match but no readable wallet address (backend field drift) → won't guess;
      "ambiguous"  — >1 distinct candidate wallets → one may be a funded LIVE strategy (E_STATE_AMBIGUOUS).

    NEVER guesses: anything short of exactly one readable, name-matched wallet refuses rather than
    binding a runtime to the wrong/old wallet — the exact reuse trap (agent hand-registers onto a
    stale wallet). Names are matched via the shared `_wallet_name` sanitizer so recovery can't drift
    from what `create` actually named the wallet."""
    if getattr(pkg, "full_instance_count", len(pkg.instances)) > 1:  # multi-instance: match by the sanitized name create assigned each wallet
        want = _wallet_name(pkg, inst)
        cands = [s for s in active if _cli.strategy_name(s) == want]
        if not cands and active:
            # ANY unmatched ACTIVE strategy blocks the "none" path — even one whose wallet address
            # is unreadable. Filtering to readable addresses here let an all-unreadable backend fall
            # through to "none" ("nothing at risk" → create), the exact teardown trap this refusal
            # split exists to prevent.
            return None, "unnamed", (
                f"{len(active)} ACTIVE {pkg.id} wallet(s) exist but none is named {want!r} for "
                f"instance {inst.name!r} — can't safely match (may be a name-rejection fallback wallet)")
    else:  # single-instance: the lone ACTIVE <id> strategy is this instance
        cands = list(active)
    addrs = [_cli.strategy_wallet(s) for s in cands if _cli.strategy_wallet(s)]
    wallets = {str(a).lower() for a in addrs}
    if len(wallets) == 1:
        return addrs[0], None, None
    if not cands:
        return None, "none", f"no ACTIVE {pkg.id} wallet on the backend for instance {inst.name!r}"
    if not wallets:
        return None, "unreadable", (f"found {len(cands)} ACTIVE {pkg.id} "
                                    f"strateg{'y' if len(cands) == 1 else 'ies'} for instance "
                                    f"{inst.name!r} but the wallet address is unreadable — won't guess")
    return None, "ambiguous", (f"{len(cands)} ACTIVE {pkg.id} wallets match instance {inst.name!r} "
                               f"— ambiguous, won't guess")


def wallets_unrecoverable_note(pkg_id, unresolved):
    """Refusal text for wallets that could not be safely recovered, split by cause. Only an
    ALL-`none` batch — every instance's wallet genuinely absent from the backend — gets the
    "nothing exists" NO_WALLETS text that steers to `create`. Anything else (a wallet exists but
    isn't name-matched / is unreadable / is one of several) uses the conservative AMBIGUOUS text:
    a funded LIVE strategy may be in the set, so it NEVER names close/recreate (the caribou
    raw-recreate incident) and points only at read-only triage. Paths are absolute so the hinted
    commands are copy-paste runnable from any cwd. Codes: docs/error-code-taxonomy.md (repo root)."""
    deploy_py = Path(__file__).with_name("deploy.py")
    status_py = Path(__file__).with_name("status.py")
    lines = "\n".join(f"    - {n}: {why}" for n, _k, why in unresolved)
    if all(k == "none" for _n, k, _w in unresolved):
        return (f"[E_STATE_NO_WALLETS] error: no ACTIVE wallet(s) on the backend:\n{lines}\n"
                f"Nothing to recover and nothing at risk — the create step has not produced wallets "
                f"for these instance(s).\n"
                f"Next: python3 {deploy_py} create {pkg_id} --budget <usd>")
    return (f"[E_STATE_AMBIGUOUS_WALLETS] error: wallet state is ambiguous — refusing to guess:\n{lines}\n"
            f"One of these wallets may be a funded LIVE strategy. Do NOT hand-register a runtime, and do "
            f"NOT tear anything down to 'start clean'.\n"
            f"Next (read-only): python3 {status_py} {pkg_id}   # map each wallet to its runtime/strategy\n"
            f"Then resolve WITH THE USER which wallet is live before re-running `python3 {deploy_py} runtime {pkg_id}`.")


def cmd_runtime(pkg, a, log):
    st = load_state(pkg)

    # Self-heal a lost/partial deploy state: if `create` succeeded but its state file was lost (the
    # sub-agent died before persisting), re-resolve each instance's FRESH wallet from the live ACTIVE
    # <id> strategies instead of dead-ending. Otherwise the agent improvises a manual `runtime update`
    # onto an OLD wallet baked into a leftover rendered yaml / a pre-existing same-name runtime — the
    # reuse trap. We never guess: an ambiguous backend refuses with a redeploy-fresh instruction.
    missing = [i for i in pkg.instances if not inst_state(st, i.name).get("wallet")]
    if missing and not a.dry_run:
        active = _cli.strategies_for(MCPClient(), skill_name=pkg.id, statuses=["ACTIVE"])
        recovered, unresolved = [], []
        for inst in missing:
            w, kind, why = _recover_wallet(pkg, inst, active)
            if w:
                inst_state(st, inst.name).update(wallet=w, status="active")
                recovered.append(inst.name)
            else:
                unresolved.append((inst.name, kind, why))
        if recovered:
            save_state(pkg, st)
            log(f"  deploy-state was lost — recovered fresh wallet(s) from the backend for: {', '.join(recovered)}")
        if unresolved:
            raise SystemExit(wallets_unrecoverable_note(pkg.id, unresolved))
    if pkg.any_needs_model and not a.decision_model and not a.dry_run:
        raise SystemExit("error: a runtime has a decision_mode: llm action — pass --decision-model <bare-model>")

    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        wallet = s.get("wallet") or "0x<wallet-from-create>"  # placeholder only reachable in --dry-run
        build = inst.runtime_path.with_name(f"{inst.name}.deploy.runtime.yaml")
        try:
            text = inst.render(wallet, model_env=pkg.model_env, model=a.decision_model)
        except _pkg.BadPackage as e:
            s.update(status="active", error=str(e))
            save_state(pkg, st)
            continue
        s["error"] = None  # render succeeded — clear any stale error from a prior run
        if a.dry_run:
            s["plan"] = f"openclaw senpi runtime create -p {build} --runtime-id {inst.runtime_name}"
            save_state(pkg, st)
            continue
        # Reconcile an existing runtime of this id. The runtime is ALWAYS (re)built from scratch on the
        # freshly-resolved wallet — we never reuse an old wallet a same-name runtime is still bound to:
        #   same + correct wallet → already deployed on this wallet (idempotent skip);
        #   wallet differs / unreadable / its wallet is CLOSED (orphaned by a prior close) → DELETE the
        #   old runtime and recreate on the fresh wallet (never `runtime update` it in place).
        existing = _cli.find_runtime(inst.runtime_name)
        if existing:
            if _cli.wallet_match(_cli.runtime_wallet(existing), wallet):
                s.update(status="registered", error=None)
                save_state(pkg, st)
                _safe_unlink(build)  # runtime owns its config now — drop the rendered yaml so no stale wallet lingers
                continue
            log(f"  [{inst.name}] existing runtime {inst.runtime_name!r} is on a different/old wallet "
                f"— deleting and recreating on the fresh wallet (never reusing the old one)")
            _cli.run_cli(["openclaw", "senpi", "runtime", "delete", inst.runtime_name], timeout=60)
        build.write_text(text)
        log(f"  [{inst.name}] runtime create…")
        rc, o, err = _cli.run_cli(["openclaw", "senpi", "runtime", "create", "-p", str(build),
                                   "--runtime-id", inst.runtime_name], timeout=120)
        if rc != 0:
            cause = _cli.error_tail(err, o) or "runtime create failed (no error output)"
            s.update(error="[E_RUNTIME_REGISTER_FAILED] " + cause)
            save_state(pkg, st)
            continue  # keep the rendered yaml on failure for debugging
        s.update(status="registered", error=None)
        save_state(pkg, st)
        _safe_unlink(build)  # registered — the runtime holds its own config; remove the rendered wallet-bearing yaml

    if a.dry_run:
        return report(pkg, st, "planned", as_json=a.json)
    failed = [i.name for i in pkg.instances if inst_state(st, i.name).get("error")]
    overall = "failed" if failed else "registered"
    note = ("Some instances failed to register — see errors above." if failed else
            "Registered — now confirm it's actually live: run `deploy.py verify " + pkg.id + _scope_flag(a) + "`. "
            "That gate checks every instance is runtime-running + scanner-active + DSL-wired + funded; "
            "the strategy is NOT live until it passes. (verify does not wait for the first scan tick.)")
    return report(pkg, st, overall, note=note, as_json=a.json)


# ---------- step 3: verify ticking ----------

def _deep_find_scanner(obj, name):
    if isinstance(obj, dict):
        if _cli.dig(obj, "name", "scanner", "scannerName") == name and any(
                k.lower() in ("runcount", "lastrunfinishedat", "lastrunstartedat", "ticks") for k in obj):
            return obj
        for v in obj.values():
            r = _deep_find_scanner(v, name)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find_scanner(v, name)
            if r:
                return r
    return None


def _scanner_verdict(inst, state, status):
    """(status, detail) for the instance's external_scanner — judged from what the RUNTIME reports,
    and NEVER failing closed on a read it couldn't get. Called ONLY after the caller has confirmed the
    runtime is RUNNING (via `runtime list`), so the reliable backbone is already established here.
      ticked     — runCount>0 / heartbeat (lastAliveAt) / runtime-reported healthy — actually scanning
      scheduled  — registered + enabled + healthy, no tick yet (first tick fires on interval_seconds)
      supervised — reads unavailable this pass, but the runtime is running and supervising the declared
                   scanner ⇒ live-but-unmeasured (the flaky status/state reads are enrichment, not a gate)
      broken     — POSITIVE evidence of breakage: disabled / erroring / runtime-reported unhealthy

    `ticked`/`scheduled`/`supervised` all count as LIVE. Two enrichment sources, keyed by the stable
    scanner name (== the runtime's `scannerId`):
      • `senpi state`  — the rich per-scanner row (runCount, lastAliveAt, lastError, enabled, health).
        Best when available, but `getSystemState` THROWS for minutes after a fresh start, so `state`
        is often None here (see `_cli.runtime_state`).
      • `senpi status` — the runtime's own per-scanner health verdict. Lighter, and it keeps
        answering while `state` is still throwing — so it's the fallback that keeps a live-but-not-
        -yet-introspectable scanner from being branded dead.

    IMPORTANT — external scanners: runCount/lastRun stay 0/null until the runtime hears the first
    POST, and a healthy scanner that finds no setup still ticks (barren heartbeat). So absence of
    runs is NEVER breakage on its own; `health`/`lastAliveAt` and the `status` verdict carry the
    truth. (Live-confirmed: a runtime whose `state` threw for ~9 min while both scanners logged and
    `status` said '2/2 enabled and healthy' — the old code called that 'scanner not mounted'.)"""
    name = inst.external_scanner.get("name")
    # POSITIVE wiring-failure evidence first (B1): the health payload's scanners component carries
    # `unwired: true` (+ the failed phase) when the runtime is up but its entry scanners never wired
    # — `runtime list` shows it as "running — NO ENTRY SCANNERS". A running-but-blind runtime must
    # never fall through to `supervised` = live on unreadable per-scanner rows (there are none).
    if status:
        scanners_comp = _cli._deep_first(status, ["scanners"])
        if isinstance(scanners_comp, dict) and _cli.dig(scanners_comp, "unwired") is True:
            phase = _cli.dig(scanners_comp, "unwiredPhase") or "wiring"
            return "broken", f"entry scanners never wired ({phase} failed; see `senpi events`)"
    sc = _deep_find_scanner(state, name) if state else None
    if sc:
        if _cli.dig(sc, "enabled", default=True) is False:
            return "broken", "scanner disabled"
        err = _cli.dig(sc, "lastError")
        cec = _cli.dig(sc, "consecutiveErrorCount", default=0) or 0
        if err or (isinstance(cec, (int, float)) and cec >= 1):
            return "broken", f"scanner erroring: {str(err)[:120] if err else f'{int(cec)} consecutive errors'}"
        if str(_cli.dig(sc, "health") or "").lower() == "unhealthy":
            return "broken", "scanner reported unhealthy by the runtime"
        runs = _cli.dig(sc, "runCount", "ticks", "runs", default=0) or 0
        if isinstance(runs, (int, float)) and runs > 0:
            return "ticked", f"{int(runs)} scan(s)"
        if _cli.dig(sc, "lastAliveAt"):
            return "ticked", "external scanner heartbeat live"
        return "scheduled", f"awaiting first tick (~{inst.interval_seconds or '?'}s cadence)"
    # `state` unreadable → trust the runtime's own scanner-health from `senpi status`
    sh = _cli.scanner_health_in_status(status, name)
    if sh == "unhealthy":
        return "broken", "scanner reported unhealthy by the runtime (per status)"
    if sh in ("healthy", "degraded"):
        return "ticked", f"healthy per runtime status ({sh})"
    # Neither `state` nor `status` was readable this pass — but we only reach here AFTER the caller
    # confirmed the runtime is RUNNING via `runtime list` (the authoritative inventory; `status`/`state`
    # JSON are flaky-empty for a minute+ after start — seen live: verify got nothing while a manual
    # `status -r`/`state -r` seconds apart returned healthy). A running runtime SPAWNS + SUPERVISES this
    # external scanner (restarting it on crash), and the scanner is declared in the deployed runtime.yaml
    # — so running runtime + declared scanner ⇒ it's being driven. Report LIVE-but-unmeasured, never
    # `broken`. A genuinely broken scanner still trips the `broken` branches above whenever a read lands.
    return "supervised", "runtime running + scanner supervised (live health read unavailable this pass)"


def _dsl_verdict(inst, status_json):
    """(status, detail) for DSL protection. The STATIC check — the deployed runtime.yaml has an
    `exit.dsl_preset` — is load-bearing (it closes the funded-but-no-DSL hole); the runtime monitor's
    `enabled` flag (from `senpi status`) confirms it wired. If status is unreadable we trust the static
    config — never fail the gate on an unreadable status alone."""
    if not inst.has_dsl:
        return "config-missing", "runtime.yaml has no exit.dsl_preset — positions would run naked"
    dsl = _cli._deep_first(status_json, ["dsl"]) if status_json else None
    if isinstance(dsl, dict) and _cli.dig(dsl, "enabled") is False:
        return "monitor-down", "DSL configured but its monitor is disabled in the runtime"
    return "wired", "exit.dsl_preset present; DSL monitor active"


def _budget_verdict(s, funded_by_id):
    """(status, detail) comparing the wallet's ACTUAL funded USDC to what was requested. Best-effort: if
    we can't read the funded amount we don't block (the create-time shortfall halt is the primary guard;
    this reconciliation also catches a backend partial-fund)."""
    req = s.get("requested")
    funded = funded_by_id.get(s.get("strategyId"))
    if not req or funded is None:
        return "ok", (f"${funded:g}" if isinstance(funded, (int, float)) else "")
    if funded < req * 0.9:
        return "underfunded", f"funded ${funded:g} of requested ${req:g}"
    return "ok", f"${funded:g} (asked ${req:g})"


def _check_live(pkg, st, mcp):
    """One pass over every instance → the composite live verdict: runtime running AND scanner active AND
    DSL wired AND budget funded. Returns a list of per-instance rows."""
    # one strategy_list read → actual funded amount per strategyId (best-effort budget reconciliation)
    funded_by_id = {}
    try:
        for m in _cli.strategies_for(mcp, skill_name=pkg.id, timeout=POLL_HTTP_TIMEOUT):
            fid = _cli.strategy_id_of(m)
            fv = _cli.dig(_cli.strategy_obj(m), "totalFunded", "netFunded", "initialBudget")
            if fid and isinstance(fv, (int, float)):
                funded_by_id[fid] = float(fv)
    except Exception:  # noqa: BLE001 — a read hiccup must not fail the gate; budget stays best-effort
        pass
    health = _cli.runtime_health_map()  # one status --json for all runtimes' DSL/health lines
    rows = []
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        # `runtime_health_map` (getHealthStatus) lists ONLY running runtimes, so a hit already proves
        # 'running' — skip the extra `runtime list` call (its default 60s timeout is verify's worst
        # tail-latency) in the common path. Only when the map is flaky-empty for this runtime do we
        # fall back to the authoritative text list to tell 'not running' from 'status hiccup'.
        status = health.get(inst.runtime_name)
        no_entry = False
        if status:
            running = True
        else:
            rt = _cli.find_runtime(inst.runtime_name)
            running = bool(rt) and _cli.runtime_running(rt)
            # `runtime list` marking the runtime "running — NO ENTRY SCANNERS" is positive
            # wiring-failure evidence from the authoritative inventory itself — it must brand the
            # scanner broken even when the status/state JSON is unreadable this pass (otherwise the
            # empty reads would fall through to `supervised` = live on a runtime that cannot scan).
            no_entry = running and _cli.runtime_no_entry_scanners(rt)
            if running:
                status = _cli.runtime_status(inst.runtime_name, POLL_HTTP_TIMEOUT)
        if not running:
            rows.append({"instance": inst.name, "live": False, "scanner": "no-runtime",
                         "dsl": "-", "budget": "-", "reason": "runtime not running"})
            continue
        state = _cli.runtime_state(inst.runtime_name, POLL_HTTP_TIMEOUT)
        if no_entry:
            sc_st, sc_d = "broken", "entry scanners never wired (runtime list: NO ENTRY SCANNERS; see `senpi events`)"
        else:
            sc_st, sc_d = _scanner_verdict(inst, state, status)
        dsl_st, dsl_d = _dsl_verdict(inst, status)
        bud_st, bud_d = _budget_verdict(s, funded_by_id)
        sc_live = sc_st in ("ticked", "scheduled", "supervised")
        live = sc_live and dsl_st == "wired" and bud_st == "ok"
        s["status"] = "live" if live else s.get("status", "registered")
        save_state(pkg, st)
        reason = "; ".join(d for ok, d in
                           ((sc_live, sc_d), (dsl_st == "wired", dsl_d),
                            (bud_st == "ok", bud_d)) if not ok and d)
        rows.append({"instance": inst.name, "live": live, "scanner": sc_st, "dsl": dsl_st,
                     "budget": bud_st, "reason": reason})
    return rows


def cmd_verify(pkg, a, log):
    # THE liveness gate: a strategy is `live` only when EVERY instance has a running runtime + an active
    # scanner (ticked / scheduled / supervised) + a wired DSL + a funded budget. The reliable backbone is
    # `runtime list` (running) + the deployed runtime.yaml (scanner + DSL preset) + MCP budget — none of
    # which depend on the flaky `status`/`state` JSON; those only DOWNGRADE a scanner to `broken` on
    # positive evidence. It does NOT wait for a scan tick (a scheduled/supervised scanner is already
    # live); --max-wait only re-checks a runtime that hasn't finished registering yet.
    mcp = MCPClient()
    st = load_state(pkg)
    deadline = time.time() + a.max_wait
    while True:
        rows = _check_live(pkg, st, mcp)
        live = bool(rows) and all(r["live"] for r in rows)
        if live or time.time() >= deadline:
            status = "live" if live else "not-live"
            out = {"strategy": pkg.id, "version": pkg.version, "status": status, "instances": rows}
            if a.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"\n{pkg.id} v{pkg.version}: {status}")
                for r in rows:
                    print(f"  - {r['instance']}: scanner={r['scanner']}, dsl={r['dsl']}, "
                          f"budget={r['budget']}" + (f"  → {r['reason']}" if r["reason"] else ""))
                if not live:
                    print(f"\nNOT live — fix the flagged component(s) and re-run `deploy.py verify {pkg.id}{_scope_flag(a)}`. "
                          "A strategy is live only when every instance is runtime-running + scanner-active "
                          "+ DSL-wired + funded.")
            if live:
                delete_state(pkg)  # deploy complete → state is ephemeral; next deploy starts clean
            return out
        log("  re-checking liveness…")
        time.sleep(POLL_EVERY)


# ---------- upgrade: apply an edited package to a LIVE strategy (close → redeploy fresh) ----------

def _emit(a, log, out):
    """Emit an upgrade PHASE-A verdict. These are hand-built dicts, not `report()` rows, so they need the
    same --json handling report() gives: under --json `log` is a no-op, so without this the consent text —
    the one thing an agent must relay to the user before a flatten — would reach an empty stdout."""
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        log("\n  " + out["note"])
    return out


def cmd_upgrade(pkg, a, log):
    """Resumable single-arm UPGRADE — apply an edited scan.py / scoring.py / runtime.yaml to a LIVE
    strategy by closing it and redeploying on a FRESH wallet, one step per call (the way `create` resumes).
    The supported way to re-score / re-scan / re-tune a deployed strategy until in-place scanner reload
    lands, at which point the close/redeploy guts swap for it. Two invariants:
      • routes through the tested `close → create → runtime → verify` path, so the runtime yaml is rendered
        INSIDE the instance dir (`./scanners` resolves) on a FRESH wallet — never a hand-rendered root yaml
        or a raw `strategy_create_custom_strategy` (the naked-wallet / "NO ENTRY SCANNERS" traps);
      • consent-gated — closing a live arm market-exits its positions, so it refuses without `--yes`.
    Per arm (`--instance <arm>`), siblings untouched. State: the deploy-state `_upgrade` block, phase
    `closing` (async flatten in flight) → `redeploy`."""
    # `upgrade` acts on ONE arm. main() already narrowed pkg to a single arm when --instance was given;
    # a still-multi-instance pkg here means the caller didn't name which sleeve to upgrade.
    if len(pkg.instances) != 1:
        names = ", ".join(i.name for i in pkg.instances if i.name)
        raise SystemExit(
            f"error: `upgrade` acts on ONE arm at a time — pass --instance <arm> (have: {names}). "
            f"Each arm is closed and redeployed on its own fresh wallet; siblings keep running.")
    if a.budget is None and not a.dry_run:
        raise SystemExit("error: --budget <usd> is required for `upgrade` — it funds the FRESH wallet the "
                         "arm is redeployed onto (the old wallet is retired on close).")
    inst = pkg.instances[0]
    mcp = MCPClient()
    st = load_state(pkg)
    up = st.get("_upgrade") or {}
    rerun = (f"python3 {Path(__file__).name} upgrade {pkg.id} --instance {inst.name}"
             + (f" --budget {budget_arg(a.budget)}" if a.budget is not None else ""))

    # ---------- PHASE A: close the currently-live arm (once), consent-gated ----------
    # Skipped on --dry-run: the preview must be side-effect + network free, so it routes straight to the
    # create dry-run without probing the backend for a live arm.
    if not a.dry_run and up.get("phase") != "redeploy":
        blocked = lambda why: _emit(a, log, {  # noqa: E731 — one refusal shape, reused
            "strategy": pkg.id, "instance": inst.name, "status": "blocked",
            "note": f"[E_STATE_AMBIGUOUS] {why} Refusing so upgrade can't skip consent or fund a second "
                    f"wallet. Triage read-only first, then resolve WITH THE USER:\n"
                    f"      python3 {Path(__file__).with_name('status.py').name} {pkg.id}"})

        if up.get("phase") == "closing":
            # A close was triggered on a prior call. strategy_close is async — poll the strategyIds we
            # CLOSED, directly. (Name-matching here would false-report `closed`: `close_one` deletes the
            # runtime, and a CLOSING strategy has already left ACTIVE, so the name fallback returns nothing
            # while the flatten is still in flight.) Read fail-CLOSED: on a failed read, keep waiting.
            ids = set(up.get("closing_ids") or [])
            rows = _cli.strategies_for_or_none(mcp, skill_name=pkg.id)
            if rows is None:
                return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                      "note": f"couldn't read strategy_list — re-run `{rerun}` to keep polling the close."})
            if [s for s in rows if _cli.strategy_open(s) and _cli.strategy_id_of(s) in ids]:
                return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                      "note": f"old {inst.runtime_name} still flattening — re-run `{rerun}` to continue."})
            up["phase"] = "redeploy"; up.pop("closing_ids", None); st["_upgrade"] = up; save_state(pkg, st)
            return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closed",
                                  "note": f"old arm closed, funds returning to main. Re-run `{rerun}` to redeploy on "
                                          f"a FRESH wallet. (If create reports `underfunded`, the funds are still "
                                          f"returning — wait a moment and re-run.)"})

        # START. Resolve the arm's wallet via the shared tri-state resolver, and REFUSE on anything but a
        # clean resolve or a verified-absent `none` — an unreadable/ambiguous backend must not disarm the
        # consent gate + double-fund guard (fund a fresh wallet next to an unread live one).
        arm_wallet, arm_kind = _arm_wallet(pkg, inst, mcp)
        if arm_kind in ("unreadable", "unnamed", "ambiguous"):
            return blocked(f"can't safely resolve `{inst.runtime_name}`'s wallet ({arm_kind}).")

        open_mine = []
        if arm_wallet:
            rows = _cli.strategies_for_or_none(mcp, wallet=arm_wallet)  # fail-CLOSED — never fund on a blind read
            if rows is None:
                return blocked(f"couldn't read strategy_list to confirm `{inst.runtime_name}`'s open book.")
            open_mine = [s for s in rows if _cli.strategy_open(s)]

        if open_mine:
            # The arm is LIVE. Closing it MARKET-EXITS its positions — never do that silently.
            if not a.yes:
                return _emit(a, log, {
                    "strategy": pkg.id, "instance": inst.name, "status": "needs-consent", "wallet": arm_wallet,
                    "note": (f"UPGRADE will CLOSE the live `{inst.runtime_name}` on wallet {arm_wallet}: it "
                             f"market-exits any open position (often NONE if the strategy isn't entering — that's "
                             f"the usual re-tune case), returns funds to your main wallet, and redeploys the edited "
                             f"arm on a FRESH wallet. The old wallet is retired, and a custom ratchet/stop ladder on "
                             f"the old positions does NOT carry over — re-apply it after if wanted. Confirm with the "
                             f"user, then re-run with --yes:\n      {rerun} --yes")})
            # consent given → close THIS arm via the tested close primitive, remembering its strategyIds so
            # the closing-wait can poll them directly, then hand off to redeploy on a fresh wallet.
            import close as _close  # noqa: E402 — sibling module, lazy import
            runtimes = _cli.list_runtimes()
            recs = [_close.close_one(pkg.id, s, runtimes, False, log) for s in open_mine]
            bad = [r for r in recs if r.get("status") == "failed"]
            if bad:
                # A failed close (runtime still listed after two delete attempts → it may re-enter
                # positions) must SURFACE, not be swallowed. State stays put, so the next run re-attempts;
                # advancing to `closing` would poll a strategy nothing is closing, forever.
                return _emit(a, log, {
                    "strategy": pkg.id, "instance": inst.name, "status": "failed",
                    "note": "close FAILED, nothing redeployed: "
                            + "; ".join(str(r.get("error") or "?") for r in bad)
                            + f"\n      Resolve it, then re-run `{rerun} --yes`."})
            st["instances"][inst.name] = {"status": "pending"}  # forget the old id → create makes a FRESH wallet
            st["_upgrade"] = {"phase": "closing",
                              "closing_ids": [_cli.strategy_id_of(s) for s in open_mine if _cli.strategy_id_of(s)]}
            save_state(pkg, st)
            return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                  "note": (f"Closing `{inst.runtime_name}` (flatten positions + return funds; "
                                           f"async). Re-run `{rerun}` to redeploy once it's closed.")})

        # Nothing live to close, reached two ways: arm_kind == "none" (verified absent — genuinely not
        # deployed), OR a wallet resolved but its strategy is already closed so `open_mine` is empty (a
        # runtime-less trap `create`'s own live-guard then backstops). Either way → straight to redeploy.
        up["phase"] = "redeploy"; st["_upgrade"] = up
        save_state(pkg, st)

    # ---------- PHASE B: redeploy the arm on a fresh wallet — one resumable step per call ----------
    s = inst_state(st, inst.name)
    status = s.get("status")
    if a.dry_run or not s.get("strategyId") or status in (None, "pending", "creating"):
        return cmd_create(pkg, a, log)
    if status == "active":
        return cmd_runtime(pkg, a, log)
    # registered → a fast single check (max_wait=0); verify deletes state on `live`, clearing _upgrade too.
    av = argparse.Namespace(**{**vars(a), "max_wait": 0})
    out = cmd_verify(pkg, av, log)
    if out.get("status") != "live" and st.get("_upgrade"):
        # Registered but NOT live mid-upgrade (e.g. the edited scanner is broken — exactly when the user
        # re-edits and re-runs). Verify-only would loop forever and the re-edit would never re-render
        # (cmd_runtime idempotent-skips the same wallet). Drop the runtime + reset to `active` so the next
        # run re-registers the CURRENT on-disk edit instead of re-judging the stale deployment.
        _cli.run_cli(["openclaw", "senpi", "runtime", "delete", inst.runtime_name], timeout=60)
        s["status"] = "active"
        save_state(pkg, st)
        return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "not-live",
                              "note": f"redeploy registered but not live yet (scanner unconfirmed). Re-run "
                                      f"`{rerun}` — it re-registers the current edit (fix the scanner on disk "
                                      f"first if it's broken)."})
    return out


# ---------- cli ----------

def _exit_code(status):
    """Process exit code for a command result. **0** = done / informational; **2** = refused or failed
    (action required — `failed`/`underfunded`/`not-live`/`needs-consent`/`blocked`); **3** = RESUMABLE,
    re-run (in-flight). `closing`/`closed` exit 3 — NOT 0 — so a `$?`/`&&` caller can't misread upgrade's
    most dangerous in-flight state (`closed`: the old arm is gone, funds are back in main, and NOTHING is
    deployed yet) as "done" and stop, stranding the user's capital. These two statuses are emitted only by
    `upgrade`; the standalone deploy steps keep their existing exit-0 done-for-this-step semantics."""
    if status in ("failed", "underfunded", "not-live", "needs-consent", "blocked"):
        return 2
    if status in ("closing", "closed"):
        return 3
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="Deploy a strategy package in resumable steps.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("package", help="Strategy id (e.g. spider) or package dir (strategies/spider).")
        p.add_argument("--ref", default=None, help="Branch/ref to fetch the package from if not on disk.")
        p.add_argument("--json", action="store_true")

    # --instance scopes create/runtime/verify to ONE arm of a multi-instance package (single-arm
    # redeploy / upgrade), leaving its siblings running. Same mapping as close.py --instance.
    def _inst(p):
        p.add_argument("--instance", default=None,
                       help="Scope to ONE instance of a multi-arm package (siblings untouched).")

    pc = sub.add_parser("create", help="Step 1: create + fund the strategy wallet(s) (resumable).")
    common(pc); _inst(pc)
    pc.add_argument("--budget", type=float, default=None, help="Total USDC split across wallets by funding_share.")
    pc.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="Poll budget for this call (s).")
    pc.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("runtime", help="Step 2: render + create the runtime(s) on the ready wallet(s).")
    common(pr); _inst(pr)
    pr.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pr.add_argument("--dry-run", action="store_true")

    pv = sub.add_parser("verify", help="Step 3: confirm each scanner is ticking (fast single check; re-run as needed).")
    common(pv); _inst(pv)
    pv.add_argument("--max-wait", type=int, default=0,
                    help="Default 0 = one fast check (first tick is gated by interval_seconds; re-run later). "
                         ">0 keeps polling up to S seconds (useful for fast instances).")

    pu = sub.add_parser("upgrade",
                        help="Apply an edited scan.py/scoring.py/runtime.yaml to a LIVE strategy: close the "
                             "arm + redeploy on a FRESH wallet (resumable; consent-gated). Per arm.")
    common(pu); _inst(pu)
    pu.add_argument("--budget", type=float, default=None,
                    help="USDC to fund the fresh wallet the arm is redeployed onto (required).")
    pu.add_argument("--yes", action="store_true",
                    help="Consent to FLATTEN: closing the live arm market-exits its open positions. Required "
                         "to proceed while the arm holds a book.")
    pu.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pu.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="Poll budget for the create step (s).")
    pu.add_argument("--dry-run", action="store_true", help="Show the plan (routes to the create dry-run) with no side effects.")

    ps = sub.add_parser("status", help="Show the deploy state.")
    common(ps)

    pval = sub.add_parser("validate",
                          help="Preflight: is the package deploy-ready? (structural + render — no side effects)")
    common(pval)

    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    pkg = ensure_pkg(a.package, a.ref, log)

    # single-arm scoping — narrow a multi-instance package to ONE arm so create/runtime/verify (and the
    # create live-guard) act on it alone, leaving siblings running. Applied BEFORE validate so we gate
    # only the arm being touched.
    if getattr(a, "instance", None):
        _scope_pkg(pkg, a.instance)

    # `validate` is the standalone, side-effect-free preflight; `create` AND `upgrade` run the SAME full
    # check before touching any wallet — this is what catches a bad `./scanners` path (the wrong-directory
    # trap) BEFORE an upgrade closes a live book; runtime/verify/status keep the structural gate.
    gate = full_validate(pkg) if a.cmd in ("validate", "create", "upgrade") else _pkg.validate(pkg)
    if a.cmd == "validate":
        if a.json:
            print(json.dumps({"status": "valid" if not gate else "invalid", "id": pkg.id, "errors": gate}))
        elif gate:
            print(f"✗ {pkg.id}: {len(gate)} issue(s) to fix before deploy:", file=sys.stderr)
            for e in gate:
                print(f"    - {e}", file=sys.stderr)
        else:
            print(f"✓ {pkg.id}: deploy-ready ({len(pkg.instances)} instance(s))")
        sys.exit(2 if gate else 0)
    if gate:
        print(f"✗ {pkg.id}: {len(gate)} issue(s) to fix before deploy:", file=sys.stderr)
        for e in gate:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    if a.cmd == "create":
        out = cmd_create(pkg, a, log)
    elif a.cmd == "runtime":
        out = cmd_runtime(pkg, a, log)
    elif a.cmd == "verify":
        out = cmd_verify(pkg, a, log)
    elif a.cmd == "upgrade":
        out = cmd_upgrade(pkg, a, log)
    else:  # status
        out = report(pkg, load_state(pkg), "status", as_json=a.json)

    sys.exit(_exit_code(out.get("status")))


if __name__ == "__main__":
    main(sys.argv)
